from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.json"
WORKFLOW = ROOT / "workflows" / "solo-github-flow" / "workflow.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"
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
            "task.py set-branch",
            "task.py set-base-branch",
            "gh pr create",
            "gh pr checks --watch --fail-fast",
            "gh pr merge --squash --delete-branch --match-head-commit",
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
        )
        for phrase in forbidden:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, self.workflow)

    def test_workflow_is_a_narrow_native_customization(self) -> None:
        digest = hashlib.sha256(WORKFLOW.read_bytes()).hexdigest()
        self.assertNotEqual(digest, NATIVE_0612_SHA256)
        self.assertGreater(len(self.workflow.splitlines()), 650)

    def test_remote_smoke_pins_the_supported_trellis_package(self) -> None:
        ci = CI.read_text(encoding="utf-8")
        self.assertIn("@mindfoldhq/trellis@0.6.12", ci)

    def test_public_source_has_no_personal_project_paths(self) -> None:
        forbidden = (
            "/Users/",
            "legal-agent",
            "Ankicenter",
            "ARCHITECTURE.md",
        )
        files = [INDEX, WORKFLOW, ROOT / "README.md"]
        content = "\n".join(path.read_text(encoding="utf-8") for path in files)
        for phrase in forbidden:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, content)


if __name__ == "__main__":
    unittest.main()
