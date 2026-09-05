---
name: trellis-setup
description: "Use when the project follows solo-github-flow but scripts/trellis_gc.py, scripts/trellis_codegraph.py, scripts/trellis_diff.py, .github/workflows/pr-gate.yml, the test CI template, this setup skill, or the local OCR readiness check is missing."
---

# Trellis Setup

Confirm which of these project assets are absent or outdated:

- `scripts/trellis_gc.py`
- `scripts/trellis_codegraph.py`
- `scripts/trellis_diff.py`
- `.github/workflows/pr-gate.yml`
- `.trellis/templates/ci/tests-python.yml` (template only; not a live workflow)
- a CI workflow that actually runs the project's test suite; a size/line-count gate alone does not count
- `.agents/skills/trellis-setup/SKILL.md`
- the warn-only local `ocr` CLI readiness check

A project that already has GC, the PR gate, and this skill still needs setup when any newer helper or the test CI template is missing. During Phase 1.0, run this installer from the **task worktree**, not the coordinating worktree, so the coordinating directory stays clean. Existing setup can still run in a caller-specified repository.

Run the release-pinned bootstrap (`v1.4.0` is the minimum release that ships the CodeGraph and diff helpers). Install the workflow and tooling from the same release:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/kakamisamas/trellis-personal-marketplace/v1.4.0/scripts/setup.sh)
```

Read the complete report. Do not overwrite an existing PR gate, test CI template, or skill when setup marks it for manual review. Exit code 2 is a non-blocking partial success. Report installed assets, skipped assets, and remaining manual actions as three separate lists. Setup never installs OCR or configures an LLM key; an unavailable CLI is advisory and must remain non-blocking. Do not copy `tests-python.yml` into `.github/workflows/` for unittest-only or non-Python projects; adapt the install command from the project's real test docs.
