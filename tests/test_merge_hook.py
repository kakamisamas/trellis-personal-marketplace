from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("merge_hook", ROOT / "scripts" / "merge_hook.py")
assert SPEC and SPEC.loader
merge_hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(merge_hook)


class MergeHookTests(unittest.TestCase):
    def test_appends_hooks_block_when_missing(self) -> None:
        candidate, changed = merge_hook.insert_hook("max_journal_lines: 2000\n")
        self.assertTrue(changed)
        self.assertTrue(candidate.endswith(merge_hook.HOOK_BLOCK))

    def test_inserts_after_archive_inside_existing_hooks(self) -> None:
        source = "hooks:\n  after_start:\n    - echo start\npackages:\n  app:\n    path: app\n"
        candidate, changed = merge_hook.insert_hook(source)
        self.assertTrue(changed)
        self.assertLess(candidate.index("after_archive"), candidate.index("packages:"))
        self.assertIn('    - "python3 scripts/trellis_gc.py --apply"', candidate)

    def test_appends_to_existing_after_archive_list(self) -> None:
        source = "hooks:\n  after_archive:\n    - echo archived\n"
        candidate, changed = merge_hook.insert_hook(source)
        self.assertTrue(changed)
        self.assertIn("    - echo archived\n    - \"python3", candidate)

    def test_unquoted_double_and_single_quoted_commands_are_idempotent(self) -> None:
        variants = (
            "python3 scripts/trellis_gc.py --apply",
            '"python3 scripts/trellis_gc.py --apply"',
            "'python3 scripts/trellis_gc.py --apply'",
            "'python3 scripts/trellis_gc.py --apply' # installed",
        )
        for variant in variants:
            with self.subTest(variant=variant):
                source = f"hooks:\n  after_archive:\n    - {variant}\n"
                candidate, changed = merge_hook.insert_hook(source)
                self.assertFalse(changed)
                self.assertEqual(candidate, source)

    def test_anchor_like_text_inside_quotes_is_allowed(self) -> None:
        source = 'hooks:\n  after_archive:\n    - "printf \'*shared\'"\n'
        candidate, changed = merge_hook.insert_hook(source)
        self.assertTrue(changed)
        self.assertIn('    - "printf \'*shared\'"', candidate)
        self.assertIn(f'    - "{merge_hook.COMMAND}"', candidate)

    def test_unsupported_shapes_are_rejected(self) -> None:
        fixtures = (
            "hooks: {after_archive: []}\n",
            "hooks:\n\tafter_archive: []\n",
            "hooks:\n  after_archive: []\n",
            "hooks:\n  after_archive:\n    - *shared\n",
            "hooks:\n  after_archive:\n    - echo one\nhooks:\n  after_start: []\n",
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                with self.assertRaises(merge_hook.UnsupportedYaml):
                    merge_hook.insert_hook(fixture)


if __name__ == "__main__":
    unittest.main()
