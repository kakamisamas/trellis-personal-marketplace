from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "setup.sh"


PARSER_STUB = '''def parse_simple_yaml(content):
    command = "python3 scripts/trellis_gc.py --apply"
    if "hooks:" in content and "after_archive:" in content and command in content:
        return {"hooks": {"after_archive": [command]}}
    return {}
'''


class SetupTests(unittest.TestCase):
    def make_repo(self, config: str = "max_journal_lines: 2000\n") -> Path:
        root = Path(self.addCleanupContext(tempfile.TemporaryDirectory()).name)
        subprocess.run(["git", "init", "-q", "-b", "main", root], check=True)
        common = root / ".trellis" / "scripts" / "common"
        common.mkdir(parents=True)
        (common / "__init__.py").write_text("", encoding="utf-8")
        (common / "config.py").write_text(PARSER_STUB, encoding="utf-8")
        (root / ".trellis" / "config.yaml").write_text(config, encoding="utf-8")
        return root

    def addCleanupContext(self, manager):  # noqa: ANN001, ANN201, N802
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        manager.name = value
        return manager

    def run_setup(self, root: Path, *args: str, without_gh: bool = False) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["TRELLIS_SETUP_ASSET_ROOT"] = str(ROOT)
        if without_gh:
            isolated_bin = root / ".test-bin"
            isolated_bin.mkdir()
            for command in (
                "chmod",
                "cmp",
                "cp",
                "date",
                "diff",
                "dirname",
                "git",
                "mkdir",
                "mktemp",
                "mv",
                "python3",
                "rm",
            ):
                source = shutil.which(command)
                assert source is not None
                (isolated_bin / command).symlink_to(source)
            env["PATH"] = str(isolated_bin)
        return subprocess.run(
            ["/bin/bash", str(SETUP), *args],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
        )

    def test_dry_run_writes_nothing_and_reports_targets(self) -> None:
        root = self.make_repo()
        before = (root / ".trellis" / "config.yaml").read_bytes()
        result = self.run_setup(root, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((root / ".trellis" / "config.yaml").read_bytes(), before)
        self.assertFalse((root / "scripts" / "trellis_gc.py").exists())
        for label in ("trellis_gc.py", "pr-gate.yml", "config.yaml", "trellis-setup/SKILL.md"):
            self.assertIn(label, result.stdout)

    def test_install_and_second_run_are_idempotent(self) -> None:
        root = self.make_repo()
        (root / ".claude").mkdir()
        first = self.run_setup(root)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue((root / "scripts" / "trellis_gc.py").is_file())
        self.assertTrue((root / ".github" / "workflows" / "pr-gate.yml").is_file())
        self.assertTrue((root / ".agents" / "skills" / "trellis-setup" / "SKILL.md").is_file())
        self.assertTrue((root / ".claude" / "skills" / "trellis-setup" / "SKILL.md").is_file())
        self.assertIn("python3 scripts/trellis_gc.py --apply", (root / ".trellis" / "config.yaml").read_text())

        backup_count = len(list((root / ".trellis").glob("config.yaml.bak.*")))
        second = self.run_setup(root)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("archive hook already installed", second.stdout)
        self.assertEqual(len(list((root / ".trellis").glob("config.yaml.bak.*"))), backup_count)

    def test_existing_gate_is_preserved_for_manual_review(self) -> None:
        root = self.make_repo()
        gate = root / ".github" / "workflows" / "pr-gate.yml"
        gate.parent.mkdir(parents=True)
        gate.write_text("name: customized\n", encoding="utf-8")
        result = self.run_setup(root)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(gate.read_text(encoding="utf-8"), "name: customized\n")
        self.assertIn("[MANUAL]", result.stdout)

    def test_unsupported_yaml_is_unchanged_and_returns_partial(self) -> None:
        source = "hooks: {after_archive: []}\n"
        root = self.make_repo(source)
        result = self.run_setup(root)
        self.assertEqual(result.returncode, 2)
        self.assertEqual((root / ".trellis" / "config.yaml").read_text(), source)

    def test_missing_gh_is_only_a_warning(self) -> None:
        root = self.make_repo()
        result = self.run_setup(root, "--dry-run", without_gh=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gh is unavailable", result.stdout)


if __name__ == "__main__":
    unittest.main()
