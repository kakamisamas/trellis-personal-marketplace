#!/usr/bin/env bash
set -euo pipefail

readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

(
  cd "$temporary"
  TRELLIS_SETUP_ASSET_ROOT="$ROOT" "$ROOT/scripts/setup.sh"
)
cmp "$ROOT/scripts/trellis_gc.py" "$temporary/scripts/trellis_gc.py"
cmp "$ROOT/assets/ci/pr-gate.yml" "$temporary/.github/workflows/pr-gate.yml"
grep -Fq 'python3 scripts/trellis_gc.py --apply' "$temporary/.trellis/config.yaml"

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

grep -Fq 'ocr review --preview --format json' <<<"$phase_22_context"
grep -Fq 'At most one fresh workspace re-review' <<<"$phase_22_context"
grep -Fq 'Workspace mode does not support `--resume`' <<<"$phase_22_context"

git -C "$temporary" config user.name "Trellis Smoke"
git -C "$temporary" config user.email "trellis-smoke@example.invalid"
git -C "$temporary" add .
git -C "$temporary" commit -q -m "test: initialize Trellis fixture"

task_name="$(date +%m-%d)-worktree-smoke"
task_branch="task/${task_name}"
worktree_path="${worktree_root}/${task_name}"
mkdir -p "$worktree_root"
git -C "$temporary" worktree add "$worktree_path" -b "$task_branch" main >/dev/null

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
git -C "$temporary" worktree remove "$worktree_path"
git -C "$temporary" branch -D "$task_branch" >/dev/null
git -C "$temporary" worktree prune

printf 'Installed, parsed, and exercised solo-github-flow with Trellis %s\n' "$actual_version"
