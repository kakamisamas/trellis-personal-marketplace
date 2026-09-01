from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("trellis_gc", ROOT / "scripts" / "trellis_gc.py")
assert SPEC and SPEC.loader
trellis_gc = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = trellis_gc
SPEC.loader.exec_module(trellis_gc)


class FakeRunner:
    def __init__(
        self,
        *,
        current: str = "main",
        tracking: str = "[gone]",
        local_head: str = "abc123",
        pr_head: str = "abc123",
        pr_state: str = "MERGED",
        dirty: str = "",
        fetch_error: str = "",
        worktree_path: str | None = "/repo-wt/task-test",
        main_branch: str = "main",
    ) -> None:
        self.current = current
        self.tracking = tracking
        self.local_head = local_head
        self.pr_head = pr_head
        self.pr_state = pr_state
        self.dirty = dirty
        self.fetch_error = fetch_error
        self.worktree_path = worktree_path
        self.main_branch = main_branch
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args, cwd=None):  # noqa: ANN001, ANN202
        command = tuple(args)
        self.calls.append(command)
        result = trellis_gc.CommandResult
        if command == ("git", "rev-parse", "--show-toplevel"):
            return result(0, "/repo", "")
        if command == ("git", "fetch", "--prune", "--quiet"):
            return result(1, "", self.fetch_error) if self.fetch_error else result(0, "", "")
        if command == ("git", "rev-parse", "--abbrev-ref", "HEAD"):
            return result(0, self.current, "")
        if command == ("git", "worktree", "list", "--porcelain"):
            blocks = [f"worktree /repo\nbranch refs/heads/{self.main_branch}"]
            if self.worktree_path:
                blocks.append(f"worktree {self.worktree_path}\nbranch refs/heads/task/test")
            return result(0, "\n\n".join(blocks), "")
        if command[:2] == ("git", "for-each-ref"):
            return result(0, f"task/test\torigin/task/test\t{self.tracking}", "")
        if command[:4] == ("gh", "pr", "view", "task/test"):
            payload = f'{{"state":"{self.pr_state}","headRefOid":"{self.pr_head}"}}'
            return result(0, payload, "")
        if command == ("git", "rev-parse", "refs/heads/task/test"):
            return result(0, self.local_head, "")
        if command == ("git", "status", "--porcelain", "--untracked-files=all"):
            return result(0, self.dirty, "")
        if command in (
            ("git", "worktree", "remove", "/repo-wt/task-test"),
            ("git", "worktree", "remove", "--force", "/repo-wt/task-test"),
            ("git", "branch", "-D", "task/test"),
            ("git", "worktree", "prune"),
        ):
            return result(0, "", "")
        raise AssertionError(f"unexpected command: {command} cwd={cwd}")


class GarbageCollectorTests(unittest.TestCase):
    def invoke(self, runner: FakeRunner, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(trellis_gc, "run", runner),
            mock.patch.object(trellis_gc.shutil, "which", return_value="/usr/bin/gh"),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = trellis_gc.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_apply_removes_verified_clean_candidate(self) -> None:
        runner = FakeRunner()
        code, stdout, _ = self.invoke(runner, "--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("[DONE] removed worktree", stdout)
        self.assertIn(("git", "branch", "-D", "task/test"), runner.calls)

    def test_live_upstream_is_retained(self) -> None:
        runner = FakeRunner(tracking="")
        code, stdout, _ = self.invoke(runner, "--apply", "--no-fetch")
        self.assertEqual(code, 0)
        self.assertIn("upstream still exists", stdout)
        self.assertNotIn(("git", "branch", "-D", "task/test"), runner.calls)

    def test_dirty_worktree_is_retained(self) -> None:
        runner = FakeRunner(dirty=" M local.txt")
        _, stdout, _ = self.invoke(runner, "--apply", "--no-fetch")
        self.assertIn("worktree dirty or unreadable", stdout)
        self.assertIn(" M local.txt", stdout)
        self.assertNotIn(("git", "branch", "-D", "task/test"), runner.calls)

    def test_force_dirty_removes_verified_dirty_candidate(self) -> None:
        runner = FakeRunner(dirty=" M .opencode/package.json")
        code, stdout, _ = self.invoke(runner, "--apply", "--no-fetch", "--force-dirty")
        self.assertEqual(code, 0)
        self.assertIn("[DONE] removed worktree", stdout)
        self.assertIn(
            ("git", "worktree", "remove", "--force", "/repo-wt/task-test"),
            runner.calls,
        )
        self.assertIn(("git", "branch", "-D", "task/test"), runner.calls)

    def test_force_dirty_does_not_override_unverified(self) -> None:
        runner = FakeRunner(
            dirty=" M .opencode/package.json",
            local_head="new-local",
            pr_head="merged-head",
        )
        _, stdout, _ = self.invoke(runner, "--apply", "--no-fetch", "--force-dirty")
        self.assertIn("differs from merged PR head", stdout)
        self.assertNotIn(("git", "branch", "-D", "task/test"), runner.calls)
        self.assertNotIn(
            ("git", "worktree", "remove", "--force", "/repo-wt/task-test"),
            runner.calls,
        )

    def test_local_head_after_merge_is_retained(self) -> None:
        runner = FakeRunner(local_head="new-local", pr_head="merged-head")
        _, stdout, _ = self.invoke(runner, "--apply", "--no-fetch")
        self.assertIn("differs from merged PR head", stdout)
        self.assertNotIn(("git", "branch", "-D", "task/test"), runner.calls)

    def test_apply_refuses_failed_fetch(self) -> None:
        runner = FakeRunner(fetch_error="offline")
        code, _, stderr = self.invoke(runner, "--apply")
        self.assertEqual(code, 1)
        self.assertIn("refusing --apply", stderr)
        self.assertNotIn(("git", "branch", "-D", "task/test"), runner.calls)

    def test_current_branch_is_retained(self) -> None:
        runner = FakeRunner(current="task/test")
        _, stdout, _ = self.invoke(runner, "--apply", "--no-fetch")
        self.assertIn("current branch", stdout)
        self.assertFalse(any(call[:2] == ("gh", "pr") for call in runner.calls))

    def test_main_worktree_is_retained(self) -> None:
        runner = FakeRunner(worktree_path=None, main_branch="task/test")
        _, stdout, _ = self.invoke(runner, "--apply", "--no-fetch")
        self.assertIn("checked out in main worktree", stdout)
        self.assertNotIn(("git", "branch", "-D", "task/test"), runner.calls)


if __name__ == "__main__":
    unittest.main()
