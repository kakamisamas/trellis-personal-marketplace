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
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo,
            capture_output=True,
            text=True,
            env=self._git_env(),
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
        self.assertIn("merged PR and head SHA verified", stdout)
        self.assertFalse(self.sandbox.branch_exists("task/test"))
        self.assertFalse(wt.exists())
        self.assertIn("removed=1 archived=0 skipped=0 managed_by_dispatch=0 pending_push=0", stdout)

    def test_live_upstream_is_retained(self) -> None:
        wt = self.sandbox.make_verified_task(gone=False)
        assert wt is not None
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("upstream still exists", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/test"))
        self.assertTrue(wt.is_dir())
        self.assertNotIn("[DONE] removed", stdout)

    def test_dirty_worktree_is_retained(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        (wt / "local.txt").write_text("dirty\n", encoding="utf-8")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("worktree dirty or unreadable", stdout)
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
        self.assertFalse(self.sandbox.branch_exists("task/test"))
        self.assertFalse(wt.exists())

    def test_force_dirty_does_not_override_unverified(self) -> None:
        wt = self.sandbox.make_verified_task(pr_head="deadbeef" * 5)
        assert wt is not None
        (wt / "local.txt").write_text("dirty\n", encoding="utf-8")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch", "--force-dirty")
        self.assertEqual(code, 0)
        self.assertIn("differs from merged PR head", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/test"))
        self.assertTrue(wt.is_dir())
        self.assertNotIn("[DONE] removed", stdout)

    def test_local_head_after_merge_is_retained(self) -> None:
        wt = self.sandbox.make_verified_task(pr_head="cafebabe" * 5)
        assert wt is not None
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("differs from merged PR head", stdout)
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
        self.assertIn("current branch", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/test"))
        self.assertTrue(wt.is_dir())

    def test_main_worktree_is_retained(self) -> None:
        self.sandbox.make_verified_task(worktree=False)
        side = self.sandbox.path / "side"
        self.sandbox.git("worktree", "add", str(side), "main")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch", cwd=side)
        self.assertEqual(code, 0)
        self.assertIn("checked out in main worktree", stdout)
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
