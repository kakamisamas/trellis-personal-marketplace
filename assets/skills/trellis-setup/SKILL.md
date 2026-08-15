---
name: trellis-setup
description: "Use when the project follows solo-github-flow but scripts/trellis_gc.py, .github/workflows/pr-gate.yml, the archive hook, or this setup skill is missing."
---

# Trellis Setup

Confirm which of these project assets are absent or outdated:

- `scripts/trellis_gc.py`
- `.github/workflows/pr-gate.yml`
- `hooks.after_archive` in `.trellis/config.yaml`
- `.agents/skills/trellis-setup/SKILL.md`

Run the release-pinned bootstrap from the repository root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/kakamisamas/trellis-personal-marketplace/v1.0.0/scripts/setup.sh)
```

Read the complete report. Do not overwrite an existing PR gate or skill when setup
marks it for manual review. Report installed assets, skipped assets, and remaining
manual actions as three separate lists.
