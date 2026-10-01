from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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


TASK_PY = """#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

def main() -> int:
    args = sys.argv[1:]
    if len(args) < 2 or args[0] != "archive":
        print("usage: task.py archive <name>", file=sys.stderr)
        return 1
    name = args[1]
    if os.environ.get("TRELLIS_GC_TASK_FAIL") == "1":
        print("forced failure", file=sys.stderr)
        return 1
    root = Path(".").resolve()
    source = root / ".trellis" / "tasks" / name
    if not source.is_dir():
        print(f"missing {source}", file=sys.stderr)
        return 1
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    dest = root / ".trellis" / "tasks" / "archive" / month / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(source), str(dest))
    task_file = dest / "task.json"
    payload = json.loads(task_file.read_text(encoding="utf-8"))
    payload["status"] = "completed"
    task_file.write_text(json.dumps(payload) + "\\n", encoding="utf-8")
    add = ["git", "add", "-A", "--", ".trellis/tasks"]
    if os.environ.get("TRELLIS_GC_TASK_TOUCH_BUSINESS") == "1":
        business = root / "business.py"
        business.write_text("touched\\n", encoding="utf-8")
        add = ["git", "add", "-A", "--", ".trellis/tasks", "business.py"]
    subprocess.run(add, check=True)
    subprocess.run(["git", "commit", "-m", f"archive {name}"], check=True)
    if os.environ.get("TRELLIS_GC_TASK_EXTRA_COMMIT") == "1":
        extra = root / ".trellis" / "tasks" / f"{name}-extra.txt"
        extra.write_text("extra\\n", encoding="utf-8")
        subprocess.run(["git", "add", "--", str(extra.relative_to(root))], check=True)
        subprocess.run(["git", "commit", "-m", f"extra {name}"], check=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
"""


OLD_DATE = {
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00 +0000",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00 +0000",
}


class Sandbox:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.origin = self.path / "origin.git"
        self.repo = self.path / "repo"
        self.gh_file = self.path / "gh.json"
        self.bin = self.path / "bin"
        self.empty_config = self.path / "empty-gitconfig"
        self.empty_config.write_text("", encoding="utf-8")
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
        env["GIT_CONFIG_GLOBAL"] = str(self.empty_config)
        env["GIT_CONFIG_SYSTEM"] = str(self.empty_config)
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
        hooks = self.path / "no-hooks"
        hooks.mkdir()
        self.git("config", "core.hooksPath", str(hooks))
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
        status: str | None = "completed",
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
        payload: dict[str, object] = {
            "id": name,
            "name": name,
            "branch": branch,
            "base_branch": "main",
            "pr_url": pr_url,
            "children": children or [],
        }
        if status is not None:
            payload["status"] = status
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

    def run_gc(
        self,
        *args: str,
        cwd: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        keys = [
            "PATH",
            "GH_SHIM_FILE",
            "PYTHONDONTWRITEBYTECODE",
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_SYSTEM",
        ]
        if extra_env:
            keys.extend(extra_env)
        backup = {key: os.environ.get(key) for key in keys}
        os.environ["PATH"] = str(self.bin) + os.pathsep + os.environ.get("PATH", "")
        os.environ["GH_SHIM_FILE"] = str(self.gh_file)
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        os.environ["GIT_CONFIG_GLOBAL"] = str(self.empty_config)
        os.environ["GIT_CONFIG_SYSTEM"] = str(self.empty_config)
        if extra_env:
            os.environ.update(extra_env)
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

    def install_archiver(self) -> None:
        path = self.repo / ".trellis" / "scripts" / "task.py"
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TASK_PY, encoding="utf-8")

    def pr_url(self, number: int) -> str:
        return f"https://example.test/pull/{number}"

    def pending_path(self) -> Path:
        common = Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
        return common / "trellis-gc" / "pending-push.json"

    def record_idle_task(
        self,
        name: str,
        *,
        branch: str | None,
        status: str = "in_progress",
        pr_url: str | None = None,
        children: list[str] | None = None,
        old: bool = True,
        push: bool = True,
        config: str | None = "# session_auto_commit: false\n",
    ) -> None:
        self.install_archiver()
        config_path = self.repo / ".trellis" / "config.yaml"
        if config is not None and not config_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(config, encoding="utf-8")
        self.write_task(
            name,
            branch=branch,
            status=status,
            archived=False,
            pr_url=pr_url,
            children=children,
        )
        self.git("add", ".trellis")
        self.git("commit", "-m", f"record {name}", extra_env=OLD_DATE if old else None)
        if push:
            self.push_main()

    def land_fast_forward(self, branch: str, filename: str = "feature.txt") -> str:
        self.git("checkout", "-b", branch)
        (self.repo / filename).write_text(branch + "\n", encoding="utf-8")
        self.git("add", filename)
        self.git("commit", "-m", f"add {filename}")
        oid = self.head("HEAD")
        self.git("checkout", "main")
        self.git("merge", "--ff-only", branch)
        self.push_main()
        return oid

    def land_merge(self, branch: str, filename: str = "feature.txt") -> str:
        self.git("checkout", "-b", branch)
        (self.repo / filename).write_text(branch + "\n", encoding="utf-8")
        self.git("add", filename)
        self.git("commit", "-m", f"add {filename}")
        oid = self.head("HEAD")
        self.git("checkout", "main")
        (self.repo / "side.txt").write_text("side\n", encoding="utf-8")
        self.git("add", "side.txt")
        self.git("commit", "-m", "diverge main")
        self.git("merge", "--no-ff", branch, "-m", f"merge {branch}")
        self.push_main()
        return oid

    def land_squash(self, branch: str, filename: str = "feature.txt") -> str:
        self.git("checkout", "-b", branch)
        (self.repo / filename).write_text(branch + "\n", encoding="utf-8")
        self.git("add", filename)
        self.git("commit", "-m", f"add {filename}")
        oid = self.head("HEAD")
        self.git("checkout", "main")
        self.git("merge", "--squash", branch)
        self.git("commit", "-m", f"squash {branch}")
        self.push_main()
        return oid

    def branch_with_gone_upstream(self, branch: str) -> str:
        self.git("checkout", "-b", branch)
        (self.repo / "only-branch.txt").write_text(branch + "\n", encoding="utf-8")
        self.git("add", "only-branch.txt")
        self.git("commit", "-m", "only on branch")
        oid = self.head("HEAD")
        self.git("push", "-u", "origin", branch)
        self.git("push", "origin", f":{branch}")
        self.git("fetch", "--prune", "origin")
        self.git("checkout", "main")
        return oid

    def clone_origin(self, name: str = "other") -> Path:
        dest = self.path / name
        subprocess.run(
            ["git", "clone", str(self.origin), str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.git("config", "user.name", "Trellis GC Test", cwd=dest)
        self.git("config", "user.email", "gc-test@example.com", cwd=dest)
        return dest

    def break_push(self) -> None:
        self.git("remote", "set-url", "--push", "origin", str(self.path / "missing-push.git"))

    def restore_push(self) -> None:
        self.git("remote", "set-url", "--push", "origin", str(self.origin))


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

    def _first_skip_reason(self, stdout: str, branch: str) -> str:
        prefix = f"[SKIP] {branch}: "
        for line in stdout.splitlines():
            if line.startswith(prefix):
                return line[len(prefix):]
        self.fail(f"no skip line for {branch}\n{stdout}")
        return ""

    def _add_clean_branch_worktree(self, branch: str) -> Path:
        slug = branch.split("/", 1)[1]
        wt = self.sandbox.repo.parent / "repo-wt" / slug
        wt.parent.mkdir(parents=True, exist_ok=True)
        self.sandbox.git("worktree", "add", "-b", branch, str(wt), "main")
        return wt

    def test_incomplete_archive_records_are_not_deleted(self) -> None:
        cases = (
            ("task/arch-progress", "in_progress"),
            ("task/arch-planning", "planning"),
            ("task/arch-missing", None),
        )
        kept: list[tuple[str, Path, Path]] = []
        for branch, status in cases:
            wt = self._add_clean_branch_worktree(branch)
            directory = self.sandbox.write_task(
                branch.split("/", 1)[1],
                branch=branch,
                status=status,
                archived=True,
            )
            payload = json.loads((directory / "task.json").read_text(encoding="utf-8"))
            if status is None:
                self.assertNotIn("status", payload)
            kept.append((branch, directory, wt))
        for args in (("--no-fetch",), ("--apply", "--no-fetch")):
            code, stdout, _ = self.sandbox.run_gc(*args)
            self.assertEqual(code, 0, stdout)
            self.assertNotIn("[PLAN] would remove", stdout)
            self.assertNotIn("[DONE] removed", stdout)
            for branch, directory, wt in kept:
                self.assertEqual(self._first_skip_reason(stdout, branch), "archive_incomplete")
                self.assertTrue(directory.is_dir())
                self.assertTrue((directory / "task.json").is_file())
                self.assertTrue(wt.is_dir())
                self.assertTrue(self.sandbox.branch_exists(branch))

    def test_active_guard_and_lock_skip_task_active_first(self) -> None:
        branch = "task/active"
        wt = self._add_clean_branch_worktree(branch)
        self.sandbox.write_task("active", branch=branch, status="in_progress", archived=False)
        self.sandbox.write_guard("active", branch)
        self.sandbox.git("worktree", "lock", str(wt))
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0, stdout)
        self.assertEqual(self._first_skip_reason(stdout, branch), "task_active")
        self.assertTrue(wt.is_dir())
        self.assertTrue(self.sandbox.branch_exists(branch))

    def test_archived_guard_and_index_lock_skip_guard_first(self) -> None:
        branch = "task/guarded"
        wt = self._add_clean_branch_worktree(branch)
        self.sandbox.write_task("guarded", branch=branch, archived=True)
        self.sandbox.write_guard("guarded", branch, phase="archived")
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
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0, stdout)
        self.assertEqual(self._first_skip_reason(stdout, branch), "guard_active")
        self.assertTrue(wt.is_dir())
        self.assertTrue(self.sandbox.branch_exists(branch))

    def test_missing_guard_without_merged_pr_is_task_unknown(self) -> None:
        branch = "task/unknown"
        self.sandbox.git("branch", branch, "main")
        self.sandbox.write_guard("unknown", branch)
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0, stdout)
        self.assertEqual(self._first_skip_reason(stdout, branch), "task_unknown")
        self.assertTrue(self.sandbox.branch_exists(branch))

    def test_missing_rule_a_then_guard_is_guard_active(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        self.sandbox.write_guard("test", "task/test")
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0, stdout)
        self.assertEqual(self._first_skip_reason(stdout, "task/test"), "guard_active")
        self.assertNotIn("[PLAN] would remove", stdout)
        self.assertTrue(wt.is_dir())
        self.assertTrue(self.sandbox.branch_exists("task/test"))

    def test_archived_completed_lock_without_guard_is_locked(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        self.sandbox.git("merge", "--ff-only", "task/test")
        self.sandbox.push_main()
        self.sandbox.write_task("test", branch="task/test", archived=True)
        self.sandbox.git("worktree", "lock", str(wt))
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0, stdout)
        self.assertEqual(self._first_skip_reason(stdout, "task/test"), "locked")
        self.assertNotIn("[PLAN] would remove", stdout)
        self.assertTrue(wt.is_dir())
        self.assertTrue(self.sandbox.branch_exists("task/test"))

    def test_position_skip_precedes_lifecycle_and_locks(self) -> None:
        current = self._add_clean_branch_worktree("task/here")
        self.sandbox.write_task("here", branch="task/here", status="in_progress")
        self.sandbox.write_guard("here", "task/here")
        self.sandbox.git("worktree", "lock", str(current))
        code, stdout, _ = self.sandbox.run_gc("--no-fetch", cwd=current)
        self.assertEqual(code, 0, stdout)
        self.assertEqual(self._first_skip_reason(stdout, "task/here"), "current_worktree")
        self.assertTrue(current.is_dir())

        self.sandbox.make_verified_task("task/held", worktree=False)
        self.sandbox.write_task("held", branch="task/held", archived=True)
        side = self.sandbox.path / "side"
        self.sandbox.git("worktree", "add", "--detach", str(side), "main")
        lock = Path(
            self.sandbox.git(
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "index.lock",
            ).stdout.strip()
        )
        lock.write_text("", encoding="utf-8")
        code, stdout, _ = self.sandbox.run_gc("--no-fetch", cwd=side)
        self.assertEqual(code, 0, stdout)
        self.assertEqual(self._first_skip_reason(stdout, "task/held"), "main_worktree")
        self.assertTrue((self.sandbox.repo / "tracked.txt").is_file())
        self.assertTrue(self.sandbox.branch_exists("task/held"))
        lock.unlink()

    def test_optional_locks_leave_linked_index_bytes_unchanged(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        index = Path(
            self.sandbox.git(
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "index",
                cwd=wt,
            ).stdout.strip()
        )
        before = index.read_bytes()
        tracked = wt / "tracked.txt"
        newer = index.stat().st_mtime + 5
        os.utime(tracked, (newer, newer))
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0, stdout)
        self.assertIn("[PLAN] would remove", stdout)
        self.assertEqual(index.read_bytes(), before)
        self.assertTrue(wt.is_dir())

    def test_card_prefix_is_still_report_only(self) -> None:
        self.sandbox.git("branch", "card/keep", "main")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch", "--prefix", "card/")
        self.assertEqual(code, 0)
        self.assertIn("[INFO managed_by=herdr-dispatch run cleanup] card/keep -", stdout)
        self.assertTrue(self.sandbox.branch_exists("card/keep"))
        self.assertNotIn("[DONE] removed", stdout)
        self.assertNotIn("[PLAN] would remove", stdout)

    def _plan(self, name: str, evidence: str, proof: str) -> str:
        return (
            rf"\[PLAN\] would archive {re.escape(name)} "
            rf"\(idle \d+d, evidence={evidence}, proof={proof}\)"
        )

    def test_apply_worktree_and_branch_snapshot(self) -> None:
        wt = self.sandbox.make_verified_task()
        assert wt is not None
        before_branches = set(self.sandbox.git("branch", "--format=%(refname:short)").stdout.split())
        before_worktrees = self.sandbox.git("worktree", "list", "--porcelain").stdout
        self.assertIn("task/test", before_branches)
        self.assertIn(str(wt.resolve()), before_worktrees)
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        after_branches = set(self.sandbox.git("branch", "--format=%(refname:short)").stdout.split())
        after_worktrees = self.sandbox.git("worktree", "list", "--porcelain").stdout
        self.assertNotIn("task/test", after_branches)
        self.assertNotIn(str(wt.resolve()), after_worktrees)
        self.assertIn("[DONE] removed", stdout)

    def test_planning_task_is_skipped(self) -> None:
        self.sandbox.write_task("plan-me", branch="task/plan-me", status="planning")
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] plan-me: planning", stdout)
        self.assertNotIn("would archive", stdout)

    def test_null_branch_task_is_skipped(self) -> None:
        self.sandbox.write_task("no-branch", branch=None, status="in_progress")
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] no-branch: branch_missing", stdout)
        self.assertNotIn("would archive", stdout)

    def test_default_branch_task_is_skipped(self) -> None:
        self.sandbox.write_task("on-main", branch="main", status="in_progress")
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] on-main: branch_is_default", stdout)
        self.assertNotIn("would archive", stdout)

    def test_idle_in_progress_without_pr_is_no_pr(self) -> None:
        # One empty commit on top of an old task directory is not completion evidence.
        self.sandbox.git("checkout", "-b", "task/nopr")
        self.sandbox.record_idle_task("nopr", branch="task/nopr", status="in_progress", push=False)
        self.sandbox.git("commit", "--allow-empty", "-m", "empty")
        self.sandbox.git("checkout", "main")
        self.sandbox.git("merge", "--ff-only", "task/nopr")
        self.sandbox.push_main()
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] nopr: no_pr", stdout)
        self.assertNotIn("would archive", stdout)
        self.assertTrue(self.sandbox.branch_exists("task/nopr"))
        self.assertTrue((self.sandbox.repo / ".trellis" / "tasks" / "nopr").is_dir())

    def test_pr_head_name_mismatch_is_skipped(self) -> None:
        oid = self.sandbox.land_fast_forward("task/real")
        url = self.sandbox.pr_url(2)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/other")
        self.sandbox.record_idle_task("real", branch="task/real", pr_url=url)
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] real: pr_branch_mismatch", stdout)
        self.assertNotIn("would archive", stdout)

    def test_open_pr_is_not_merged(self) -> None:
        oid = self.sandbox.land_fast_forward("task/openpr")
        url = self.sandbox.pr_url(3)
        self.sandbox.set_pr(url, "OPEN", oid, headRefName="task/openpr")
        self.sandbox.record_idle_task("openpr", branch="task/openpr", pr_url=url)
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] openpr: pr_not_merged", stdout)

    def test_unlanded_pr_head_is_not_landed(self) -> None:
        self.sandbox.git("checkout", "-b", "task/unlanded")
        (self.sandbox.repo / "feature.txt").write_text("only-branch\n", encoding="utf-8")
        self.sandbox.git("add", "feature.txt")
        self.sandbox.git("commit", "-m", "only branch")
        oid = self.sandbox.head("HEAD")
        self.sandbox.git("checkout", "main")
        url = self.sandbox.pr_url(4)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/unlanded")
        self.sandbox.record_idle_task("unlanded", branch="task/unlanded", pr_url=url)
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] unlanded: not_landed tree_differs", stdout)
        self.assertNotIn("would archive", stdout)

    def test_fast_forward_merged_pr_is_archived(self) -> None:
        oid = self.sandbox.land_fast_forward("task/ff")
        url = self.sandbox.pr_url(11)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/ff")
        self.sandbox.record_idle_task("ff", branch="task/ff", pr_url=url)
        config = (self.sandbox.repo / ".trellis" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("# session_auto_commit: false", config)
        origin_before = self.sandbox.head("origin/main")
        code, dry, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertRegex(dry, self._plan("ff", "pr_merged", "ancestor"))
        self.assertEqual(self.sandbox.head("origin/main"), origin_before)
        code, applied, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[DONE] archived ff (evidence=pr_merged, proof=ancestor)", applied)
        self.assertIn("archived=1", applied)
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        archived = self.sandbox.repo / ".trellis" / "tasks" / "archive" / month / "ff"
        self.assertTrue((archived / "task.json").is_file())
        self.assertFalse((self.sandbox.repo / ".trellis" / "tasks" / "ff").exists())
        self.assertTrue(self.sandbox.branch_exists("task/ff"))
        self.assertNotEqual(self.sandbox.head("origin/main"), origin_before)
        names = self.sandbox.git(
            "diff-tree", "-r", "--name-only", "--no-commit-id", origin_before, "origin/main"
        ).stdout.split()
        self.assertTrue(names)
        self.assertTrue(all(name.startswith(".trellis/tasks/") for name in names))
        parents = self.sandbox.git("rev-list", "--parents", "-n1", "origin/main").stdout.split()
        self.assertEqual(len(parents), 2)
        self.assertFalse(self.sandbox.pending_path().exists())
        status = self.sandbox.git("status", "--porcelain", "--untracked-files=all")
        self.assertEqual(status.stdout.strip(), "")

    def test_merge_commit_pr_is_archived(self) -> None:
        oid = self.sandbox.land_merge("task/merged")
        url = self.sandbox.pr_url(12)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/merged")
        self.sandbox.record_idle_task("merged", branch="task/merged", pr_url=url)
        code, dry, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertRegex(dry, self._plan("merged", "pr_merged", "ancestor"))
        code, applied, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[DONE] archived merged (evidence=pr_merged, proof=ancestor)", applied)

    def test_squash_merged_pr_is_archived(self) -> None:
        oid = self.sandbox.land_squash("task/squashed")
        url = self.sandbox.pr_url(13)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/squashed")
        self.sandbox.record_idle_task("squashed", branch="task/squashed", pr_url=url)
        code, dry, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertRegex(dry, self._plan("squashed", "pr_merged", "tree_equal"))
        code, applied, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[DONE] archived squashed (evidence=pr_merged, proof=tree_equal)", applied)

    def test_completed_task_outside_archive_is_archived(self) -> None:
        self.sandbox.land_fast_forward("task/stuck-done", filename="done.txt")
        self.sandbox.record_idle_task("stuck-done", branch="task/stuck-done", status="completed")
        code, dry, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertRegex(dry, self._plan("stuck-done", "completed", "ancestor"))
        code, applied, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[DONE] archived stuck-done (evidence=completed, proof=ancestor)", applied)
        self.assertTrue(self.sandbox.branch_exists("task/stuck-done"))

    def test_completed_task_with_gone_upstream_uses_pr_proof(self) -> None:
        oid = self.sandbox.branch_with_gone_upstream("task/prdone")
        self.sandbox.set_pr("task/prdone", "MERGED", oid)
        self.sandbox.record_idle_task("prdone", branch="task/prdone", status="completed")
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertRegex(stdout, self._plan("prdone", "completed", "pr"))

    def test_active_child_blocks_archive(self) -> None:
        oid = self.sandbox.land_fast_forward("task/parent")
        url = self.sandbox.pr_url(21)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/parent")
        self.sandbox.install_archiver()
        self.sandbox.write_task(
            "parent",
            branch="task/parent",
            status="in_progress",
            pr_url=url,
            children=["child"],
        )
        self.sandbox.write_task("child", branch="task/child", status="in_progress")
        self.sandbox.git("add", ".trellis")
        self.sandbox.git("commit", "-m", "record parent", extra_env=OLD_DATE)
        self.sandbox.push_main()
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] parent: children_active", stdout)
        self.assertNotIn("would archive", stdout)

    def test_dirty_task_directory_is_skipped(self) -> None:
        oid = self.sandbox.land_fast_forward("task/dirtydir")
        url = self.sandbox.pr_url(22)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/dirtydir")
        self.sandbox.record_idle_task("dirtydir", branch="task/dirtydir", pr_url=url)
        task_file = self.sandbox.repo / ".trellis" / "tasks" / "dirtydir" / "task.json"
        task_file.write_text(task_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] dirtydir: dirty_task_dir", stdout)
        self.assertNotIn("would archive", stdout)

    def test_guard_marker_blocks_archive(self) -> None:
        oid = self.sandbox.land_fast_forward("task/marked")
        url = self.sandbox.pr_url(23)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/marked")
        self.sandbox.record_idle_task("marked", branch="task/marked", pr_url=url)
        self.sandbox.write_guard("marked", "task/marked", phase="hold")
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] marked: guard_active", stdout)
        self.assertNotIn("would archive", stdout)

    def test_worktree_on_task_branch_blocks_archive(self) -> None:
        oid = self.sandbox.land_fast_forward("task/busy")
        url = self.sandbox.pr_url(24)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/busy")
        self.sandbox.record_idle_task("busy", branch="task/busy", pr_url=url)
        wt = self.sandbox.path / "busy-wt"
        self.sandbox.git("worktree", "add", str(wt), "task/busy")
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] busy: worktree_active", stdout)
        self.assertNotIn("would archive", stdout)

    def test_recent_task_is_skipped(self) -> None:
        oid = self.sandbox.land_fast_forward("task/fresh")
        url = self.sandbox.pr_url(25)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/fresh")
        self.sandbox.record_idle_task("fresh", branch="task/fresh", pr_url=url, old=False)
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] fresh: recent", stdout)
        self.assertNotIn("would archive", stdout)

    def test_task_directory_without_history_is_skipped(self) -> None:
        oid = self.sandbox.land_fast_forward("task/untracked")
        url = self.sandbox.pr_url(26)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/untracked")
        self.sandbox.write_task("untracked", branch="task/untracked", status="in_progress", pr_url=url)
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] untracked: no_history", stdout)
        self.assertNotIn("would archive", stdout)

    def test_missing_pr_head_without_fetch_is_unavailable(self) -> None:
        self.sandbox.git("branch", "task/missing-head", "main")
        url = self.sandbox.pr_url(27)
        missing = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        self.sandbox.set_pr(url, "MERGED", missing, headRefName="task/missing-head")
        self.sandbox.write_task("missing-head", branch="task/missing-head", status="in_progress", pr_url=url)
        code, stdout, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] missing-head: not_landed head_unavailable", stdout)

    def test_missing_pr_head_is_fetched_once(self) -> None:
        other = self.sandbox.clone_origin()
        (other / "pulled.txt").write_text("from-pr\n", encoding="utf-8")
        self.sandbox.git("add", "pulled.txt", cwd=other)
        self.sandbox.git("commit", "-m", "pr head", cwd=other)
        oid = self.sandbox.head("HEAD", cwd=other)
        self.sandbox.git("push", "origin", "HEAD:refs/pull/28/head", cwd=other)
        self.sandbox.git("branch", "task/fetched-head", "main")
        url = self.sandbox.pr_url(28)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/fetched-head")
        self.sandbox.write_task(
            "fetched-head",
            branch="task/fetched-head",
            status="in_progress",
            pr_url=url,
        )
        code, blocked, _ = self.sandbox.run_gc("--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] fetched-head: not_landed head_unavailable", blocked)
        self.assertNotEqual(
            self.sandbox.git("cat-file", "-e", f"{oid}^{{commit}}", check=False).returncode,
            0,
        )
        code, fetched, _ = self.sandbox.run_gc()
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] fetched-head: not_landed tree_differs", fetched)
        self.assertEqual(
            self.sandbox.git("cat-file", "-e", f"{oid}^{{commit}}", check=False).returncode,
            0,
        )

    def test_archive_idle_days_zero_disables_plans(self) -> None:
        oid = self.sandbox.land_fast_forward("task/idleoff")
        url = self.sandbox.pr_url(29)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/idleoff")
        self.sandbox.record_idle_task("idleoff", branch="task/idleoff", pr_url=url)
        for days in ("0", "-1"):
            code, stdout, _ = self.sandbox.run_gc("--no-fetch", "--archive-idle-days", days)
            self.assertEqual(code, 0, stdout)
            self.assertNotIn("would archive", stdout)
            self.assertNotIn("[PLAN] would archive", stdout)
        self.assertTrue((self.sandbox.repo / ".trellis" / "tasks" / "idleoff").is_dir())

    def test_sync_refuses_when_not_default_branch(self) -> None:
        oid = self.sandbox.land_fast_forward("task/side")
        url = self.sandbox.pr_url(31)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/side")
        self.sandbox.record_idle_task("side", branch="task/side", pr_url=url)
        self.sandbox.git("checkout", "-b", "side")
        origin = self.sandbox.head("origin/main")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 1)
        self.assertIn("[WARN] sync precondition failed: not_default_branch", stdout)
        self.assertNotIn("[DONE] archived", stdout)
        self.assertEqual(self.sandbox.head("origin/main"), origin)
        self.assertTrue((self.sandbox.repo / ".trellis" / "tasks" / "side").is_dir())

    def test_sync_refuses_from_linked_worktree(self) -> None:
        oid = self.sandbox.land_fast_forward("task/linked")
        url = self.sandbox.pr_url(32)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/linked")
        self.sandbox.record_idle_task("linked", branch="task/linked", pr_url=url)
        linked = self.sandbox.path / "linked"
        self.sandbox.git("worktree", "add", "--detach", str(linked), "main")
        origin = self.sandbox.head("origin/main")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch", cwd=linked)
        self.assertEqual(code, 1)
        self.assertIn("[WARN] sync precondition failed: not_main_checkout", stdout)
        self.assertNotIn("[DONE] archived", stdout)
        self.assertEqual(self.sandbox.head("origin/main"), origin)
        self.assertTrue((self.sandbox.repo / ".trellis" / "tasks" / "linked").is_dir())

    def test_sync_refuses_unknown_local_commit(self) -> None:
        oid = self.sandbox.land_fast_forward("task/ahead")
        url = self.sandbox.pr_url(33)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/ahead")
        self.sandbox.record_idle_task("ahead", branch="task/ahead", pr_url=url)
        (self.sandbox.repo / "README.md").write_text("ahead\n", encoding="utf-8")
        self.sandbox.git("add", "README.md")
        self.sandbox.git("commit", "-m", "local only")
        origin = self.sandbox.head("origin/main")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 1)
        self.assertIn("[WARN] sync precondition failed: unknown_local_commits", stdout)
        self.assertIn("[WARN] recovery:", stdout)
        self.assertNotIn("[DONE] archived", stdout)
        self.assertEqual(self.sandbox.head("origin/main"), origin)
        self.assertTrue((self.sandbox.repo / ".trellis" / "tasks" / "ahead").is_dir())

    def test_sync_refuses_when_behind_remote(self) -> None:
        oid = self.sandbox.land_fast_forward("task/behind")
        url = self.sandbox.pr_url(34)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/behind")
        self.sandbox.record_idle_task("behind", branch="task/behind", pr_url=url)
        other = self.sandbox.clone_origin()
        (other / "remote.txt").write_text("remote\n", encoding="utf-8")
        self.sandbox.git("add", "remote.txt", cwd=other)
        self.sandbox.git("commit", "-m", "remote only", cwd=other)
        self.sandbox.git("push", "origin", "main", cwd=other)
        code, stdout, _ = self.sandbox.run_gc("--apply")
        self.assertEqual(code, 1)
        self.assertIn("[WARN] sync precondition failed: behind_remote", stdout)
        self.assertNotIn("[DONE] archived", stdout)
        self.assertTrue((self.sandbox.repo / ".trellis" / "tasks" / "behind").is_dir())

    def test_sync_refuses_when_diverged(self) -> None:
        oid = self.sandbox.land_fast_forward("task/diverged")
        url = self.sandbox.pr_url(35)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/diverged")
        self.sandbox.record_idle_task("diverged", branch="task/diverged", pr_url=url)
        (self.sandbox.repo / "local.txt").write_text("local\n", encoding="utf-8")
        self.sandbox.git("add", "local.txt")
        self.sandbox.git("commit", "-m", "local only")
        other = self.sandbox.clone_origin()
        (other / "remote.txt").write_text("remote\n", encoding="utf-8")
        self.sandbox.git("add", "remote.txt", cwd=other)
        self.sandbox.git("commit", "-m", "remote only", cwd=other)
        self.sandbox.git("push", "origin", "main", cwd=other)
        local = self.sandbox.head("HEAD")
        code, stdout, _ = self.sandbox.run_gc("--apply")
        self.assertEqual(code, 1)
        self.assertIn("[WARN] sync precondition failed: diverged", stdout)
        self.assertNotIn("[DONE] archived", stdout)
        published = set(self.sandbox.git("rev-list", "origin/main").stdout.split())
        self.assertNotIn(local, published)
        self.assertTrue((self.sandbox.repo / ".trellis" / "tasks" / "diverged").is_dir())

    def test_sync_refuses_dirty_worktree(self) -> None:
        oid = self.sandbox.land_fast_forward("task/dirtyrepo")
        url = self.sandbox.pr_url(36)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/dirtyrepo")
        self.sandbox.record_idle_task("dirtyrepo", branch="task/dirtyrepo", pr_url=url)
        (self.sandbox.repo / "junk.txt").write_text("x\n", encoding="utf-8")
        origin = self.sandbox.head("origin/main")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 1)
        self.assertIn("[WARN] sync precondition failed: dirty", stdout)
        self.assertNotIn("[DONE] archived", stdout)
        self.assertEqual(self.sandbox.head("origin/main"), origin)
        self.assertTrue((self.sandbox.repo / ".trellis" / "tasks" / "dirtyrepo").is_dir())

    def test_auto_commit_disabled_skips_archive(self) -> None:
        oid = self.sandbox.land_fast_forward("task/nocommit")
        url = self.sandbox.pr_url(37)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/nocommit")
        self.sandbox.record_idle_task(
            "nocommit",
            branch="task/nocommit",
            pr_url=url,
            config="session_auto_commit: false\n",
        )
        origin = self.sandbox.head("origin/main")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[SKIP] nocommit: auto_commit_disabled", stdout)
        self.assertNotIn("[DONE] archived", stdout)
        self.assertEqual(self.sandbox.head("origin/main"), origin)
        self.assertTrue((self.sandbox.repo / ".trellis" / "tasks" / "nocommit").is_dir())

    def test_archive_command_failure_stops(self) -> None:
        oid = self.sandbox.land_fast_forward("task/failarch")
        url = self.sandbox.pr_url(38)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/failarch")
        self.sandbox.record_idle_task("failarch", branch="task/failarch", pr_url=url)
        origin = self.sandbox.head("origin/main")
        code, stdout, _ = self.sandbox.run_gc(
            "--apply",
            "--no-fetch",
            extra_env={"TRELLIS_GC_TASK_FAIL": "1"},
        )
        self.assertEqual(code, 1)
        self.assertIn("[WARN] archive failarch failed", stdout)
        self.assertNotIn("[DONE] archived", stdout)
        self.assertEqual(self.sandbox.head("origin/main"), origin)
        self.assertTrue((self.sandbox.repo / ".trellis" / "tasks" / "failarch").is_dir())
        self.assertFalse(self.sandbox.pending_path().exists())

    def test_two_parent_pending_commit_is_not_pushed(self) -> None:
        (self.sandbox.repo / "business.py").write_text("base\n", encoding="utf-8")
        self.sandbox.git("add", "business.py")
        self.sandbox.git("commit", "-m", "business base")
        self.sandbox.push_main()
        base = self.sandbox.head("HEAD")
        note = self.sandbox.repo / ".trellis" / "tasks" / "note.txt"
        note.parent.mkdir(parents=True)
        note.write_text("t\n", encoding="utf-8")
        self.sandbox.git("add", ".trellis/tasks/note.txt")
        self.sandbox.git("commit", "-m", "tasks only")
        tasks_sha = self.sandbox.head("HEAD")
        self.sandbox.git("checkout", "-b", "side", base)
        (self.sandbox.repo / "business.py").write_text("side\n", encoding="utf-8")
        self.sandbox.git("add", "business.py")
        self.sandbox.git("commit", "-m", "side business")
        self.sandbox.git("checkout", "main")
        self.sandbox.git("merge", "--no-commit", "side")
        (self.sandbox.repo / "business.py").write_text("resolved\n", encoding="utf-8")
        self.sandbox.git("add", "business.py")
        self.sandbox.git("commit", "-m", "merge business")
        merge_sha = self.sandbox.head("HEAD")
        parents = self.sandbox.git("rev-list", "--parents", "-n1", merge_sha).stdout.split()
        self.assertEqual(len(parents), 3)
        ahead = [sha for sha in self.sandbox.git("rev-list", "origin/main..HEAD").stdout.split() if sha]
        self.assertIn(merge_sha, ahead)
        self.assertIn(tasks_sha, ahead)
        trellis_gc.write_pending(self.sandbox.pending_path(), "main", ahead)
        origin = self.sandbox.head("origin/main")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch", "--archive-idle-days", "0")
        self.assertEqual(code, 1)
        self.assertIn("[WARN] sync precondition failed: unexpected_commit_type", stdout)
        self.assertNotIn("[DONE] archived", stdout)
        self.assertEqual(self.sandbox.head("origin/main"), origin)
        self.assertTrue(self.sandbox.pending_path().is_file())

    def test_extra_archive_commit_is_not_pushed(self) -> None:
        oid = self.sandbox.land_fast_forward("task/extra")
        url = self.sandbox.pr_url(41)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/extra")
        self.sandbox.record_idle_task("extra", branch="task/extra", pr_url=url)
        origin = self.sandbox.head("origin/main")
        code, stdout, _ = self.sandbox.run_gc(
            "--apply",
            "--no-fetch",
            extra_env={"TRELLIS_GC_TASK_EXTRA_COMMIT": "1"},
        )
        self.assertEqual(code, 1)
        self.assertIn("[WARN] unexpected_archive_commits", stdout)
        self.assertNotIn("[DONE] archived", stdout)
        self.assertEqual(self.sandbox.head("origin/main"), origin)
        ahead = [line for line in self.sandbox.git("rev-list", "origin/main..HEAD").stdout.split() if line]
        self.assertEqual(len(ahead), 2)
        self.assertFalse(self.sandbox.pending_path().exists())
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        self.assertTrue((self.sandbox.repo / ".trellis" / "tasks" / "archive" / month / "extra").is_dir())

    def test_archive_commit_touching_business_file_is_not_pushed(self) -> None:
        oid = self.sandbox.land_fast_forward("task/biz")
        url = self.sandbox.pr_url(42)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/biz")
        self.sandbox.record_idle_task("biz", branch="task/biz", pr_url=url)
        origin = self.sandbox.head("origin/main")
        code, stdout, _ = self.sandbox.run_gc(
            "--apply",
            "--no-fetch",
            extra_env={"TRELLIS_GC_TASK_TOUCH_BUSINESS": "1"},
        )
        self.assertEqual(code, 1)
        self.assertIn("[WARN] unexpected_commit_paths", stdout)
        self.assertEqual(self.sandbox.head("origin/main"), origin)
        self.assertFalse(self.sandbox.pending_path().exists())
        names = self.sandbox.git(
            "diff-tree", "-r", "--name-only", "--no-commit-id", "HEAD"
        ).stdout.split()
        self.assertIn("business.py", names)

    def test_failed_push_keeps_pending_and_retries(self) -> None:
        oid = self.sandbox.land_fast_forward("task/retry")
        url = self.sandbox.pr_url(43)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/retry")
        self.sandbox.record_idle_task("retry", branch="task/retry", pr_url=url)
        self.sandbox.break_push()
        origin = self.sandbox.head("origin/main")
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 1)
        self.assertIn("[DONE] archived retry", stdout)
        self.assertIn("[WARN] pending push", stdout)
        self.assertIn("pending_push=1", stdout)
        self.assertTrue(self.sandbox.pending_path().is_file())
        status = self.sandbox.git("status", "--porcelain", "--untracked-files=all")
        self.assertEqual(status.stdout.strip(), "")
        self.assertEqual(self.sandbox.head("origin/main"), origin)
        stored = json.loads(self.sandbox.pending_path().read_text(encoding="utf-8"))
        self.assertEqual(stored["commits"], [self.sandbox.head("HEAD")])
        self.assertEqual(stored["default_branch"], "main")
        self.sandbox.restore_push()
        code, retried, _ = self.sandbox.run_gc("--apply", "--no-fetch", "--archive-idle-days", "0")
        self.assertEqual(code, 0, retried)
        self.assertFalse(self.sandbox.pending_path().exists())
        self.assertNotEqual(self.sandbox.head("origin/main"), origin)
        self.assertIn("pending_push=0", retried)

    def test_unknown_commit_during_pending_is_not_pushed(self) -> None:
        oid = self.sandbox.land_fast_forward("task/sneak")
        url = self.sandbox.pr_url(44)
        self.sandbox.set_pr(url, "MERGED", oid, headRefName="task/sneak")
        self.sandbox.record_idle_task("sneak", branch="task/sneak", pr_url=url)
        self.sandbox.break_push()
        code, stdout, _ = self.sandbox.run_gc("--apply", "--no-fetch")
        self.assertEqual(code, 1, stdout)
        self.assertTrue(self.sandbox.pending_path().is_file())
        (self.sandbox.repo / "README.md").write_text("sneak\n", encoding="utf-8")
        self.sandbox.git("add", "README.md")
        self.sandbox.git("commit", "-m", "unknown while pending")
        self.sandbox.restore_push()
        origin = self.sandbox.head("origin/main")
        code, retried, _ = self.sandbox.run_gc("--apply", "--no-fetch", "--archive-idle-days", "0")
        self.assertEqual(code, 1)
        self.assertIn("[WARN] sync precondition failed: unknown_local_commits", retried)
        self.assertEqual(self.sandbox.head("origin/main"), origin)
        self.assertTrue(self.sandbox.pending_path().is_file())

    def test_dry_run_leaves_pending_record(self) -> None:
        trellis_gc.write_pending(self.sandbox.pending_path(), "main", ["abc123"])
        code, stdout, _ = self.sandbox.run_gc("--no-fetch", "--archive-idle-days", "0")
        self.assertEqual(code, 0)
        self.assertIn("pending_push=1", stdout)
        self.assertEqual(
            json.loads(self.sandbox.pending_path().read_text(encoding="utf-8"))["commits"],
            ["abc123"],
        )
