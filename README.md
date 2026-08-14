# Trellis Personal Marketplace

Personal Trellis workflow templates. The current template is
`solo-github-flow`, based on the Trellis 0.6.12 `native` workflow.

It keeps the native planning and quality gates and adds four behaviors:

- plans explain both the technical action and its plain-language result;
- completion reports summarize the outcome, changed files, validation, and
  unresolved issues;
- the coordinating worktree stays on the base branch while each implementation
  task runs in a sibling Git worktree;
- after the user says “结束工作” or “收尾”, the agent completes the normal
  commit, native finish-work, PR, CI, squash-merge, worktree cleanup, and
  branch-cleanup flow.

The template does not replace or modify Trellis's `trellis-finish-work` skill.
Repository rules, hooks, PR templates, and branch protection remain
authoritative.

## Worktree model

- The main AI session keeps the coordinating worktree checked out on the base
  branch for planning, acceptance, merge, and cleanup.
- Phase 1.0 creates `../<repo>-wt/<MM-DD-slug>` on
  `task/<MM-DD-slug>`, initializes the worktree-local Trellis developer state,
  then creates the task inside that worktree.
- Implement/check dispatch prompts begin with absolute `Active task:` and
  `Workdir:` lines. Agents may operate only inside that worktree.
- Phase 3.5 removes the worktree and local task branch only after the squash
  merge and remote-branch deletion are verified.

This is a workflow-only marketplace template: Trellis downloads only
`workflow.md`, not companion scripts or `.trellis/config.yaml`. Cleanup is
therefore performed explicitly by the coordinating session. Do not attach
`git worktree remove` to `after_archive`; archive runs before push, checks, and
merge, so that hook would delete the task workspace too early.

## Requirements

- Trellis 0.6.12
- Git with `git worktree` support and an authenticated GitHub CLI (`gh`)
- a GitHub repository whose pull requests publish at least one check result

## Install in a new project

```bash
trellis init --yes --user <name> --codex \
  --workflow solo-github-flow \
  --workflow-source gh:kakamisamas/trellis-personal-marketplace
```

Select the platform flags your project actually uses; `--codex` is only an
example.

## Switch an existing project

List the remote templates, then switch:

```bash
trellis workflow --list \
  --marketplace gh:kakamisamas/trellis-personal-marketplace

trellis workflow \
  --marketplace gh:kakamisamas/trellis-personal-marketplace \
  --template solo-github-flow
```

If `.trellis/workflow.md` has local edits, preview the replacement first:

```bash
trellis workflow \
  --marketplace gh:kakamisamas/trellis-personal-marketplace \
  --template solo-github-flow \
  --create-new
```

Review `.trellis/workflow.md.new`. Use `--force` only when replacing the active
workflow is intentional.

## Update and rollback

Remote workflow updates are not applied silently. Repeat the `--create-new`
preview, review the diff, and then switch deliberately.

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
