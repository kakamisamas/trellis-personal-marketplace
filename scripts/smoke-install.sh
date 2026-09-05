#!/usr/bin/env bash
set -euo pipefail

readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# v1.3.0 is a published fixture pin for the Trellis native skeleton only.
# Candidate workflow/assets come from TRELLIS_WORKFLOW_FILE and TRELLIS_SETUP_ASSET_ROOT.
readonly SOURCE="${TRELLIS_MARKETPLACE_SOURCE:-gh:kakamisamas/trellis-personal-marketplace}"
readonly EXPECTED_VERSION="${TRELLIS_EXPECTED_VERSION:-0.6.12}"
readonly WORKFLOW_FILE="${TRELLIS_WORKFLOW_FILE:-}"

actual_version="$(trellis --version)"
if [[ "$actual_version" != "$EXPECTED_VERSION" ]]; then
  printf 'Expected Trellis %s, found %s\n' "$EXPECTED_VERSION" "$actual_version" >&2
  exit 1
fi

temporary="$(mktemp -d "${TMPDIR:-/tmp}/trellis-solo-flow.XXXXXX")"
worktree_root="${temporary}-wt"
worktree_path=""
cleanup() {
  if [[ -n "$worktree_path" ]]; then
    git -C "$temporary" worktree remove --force "$worktree_path" 2>/dev/null || true
  fi
  rm -rf "$worktree_root"
  rm -rf "$temporary"
}
trap cleanup EXIT

git -C "$temporary" init -q -b main
(
  cd "$temporary"
  trellis init --yes --user smoke --codex \
    --workflow solo-github-flow \
    --workflow-source "$SOURCE"
)

if [[ -n "$WORKFLOW_FILE" ]]; then
  cp "$WORKFLOW_FILE" "$temporary/.trellis/workflow.md"
else
  cmp "$ROOT/workflows/solo-github-flow/workflow.md" "$temporary/.trellis/workflow.md"
fi

phase_22_context=""
for step in 1.0 1.4 2.1 2.2 3.3 3.4 3.5; do
  if [[ "$step" == "2.2" ]]; then
    phase_22_context="$(
      cd "$temporary"
      python3 .trellis/scripts/get_context.py \
        --mode phase --step "$step" --platform codex
    )"
  else
    (
      cd "$temporary"
      python3 .trellis/scripts/get_context.py \
        --mode phase --step "$step" --platform codex >/dev/null
    )
  fi
done

assert_phase_22_context() {
  local phrase="$1"
  grep -Fq "$phrase" <<<"$phase_22_context" || {
    printf 'Phase 2.2 context missing required contract: %s\n' "$phrase" >&2
    exit 1
  }
}
assert_phase_22_context 'ocr review --preview --format json'
assert_phase_22_context 'Run OCR exactly once per task'
assert_phase_22_context 'do not run OCR again'
assert_phase_22_context 'does not support `--resume`'

git -C "$temporary" config user.name "Trellis Smoke"
git -C "$temporary" config user.email "trellis-smoke@example.invalid"
git -C "$temporary" add .
git -C "$temporary" commit -q -m "test: initialize Trellis fixture"

task_name="$(date +%m-%d)-worktree-smoke"
task_branch="task/${task_name}"
worktree_path="${worktree_root}/${task_name}"
mkdir -p "$worktree_root"
git -C "$temporary" worktree add "$worktree_path" -b "$task_branch" main >/dev/null

(
  cd "$worktree_path"
  TRELLIS_SETUP_ASSET_ROOT="$ROOT" "$ROOT/scripts/setup.sh"
)
cmp "$ROOT/scripts/trellis_gc.py" "$worktree_path/scripts/trellis_gc.py"
cmp "$ROOT/scripts/trellis_codegraph.py" "$worktree_path/scripts/trellis_codegraph.py"
cmp "$ROOT/scripts/trellis_diff.py" "$worktree_path/scripts/trellis_diff.py"
cmp "$ROOT/assets/ci/pr-gate.yml" "$worktree_path/.github/workflows/pr-gate.yml"
cmp "$ROOT/assets/ci/tests-python.yml" "$worktree_path/.trellis/templates/ci/tests-python.yml"
[[ -x "$worktree_path/scripts/trellis_gc.py" ]]
[[ -x "$worktree_path/scripts/trellis_codegraph.py" ]]
[[ -x "$worktree_path/scripts/trellis_diff.py" ]]
[[ -z "$(git -C "$temporary" status --porcelain --untracked-files=all)" ]]
[[ ! -e "$temporary/scripts/trellis_gc.py" ]]
[[ ! -e "$temporary/scripts/trellis_codegraph.py" ]]
[[ ! -e "$temporary/scripts/trellis_diff.py" ]]

(
  cd "$worktree_path"
  python3 scripts/trellis_codegraph.py prepare \
    --base-worktree "$temporary" \
    --worktree "$worktree_path"
)
installed_assets=(
  scripts/trellis_gc.py
  scripts/trellis_codegraph.py
  scripts/trellis_diff.py
  .github/workflows/pr-gate.yml
  .trellis/templates/ci/tests-python.yml
  .agents/skills/trellis-setup/SKILL.md
)
git -C "$worktree_path" add -f -- "${installed_assets[@]}"
if ! git -C "$worktree_path" diff --cached --quiet; then
  git -C "$worktree_path" commit -q -m "test: install marketplace tooling"
fi

task_rel="$({
  cd "$worktree_path"
  python3 .trellis/scripts/init_developer.py smoke >/dev/null
  TRELLIS_CONTEXT_ID=smoke-task \
    python3 .trellis/scripts/task.py create \
      "Worktree smoke" --slug worktree-smoke
})"
task_path="${worktree_path}/${task_rel}"

(
  cd "$worktree_path"
  python3 .trellis/scripts/task.py set-branch "$task_rel" "$task_branch" >/dev/null
  python3 .trellis/scripts/task.py set-base-branch "$task_rel" main >/dev/null
  python3 .trellis/scripts/task.py set-meta "$task_rel" worktree "$worktree_path" >/dev/null
)

python3 - "$task_path/task.json" "$task_branch" "$worktree_path" <<'PY'
import json
import sys
from pathlib import Path

task = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert task["branch"] == sys.argv[2]
assert task["base_branch"] == "main"
assert task["meta"]["worktree"] == sys.argv[3]
PY

(
  cd "$temporary"
  TRELLIS_CONTEXT_ID=smoke-coordinator \
    python3 .trellis/scripts/task.py start "$task_path" >/dev/null
)

python3 - "$task_path/task.json" <<'PY'
import json
import sys
from pathlib import Path

task = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert task["status"] == "in_progress"
PY

git -C "$worktree_path" add "$task_rel"
git -C "$worktree_path" add -f -- "${installed_assets[@]}"
git -C "$worktree_path" commit -q -m "test: record worktree task"

archive_log="$({
  cd "$worktree_path"
  TRELLIS_CONTEXT_ID=smoke-task \
    python3 .trellis/scripts/task.py archive "$task_rel" --no-commit
} 2>&1)"
if grep -Fq "Hook failed" <<<"$archive_log"; then
  printf '%s\n' "$archive_log" >&2
  exit 1
fi
[[ -d "$temporary" ]]
[[ -d "$worktree_path" ]]

git -C "$worktree_path" add .trellis
if ! git -C "$worktree_path" diff --cached --quiet; then
  git -C "$worktree_path" commit -q -m "test: archive worktree task"
fi
if [[ -n "$(git -C "$worktree_path" status --porcelain --untracked-files=all)" ]]; then
  git -C "$worktree_path" status --porcelain --untracked-files=all >&2
  printf 'task worktree is dirty; refusing git worktree remove --force\n' >&2
  exit 1
fi
[[ -z "$(git -C "$temporary" status --porcelain --untracked-files=all)" ]]
git -C "$temporary" worktree remove "$worktree_path"
git -C "$temporary" branch -D "$task_branch" >/dev/null
git -C "$temporary" worktree prune

printf 'Installed, parsed, and exercised solo-github-flow with Trellis %s\n' "$actual_version"
