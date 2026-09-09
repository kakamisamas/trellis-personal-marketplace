from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "workflows" / "solo-github-flow" / "workflow.md"
README = ROOT / "README.md"


class HerdrCardWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")

    def test_pointer_is_local_skill_not_personal_home(self) -> None:
        self.assertIn("~/.skills-manager/skills/herdr-dispatch/SKILL.md", self.workflow)
        self.assertNotIn("/Users/", self.workflow)
        self.assertIn("run doctor", self.workflow)

    def test_full_run_does_not_dispatch_native_implement(self) -> None:
        self.assertIn("do not also dispatch `trellis-implement`", self.workflow)
        self.assertIn("authorization.kind=full_run", self.workflow)
        self.assertIn("Do not create a native Trellis child", self.workflow)

    def test_both_in_progress_branches_route_card_run(self) -> None:
        for state in ("in_progress", "in_progress-inline"):
            start = self.workflow.index(f"\n[workflow-state:{state}]\n") + 1
            end = self.workflow.index(f"[/workflow-state:{state}]", start)
            block = self.workflow[start:end]
            with self.subTest(state=state):
                self.assertIn("herdr-dispatch", block)
                self.assertIn("结束工作", block)
                self.assertIn("收尾", block)
                self.assertIn("local OCR advisory review", block)

    def test_planning_keeps_contract_review_optional(self) -> None:
        for state in ("planning", "planning-inline"):
            start = self.workflow.index(f"[workflow-state:{state}]")
            end = self.workflow.index(f"[/workflow-state:{state}]", start)
            self.assertIn("contract_review", self.workflow[start:end])

    def test_wrap_up_inherits_saved_authorization(self) -> None:
        phase_34 = self.workflow.index("#### 3.4 Commit changes")
        phase_35 = self.workflow.index("#### 3.5 Wrap-up reminder")
        block = self.workflow[phase_34:phase_35]
        self.assertIn("one-shot authorization", block)
        self.assertIn("saved herdr-dispatch full-run authorization", block)
        self.assertIn("Do not wait for another 收尾", block)
        self.assertIn("trellis-wrap-up", block)

    def test_check_handoff_and_ocr_once_remain(self) -> None:
        self.assertIn("run handoff-check", self.workflow)
        self.assertIn("Run OCR exactly once per pull request", self.workflow)
        self.assertIn("not once per card commit", self.workflow)

    def test_readme_does_not_claim_unpublished_install(self) -> None:
        plain = self.readme.replace("*", "")
        self.assertIn("is not remotely", plain)
        self.assertIn("installable until that tag exists", plain)
        self.assertIn("Do not run unpublished version refs", self.readme)
        self.assertIn("gh:kakamisamas/trellis-personal-marketplace#v1.5.0", self.readme)
