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

    def run_setup(
        self,
        root: Path,
        *args: str,
        without_gh: bool = False,
        without_ocr: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["TRELLIS_SETUP_ASSET_ROOT"] = str(ROOT)
        if without_gh or without_ocr:
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
            true_command = shutil.which("true")
            assert true_command is not None
            if not without_gh:
                (isolated_bin / "gh").symlink_to(true_command)
            if not without_ocr:
                (isolated_bin / "ocr").symlink_to(true_command)
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
        for label in (
            "trellis_gc.py",
            "trellis_codegraph.py",
            "trellis_diff.py",
            "pr-gate.yml",
            "tests-python.yml",
            "trellis-setup/SKILL.md",
        ):
            self.assertIn(label, result.stdout)

    def test_install_and_second_run_are_idempotent(self) -> None:
        root = self.make_repo()
        (root / ".claude").mkdir()
        first = self.run_setup(root)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue((root / "scripts" / "trellis_gc.py").is_file())
        self.assertTrue((root / "scripts" / "trellis_codegraph.py").is_file())
        self.assertTrue((root / "scripts" / "trellis_diff.py").is_file())
        self.assertTrue(os.access(root / "scripts" / "trellis_codegraph.py", os.X_OK))
        self.assertTrue(os.access(root / "scripts" / "trellis_diff.py", os.X_OK))
        self.assertTrue((root / ".github" / "workflows" / "pr-gate.yml").is_file())
        self.assertTrue((root / ".trellis" / "templates" / "ci" / "tests-python.yml").is_file())
        self.assertFalse((root / ".github" / "workflows" / "tests.yml").exists())
        self.assertTrue((root / ".agents" / "skills" / "trellis-setup" / "SKILL.md").is_file())
        self.assertTrue((root / ".claude" / "skills" / "trellis-setup" / "SKILL.md").is_file())

        backup_count = len(list((root / ".trellis").glob("config.yaml.bak.*")))
        second = self.run_setup(root)
        self.assertEqual(second.returncode, 0, second.stderr)
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

    def test_missing_gh_is_only_a_warning(self) -> None:
        root = self.make_repo()
        result = self.run_setup(root, "--dry-run", without_gh=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gh is unavailable", result.stdout)

    def test_missing_ocr_is_only_a_warning(self) -> None:
        root = self.make_repo()
        result = self.run_setup(root, "--dry-run", without_ocr=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ocr is unavailable", result.stdout)
        self.assertIn("@alibaba-group/open-code-review", result.stdout)

    def test_existing_test_template_is_preserved_for_manual_review(self) -> None:
        root = self.make_repo()
        template = root / ".trellis" / "templates" / "ci" / "tests-python.yml"
        template.parent.mkdir(parents=True)
        template.write_text("name: customized-tests\n", encoding="utf-8")
        result = self.run_setup(root)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(template.read_text(encoding="utf-8"), "name: customized-tests\n")
        self.assertIn("[MANUAL]", result.stdout)

    def test_old_three_piece_install_gains_new_helpers(self) -> None:
        root = self.make_repo()
        scripts = root / "scripts"
        scripts.mkdir()
        (scripts / "trellis_gc.py").write_text("#!/usr/bin/env python3\nprint('old')\n", encoding="utf-8")
        gate = root / ".github" / "workflows" / "pr-gate.yml"
        gate.parent.mkdir(parents=True)
        gate.write_text((ROOT / "assets" / "ci" / "pr-gate.yml").read_text(encoding="utf-8"), encoding="utf-8")
        skill = root / ".agents" / "skills" / "trellis-setup" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text((ROOT / "assets" / "skills" / "trellis-setup" / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8")
        result = self.run_setup(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((root / "scripts" / "trellis_codegraph.py").is_file())
        self.assertTrue((root / "scripts" / "trellis_diff.py").is_file())
        self.assertTrue((root / ".trellis" / "templates" / "ci" / "tests-python.yml").is_file())
        self.assertNotEqual((scripts / "trellis_gc.py").read_text(encoding="utf-8"), "#!/usr/bin/env python3\nprint('old')\n")

    def test_setup_in_task_worktree_leaves_base_clean(self) -> None:
        parent = Path(self.addCleanupContext(tempfile.TemporaryDirectory(prefix="setup-wt-")).name)
        base = parent / "base"
        worktree = parent / "task"
        subprocess.run(["git", "init", "-q", "-b", "main", base], check=True)
        common = base / ".trellis" / "scripts" / "common"
        common.mkdir(parents=True)
        (common / "__init__.py").write_text("", encoding="utf-8")
        (common / "config.py").write_text(PARSER_STUB, encoding="utf-8")
        (base / ".trellis" / "config.yaml").write_text("max_journal_lines: 2000\n", encoding="utf-8")
        subprocess.run(["git", "-C", base, "config", "user.name", "Setup Test"], check=True)
        subprocess.run(["git", "-C", base, "config", "user.email", "setup@example.invalid"], check=True)
        subprocess.run(["git", "-C", base, "add", "."], check=True)
        subprocess.run(["git", "-C", base, "commit", "-q", "-m", "trellis"], check=True)
        subprocess.run(["git", "-C", base, "worktree", "add", str(worktree), "-b", "task/setup"], check=True)
        self.addCleanup(
            lambda: subprocess.run(
                ["git", "-C", base, "worktree", "remove", "--force", str(worktree)],
                check=False,
                capture_output=True,
            )
        )
        result = self.run_setup(worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        status = subprocess.run(
            ["git", "-C", base, "status", "--porcelain", "--untracked-files=all"],
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertEqual(status.stdout.strip(), "")
        self.assertFalse((base / "scripts" / "trellis_gc.py").exists())
        self.assertTrue((worktree / "scripts" / "trellis_gc.py").is_file())
        self.assertTrue((worktree / "scripts" / "trellis_codegraph.py").is_file())
        self.assertTrue((worktree / "scripts" / "trellis_diff.py").is_file())
        self.assertTrue((worktree / ".trellis" / "templates" / "ci" / "tests-python.yml").is_file())

    def test_setup_install_targets_appear_in_phase_10_and_skill(self) -> None:
        setup = SETUP.read_text(encoding="utf-8")
        workflow = (ROOT / "workflows" / "solo-github-flow" / "workflow.md").read_text(encoding="utf-8")
        skill = (ROOT / "assets" / "skills" / "trellis-setup" / "SKILL.md").read_text(encoding="utf-8")
        targets = (
            "scripts/trellis_gc.py",
            "scripts/trellis_codegraph.py",
            "scripts/trellis_diff.py",
            ".github/workflows/pr-gate.yml",
            ".trellis/templates/ci/tests-python.yml",
        )
        for target in targets:
            with self.subTest(target=target):
                self.assertIn(target, setup)
                self.assertIn(target, workflow)
                self.assertIn(target, skill)

    def test_available_ocr_is_reported_without_running_it(self) -> None:
        root = self.make_repo()
        result = self.run_setup(root, "--dry-run", without_gh=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ocr is available", result.stdout)
        self.assertNotIn("ocr is unavailable", result.stdout)


if __name__ == "__main__":
    unittest.main()
