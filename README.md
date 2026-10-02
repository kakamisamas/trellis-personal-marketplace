# Trellis Personal Marketplace

Personal Trellis workflow templates. The current template is
`solo-github-flow`, based on the Trellis 0.6.12 `native` workflow.

It keeps the native planning and quality gates and adds these behaviors:

- plans explain both the technical action and its plain-language result;
- completion reports summarize the outcome, changed files, validation, and
  unresolved issues;
- the coordinating worktree stays on the base branch while each implementation
  task runs in a sibling Git worktree;
- after the user says “开始”, the agent runs through Phase 3.4–3.5 (commit,
  native finish-work, PR, CI, squash merge, worktree and branch cleanup)
  without waiting for a wrap-up instruction; it stops only for a product
  decision, missing credentials or permissions, a CI failure outside the
  task's scope, or dirty files of unknown ownership;
- a release-pinned setup installs safe merged-task GC, a 3,500-line PR gate,
  the archive lifecycle hook, and a project-local setup skill;
- each mergeable pull request runs one advisory Open Code Review (OCR) of that
  PR's product diff, requires every finding to be fixed or rejected with
  evidence, and persists the result in that pull-request body; a later PR in
  the same task is a new review, not a deferred whole-task review;
- planning and spec updates consult an optional architecture baseline.

The template does not replace or modify Trellis's `trellis-finish-work` skill.
Repository rules, hooks, PR templates, and branch protection remain
authoritative.

## Worktree model

- The main AI session keeps the coordinating worktree checked out on the base
  branch for planning, acceptance, merge, and cleanup.
- Every worktree lives under `<repo>-wt/`. Phase 1.0 creates the task worktree
  `../<repo>-wt/<MM-DD-slug>` on `task/<MM-DD-slug>` after a read-only
  coordinating-worktree check, runs first-time setup inside that task worktree
  so the coordinating directory stays clean, initializes the worktree-local
  Trellis developer state, then creates the task inside that worktree.
- A single-card run executes on that task worktree. It does not create a card
  worktree, an integration worktree, or a new branch. Phase 3.5 removes that
  task worktree and its branch with `trellis_gc.py --apply`. If that script is
  unavailable, re-confirm the pull request is merged, its head SHA equals the
  local branch, and the worktree is clean, then remove them by hand with
  `git worktree remove`, `git branch -D`, and `git worktree prune`. Dispatch
  `run cleanup` never removes the task worktree.
- A multi-card run places each card worktree at
  `<repo>-wt/<run_id>/<run_id>-<card>` and the integration worktree at
  `<repo>-wt/<run_id>/<run_id>-integration`.
- If the coordinating worktree already has a `.codegraph/` index, Phase 1.0
  prepares an independent CodeGraph index in the task worktree with
  `scripts/trellis_codegraph.py` before task creation. It never copies or
  symlinks the base index. MCP queries must pass `projectPath` set to the task
  worktree absolute path.
- Implement/check dispatch prompts begin with absolute `Active task:` and
  `Workdir:` lines. Agents may operate only inside that worktree.
- Phase 3.5 removes the worktree and local task branch only after the squash
  merge and remote-branch deletion are verified.

The Trellis marketplace transport still downloads only `workflow.md`; it does not copy companion scripts or `.trellis/config.yaml`. Phase 1.0 therefore runs
the release-pinned setup in the task worktree when project tooling is missing.
First-time adoption should finish that install in the task directory, not the
coordinating worktree. Do not attach raw
`git worktree remove` to archive-time automation. Phase 3.5 removes the worktree
only after the squash merge and remote-branch deletion are verified.

## Requirements

- Trellis 0.6.12
- Python 3
- Git with `git worktree` support
- an authenticated GitHub CLI (`gh`) for normal GC verification and PR finish
- a GitHub repository whose pull requests publish at least one check result
  that actually runs the project's test suite; a size/line-count gate alone is
  not enough
- optional CodeGraph CLI (`@colbymchenry/codegraph@1.6.0` is the version these
  helpers verify); it becomes required for task worktree creation when the
  coordinating worktree already contains `.codegraph/`
- optional Open Code Review 1.9.4 or later (`ocr`) plus Git 2.41 or later for
  local AI review; missing or unconfigured OCR is recorded but does not block
  the workflow
- optional local `herdr-dispatch` skill at
  `~/.skills-manager/skills/herdr-dispatch/SKILL.md` when a task enables full
  card-run mode; marketplace transport still downloads only `workflow.md` and
  does not copy that skill. Missing skill is an error only for card-run tasks.
  `python3 ~/.skills-manager/skills/herdr-dispatch/scripts/herdr_dispatch.py run doctor`
  reports the missing entry. Ordinary Trellis tasks without card-run mode are
  unaffected.

The release commands below target `v1.6.1`. A version is not remotely
installable until that tag exists. Do not run unpublished version refs.

## Install in a new project

```bash
trellis init --yes --user <name> --codex \
  --workflow solo-github-flow \
  --workflow-source gh:kakamisamas/trellis-personal-marketplace#v1.6.1
```

Select the platform flags your project actually uses; `--codex` is only an
example.

## Switch an existing project

List the remote templates, then switch:

```bash
trellis workflow --list \
  --marketplace gh:kakamisamas/trellis-personal-marketplace#v1.6.1

trellis workflow \
  --marketplace gh:kakamisamas/trellis-personal-marketplace#v1.6.1 \
  --template solo-github-flow
```

If `.trellis/workflow.md` has local edits, preview the replacement first:

```bash
trellis workflow \
  --marketplace gh:kakamisamas/trellis-personal-marketplace#v1.6.1 \
  --template solo-github-flow \
  --create-new
```

Review `.trellis/workflow.md.new`. Use `--force` only when replacing the active
workflow is intentional.

## Install project tooling

From the **task worktree** on first adoption (or any caller-specified repository
when running setup by hand), preview and then apply the release-pinned
installer. `v1.4.0` is the minimum release that ships `trellis_codegraph.py` and
`trellis_diff.py`. Install the workflow and tooling from the same release.
Existing projects must run the installer to adopt these helpers; publishing a
marketplace release does not upgrade downstream projects automatically.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/kakamisamas/trellis-personal-marketplace/v1.6.1/scripts/setup.sh) --dry-run
bash <(curl -fsSL https://raw.githubusercontent.com/kakamisamas/trellis-personal-marketplace/v1.6.1/scripts/setup.sh)
```

The installer manages these targets:

- `scripts/trellis_gc.py`, `scripts/trellis_codegraph.py`, and
  `scripts/trellis_diff.py` are installed or updated atomically; a changed copy
  is backed up first with a UTC timestamp;
- `.github/workflows/pr-gate.yml` is installed only when absent;
- `.trellis/templates/ci/tests-python.yml` is installed only when absent (a
  template, not a live workflow);
- `trellis-setup` is installed in the shared skill root and the skill roots of
  configured platforms.

An existing PR gate, test CI template, or setup skill is never overwritten.
Setup prints a diff and exits `2` so project-specific changes can be reviewed
manually; that exit is a non-blocking partial success. There is no `--force` mode in v1. After changing `trellis_diff.py`, ship the PR gate in the same
release. A missing or unauthenticated `gh` is only a warning: GC keeps
candidates it cannot verify. The installer also checks whether `ocr` is on
`PATH`, but never installs it, chooses a model, or writes an API key. Do not
copy the Python/pytest test template into `.github/workflows/` for unittest-only or non-Python projects.

Lifecycle hooks run from the repository or linked-worktree root in Trellis
0.6.12. If a later Trellis version changes the hook CWD, that does not change
this workflow's GC path: the explicit Phase 1.0 and Phase 3.5 GC runs remain
the primary cleanup path. GC prints every deletion.

After the first PR runs the gate, configure branch protection to require its
`size-gate` check and enable automatic deletion of merged head branches.

### Merged-task GC

`scripts/trellis_gc.py` defaults to dry-run. `--apply` is what deletes and
archives. Every deletion prints the evidence that allowed it. Phase 3.5 runs
it from the base worktree after `turn_guard.py mark-merged`.

A `task/*` branch needs lifecycle evidence before any content proof. Identify
the task by its task directory name (`.trellis/tasks/<MM-DD-slug>`). The task
is eligible when that directory is already under `.trellis/tasks/archive/`
with `status == completed`. When no task file matches the branch, the only
other accepted lifecycle evidence is the older rule: upstream is `[gone]`,
the pull request is `MERGED`, and the heads are equal. A match still under
`.trellis/tasks/` is kept. A match under `archive/` whose status is not
`completed` is kept and does not proceed to a content proof. The content
proof is one of `pr`, `ancestor`, `tree_equal`, or `forced` (`--force-gone`
when upstream is already gone). A Git proof by itself cannot delete the
branch: the tip of a newly created branch is already an ancestor of the
default branch.

`card/*` and `run/*` branches and worktrees belong to the dispatch script's
`run cleanup`. GC only prints `[INFO managed_by=herdr-dispatch run cleanup]`
for them.

`--archive-idle-days N` (default 7; `0` disables) is the fallback archival.
It accepts two kinds of completion evidence: `status == completed`, or
`in_progress` whose `pr_url` pull request is `MERGED`, whose `headRefName`
equals the task branch, and whose head has landed. An `in_progress` task
with no pull request is `no_pr` and stays for a person to archive.

Before `--apply` archives or pushes, every sync check has to hold. The
current directory is the main checkout. The current branch is the default
branch. The worktree is clean. Unless `--no-fetch` is passed,
`git fetch origin <default>` must succeed; a failed fetch is `fetch_failed`,
the script exits 1, and it archives nothing, pushes nothing, and does not
reset. `--no-fetch` uses the `origin/<default>` ref already present locally.
`origin/<default>` is an ancestor of `HEAD`.
Every commit in `origin/<default>..HEAD` is listed in
`$(git rev-parse --git-common-dir)/trellis-gc/pending-push.json`, changes
only paths under `.trellis/tasks/`, and has exactly one parent. If a check
fails, the script exits 1, archives nothing, pushes nothing, and does not
reset. The pending record is what a later run retries after a failed push.
A top-level uncommented `session_auto_commit: false` skips new archives.
An inline comment is cut at the earliest ` #` or tab-`#`, then one pair of
quotes is removed. `false`, `no`, `0`, and `off` skip new archives; `true`,
`yes`, `1`, and `on` do not. An unrecognized value is treated as true and
the script prints
`[WARN] session_auto_commit: unrecognized value <v>, treating as true`.

## Turn guard

Turn guard is an optional user-level turn guard. When installed, the script
is `~/.skills-manager/skills/herdr-dispatch/scripts/turn_guard.py`.

Register it for the user. Preview the merge, then install. `install` merges
idempotently into `~/.claude/settings.json`, `~/.codex/hooks.json`, and
`~/.grok/hooks/turn-guard.json`. It backs each file up before changing it
and leaves existing entries in place. `doctor` prints JSON.

```bash
python3 ~/.skills-manager/skills/herdr-dispatch/scripts/turn_guard.py install --dry-run
python3 ~/.skills-manager/skills/herdr-dispatch/scripts/turn_guard.py install
python3 ~/.skills-manager/skills/herdr-dispatch/scripts/turn_guard.py doctor
```

On the project side, `.trellis/config.yaml` only needs `hooks.after_start`
and `hooks.after_archive`. `mark-start` writes the task marker when the task
starts. `mark-archive` updates that marker to the archived `task.json` path
and does not delete it.

```yaml
hooks:
  after_start:
    - "python3 ~/.skills-manager/skills/herdr-dispatch/scripts/turn_guard.py mark-start 2>/dev/null || true"
  after_archive:
    - "python3 ~/.skills-manager/skills/herdr-dispatch/scripts/turn_guard.py mark-archive 2>/dev/null || true"
```

If turn_guard.py is not installed, skip its calls; the flow is unchanged.
Without this hook, or when the script is not installed, the guard stays silent and does not block.

### Herdr and headless workers

When dispatched through herdr-dispatch, the dispatching master session runs in
a Herdr pane. Executors and reviewers run headless by default (`codex exec`,
`grok -p`, `cursor-agent -p`); the dispatcher owns their process, exit code,
and logs. By default, the only worker that stays in a Herdr pane is a
`claude`-harness worker. `assignee.session: headless | pane` in the task file
overrides that default. Combining `claude` with `headless` is rejected.

## Configure local OCR review

Install OCR once per machine, then choose and test the user-level LLM provider:

```bash
npm i -g @alibaba-group/open-code-review
ocr llm providers
ocr config provider
ocr config model
ocr llm test
```

The workflow calls the OCR CLI directly in workspace mode after the PR-bound
Phase 2.2 checks and before that pull request is merged. If the PR is already
committed, the review uses `--from <base-branch> --to HEAD`. Its default design
uses OCR's own configured LLM as an independent reviewer. `ocr delegate` is an
optional lower-cost alternative for manual use, but the workflow does not fall
back to it automatically because that would collapse author and reviewer into
the same agent context. Official Claude Code or Codex plugins may help invoke
OCR interactively; they are optional and do not replace the CLI contract above.

OCR first previews the supported files, excludes Trellis task/runtime metadata,
and records `complete`, `partial`, `skipped`, or `failed`. It runs exactly once
per pull request; after the agent disposes every finding, tests validate the
fixes without another OCR call on that PR. A later PR in the same task is a new
review. Workspace reviews never use `--resume`, even if OCR stderr suggests it. The resulting coverage and
per-finding decisions are written into a marked PR-body section and read back
before GitHub checks begin. No OCR secret or review job is added to CI.

## Workflow and spec templates

This repository publishes both workflow and spec templates from one `index.json`.
Trellis filters `templates[].type`: `workflow` for `--workflow` /
`--workflow-source`, and `spec` for `--registry` / `--template`.

The `solo-baseline` spec template was first published from
`gh:kakamisamas/trellis-spec-marketplace#v1.0.0`. Install it from this
repository instead.

### Install in a new project

Initialize Trellis and install the architecture baseline in one command:

```bash
trellis init --yes --user <name> --codex \
  --registry gh:kakamisamas/trellis-personal-marketplace#v1.6.1 \
  --template solo-baseline
```

Choose the platform flags your project actually uses; `--codex` is only an
example. The template is installed directly under `.trellis/spec/`, so a valid
installation contains `.trellis/spec/guides/architecture-baseline.md` and never
`.trellis/spec/spec/`.

### Add missing files without replacing local edits

For an existing Trellis project, `--append` copies only files that do not
already exist:

```bash
trellis init --yes --user <name> --codex \
  --registry gh:kakamisamas/trellis-personal-marketplace#v1.6.1 \
  --template solo-baseline \
  --append
```

This is the conservative choice when the project has already customized its
architecture baseline. It does not update an existing file.

### Upgrade to a newer immutable release

Replace `<new-tag>` with the release you reviewed. Preview the registry diff on
GitHub, commit local spec changes, then either merge the new baseline manually
or intentionally replace the installed template:

```bash
trellis init --yes --user <name> --codex \
  --registry gh:kakamisamas/trellis-personal-marketplace#<new-tag> \
  --template solo-baseline \
  --overwrite
```

Use `--append` instead when the new release adds files and every existing local
file must remain untouched. Old projects stay pinned until their registry source
is explicitly changed.

## Update and rollback

Remote workflow and tooling updates are not applied silently. For a later
release, replace `v1.6.1` with the new immutable tag, preview the workflow with
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
