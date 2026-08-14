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

for step in 1.0 1.4 2.1 2.2 3.3 3.4 3.5; do
  (
    cd "$temporary"
    python3 .trellis/scripts/get_context.py \
      --mode phase --step "$step" --platform codex >/dev/null
  )
done

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
(
  cd "$temporary"
  TRELLIS_CONTEXT_ID=smoke-coordinator \
    python3 .trellis/scripts/task.py finish >/dev/null
)
git -C "$temporary" worktree remove "$worktree_path"
git -C "$temporary" branch -D "$task_branch" >/dev/null
git -C "$temporary" worktree prune

printf 'Installed, parsed, and exercised solo-github-flow with Trellis %s\n' "$actual_version"
