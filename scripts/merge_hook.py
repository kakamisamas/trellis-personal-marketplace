#!/usr/bin/env python3
"""Add the solo-github-flow archive hook without rewriting unrelated YAML."""

from __future__ import annotations

import argparse
import importlib
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


COMMAND = "python3 scripts/trellis_gc.py --apply"
HOOK_BLOCK = f'hooks:\n  after_archive:\n    - "{COMMAND}"\n'
ROOT_HOOKS = re.compile(r"^hooks:\s*(?:#.*)?$")
ROOT_HOOKS_ANY = re.compile(r"^hooks:\s*(.*)$")
AFTER_ARCHIVE = re.compile(r"^  after_archive:\s*(?:#.*)?$")
AFTER_ARCHIVE_ANY = re.compile(r"^  after_archive:\s*(.*)$")
ANCHOR_OR_ALIAS = re.compile(r"(^|[\s:\-])(?:&|\*)[A-Za-z0-9_-]+")


class UnsupportedYaml(ValueError):
    pass


def strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index]
    return value


def normalize_list_scalar(line: str) -> str:
    match = re.match(r"^    -\s+(.+?)\s*$", line)
    if not match:
        raise UnsupportedYaml("after_archive must be a four-space-indented scalar list")
    value = strip_inline_comment(match.group(1)).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def meaningful(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped and not stripped.startswith("#"))


def unquoted_yaml(line: str) -> str:
    """Return only syntax outside quoted scalars, stopping at a YAML comment."""
    result: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            result.append(" ")
        elif quote == "'":
            if char == quote and index + 1 < len(line) and line[index + 1] == quote:
                result.extend((" ", " "))
                index += 1
            elif char == quote:
                quote = None
            result.append(" ")
        elif char in ("'", '"'):
            quote = char
            result.append(" ")
        elif char == "#" and (index == 0 or line[index - 1].isspace()):
            break
        else:
            result.append(char)
        index += 1
    return "".join(result)


def validate_supported_syntax(lines: list[str]) -> None:
    for line in lines:
        if "\t" in line:
            raise UnsupportedYaml("tabs are not supported")
        if meaningful(line) and ANCHOR_OR_ALIAS.search(unquoted_yaml(line)):
            raise UnsupportedYaml("YAML anchors and aliases are not supported")


def insert_hook(content: str) -> tuple[str, bool]:
    lines = content.splitlines()
    validate_supported_syntax(lines)

    root_candidates = [index for index, line in enumerate(lines) if ROOT_HOOKS_ANY.match(line)]
    if len(root_candidates) > 1:
        raise UnsupportedYaml("duplicate top-level hooks keys")
    if not root_candidates:
        prefix = content.rstrip("\n")
        candidate = f"{prefix}\n\n{HOOK_BLOCK}" if prefix else HOOK_BLOCK
        return candidate, True

    hooks_index = root_candidates[0]
    if not ROOT_HOOKS.match(lines[hooks_index]):
        raise UnsupportedYaml("hooks must use block style")

    hooks_end = len(lines)
    for index in range(hooks_index + 1, len(lines)):
        if meaningful(lines[index]) and indent_of(lines[index]) == 0:
            hooks_end = index
            break

    archive_candidates = [
        index
        for index in range(hooks_index + 1, hooks_end)
        if AFTER_ARCHIVE_ANY.match(lines[index])
    ]
    if len(archive_candidates) > 1:
        raise UnsupportedYaml("duplicate after_archive keys")
    if not archive_candidates:
        lines[hooks_end:hooks_end] = ["  after_archive:", f'    - "{COMMAND}"']
        return "\n".join(lines) + "\n", True

    archive_index = archive_candidates[0]
    if not AFTER_ARCHIVE.match(lines[archive_index]):
        raise UnsupportedYaml("after_archive must use a block-style list")

    archive_end = hooks_end
    for index in range(archive_index + 1, hooks_end):
        if meaningful(lines[index]) and indent_of(lines[index]) <= 2:
            archive_end = index
            break

    saw_item = False
    for index in range(archive_index + 1, archive_end):
        if not meaningful(lines[index]):
            continue
        saw_item = True
        if normalize_list_scalar(lines[index]) == COMMAND:
            return content, False
    if not saw_item and any(meaningful(line) for line in lines[archive_index + 1 : archive_end]):
        raise UnsupportedYaml("after_archive is not a scalar list")

    lines[archive_end:archive_end] = [f'    - "{COMMAND}"']
    return "\n".join(lines) + "\n", True


def validate_with_trellis(candidate: str, repo_root: Path) -> None:
    scripts_dir = repo_root / ".trellis" / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        module = importlib.import_module("common.config")
        parsed = module.parse_simple_yaml(candidate)
    except Exception as error:
        raise UnsupportedYaml(f"Trellis parser rejected candidate: {error}") from error
    finally:
        sys.path.pop(0)
        for name in [key for key in sys.modules if key == "common" or key.startswith("common.")]:
            sys.modules.pop(name, None)
    hooks = parsed.get("hooks") if isinstance(parsed, dict) else None
    commands = hooks.get("after_archive") if isinstance(hooks, dict) else None
    if not isinstance(commands, list) or COMMAND not in commands:
        raise UnsupportedYaml("Trellis parser did not recover the installed hook")


def backup_path(path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.bak.{timestamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak.{timestamp}.{counter}")
        counter += 1
    return candidate


def write_atomic(path: Path, content: str) -> Path | None:
    backup: Path | None = None
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = backup_path(path)
        shutil.copy2(path, backup)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        if path.exists():
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the Trellis after_archive GC hook")
    parser.add_argument("config", type=Path)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    content = args.config.read_text(encoding="utf-8") if args.config.exists() else ""
    try:
        candidate, changed = insert_hook(content)
        validate_with_trellis(candidate, args.repo_root)
    except (OSError, UnsupportedYaml) as error:
        print(f"[MANUAL] {args.config}: {error}", file=sys.stderr)
        return 2

    if not changed:
        print(f"[SKIP] {args.config}: archive hook already installed")
        return 0
    if args.dry_run:
        print(f"[PLAN] would update {args.config}: add archive hook")
        return 0

    backup = write_atomic(args.config, candidate)
    suffix = f" (backup: {backup})" if backup else ""
    print(f"[UPDATE] {args.config}: added archive hook{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
