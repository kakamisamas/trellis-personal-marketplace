from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "trellis_diff.py"
CODEGRAPH = ROOT / "scripts" / "trellis_codegraph.py"


class DiffHelperTests(unittest.TestCase):
    def addCleanupContext(self, manager):  # noqa: ANN001, ANN201, N802
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def make_repo(self) -> Path:
        root = Path(self.addCleanupContext(tempfile.TemporaryDirectory(prefix="diff-")))
        subprocess.run(["git", "init", "-q", "-b", "main", root], check=True)
        subprocess.run(["git", "-C", root, "config", "user.name", "Diff Test"], check=True)
        subprocess.run(
            ["git", "-C", root, "config", "user.email", "diff@example.invalid"],
            check=True,
        )
        (root / ".gitignore").write_text("*.lock\n", encoding="utf-8")
        (root / "keep.txt").write_text("keep\n", encoding="utf-8")
        (root / "edit.txt").write_text("old\n", encoding="utf-8")
        subprocess.run(["git", "-C", root, "add", "."], check=True)
        subprocess.run(["git", "-C", root, "commit", "-q", "-m", "base"], check=True)
        return root

    def git(self, root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=check,
            text=True,
            capture_output=True,
        )

    def sha(self, root: Path, ref: str = "HEAD") -> str:
        return self.git(root, "rev-parse", ref).stdout.strip()

    def fingerprint(self, root: Path) -> tuple[str, str, str]:
        rel = self.git(root, "rev-parse", "--git-path", "index").stdout.strip()
        index = Path(rel) if Path(rel).is_absolute() else root / rel
        digest = hashlib.sha256(index.read_bytes() if index.is_file() else b"").hexdigest()
        status = self.git(root, "status", "--porcelain", "--untracked-files=all").stdout
        cached = self.git(root, "diff", "--cached", "--raw", "-z").stdout
        return digest, status, cached

    def run_helper(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        self.assertTrue(HELPER.is_file(), f"missing helper: {HELPER}")
        return subprocess.run(
            ["python3", str(HELPER), *args],
            cwd=root,
            text=True,
            capture_output=True,
        )

    def count(self, result: subprocess.CompletedProcess[str]) -> int:
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        for line in result.stdout.splitlines():
            if line.startswith("changed lines:"):
                return int(line.split(":", 1)[1].strip())
        self.fail(f"missing changed lines in:\n{result.stdout}")
        return -1

    def test_helper_is_an_executable_cli(self) -> None:
        self.assertTrue(HELPER.is_file())
        self.assertTrue(os.access(HELPER, os.X_OK))

    def test_mixed_committed_staged_unstaged_and_untracked_count_once(self) -> None:
        root = self.make_repo()
        base = self.sha(root)
        (root / "edit.txt").write_text("old\ncommitted\n", encoding="utf-8")
        (root / "committed_new.txt").write_text("c1\nc2\n", encoding="utf-8")
        self.git(root, "add", ".")
        self.git(root, "commit", "-q", "-m", "committed")
        (root / "edit.txt").write_text("old\ncommitted\nstaged\n", encoding="utf-8")
        (root / "staged_new.txt").write_text("s1\n", encoding="utf-8")
        self.git(root, "add", "edit.txt", "staged_new.txt")
        (root / "edit.txt").write_text("old\ncommitted\nstaged\nunstaged\n", encoding="utf-8")
        (root / "keep.txt").write_text("keep\nchanged\n", encoding="utf-8")
        (root / "untracked.txt").write_text("n1\nn2\nn3\n", encoding="utf-8")
        (root / "ignored.lock").write_text("lock\n" * 20, encoding="utf-8")
        before = self.fingerprint(root)
        result = self.run_helper(root, "--base", base)
        self.assertEqual(self.fingerprint(root), before)
        # edit +3, committed_new +2, staged_new +1, keep +1, untracked +3
        self.assertEqual(self.count(result), 10)
        self.assertIn("planning budget: 2500", result.stdout)
        self.assertIn("hard limit: 3500", result.stdout)

    def test_reverted_working_tree_does_not_double_count(self) -> None:
        root = self.make_repo()
        base = self.sha(root)
        (root / "edit.txt").write_text("old\ncommitted\n", encoding="utf-8")
        self.git(root, "add", "edit.txt")
        self.git(root, "commit", "-q", "-m", "committed")
        (root / "edit.txt").write_text("old\ncommitted\nextra\n", encoding="utf-8")
        self.git(root, "add", "edit.txt")
        (root / "edit.txt").write_text("old\ncommitted\n", encoding="utf-8")
        result = self.run_helper(root, "--base", base)
        self.assertEqual(self.count(result), 1)

    def test_head_mode_ignores_workspace_and_untracked(self) -> None:
        root = self.make_repo()
        base = self.sha(root)
        (root / "edit.txt").write_text("old\ncommitted\n", encoding="utf-8")
        (root / "committed_new.txt").write_text("c1\nc2\n", encoding="utf-8")
        self.git(root, "add", ".")
        self.git(root, "commit", "-q", "-m", "committed")
        head = self.sha(root)
        (root / "edit.txt").write_text("old\ncommitted\nlocal\n", encoding="utf-8")
        (root / "untracked.txt").write_text("n1\nn2\nn3\n", encoding="utf-8")
        local = self.run_helper(root, "--base", base)
        ci = self.run_helper(root, "--base", base, "--head", head)
        self.assertEqual(self.count(local), 1 + 2 + 1 + 3)
        self.assertEqual(self.count(ci), 3)

    def test_local_matches_ci_after_commit_and_ignores_codegraph(self) -> None:
        root = self.make_repo()
        base = self.sha(root)
        (root / "source.txt").write_text("line\n" * 4, encoding="utf-8")
        (root / ".codegraph").mkdir()
        (root / ".codegraph" / "codegraph.db").write_bytes(b"x" * 8000)
        self.assertTrue(CODEGRAPH.is_file())
        isolated = Path(self.addCleanupContext(tempfile.TemporaryDirectory(prefix="diff-bin-")))
        for command in ("git", "python3"):
            (isolated / command).symlink_to(shutil.which(command))
        stub = isolated / "codegraph"
        stub.write_text(
            "#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\n"
            "args=sys.argv[1:]\n"
            "path=Path([a for a in args if not a.startswith('-')][-1]).resolve()\n"
            "if args[0]=='status':\n"
            "  print(json.dumps({'initialized':True,'version':'1.6.0',"
            "'projectPath':str(path),'indexPath':str(path/'.codegraph'),"
            "'pendingChanges':{'added':0,'modified':0,'removed':0},"
            "'worktreeMismatch':None,'index':{'state':'complete',"
            "'reindexRecommended':False,'pendingRefs':0}}))\n",
            encoding="utf-8",
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        env = dict(os.environ, PATH=str(isolated))
        prepared = subprocess.run(
            [
                "python3",
                str(CODEGRAPH),
                "prepare",
                "--base-worktree",
                str(root),
                "--worktree",
                str(root),
            ],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.git(root, "add", "source.txt", ".gitignore")
        self.git(root, "commit", "-q", "-m", "with ignore")
        head = self.sha(root)
        local = self.run_helper(root, "--base", base)
        ci = self.run_helper(root, "--base", base, "--head", head)
        self.assertEqual(self.count(local), self.count(ci))
        self.assertEqual(self.count(local), 4 + 1)  # source.txt + /.codegraph/ ignore line
        check = self.git(root, "check-ignore", "-q", ".codegraph/codegraph.db", check=False)
        self.assertEqual(check.returncode, 0)

    def test_special_paths_rename_binary_and_missing_newline(self) -> None:
        root = self.make_repo()
        base = self.sha(root)
        (root / "file with space.txt").write_text("a\nb\n", encoding="utf-8")
        (root / "file\twith\ttab.txt").write_text("tab\n", encoding="utf-8")
        (root / "nonewline.txt").write_text("a\nb", encoding="utf-8")
        (root / "binary.bin").write_bytes(bytes(range(256)) * 4)
        (root / "rename_me.txt").write_text("hello\nworld\n", encoding="utf-8")
        self.git(root, "add", ".")
        self.git(root, "commit", "-q", "-m", "special")
        self.git(root, "mv", "rename_me.txt", "renamed file.txt")
        self.git(root, "commit", "-q", "-m", "rename")
        head = self.sha(root)
        result = self.run_helper(root, "--base", base, "--head", head)
        # vs original base: space +2, tab +1, nonewline +2, renamed file +2, binary 0
        self.assertEqual(self.count(result), 7)
        newline_name = root / "a\nb.txt"
        newline_name.write_text("z\n", encoding="utf-8")
        local = self.run_helper(root, "--base", base)
        self.assertEqual(self.count(local), 7 + 1)

    def test_check_limit_and_missing_sha(self) -> None:
        root = self.make_repo()
        base = self.sha(root)
        (root / "source.txt").write_text("line\n" * 3500, encoding="utf-8")
        self.git(root, "add", "source.txt")
        self.git(root, "commit", "-q", "-m", "limit")
        head = self.sha(root)
        exact = self.run_helper(root, "--base", base, "--head", head, "--check")
        self.assertEqual(exact.returncode, 0, exact.stderr)
        self.assertIn("changed lines: 3500", exact.stdout)
        (root / "source.txt").write_text("line\n" * 3501, encoding="utf-8")
        self.git(root, "add", "source.txt")
        self.git(root, "commit", "-q", "-m", "over")
        over = self.run_helper(root, "--base", base, "--head", self.sha(root), "--check")
        self.assertNotEqual(over.returncode, 0)
        self.assertIn("above the 3500-line limit", over.stdout + over.stderr)
        missing = self.run_helper(
            root, "--base", "0" * 40, "--head", "1" * 40, "--check"
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("fetch-depth: 0", missing.stdout + missing.stderr)
        self.assertNotIn("changed lines: 0", missing.stdout)

    def test_untracked_pure_rename_matches_committed_ci(self) -> None:
        root = Path(self.addCleanupContext(tempfile.TemporaryDirectory(prefix="diff-rename-")))
        subprocess.run(["git", "init", "-q", "-b", "main", root], check=True)
        self.git(root, "config", "user.name", "Diff Test")
        self.git(root, "config", "user.email", "diff@example.invalid")
        (root / "old.txt").write_text("one\ntwo\n", encoding="utf-8")
        self.git(root, "add", "old.txt")
        self.git(root, "commit", "-q", "-m", "baseline")
        base = self.sha(root)
        os.rename(root / "old.txt", root / "new.txt")
        before = self.fingerprint(root)
        local = self.run_helper(root, "--base", base)
        self.assertEqual(self.fingerprint(root), before)
        self.assertEqual(self.count(local), 0)
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "rename")
        ci = self.run_helper(root, "--base", base, "--head", self.sha(root))
        self.assertEqual(self.count(ci), 0)

    def test_cached_remove_of_identical_worktree_file_is_zero(self) -> None:
        root = Path(self.addCleanupContext(tempfile.TemporaryDirectory(prefix="diff-rmcached-")))
        subprocess.run(["git", "init", "-q", "-b", "main", root], check=True)
        self.git(root, "config", "user.name", "Diff Test")
        self.git(root, "config", "user.email", "diff@example.invalid")
        (root / "new.txt").write_text("one\ntwo\n", encoding="utf-8")
        self.git(root, "add", "new.txt")
        self.git(root, "commit", "-q", "-m", "add")
        self.git(root, "rm", "--cached", "new.txt")
        self.assertEqual((root / "new.txt").read_text(encoding="utf-8"), "one\ntwo\n")
        before = self.fingerprint(root)
        result = self.run_helper(root, "--base", "HEAD")
        self.assertEqual(self.fingerprint(root), before)
        self.assertEqual(self.count(result), 0)

    def test_minified_and_dist_exclusions_match_gate(self) -> None:
        root = self.make_repo()
        base = self.sha(root)
        (root / "app.min.js").write_text("x\n" * 50, encoding="utf-8")
        (root / "dist").mkdir()
        (root / "dist" / "bundle.js").write_text("y\n" * 50, encoding="utf-8")
        (root / "package-lock.json").write_text("z\n" * 50, encoding="utf-8")
        (root / "ok.txt").write_text("keep-me\n", encoding="utf-8")
        self.git(root, "add", ".")
        self.git(root, "commit", "-q", "-m", "excluded")
        result = self.run_helper(root, "--base", base, "--head", self.sha(root))
        self.assertEqual(self.count(result), 1)


if __name__ == "__main__":
    unittest.main()
