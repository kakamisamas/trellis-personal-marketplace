#!/usr/bin/env bash
set -uo pipefail

readonly RELEASE_REF="v1.1.0"
readonly RAW_BASE="https://raw.githubusercontent.com/kakamisamas/trellis-personal-marketplace/${RELEASE_REF}"
readonly LOCAL_ASSET_ROOT="${TRELLIS_SETUP_ASSET_ROOT:-}"
readonly HOOK_COMMAND='python3 scripts/trellis_gc.py --apply'

dry_run=false
if [[ "${1:-}" == "--dry-run" ]]; then
  dry_run=true
  shift
fi
if [[ $# -ne 0 ]]; then
  printf '[ERROR] usage: setup.sh [--dry-run]\n' >&2
  exit 1
fi

command -v git >/dev/null 2>&1 || { printf '[ERROR] git is required\n' >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { printf '[ERROR] python3 is required\n' >&2; exit 1; }
repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  printf '[ERROR] run setup inside a git repository\n' >&2
  exit 1
}
if [[ ! -d "${repo_root}/.trellis" ]]; then
  printf '[ERROR] %s is not a Trellis project; run trellis init first\n' "$repo_root" >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  printf '[WARN] gh is unavailable; GC will conservatively retain candidates unless --force-gone is used\n'
elif ! gh auth status >/dev/null 2>&1; then
  printf '[WARN] gh is not authenticated; GC will conservatively retain unverifiable candidates\n'
fi
if ! command -v ocr >/dev/null 2>&1; then
  printf '[WARN] ocr is unavailable; local AI review will be recorded as skipped (install with: npm i -g @alibaba-group/open-code-review)\n'
else
  printf '[READY] ocr is available; configure and verify its user-level LLM separately with ocr llm test\n'
fi

temporary="$(mktemp -d "${TMPDIR:-/tmp}/trellis-setup.XXXXXX")" || exit 1
cleanup() { rm -rf "$temporary"; }
trap cleanup EXIT

fetch_asset() {
  local relative="$1"
  local destination="${temporary}/${relative}"
  mkdir -p "$(dirname "$destination")"
  if [[ -n "$LOCAL_ASSET_ROOT" ]]; then
    [[ -f "${LOCAL_ASSET_ROOT}/${relative}" ]] || {
      printf '[ERROR] local asset missing: %s\n' "${LOCAL_ASSET_ROOT}/${relative}" >&2
      return 1
    }
    cp "${LOCAL_ASSET_ROOT}/${relative}" "$destination"
  else
    curl -fsSL "${RAW_BASE}/${relative}" -o "$destination" || {
      printf '[ERROR] failed to download %s/%s\n' "$RAW_BASE" "$relative" >&2
      return 1
    }
  fi
}

assets=(
  scripts/trellis_gc.py
  scripts/merge_hook.py
  assets/ci/pr-gate.yml
  assets/skills/trellis-setup/SKILL.md
)
for asset in "${assets[@]}"; do
  fetch_asset "$asset" || exit 1
done

partial=false
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

install_replaceable() {
  local source="$1" target="$2"
  printf '[TARGET] %s\n' "$target"
  if [[ -f "$target" ]] && cmp -s "$source" "$target"; then
    printf '[SKIP] %s: already current\n' "$target"
    return
  fi
  if $dry_run; then
    printf '[PLAN] would %s %s\n' "$([[ -e "$target" ]] && printf update || printf install)" "$target"
    return
  fi
  mkdir -p "$(dirname "$target")"
  if [[ -e "$target" ]]; then
    local backup="${target}.bak.${timestamp}"
    local counter=1
    while [[ -e "$backup" ]]; do
      backup="${target}.bak.${timestamp}.${counter}"
      counter=$((counter + 1))
    done
    cp -p "$target" "$backup"
    printf '[BACKUP] %s\n' "$backup"
  fi
  local staged="${target}.tmp.$$"
  cp "$source" "$staged"
  chmod +x "$staged"
  mv "$staged" "$target"
  printf '[UPDATE] %s\n' "$target"
}

install_conservative() {
  local source="$1" target="$2"
  printf '[TARGET] %s\n' "$target"
  if [[ -f "$target" ]] && cmp -s "$source" "$target"; then
    printf '[SKIP] %s: already current\n' "$target"
    return
  fi
  if [[ -e "$target" ]]; then
    printf '[MANUAL] %s exists and differs; review this diff before replacing:\n' "$target"
    diff -u "$target" "$source" || true
    partial=true
    return
  fi
  if $dry_run; then
    printf '[PLAN] would install %s\n' "$target"
    return
  fi
  mkdir -p "$(dirname "$target")"
  local staged="${target}.tmp.$$"
  cp "$source" "$staged"
  mv "$staged" "$target"
  printf '[INSTALL] %s\n' "$target"
}

install_replaceable "${temporary}/scripts/trellis_gc.py" "${repo_root}/scripts/trellis_gc.py"
install_conservative "${temporary}/assets/ci/pr-gate.yml" "${repo_root}/.github/workflows/pr-gate.yml"

merge_args=("${temporary}/scripts/merge_hook.py" "${repo_root}/.trellis/config.yaml" --repo-root "$repo_root")
$dry_run && merge_args+=(--dry-run)
python3 "${merge_args[@]}"
merge_status=$?
if [[ $merge_status -eq 2 ]]; then
  partial=true
elif [[ $merge_status -ne 0 ]]; then
  printf '[ERROR] archive hook installation failed\n' >&2
  exit 1
fi

skill_source="${temporary}/assets/skills/trellis-setup/SKILL.md"
skill_roots=("${repo_root}/.agents/skills")
platform_mappings=(
  ".claude:.claude/skills" ".cursor:.cursor/skills" ".opencode:.opencode/skills"
  ".kilocode:.kilocode/skills" ".kiro:.kiro/skills" ".agent:.agent/skills"
  ".devin:.devin/skills" ".qoder:.qoder/skills" ".codebuddy:.codebuddy/skills"
  ".factory:.factory/skills" ".trae:.trae/skills" ".reasonix:.reasonix/skills"
  ".zcode:.zcode/skills" ".grok:.grok/skills" ".kimi-code:.kimi-code/skills"
  ".snow:.snow/skills" ".omp:.omp/skills" ".dsh:.dsh/skills"
)
for mapping in "${platform_mappings[@]}"; do
  marker="${mapping%%:*}"
  relative_root="${mapping#*:}"
  [[ -d "${repo_root}/${marker}" ]] && skill_roots+=("${repo_root}/${relative_root}")
done
if [[ -d "${repo_root}/.github/skills" || -d "${repo_root}/.github/copilot" ]]; then
  skill_roots+=("${repo_root}/.github/skills")
fi
for skill_root in "${skill_roots[@]}"; do
  install_conservative "$skill_source" "${skill_root}/trellis-setup/SKILL.md"
done

printf '[REPORT] GC script, PR gate, archive hook, setup skill, and OCR CLI readiness checked for %s\n' "$repo_root"
printf '[MANUAL] Enable automatic head-branch deletion and require the size-gate check in repository settings.\n'
if $partial; then
  exit 2
fi
exit 0
