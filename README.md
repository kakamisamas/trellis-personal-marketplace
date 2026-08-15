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

## Install in a new project

```bash
trellis init --yes --user <name> --codex \
  --workflow solo-github-flow \
  --workflow-source gh:kakamisamas/trellis-personal-marketplace#v1.0.0
```

Select the platform flags your project actually uses; `--codex` is only an
example.

## Switch an existing project

List the remote templates, then switch:

```bash
trellis workflow --list \
  --marketplace gh:kakamisamas/trellis-personal-marketplace#v1.0.0

trellis workflow \
  --marketplace gh:kakamisamas/trellis-personal-marketplace#v1.0.0 \
  --template solo-github-flow
```

If `.trellis/workflow.md` has local edits, preview the replacement first:

```bash
trellis workflow \
  --marketplace gh:kakamisamas/trellis-personal-marketplace#v1.0.0 \
  --template solo-github-flow \
  --create-new
```

Review `.trellis/workflow.md.new`. Use `--force` only when replacing the active
workflow is intentional.

## Install project tooling

From the target repository root, preview and then apply the release-pinned
installer:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/kakamisamas/trellis-personal-marketplace/v1.0.0/scripts/setup.sh) --dry-run
bash <(curl -fsSL https://raw.githubusercontent.com/kakamisamas/trellis-personal-marketplace/v1.0.0/scripts/setup.sh)
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
keeps candidates it cannot verify.

Lifecycle hooks run from the repository or linked-worktree root in Trellis
0.6.12, so the relative GC path resolves there. The repository smoke test
archives a real linked-worktree task to verify this contract. If a later Trellis
version changes the hook CWD, the hook produces a non-blocking warning; the
explicit Phase 1.0 and Phase 3.5 GC runs remain the primary cleanup path. GC
prints every deletion, although Trellis currently captures successful hook
stdout.

After the first PR runs the gate, configure branch protection to require its
`size-gate` check and enable automatic deletion of merged head branches.

## Install the architecture baseline

The separate spec registry provides the optional baseline:

```bash
trellis init --registry gh:kakamisamas/trellis-spec-marketplace#v1.0.0 \
  --template solo-baseline --append
```

## Update and rollback

Remote workflow and tooling updates are not applied silently. For a later
release, replace `v1.0.0` with the new immutable tag, preview the workflow with
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
