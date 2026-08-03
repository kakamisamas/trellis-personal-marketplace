#!/usr/bin/env bash
set -euo pipefail

readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SOURCE="${TRELLIS_MARKETPLACE_SOURCE:-gh:kakamisamas/trellis-personal-marketplace}"
readonly EXPECTED_VERSION="${TRELLIS_EXPECTED_VERSION:-0.6.12}"

actual_version="$(trellis --version)"
if [[ "$actual_version" != "$EXPECTED_VERSION" ]]; then
  printf 'Expected Trellis %s, found %s\n' "$EXPECTED_VERSION" "$actual_version" >&2
  exit 1
fi

temporary="$(mktemp -d "${TMPDIR:-/tmp}/trellis-solo-flow.XXXXXX")"
cleanup() {
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

cmp "$ROOT/workflows/solo-github-flow/workflow.md" "$temporary/.trellis/workflow.md"

for step in 1.0 1.4 2.1 2.2 3.3 3.4 3.5; do
  (
    cd "$temporary"
    python3 .trellis/scripts/get_context.py \
      --mode phase --step "$step" --platform codex >/dev/null
  )
done

printf 'Installed and parsed solo-github-flow with Trellis %s\n' "$actual_version"
