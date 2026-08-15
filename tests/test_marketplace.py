from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.json"
WORKFLOW = ROOT / "workflows" / "solo-github-flow" / "workflow.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"
SETUP = ROOT / "scripts" / "setup.sh"
GC = ROOT / "scripts" / "trellis_gc.py"
LICENSE = ROOT / "LICENSE"
NATIVE_0612_SHA256 = "e2c5ab7004ff83a5a804b50df81746aa1d558dd4480463287622605f86a82a76"


class MarketplaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(INDEX.read_text(encoding="utf-8"))
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_registry_entry_is_a_safe_workflow_path(self) -> None:
        self.assertEqual(self.payload["version"], 1)
        self.assertEqual(len(self.payload["templates"]), 1)
        entry = self.payload["templates"][0]
        self.assertEqual(entry["id"], "solo-github-flow")
        self.assertEqual(entry["type"], "workflow")
        self.assertNotIn("version", entry)
        path = Path(entry["path"])
        self.assertFalse(path.is_absolute())
        self.assertNotIn("..", path.parts)
        self.assertEqual(path.suffix, ".md")
        self.assertEqual((ROOT / path).resolve(), WORKFLOW.resolve())

    def test_native_parser_contract_is_preserved(self) -> None:
        required = (
            "# Development Workflow",
            "## Phase Index",
            "### Phase 1: Plan",
            "### Phase 2: Execute",
            "### Phase 3: Finish",
            "[workflow-state:no_task]",
            "[/workflow-state:no_task]",
            "[workflow-state:planning]",
            "[/workflow-state:planning]",
            "[workflow-state:planning-inline]",
            "[/workflow-state:planning-inline]",
            "[workflow-state:in_progress]",
            "[/workflow-state:in_progress]",
            "[workflow-state:in_progress-inline]",
            "[/workflow-state:in_progress-inline]",
            "[workflow-state:completed]",
            "[/workflow-state:completed]",
            "#### 1.4 Activate task `[required · once]`",
            "#### 2.1 Implement `[required · repeatable]`",
            "#### 2.2 Quality check `[required · repeatable]`",
            "#### 3.3 Spec update `[required · once]`",
            "#### 3.4 Commit changes `[required · once]`",
            "#### 3.5 Wrap-up reminder",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, self.workflow)

    def test_reporting_and_finish_intent_are_explicit(self) -> None:
        required = (
            "默认使用中文",
            "AI 生成文档和进度汇报",
            "代码、命令、标识符和配置键保留英文",
            "technical action",
            "plain-language function or result",
            "user-visible outcome",
            "changed files and reasons",
            "validation and results",
            "unresolved issues or uncertainty",
            "结束工作",
            "收尾",
            "one-shot authorization",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.workflow)

    def test_terminal_pipeline_uses_real_github_commands(self) -> None:
        required = (
            "git worktree add",
            "task.py set-branch",
            "task.py set-base-branch",
            "task.py set-meta",
            "gh pr create",
            "gh pr checks --watch --fail-fast",
            "gh pr merge --squash --delete-branch --match-head-commit",
            "git worktree remove",
            "git worktree prune",
            "git branch -D",
            "git status --porcelain --untracked-files=all",
            "trellis-finish-work",
        )
        for command in required:
            with self.subTest(command=command):
                self.assertIn(command, self.workflow)

        forbidden = (
            "task.py create-pr",
            "gh pr merge --admin",
            "--no-verify",
            "force-push",
            "push origin main",
            "git checkout -b",
        )
        for phrase in forbidden:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, self.workflow)

    def test_worktree_lifecycle_and_dispatch_order_are_explicit(self) -> None:
        required = (
            "the main worktree stays on the base branch",
            'task/<MM-DD-slug>',
            '../<repo>-wt/<MM-DD-slug>',
            "Active task: <absolute task path>",
            "Workdir: <absolute task worktree path>",
            "only inside `Workdir`",
            "Do not remove the task worktree during `after_archive`",
            "lifecycle hook failures are non-blocking",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.workflow)

        phase_one = self.workflow.index("#### 1.0 Create task")
        phase_two = self.workflow.index("#### 2.1 Implement")
        phase_three = self.workflow.index("#### 3.5 Wrap-up reminder")
        worktree_add = self.workflow.index("git worktree add", phase_one, phase_two)
        task_create = self.workflow.index("task.py create", worktree_add, phase_two)
        merge = self.workflow.index("gh pr merge", phase_three)
        worktree_remove = self.workflow.index("git worktree remove", merge)

        self.assertLess(worktree_add, task_create)
        self.assertLess(merge, worktree_remove)

    def test_gc_baseline_and_breadcrumb_contracts_are_explicit(self) -> None:
        required = (
            "trellis-personal-marketplace/v1.0.0/scripts/setup.sh",
            "python3 scripts/trellis_gc.py --apply",
            ".trellis/spec/guides/architecture-baseline.md",
            "Decision Log",
            "headRefOid",
            "It is safe to leave uncertain candidates behind",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.workflow)

        for state in ("planning", "planning-inline"):
            start = self.workflow.index(f"[workflow-state:{state}]")
            end = self.workflow.index(f"[/workflow-state:{state}]", start)
            self.assertIn("architecture-baseline.md", self.workflow[start:end])
        for state in ("in_progress", "in_progress-inline"):
            start = self.workflow.index(f"[workflow-state:{state}]")
            end = self.workflow.index(f"[/workflow-state:{state}]", start)
            block = self.workflow[start:end]
            self.assertIn("Decision Log", block)
            self.assertIn("trellis_gc.py --apply", block)

        completed_start = self.workflow.index("\n[workflow-state:completed]\n") + 1
        completed_end = self.workflow.index("[/workflow-state:completed]", completed_start)
        completed = self.workflow[completed_start:completed_end]
        self.assertNotIn("trellis_gc.py", completed)
        self.assertNotIn("architecture-baseline", completed)

    def test_readme_explains_distribution_and_cleanup_boundaries(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        required = (
            "coordinating worktree",
            "downloads only `workflow.md`",
            "does not copy companion scripts or `.trellis/config.yaml`",
            "Do not attach raw",
            "after_archive",
            "v1.0.0/scripts/setup.sh",
            "trellis-spec-marketplace#v1.0.0",
            "no `--force` mode",
            "hook CWD",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, readme)

    def test_workflow_is_a_narrow_native_customization(self) -> None:
        digest = hashlib.sha256(WORKFLOW.read_bytes()).hexdigest()
        self.assertNotEqual(digest, NATIVE_0612_SHA256)
        self.assertGreater(len(self.workflow.splitlines()), 650)

    def test_remote_smoke_pins_the_supported_trellis_package(self) -> None:
        ci = CI.read_text(encoding="utf-8")
        self.assertIn("@mindfoldhq/trellis@0.6.12", ci)
        self.assertNotIn("@mindfoldhq/trellis@0.6.15", ci)

    def test_release_assets_and_license_are_present(self) -> None:
        self.assertTrue(SETUP.is_file())
        self.assertTrue(GC.is_file())
        self.assertIn('RELEASE_REF="v1.0.0"', SETUP.read_text(encoding="utf-8"))
        self.assertIn("MIT License", LICENSE.read_text(encoding="utf-8"))

    def test_public_source_has_no_personal_project_paths(self) -> None:
        forbidden = (
            "/Users/",
            "legal-agent",
            "Ankicenter",
            "ARCHITECTURE.md",
        )
        files = [INDEX, WORKFLOW, ROOT / "README.md", SETUP, GC]
        content = "\n".join(path.read_text(encoding="utf-8") for path in files)
        for phrase in forbidden:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, content)


if __name__ == "__main__":
    unittest.main()
