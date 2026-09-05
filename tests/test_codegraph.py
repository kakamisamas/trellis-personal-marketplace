from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "trellis_codegraph.py"

# Fake CLI for 1.6.0: status --json, init -y, sync. Logs argv; status reads
# <path>/.codegraph/status.json when present.
STUB_SOURCE = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

log = Path(os.environ["CODEGRAPH_STUB_LOG"])
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\n")

args = sys.argv[1:]


def path_arg(values):
    for item in values:
        if item.startswith("-"):
            continue
        return str(Path(item).resolve())
    raise SystemExit("stub missing path")


def write_status(root: Path, **fields) -> None:
    payload = {
        "initialized": True,
        "version": "1.6.0",
        "projectPath": str(root.resolve()),
        "indexPath": str((root / ".codegraph").resolve()),
        "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
        "worktreeMismatch": None,
        "index": {"state": "complete", "reindexRecommended": False},
    }
    payload.update(fields)
    root.joinpath(".codegraph").mkdir(parents=True, exist_ok=True)
    root.joinpath(".codegraph", "status.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


if not args:
    raise SystemExit("stub: missing command")

command = args[0]
if command == "status":
    target = Path(path_arg(args[1:]))
    marker = target / ".codegraph" / "status.json"
    if marker.is_file():
        sys.stdout.write(marker.read_text(encoding="utf-8"))
    else:
        sys.stdout.write(
            json.dumps(
                {
                    "initialized": False,
                    "version": "1.6.0",
                    "projectPath": str(target),
                    "indexPath": str(target / ".codegraph"),
                    "lastIndexed": None,
                }
            )
        )
    sys.stdout.write("\n")
    raise SystemExit(0)

if command == "init":
    if os.environ.get("CODEGRAPH_STUB_INIT_FAIL") == "1":
        sys.stderr.write("stub init failed\n")
        raise SystemExit(1)
    target = Path(path_arg(args[1:]))
    extra = {}
    raw = os.environ.get("CODEGRAPH_STUB_INIT_STATUS")
    if raw:
        extra = json.loads(raw)
    write_status(target, **extra)
    raise SystemExit(0)

if command == "sync":
    if os.environ.get("CODEGRAPH_STUB_SYNC_FAIL") == "1":
        sys.stderr.write("stub sync failed\n")
        raise SystemExit(1)
    target = Path(path_arg(args[1:]))
    marker = target / ".codegraph" / "status.json"
    if marker.is_file():
        payload = json.loads(marker.read_text(encoding="utf-8"))
        payload["pendingChanges"] = {"added": 0, "modified": 0, "removed": 0}
        if isinstance(payload.get("index"), dict):
            payload["index"]["state"] = "complete"
            payload["index"]["pendingRefs"] = 0
        marker.write_text(json.dumps(payload), encoding="utf-8")
    else:
        write_status(target)
    raise SystemExit(0)

raise SystemExit(f"stub unknown command: {command}")
"""


class CodegraphHelperTests(unittest.TestCase):
    def addCleanupContext(self, manager):  # noqa: ANN001, ANN201, N802
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def make_git_dir(self, name: str) -> Path:
        parent = Path(self.addCleanupContext(tempfile.TemporaryDirectory(prefix="cg-")))
        root = parent / name
        root.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", root], check=True)
        subprocess.run(["git", "-C", root, "config", "user.name", "CodeGraph Test"], check=True)
        subprocess.run(
            ["git", "-C", root, "config", "user.email", "codegraph@example.invalid"],
            check=True,
        )
        (root / "README.md").write_text(f"{name}\n", encoding="utf-8")
        subprocess.run(["git", "-C", root, "add", "README.md"], check=True)
        subprocess.run(["git", "-C", root, "commit", "-q", "-m", "base"], check=True)
        return root

    def enable(self, root: Path, **fields: object) -> None:
        payload = {
            "initialized": True,
            "version": "1.6.0",
            "projectPath": str(root.resolve()),
            "indexPath": str((root / ".codegraph").resolve()),
            "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
            "worktreeMismatch": None,
            "index": {"state": "complete", "reindexRecommended": False},
        }
        payload.update(fields)
        (root / ".codegraph").mkdir()
        (root / ".codegraph" / "status.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        (root / ".codegraph" / "marker").write_text(root.name, encoding="utf-8")

    def isolated_env(
        self,
        root: Path,
        *,
        with_stub: bool = True,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        env = dict(os.environ)
        isolated_bin = root / ".test-bin"
        isolated_bin.mkdir()
        for command in ("git", "python3"):
            source = shutil.which(command)
            assert source is not None
            (isolated_bin / command).symlink_to(source)
        log = root / "codegraph-stub.log"
        env["CODEGRAPH_STUB_LOG"] = str(log)
        env["PATH"] = str(isolated_bin)
        if with_stub:
            stub = isolated_bin / "codegraph"
            stub.write_text(STUB_SOURCE, encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        if extra:
            env.update(extra)
        return env

    def run_helper(
        self,
        env: dict[str, str],
        *args: str,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.assertTrue(HELPER.is_file(), f"missing helper: {HELPER}")
        return subprocess.run(
            [sys_executable(), str(HELPER), *args],
            cwd=cwd or ROOT,
            env=env,
            text=True,
            capture_output=True,
        )

    def stub_calls(self, env: dict[str, str]) -> list[list[str]]:
        log = Path(env["CODEGRAPH_STUB_LOG"])
        if not log.is_file():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]

    def test_helper_is_an_executable_cli(self) -> None:
        self.assertTrue(HELPER.is_file())
        self.assertTrue(os.access(HELPER, os.X_OK))

    def test_both_disabled_skips_without_cli_or_index(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        env = self.isolated_env(base, with_stub=False)
        result = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipped", result.stdout.lower())
        self.assertFalse((base / ".codegraph").exists())
        self.assertFalse((task / ".codegraph").exists())
        self.assertFalse((task / ".gitignore").exists())

    def test_missing_cli_fails_when_base_is_enabled(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(base)
        env = self.isolated_env(base, with_stub=False)
        result = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CLI", result.stderr)
        self.assertFalse((task / ".codegraph").exists())

    def test_invalid_status_json_fails(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(task)
        (task / ".codegraph" / "status.json").write_text("not-json {", encoding="utf-8")
        env = self.isolated_env(base)
        result = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr, r"JSON|json")

    def test_project_root_mismatch_fails(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(task, projectPath="/tmp/other-codegraph-root")
        env = self.isolated_env(base)
        result = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr, r"project|root|worktree", result.stderr)

    def test_init_failure_is_not_reported_as_success(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(base)
        env = self.isolated_env(base, extra={"CODEGRAPH_STUB_INIT_FAIL": "1"})
        result = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("init", result.stderr.lower())
        self.assertNotIn("[OK]", result.stdout)

    def test_sync_failure_is_not_reported_as_success(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(task)
        env = self.isolated_env(base, extra={"CODEGRAPH_STUB_SYNC_FAIL": "1"})
        result = self.run_helper(env, "sync", "--worktree", str(task))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sync", result.stderr.lower())
        self.assertNotIn("[OK]", result.stdout)

    def test_prepare_inits_task_with_absolute_paths_and_does_not_copy_base(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(base)
        env = self.isolated_env(base)
        result = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue((task / ".codegraph").is_dir())
        self.assertFalse((task / ".codegraph").is_symlink())
        self.assertFalse((task / ".codegraph" / "marker").exists())
        self.assertEqual((base / ".codegraph" / "marker").read_text(encoding="utf-8"), "base")
        calls = self.stub_calls(env)
        self.assertTrue(any(call and call[0] == "init" for call in calls))
        for call in calls:
            for item in call[1:]:
                if item.startswith("-"):
                    continue
                self.assertTrue(Path(item).is_absolute(), call)
                self.assertEqual(Path(item).resolve(), Path(item))

    def test_task_enabled_base_disabled_verifies_without_init(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(task)
        env = self.isolated_env(base)
        result = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = [call[0] for call in self.stub_calls(env)]
        self.assertNotIn("init", commands)
        self.assertIn("status", commands)

    def test_prepare_syncs_pending_changes_instead_of_reinit(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(task, pendingChanges={"added": 2, "modified": 0, "removed": 0})
        env = self.isolated_env(base)
        result = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = [call[0] for call in self.stub_calls(env)]
        self.assertNotIn("init", commands)
        self.assertIn("sync", commands)
        self.assertIn("status", commands)

    def test_status_exit_zero_with_uninitialized_is_unhealthy(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(task, initialized=False)
        env = self.isolated_env(base)
        result = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("initialized", result.stderr.lower())

    def test_incomplete_index_state_fails(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(task, index={"state": "building", "reindexRecommended": False})
        env = self.isolated_env(base)
        result = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr.lower(), r"complete|incomplete|state")

    def test_appends_ignore_rule_only_on_task_and_is_idempotent(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(base)
        (base / ".gitignore").write_text("base-only\n", encoding="utf-8")
        env = self.isolated_env(base)
        first = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        ignore = (task / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/.codegraph/", ignore.splitlines())
        self.assertEqual((base / ".gitignore").read_text(encoding="utf-8"), "base-only\n")
        second = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((task / ".gitignore").read_text(encoding="utf-8").count("/.codegraph/"), 1)

    def test_tracked_codegraph_is_rejected_without_deleting_files(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(base)
        (task / ".codegraph").mkdir()
        (task / ".codegraph" / "keep.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "-C", task, "add", "-f", ".codegraph/keep.txt"], check=True)
        subprocess.run(["git", "-C", task, "commit", "-q", "-m", "track index"], check=True)
        env = self.isolated_env(base)
        result = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr.lower(), r"track")
        self.assertTrue((task / ".codegraph" / "keep.txt").is_file())
        self.assertEqual((task / ".codegraph" / "keep.txt").read_text(encoding="utf-8"), "tracked\n")

    def test_sync_skips_when_task_disabled(self) -> None:
        task = self.make_git_dir("task")
        env = self.isolated_env(task, with_stub=False)
        result = self.run_helper(env, "sync", "--worktree", str(task))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipped", result.stdout.lower())

    def test_sync_uses_absolute_worktree_path(self) -> None:
        task = self.make_git_dir("task")
        self.enable(task)
        env = self.isolated_env(task)
        result = self.run_helper(env, "sync", "--worktree", str(task))
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.stub_calls(env)
        self.assertTrue(any(call and call[0] == "sync" for call in calls))
        for call in calls:
            for item in call[1:]:
                if item.startswith("-"):
                    continue
                self.assertEqual(Path(item), Path(item).resolve())
                self.assertEqual(Path(item), task.resolve())

    def test_index_path_in_other_worktree_is_unhealthy(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(base)
        self.enable(task, indexPath=str((base / ".codegraph").resolve()))
        env = self.isolated_env(base)
        result = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("[OK]", result.stdout)
        self.assertRegex(result.stderr.lower(), r"indexpath|index path|another|other")
        commands = [call[0] for call in self.stub_calls(env)]
        self.assertNotIn("sync", commands)

    def test_codegraph_symlink_is_rejected_before_sync(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(base)
        os.symlink(base / ".codegraph", task / ".codegraph")
        env = self.isolated_env(base)
        result = self.run_helper(
            env,
            "sync",
            "--worktree",
            str(task),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("[OK]", result.stdout)
        self.assertRegex(result.stderr.lower(), r"symlink|symbolic")
        self.assertEqual((base / ".codegraph" / "marker").read_text(encoding="utf-8"), "base")
        self.assertTrue((task / ".codegraph").is_symlink())
        self.assertEqual(self.stub_calls(env), [])

    def test_pending_refs_are_synced_then_rechecked(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(
            task,
            index={"state": "complete", "reindexRecommended": False, "pendingRefs": 4},
        )
        env = self.isolated_env(base)
        result = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = [call[0] for call in self.stub_calls(env)]
        self.assertIn("sync", commands)
        self.assertNotIn("init", commands)

    def test_reindex_recommended_fails_without_rebuild_or_upgrade(self) -> None:
        base = self.make_git_dir("base")
        task = self.make_git_dir("task")
        self.enable(
            task,
            index={"state": "complete", "reindexRecommended": True, "pendingRefs": 0},
        )
        env = self.isolated_env(base)
        result = self.run_helper(
            env,
            "prepare",
            "--base-worktree",
            str(base),
            "--worktree",
            str(task),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("[OK]", result.stdout)
        self.assertRegex(result.stderr.lower(), r"rebuild|reindex")
        self.assertIn("do not auto-upgrade", result.stderr.lower())
        commands = [call[0] for call in self.stub_calls(env)]
        self.assertNotIn("init", commands)


def sys_executable() -> str:
    return shutil.which("python3") or "python3"


if __name__ == "__main__":
    unittest.main()
