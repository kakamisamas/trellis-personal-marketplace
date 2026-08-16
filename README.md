# Trellis Personal Marketplace

Personal Trellis workflow templates. The current template is
`solo-github-flow`, based on the Trellis 0.6.12 `native` workflow.

It keeps the native planning and quality gates and adds these behaviors:

- plans explain both the technical action and its plain-language result;
- completion reports summarize the outcome, changed files, validation, and
  unresolved issues;
- the coordinating worktree stays on the base branch while each implementation
  task runs in a sibling Git worktree;
- after the user says “结束工作” or “收尾”, the agent completes the normal
  commit, native finish-work, PR, CI, squash-merge, worktree cleanup, and
  branch-cleanup flow;
- a release-pinned setup installs safe merged-task GC, a 3,500-line PR gate,
  the archive lifecycle hook, and a project-local setup skill;
- the final local quality pass runs advisory Open Code Review (OCR), requires
  every finding to be fixed or rejected with evidence, and persists the result
  in the pull-request body;
- planning and spec updates consult an optional architecture baseline.

The template does not replace or modify Trellis's `trellis-finish-work` skill.
Repository rules, hooks, PR templates, and branch protection remain
authoritative.

## Worktree model

- The main AI session keeps the coordinating worktree checked out on the base
  branch for planning, acceptance, merge, and cleanup.
- Phase 1.0 creates `../<repo>-wt/<MM-DD-slug>` on
  `task/<MM-DD-slug>`, initializes the worktree-local Trellis developer state,
  then creates the task inside that worktree.
- If the coordinating worktree already has a `.codegraph/` index, Phase 1.0
  initializes and verifies an independent CodeGraph index in the task worktree
  before task creation. It never copies or symlinks the base index.
- Implement/check dispatch prompts begin with absolute `Active task:` and
  `Workdir:` lines. Agents may operate only inside that worktree.
- Phase 3.5 removes the worktree and local task branch only after the squash
  merge and remote-branch deletion are verified.

The Trellis marketplace transport still downloads only `workflow.md`; it does not copy companion scripts or `.trellis/config.yaml`. Phase 1.0 therefore runs
the release-pinned setup when project tooling is missing. Do not attach raw
`git worktree remove` to `after_archive`: archive runs before push, checks, and
merge. The installed GC hook is safe because it verifies the merged PR and
skips the current and main worktrees, so it only clears older leftovers.

## Requirements

- Trellis 0.6.12
- Python 3
- Git with `git worktree` support
- an authenticated GitHub CLI (`gh`) for normal GC verification and PR finish
- a GitHub repository whose pull requests publish at least one check result
- optional CodeGraph CLI; it becomes required for task worktree creation when
  the coordinating worktree already contains `.codegraph/`
- optional Open Code Review 1.9.4 or later (`ocr`) plus Git 2.41 or later for
  local AI review; missing or unconfigured OCR is recorded but does not block
  the workflow

## Install in a new project

```bash
trellis init --yes --user <name> --codex \
  --workflow solo-github-flow \
  --workflow-source gh:kakamisamas/trellis-personal-marketplace#v1.2.1
```

Select the platform flags your project actually uses; `--codex` is only an
example.

## Switch an existing project

List the remote templates, then switch:

```bash
trellis workflow --list \
  --marketplace gh:kakamisamas/trellis-personal-marketplace#v1.2.1

trellis workflow \
  --marketplace gh:kakamisamas/trellis-personal-marketplace#v1.2.1 \
  --template solo-github-flow
```

If `.trellis/workflow.md` has local edits, preview the replacement first:

```bash
trellis workflow \
  --marketplace gh:kakamisamas/trellis-personal-marketplace#v1.2.1 \
  --template solo-github-flow \
  --create-new
```

Review `.trellis/workflow.md.new`. Use `--force` only when replacing the active
workflow is intentional.

## Install project tooling

From the target repository root, preview and then apply the release-pinned
installer:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/kakamisamas/trellis-personal-marketplace/v1.2.1/scripts/setup.sh) --dry-run
bash <(curl -fsSL https://raw.githubusercontent.com/kakamisamas/trellis-personal-marketplace/v1.2.1/scripts/setup.sh)
```

The installer manages four targets:

- `scripts/trellis_gc.py` is installed or updated atomically; a changed copy is
  backed up first with a UTC timestamp;
- `.github/workflows/pr-gate.yml` is installed only when absent;
- `.trellis/config.yaml` receives
  `python3 scripts/trellis_gc.py --apply` under `hooks.after_archive`;
- `trellis-setup` is installed in the shared skill root and the skill roots of
  configured platforms.

An existing PR gate or setup skill is never overwritten. Setup prints a diff
and exits `2` so project-specific changes can be reviewed manually. There is no `--force` mode in v1. A missing or unauthenticated `gh` is only a warning: GC
keeps candidates it cannot verify. The installer also checks whether `ocr` is on
`PATH`, but never installs it, chooses a model, or writes an API key.

Lifecycle hooks run from the repository or linked-worktree root in Trellis
0.6.12, so the relative GC path resolves there. The repository smoke test
archives a real linked-worktree task to verify this contract. If a later Trellis
version changes the hook CWD, the hook produces a non-blocking warning; the
explicit Phase 1.0 and Phase 3.5 GC runs remain the primary cleanup path. GC
prints every deletion, although Trellis currently captures successful hook
stdout.

After the first PR runs the gate, configure branch protection to require its
`size-gate` check and enable automatic deletion of merged head branches.

## Configure local OCR review

Install OCR once per machine, then choose and test the user-level LLM provider:

```bash
npm i -g @alibaba-group/open-code-review
ocr llm providers
ocr config provider
ocr config model
ocr llm test
```

The workflow calls the OCR CLI directly in workspace mode after the final Phase
2.2 checks and before the task is committed. Its default design uses OCR's own
configured LLM as an independent reviewer. `ocr delegate` is an optional lower-
cost alternative for manual use, but the workflow does not fall back to it
automatically because that would collapse author and reviewer into the same
agent context. Official Claude Code or Codex plugins may help invoke OCR
interactively; they are optional and do not replace the CLI contract above.

OCR first previews the supported files, excludes Trellis task/runtime metadata,
and records `complete`, `partial`, `skipped`, or `failed`. It runs exactly once
per task; after the agent disposes every finding, tests validate the fixes without
another OCR call. Workspace reviews never use `--resume`, even if OCR stderr suggests it. The resulting coverage and
per-finding decisions are written into a marked PR-body section and read back
before GitHub checks begin. No OCR secret or review job is added to CI.

## Install the architecture baseline

The separate spec registry provides the optional baseline:

```bash
trellis init --registry gh:kakamisamas/trellis-spec-marketplace#v1.0.0 \
  --template solo-baseline --append
```

## Update and rollback

Remote workflow and tooling updates are not applied silently. For a later
release, replace `v1.2.1` with the new immutable tag, preview the workflow with
`--create-new`, review the installer dry-run and diffs, then switch deliberately.

To return to Trellis's bundled workflow:

```bash
trellis workflow --template native --create-new
```

Review the sidecar before running `trellis workflow --template native --force`.
After a merged adoption, make rollback through the target project's normal task
branch and pull-request process.

## Verify this repository

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
scripts/smoke-install.sh
```
