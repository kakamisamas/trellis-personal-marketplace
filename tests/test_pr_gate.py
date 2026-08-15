from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / ".github" / "workflows" / "pr-gate.yml"
ASSET = ROOT / "assets" / "ci" / "pr-gate.yml"


def run_block() -> str:
    lines = CANONICAL.read_text(encoding="utf-8").splitlines()
    start = lines.index("        run: |") + 1
    return textwrap.dedent("\n".join(lines[start:])) + "\n"


class PullRequestGateTests(unittest.TestCase):
    def make_repo(self) -> tuple[Path, str]:
        manager = tempfile.TemporaryDirectory()
        self.addCleanup(manager.cleanup)
        root = Path(manager.name)
        subprocess.run(["git", "init", "-q", "-b", "main", root], check=True)
        subprocess.run(["git", "-C", root, "config", "user.name", "Gate Test"], check=True)
        subprocess.run(["git", "-C", root, "config", "user.email", "gate@example.invalid"], check=True)
        (root / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", root, "add", "."], check=True)
        subprocess.run(["git", "-C", root, "commit", "-q", "-m", "base"], check=True)
        base = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()
        return root, base

    def commit(self, root: Path, message: str) -> str:
        subprocess.run(["git", "-C", root, "add", "."], check=True)
        subprocess.run(["git", "-C", root, "commit", "-q", "-m", message], check=True)
        return subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()

    def execute(self, root: Path, base: str, head: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, BASE_SHA=base, HEAD_SHA=head)
        return subprocess.run(["bash", "-c", run_block()], cwd=root, env=env, text=True, capture_output=True)

    def test_distribution_asset_is_canonical_workflow(self) -> None:
        self.assertEqual(CANONICAL.read_bytes(), ASSET.read_bytes())
        content = CANONICAL.read_text(encoding="utf-8")
        self.assertIn("workflow_call:", content)
        self.assertIn("fetch-depth: 0", content)
        self.assertIn("git cat-file -e", content)

    def test_exact_limit_passes_even_with_excluded_and_binary_files(self) -> None:
        root, base = self.make_repo()
        (root / "source.txt").write_text("line\n" * 3500, encoding="utf-8")
        (root / "ignored.lock").write_text("lock\n" * 4000, encoding="utf-8")
        (root / "binary.bin").write_bytes(bytes(range(256)) * 8)
        head = self.commit(root, "limit")
        result = self.execute(root, base, head)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("changed lines: 3500", result.stdout)

    def test_over_limit_fails(self) -> None:
        root, base = self.make_repo()
        (root / "source.txt").write_text("line\n" * 3501, encoding="utf-8")
        head = self.commit(root, "over limit")
        result = self.execute(root, base, head)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("above the 3500-line limit", result.stdout)

    def test_missing_commit_fails_with_checkout_hint(self) -> None:
        root, _ = self.make_repo()
        result = self.execute(root, "0" * 40, "1" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fetch-depth: 0", result.stdout)


if __name__ == "__main__":
    unittest.main()
