from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("trellis_gc", ROOT / "scripts" / "trellis_gc.py")
assert SPEC and SPEC.loader
trellis_gc = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = trellis_gc
SPEC.loader.exec_module(trellis_gc)


GH_SHIM = """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
if len(args) < 3 or args[0] != "pr" or args[1] != "view":
    print("unsupported gh invocation: " + " ".join(args), file=sys.stderr)
    raise SystemExit(1)
target = args[2]
state_path = os.environ.get("GH_SHIM_FILE")
if not state_path:
    print("GH_SHIM_FILE unset", file=sys.stderr)
    raise SystemExit(1)
payload = json.loads(open(state_path, encoding="utf-8").read())
entry = payload.get(target)
if not isinstance(entry, dict):
    print(f"no pull request for {target}", file=sys.stderr)
    raise SystemExit(1)
if entry.get("exit"):
    print(entry.get("stderr") or "fail", file=sys.stderr)
    raise SystemExit(int(entry["exit"]))
fields = []
if "--json" in args:
    fields = args[args.index("--json") + 1].split(",")
body = {key: entry[key] for key in fields if key in entry} if fields else entry
print(json.dumps(body))
"""


class Sandbox:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.origin = self.path / "origin.git"
        self.repo = self.path / "repo"
        self.gh_file = self.path / "gh.json"
        self.bin = self.path / "bin"
        self.gh_file.write_text("{}\n", encoding="utf-8")
        self.bin.mkdir()
        shim = self.bin / "gh"
        shim.write_text(GH_SHIM, encoding="utf-8")
        shim.chmod(0o755)
        self._init_repo()

    def cleanup(self) -> None:
        self.tmp.cleanup()

    def _git_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = "Trellis GC Test"
        env["GIT_AUTHOR_EMAIL"] = "gc-test@example.com"
        env["GIT_COMMITTER_NAME"] = "Trellis GC Test"
        env["GIT_COMMITTER_EMAIL"] = "gc-test@example.com"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def git(
        self,
        *args: str,
        cwd: Path | None = None,
        check: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = self._git_env()
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if check and proc.returncode != 0:
            raise AssertionError(
                f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
            )
        return proc

    def _init_repo(self) -> None:
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(self.origin)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "init", "-b", "main", str(self.repo)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.git("config", "user.name", "Trellis GC Test")
        self.git("config", "user.email", "gc-test@example.com")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-m", "base")
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "-u", "origin", "main")
        self.git("symbolic-ref", "HEAD", "refs/heads/main", cwd=self.origin)

    def set_pr(self, key: str, state: str, oid: str, **extra: object) -> None:
        data = json.loads(self.gh_file.read_text(encoding="utf-8"))
        data[key] = {"state": state, "headRefOid": oid, **extra}
        self.gh_file.write_text(json.dumps(data), encoding="utf-8")

    def branch_exists(self, name: str) -> bool:
        proc = self.git("show-ref", "--verify", "--quiet", f"refs/heads/{name}", check=False)
        return proc.returncode == 0

    def head(self, rev: str, cwd: Path | None = None) -> str:
        return self.git("rev-parse", rev, cwd=cwd).stdout.strip()

    def write_task(
        self,
        name: str,
        *,
        branch: str | None,
        status: str = "completed",
        archived: bool = False,
        pr_url: str | None = None,
        children: list[str] | None = None,
        month: str = "2026-09",
    ) -> Path:
        if archived:
            directory = self.repo / ".trellis" / "tasks" / "archive" / month / name
        else:
            directory = self.repo / ".trellis" / "tasks" / name
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": name,
            "name": name,
            "status": status,
            "branch": branch,
            "base_branch": "main",
            "pr_url": pr_url,
            "children": children or [],
        }
        (directory / "task.json").write_text(json.dumps(payload), encoding="utf-8")
        return directory

    def write_guard(self, task: str, branch: str, phase: str = "active") -> Path:
        common = Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
        directory = common / "trellis-guard"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{task}.json"
        path.write_text(
            json.dumps({"task": task, "branch": branch, "phase": phase}),
            encoding="utf-8",
        )
        return path

    def push_main(self) -> None:
        self.git("push", "origin", "main")

    def make_verified_task(
        self,
        branch: str = "task/test",
        *,
        gone: bool = True,
        worktree: bool = True,
        pr_head: str | None = None,
    ) -> Path | None:
        wt: Path | None = None
        if worktree:
            slug = branch.replace("/", "-")
            wt = self.repo.parent / f"{self.repo.name}-wt" / slug
            wt.parent.mkdir(parents=True, exist_ok=True)
            self.git("worktree", "add", "-b", branch, str(wt), "main")
            (wt / "tracked.txt").write_text(f"{branch}\n", encoding="utf-8")
            self.git("add", "tracked.txt", cwd=wt)
            self.git("commit", "-m", f"work on {branch}", cwd=wt)
        else:
            self.git("checkout", "-b", branch)
            (self.repo / "tracked.txt").write_text(f"{branch}\n", encoding="utf-8")
            self.git("add", "tracked.txt")
            self.git("commit", "-m", f"work on {branch}")
        self.git("push", "-u", "origin", branch)
        if gone:
            self.git("push", "origin", f":{branch}")
            self.git("fetch", "--prune", "origin")
        local = self.head(branch)
        self.set_pr(branch, "MERGED", pr_head if pr_head is not None else local)
        return wt

    def run_gc(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        backup = {key: os.environ.get(key) for key in ("PATH", "GH_SHIM_FILE", "PYTHONDONTWRITEBYTECODE")}
        os.environ["PATH"] = str(self.bin) + os.pathsep + os.environ.get("PATH", "")
        os.environ["GH_SHIM_FILE"] = str(self.gh_file)
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        previous = os.getcwd()
        os.chdir(cwd or self.repo)
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = trellis_gc.main(list(args))
        finally:
            os.chdir(previous)
            for key, value in backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return code, stdout.getvalue(), stderr.getvalue()


class GarbageCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = Sandbox()
        self.addCleanup(self.sandbox.cleanup)

    def test_apply_removes_verified_clean_candidate(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[DONE] removed worktree", stdout)
        self.assertIn("proof=pr", stdout)
        self.assertIn("[INFO] default branch: origin/main", stdout)
        self.assertFalse(self.sandbox.branch_exists("task/test"))
        self.assertFalse(wt.exists())
        self.assertIn("removed=1 archived=0 skipped=0 managed_by_dispatch=0 pending_push=0", stdout)

    def test_live_upstream_is_retained(self) -> None:
        wt = self.sandbox.make_verified_task(gone=False)
        assert wt is not None
        self.sandbox.write_task("test", branch="task/test", archived=True)
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/test: tree_differs", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/test"))
        self.assertTrue(wt.is_dir())
        self.assertNotIn("[DONE] removed", stdout)

    def test_dirty_worktree_is_retained(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        (wt / "local.txt").write_text("dirty\n", encoding="utf-8")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/test: dirty", stdout)
        self.assertIn("local.txt", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/test"))
        self.assertTrue(wt.is_dir())

    def test_force_dirty_removes_verified_dirty_candidate(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        (wt / "local.txt").write_text("dirty\n", encoding="utf-8")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch", "--force-dirty")
        self.assertEqual(code, 0)
        self.assertIn("[DONE] removed worktree", stdout)
        self.assertIn("proof=pr", stdout)
        self.assertFalse(self.sandbox.branch_exists("task/test"))
        self.assertFalse(wt.exists())

    def test_force_dirty_does_not_override_unverified(self) -> None:
        wt = self.sandbox.make_verified_task(pr_head="deadbeef" * 5)
        assert wt is not None
        self.sandbox.write_task("test", branch="task/test", archived=True)
        (wt / "local.txt").write_text("dirty\n", encoding="utf-8")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch", "--force-dirty")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/test: tree_differs", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/test"))
        self.assertTrue(wt.is_dir())
        self.assertNotIn("[DONE] removed", stdout)

    def test_local_head_after_merge_is_retained(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        merged = self.sandbox.head("task/test")
        self.sandbox.git("checkout", "main")
        self.sandbox.git("merge", "--ff-only", "task/test")
        self.sandbox.push_main()
        (wt / "tracked.txt").write_text("after merge\n", encoding="utf-8")
        self.sandbox.git("add", "tracked.txt", cwd=wt)
        self.sandbox.git("commit", "-m", "after merge", cwd=wt)
        self.sandbox.set_pr("task/test", "MERGED", merged)
        self.sandbox.write_task("test", branch="task/test", archived=True)
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/test: tree_differs", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/test"))
        self.assertTrue(wt.is_dir())

    def test_apply_refuses_failed_fetch(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        self.sandbox.git("remote", "set-url", "origin", str(self.sandbox.path / "missing.git"))
        code, _, stderr = self.sandbox.run_gc("--apply")
        self.assertEqual(code, 1)
        self.assertIn("refusing --apply", stderr)
        self.assertTrue(self.sandbox.branch_exists("task/test"))
        self.assertTrue(wt.is_dir())

    def test_current_branch_is_retained(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch", cwd=wt)
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/test: current_worktree", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/test"))
        self.assertTrue(wt.is_dir())

    def test_main_worktree_is_retained(self) -> None:
        self.sandbox.make_verified_task(worktree=False)
        side = self.sandbox.path / "side"
        self.sandbox.git("worktree", "add", str(side), "main")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch", cwd=side)
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/test: main_worktree", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/test"))
        self.assertTrue((self.sandbox.repo / "tracked.txt").is_file())

    def test_default_prefix_ignores_other_branches(self) -> None:
        self.sandbox.git("branch", "task/keep", "main")
        self.sandbox.git("branch", "extra/one", "main")
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/keep:", stdout)
        self.assertNotIn("extra/one", stdout)
        self.assertIn("managed_by_dispatch=0", stdout)

    def test_repeated_prefix_scans_each_prefix(self) -> None:
        self.sandbox.git("branch", "task/keep", "main")
        self.sandbox.git("branch", "extra/one", "main")
        code, stdout, _ = self.sandbox.run_gc("--no-fetch", "--prefix", "extra/", "--prefix", "task/")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] extra/one:", stdout)
        self.assertIn("[SKIP] task/keep:", stdout)
        only_extra, extra_out, _ = self.sandbox.run_gc("--no-fetch", "--prefix", "extra/")
        self.assertEqual(only_extra, 0)
        self.assertIn("[SKIP] extra/one:", extra_out)
        self.assertNotIn("task/keep", extra_out)

    def test_explicit_prefix_can_remove_that_prefix_only(self) -> None:
        extra = self.sandbox.make_verified_task("extra/one")
        task = self.sandbox.make_verified_task("task/test")
        assert extra is not None and task is not None
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch", "--prefix", "extra/")
        self.assertEqual(code, 0)
        self.assertFalse(self.sandbox.branch_exists("extra/one"))
        self.assertFalse(extra.exists())
        self.assertTrue(self.sandbox.branch_exists("task/test"))
        self.assertTrue(task.is_dir())
        self.assertIn("removed=1", stdout)

    def test_card_and_run_are_reported_not_removed(self) -> None:
        herdr = self.sandbox.path / "state" / "herdr-dispatch" / "runs" / "enf" / "worktrees" / "E1"
        run_wt = self.sandbox.repo.parent / "repo-wt" / "enf" / "integration"
        self.sandbox.git("branch", "card/enf/E1", "main")
        self.sandbox.git("branch", "run/enf/integration", "main")
        self.sandbox.git("branch", "card/bare", "main")
        herdr.parent.mkdir(parents=True, exist_ok=True)
        self.sandbox.git("worktree", "add", str(herdr), "card/enf/E1")
        run_wt.parent.mkdir(parents=True, exist_ok=True)
        self.sandbox.git("worktree", "add", str(run_wt), "run/enf/integration")
        for branch in ("card/enf/E1", "run/enf/integration", "card/bare"):
            self.sandbox.git("push", "-u", "origin", branch)
            self.sandbox.git("push", "origin", f":{branch}")
            self.sandbox.set_pr(branch, "MERGED", self.sandbox.head(branch))
        self.sandbox.git("fetch", "--prune", "origin")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn(
            f"[INFO managed_by=herdr-dispatch run cleanup] card/enf/E1 {herdr.resolve()}",
            stdout,
        )
        self.assertIn(
            f"[INFO managed_by=herdr-dispatch run cleanup] run/enf/integration {run_wt.resolve()}",
            stdout,
        )
        self.assertIn("[INFO managed_by=herdr-dispatch run cleanup] card/bare -", stdout)
        for branch in ("card/enf/E1", "run/enf/integration", "card/bare"):
            self.assertTrue(self.sandbox.branch_exists(branch), branch)
        self.assertTrue(herdr.is_dir())
        self.assertTrue(run_wt.is_dir())
        self.assertNotIn("[DONE] removed", stdout)
        self.assertIn("managed_by_dispatch=3", stdout)
        self.assertTrue(stdout.strip().endswith("managed_by_dispatch=3 pending_push=0"))

    def test_empty_run_directory_removed_only_on_apply(self) -> None:
        wt_root = self.sandbox.repo.parent / "repo-wt"
        empty_run = wt_root / "run-123"
        kept = wt_root / "run-keep"
        empty_run.mkdir(parents=True)
        kept.mkdir()
        (kept / "note").write_text("keep\n", encoding="utf-8")
        code, _, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertTrue(empty_run.is_dir())
        code, _, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertFalse(empty_run.exists())
        self.assertTrue(kept.is_dir())
        self.assertTrue(wt_root.is_dir())

    def test_empty_wt_root_is_removed_when_every_child_is_empty(self) -> None:
        wt_root = self.sandbox.repo.parent / "repo-wt"
        (wt_root / "run-123").mkdir(parents=True)
        code, _, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertFalse(wt_root.exists())

    def test_removed_task_worktree_drops_empty_wt_parent(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        parent = wt.parent
        self.assertTrue(parent.name.endswith("-wt"))
        code, _, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertFalse(wt.exists())
        self.assertFalse(parent.exists())

    def test_merge_tree_version_gate(self) -> None:
        self.assertTrue(trellis_gc.version_at_least("git version 2.54.0", (2, 38)))
        self.assertTrue(trellis_gc.version_at_least("git version 2.38.0", (2, 38)))
        self.assertFalse(trellis_gc.version_at_least("git version 2.37.9", (2, 38)))
        self.assertTrue(trellis_gc.merge_tree_supported())

    def test_no_upstream_squash_archived_task_is_removed(self) -> None:
        self.sandbox.git("checkout", "-b", "task/squash")
        (self.sandbox.repo / "feature.txt").write_text("one\n", encoding="utf-8")
        self.sandbox.git("add", "feature.txt")
        self.sandbox.git("commit", "-m", "add one")
        (self.sandbox.repo / "feature.txt").write_text("two\n", encoding="utf-8")
        self.sandbox.git("add", "feature.txt")
        self.sandbox.git("commit", "-m", "edit two")
        self.sandbox.git("checkout", "main")
        self.sandbox.git("merge", "--squash", "task/squash")
        self.sandbox.git("commit", "-m", "squash task/squash")
        self.sandbox.push_main()
        self.sandbox.write_task("squash", branch="task/squash", archived=True)
        code, planned, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[PLAN] would remove local branch task/squash (proof=tree_equal)", planned)
        self.assertTrue(self.sandbox.branch_exists("task/squash"))
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[DONE] removed local branch task/squash (proof=tree_equal)", stdout)
        self.assertFalse(self.sandbox.branch_exists("task/squash"))

    def test_no_upstream_ancestor_archived_task_is_removed(self) -> None:
        self.sandbox.git("checkout", "-b", "task/landed")
        (self.sandbox.repo / "feature.txt").write_text("landed\n", encoding="utf-8")
        self.sandbox.git("add", "feature.txt")
        self.sandbox.git("commit", "-m", "feature")
        self.sandbox.git("checkout", "main")
        self.sandbox.git("merge", "--ff-only", "task/landed")
        self.sandbox.push_main()
        self.sandbox.write_task("landed", branch="task/landed", archived=True)
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("proof=ancestor", stdout)
        self.assertFalse(self.sandbox.branch_exists("task/landed"))

    def test_rebase_then_merge_is_removed(self) -> None:
        self.sandbox.git("checkout", "-b", "task/rebase")
        (self.sandbox.repo / "feature.txt").write_text("from-feature\n", encoding="utf-8")
        self.sandbox.git("add", "feature.txt")
        self.sandbox.git("commit", "-m", "feature work")
        self.sandbox.git("checkout", "main")
        (self.sandbox.repo / "main.txt").write_text("from-main\n", encoding="utf-8")
        self.sandbox.git("add", "main.txt")
        self.sandbox.git("commit", "-m", "main moves")
        self.sandbox.git("checkout", "task/rebase")
        self.sandbox.git("rebase", "main")
        self.sandbox.git("checkout", "main")
        self.sandbox.git("merge", "--ff-only", "task/rebase")
        self.sandbox.push_main()
        self.sandbox.write_task("rebase", branch="task/rebase", archived=True)
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("proof=ancestor", stdout)
        self.assertFalse(self.sandbox.branch_exists("task/rebase"))

    def test_autosquash_then_merge_is_removed(self) -> None:
        base = self.sandbox.head("main")
        self.sandbox.git("checkout", "-b", "task/autosquash")
        (self.sandbox.repo / "fix.txt").write_text("v1\n", encoding="utf-8")
        self.sandbox.git("add", "fix.txt")
        self.sandbox.git("commit", "-m", "first")
        (self.sandbox.repo / "other.txt").write_text("keep\n", encoding="utf-8")
        self.sandbox.git("add", "other.txt")
        self.sandbox.git("commit", "-m", "second")
        (self.sandbox.repo / "fix.txt").write_text("v2\n", encoding="utf-8")
        self.sandbox.git("add", "fix.txt")
        self.sandbox.git("commit", "-m", "fixup! first")
        self.sandbox.git(
            "rebase",
            "-i",
            "--autosquash",
            base,
            extra_env={"GIT_SEQUENCE_EDITOR": "true", "GIT_EDITOR": "true"},
        )
        self.sandbox.git("checkout", "main")
        self.sandbox.git("merge", "--ff-only", "task/autosquash")
        self.sandbox.push_main()
        self.sandbox.write_task("autosquash", branch="task/autosquash", archived=True)
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("proof=ancestor", stdout)
        self.assertFalse(self.sandbox.branch_exists("task/autosquash"))

    def test_add_then_revert_on_target_is_kept(self) -> None:
        base = self.sandbox.head("main")
        (self.sandbox.repo / "patch.txt").write_text("same\n", encoding="utf-8")
        self.sandbox.git("add", "patch.txt")
        self.sandbox.git("commit", "-m", "add patch")
        self.sandbox.git("revert", "--no-edit", "HEAD")
        self.sandbox.push_main()
        self.sandbox.git("checkout", "-b", "task/revert-case", base)
        (self.sandbox.repo / "patch.txt").write_text("same\n", encoding="utf-8")
        self.sandbox.git("add", "patch.txt")
        self.sandbox.git("commit", "-m", "add the same patch")
        self.sandbox.git("checkout", "main")
        self.sandbox.write_task("revert-case", branch="task/revert-case", archived=True)
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/revert-case: tree_differs", stdout)
        self.assertNotIn("proof=ancestor", stdout)
        self.assertNotIn("proof=tree_equal", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/revert-case"))

    def test_independent_same_patch_is_kept(self) -> None:
        # Source adds the patch on its own commit. Main diverges, cherry-picks
        # that patch, then reverts it. patch-id matches; the tree does not.
        self.sandbox.git("checkout", "-b", "task/same-patch")
        (self.sandbox.repo / "patch.txt").write_text("same\n", encoding="utf-8")
        self.sandbox.git("add", "patch.txt")
        self.sandbox.git("commit", "-m", "source patch")
        self.sandbox.git("checkout", "main")
        (self.sandbox.repo / "other.txt").write_text("diverge\n", encoding="utf-8")
        self.sandbox.git("add", "other.txt")
        self.sandbox.git("commit", "-m", "diverge main")
        self.sandbox.git("cherry-pick", "task/same-patch")
        self.sandbox.git("revert", "--no-edit", "HEAD")
        self.sandbox.push_main()
        self.sandbox.write_task("same-patch", branch="task/same-patch", archived=True)
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/same-patch: tree_differs", stdout)
        self.assertNotIn("proof=ancestor", stdout)
        self.assertNotIn("proof=tree_equal", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/same-patch"))

    def test_empty_commits_are_tree_equal_and_removed(self) -> None:
        # Expected: empty commits do not change the tree, so an archived task
        # branch is tree_equal with origin/main and is removed. This is a
        # content proof only; it is not a task-done proof by itself.
        self.sandbox.git("checkout", "-b", "task/empty")
        self.sandbox.git("commit", "--allow-empty", "-m", "empty one")
        self.sandbox.git("commit", "--allow-empty", "-m", "empty two")
        self.sandbox.git("checkout", "main")
        self.sandbox.write_task("empty", branch="task/empty", archived=True)
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[DONE] removed local branch task/empty (proof=tree_equal)", stdout)
        self.assertFalse(self.sandbox.branch_exists("task/empty"))

    def test_unlanded_archived_branch_is_skipped(self) -> None:
        self.sandbox.git("checkout", "-b", "task/open")
        (self.sandbox.repo / "only-here.txt").write_text("nope\n", encoding="utf-8")
        self.sandbox.git("add", "only-here.txt")
        self.sandbox.git("commit", "-m", "not landed")
        self.sandbox.git("checkout", "main")
        self.sandbox.write_task("open", branch="task/open", archived=True)
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/open: tree_differs", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/open"))

    def test_merge_tree_conflict_is_skipped(self) -> None:
        self.sandbox.git("checkout", "-b", "task/conflict")
        (self.sandbox.repo / "README.md").write_text("feature side\n", encoding="utf-8")
        self.sandbox.git("add", "README.md")
        self.sandbox.git("commit", "-m", "feature edit")
        self.sandbox.git("checkout", "main")
        (self.sandbox.repo / "README.md").write_text("main side\n", encoding="utf-8")
        self.sandbox.git("add", "README.md")
        self.sandbox.git("commit", "-m", "main edit")
        self.sandbox.push_main()
        self.sandbox.write_task("conflict", branch="task/conflict", archived=True)
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/conflict: conflict", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/conflict"))

    def test_branch_equal_to_default_is_skipped(self) -> None:
        self.sandbox.git("branch", "task/same", "main")
        self.sandbox.git("push", "origin", "task/same")
        self.sandbox.git("symbolic-ref", "HEAD", "refs/heads/task/same", cwd=self.sandbox.origin)
        self.sandbox.git("remote", "set-head", "origin", "--auto")
        self.sandbox.write_task("same", branch="task/same", archived=True)
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[INFO] default branch: origin/task/same", stdout)
        self.assertIn("[SKIP] task/same: branch_is_default", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/same"))

    def test_locked_worktree_is_skipped(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        self.sandbox.write_task("test", branch="task/test", archived=True)
        self.sandbox.git("worktree", "lock", str(wt))
        code, dry, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/test: locked", dry)
        self.assertNotIn("[PLAN] would remove", dry)
        code, applied, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/test: locked", applied)
        self.assertTrue(wt.is_dir())
        self.assertTrue(self.sandbox.branch_exists("task/test"))

    def test_git_locked_real_worktree_blocks_until_cleared(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        self.sandbox.git("merge", "--ff-only", "task/test")
        self.sandbox.push_main()
        self.sandbox.write_task("test", branch="task/test", archived=True)
        lock = Path(
            self.sandbox.git(
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "index.lock",
                cwd=wt,
            ).stdout.strip()
        )
        lock.write_text("", encoding="utf-8")
        code, dry, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/test: git_locked", dry)
        self.assertNotIn("[PLAN] would remove", dry)
        self.assertTrue(wt.is_dir())
        self.assertTrue(self.sandbox.branch_exists("task/test"))
        code, applied, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/test: git_locked", applied)
        self.assertNotIn("[DONE] removed", applied)
        self.assertTrue(wt.is_dir())
        self.assertTrue(self.sandbox.branch_exists("task/test"))
        lock.unlink()
        code, cleaned, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[DONE] removed worktree", cleaned)
        self.assertFalse(wt.exists())
        self.assertFalse(self.sandbox.branch_exists("task/test"))

    def test_task_active_keeps_clean_ancestor_from_main_checkout(self) -> None:
        self.sandbox.git("branch", "task/demo", "main")
        wt = self.sandbox.repo.parent / "repo-wt" / "demo"
        wt.parent.mkdir(parents=True, exist_ok=True)
        self.sandbox.git("worktree", "add", str(wt), "task/demo")
        self.sandbox.write_task("demo", branch="task/demo", status="in_progress", archived=False)
        code, dry, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/demo: task_active", dry)
        self.assertNotIn("proof=", dry)
        code, applied, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/demo: task_active", applied)
        self.assertTrue(self.sandbox.branch_exists("task/demo"))
        self.assertTrue(wt.is_dir())
        self.assertNotIn("[DONE] removed", applied)

    def test_guard_marker_keeps_archived_ancestor(self) -> None:
        self.sandbox.git("branch", "task/demo", "main")
        wt = self.sandbox.repo.parent / "repo-wt" / "demo"
        wt.parent.mkdir(parents=True, exist_ok=True)
        self.sandbox.git("worktree", "add", str(wt), "task/demo")
        self.sandbox.write_task("demo", branch="task/demo", archived=True)
        self.sandbox.write_guard("demo", "task/demo", phase="archived")
        for args in (("--no-fetch",), ("--apply", "--no-fetch")):
            code, stdout, _ = self.sandbox.run_gc(*args)
            self.assertEqual(code, 0)
            self.assertIn("[SKIP] task/demo: guard_active", stdout)
            self.assertTrue(self.sandbox.branch_exists("task/demo"))
            self.assertTrue(wt.is_dir())

    def test_task_unknown_without_record_or_merged_pr(self) -> None:
        self.sandbox.git("branch", "task/orphan", "main")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/orphan: task_unknown", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/orphan"))

    def test_completed_status_outside_archive_stays_task_active(self) -> None:
        self.sandbox.git("branch", "task/stuck", "main")
        self.sandbox.write_task("stuck", branch="task/stuck", status="completed", archived=False)
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/stuck: task_active", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/stuck"))

    def test_force_gone_does_not_bypass_missing_task(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        self.sandbox.set_pr("task/test", "OPEN", self.sandbox.head("task/test"))
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch", "--force-gone")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] task/test: task_unknown", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/test"))
        self.assertTrue(wt.is_dir())

    def test_force_gone_removes_archived_unlanded_branch(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        self.sandbox.set_pr("task/test", "OPEN", self.sandbox.head("task/test"))
        self.sandbox.write_task("test", branch="task/test", archived=True)
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch", "--force-gone")
        self.assertEqual(code, 0)
        self.assertIn("proof=forced", stdout)
        self.assertFalse(self.sandbox.branch_exists("task/test"))
        self.assertFalse(wt.exists())

    def test_card_prefix_is_still_report_only(self) -> None:
        self.sandbox.git("branch", "card/keep", "main")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch", "--prefix", "card/")
        self.assertEqual(code, 0)
        self.assertIn("[INFO managed_by=herdr-dispatch run cleanup] card/keep -", stdout)
        self.assertTrue(self.sandbox.branch_exists("card/keep"))
        self.assertNotIn("[DONE] removed", stdout)
        self.assertNotIn("[PLAN] would remove", stdout)
