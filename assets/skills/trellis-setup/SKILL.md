---
name: trellis-setup
description: "Use when the project follows solo-github-flow but scripts/trellis_gc.py, .github/workflows/pr-gate.yml, this setup skill, or the local OCR readiness check is missing."
---

# Trellis Setup

Confirm which of these project assets are absent or outdated:

- `scripts/trellis_gc.py`
- `.github/workflows/pr-gate.yml`
- a CI workflow that runs the project's test suite; if missing, install from `assets/ci/tests-python.yml`
- `.agents/skills/trellis-setup/SKILL.md`
- the warn-only local `ocr` CLI readiness check

Run the release-pinned bootstrap from the repository root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/kakamisamas/trellis-personal-marketplace/v1.3.0/scripts/setup.sh)
```

Read the complete report. Do not overwrite an existing PR gate or skill when setup
marks it for manual review. Report installed assets, skipped assets, and remaining
manual actions as three separate lists. Setup never installs OCR or configures an
LLM key; an unavailable CLI is advisory and must remain non-blocking.
