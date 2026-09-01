from __future__ import annotations

import json
import unittest
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.json"
README = ROOT / "README.md"
BASELINE = ROOT / "specs" / "solo-baseline" / "guides" / "architecture-baseline.md"
PINNED_SOURCE = "gh:kakamisamas/trellis-personal-marketplace#v1.3.0"


class RegistrySmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(INDEX.read_text(encoding="utf-8"))

    def test_index_schema_and_template_identity(self) -> None:
        self.assertEqual(self.payload["version"], 1)
        self.assertEqual(set(self.payload), {"version", "templates"})
        self.assertIsInstance(self.payload["templates"], list)

        template = next(t for t in self.payload["templates"] if t["type"] == "spec")
        self.assertEqual(template["id"], "solo-baseline")
        self.assertEqual(template["type"], "spec")
        self.assertEqual(template["name"], "Solo Architecture Baseline")
        self.assertNotIn("version", template)
        self.assertIsInstance(template.get("description"), str)
        self.assertTrue(template["description"])
        self.assertTrue(template.get("tags"))
        self.assertTrue(all(isinstance(tag, str) and tag for tag in template["tags"]))

    def test_template_paths_are_safe_and_exist(self) -> None:
        seen_ids: set[str] = set()
        for template in self.payload["templates"]:
            if template.get("type") != "spec":
                continue
            with self.subTest(template=template["id"]):
                self.assertNotIn(template["id"], seen_ids)
                seen_ids.add(template["id"])
                raw_path = template["path"]
                self.assertIsInstance(raw_path, str)
                self.assertNotIn("\\", raw_path)
                path = PurePosixPath(raw_path)
                self.assertFalse(path.is_absolute())
                self.assertNotIn("..", path.parts)
                self.assertEqual(path.parts[:1], ("specs",))
                root = ROOT.joinpath(*path.parts)
                self.assertTrue(root.is_dir())
                self.assertTrue(any(candidate.is_file() for candidate in root.rglob("*")))
                for candidate in root.rglob("*"):
                    relative = candidate.relative_to(root)
                    self.assertNotIn("spec", relative.parts)

    def test_architecture_baseline_has_only_the_required_contract(self) -> None:
        content = BASELINE.read_text(encoding="utf-8")
        required = (
            "## Current State",
            "## 6-12 Month Verifiable Targets",
            "## Risk Gaps",
            "## ADR-Lite Decision Log",
            "Verification measure",
            "Revisit trigger",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, content)
        self.assertNotIn("periodic scan", content.lower())
        self.assertNotIn("周期扫描", content)

    def test_readme_documents_pinned_install_append_and_upgrade(self) -> None:
        content = README.read_text(encoding="utf-8")
        required = (
            PINNED_SOURCE,
            "--template solo-baseline",
            "--append",
            "--overwrite",
            "Install in a new project",
            "Upgrade to a newer immutable release",
            ".trellis/spec/guides/architecture-baseline.md",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, content)


if __name__ == "__main__":
    unittest.main()
