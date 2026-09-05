#!/usr/bin/env python3
"""Prepare and refresh a task-worktree CodeGraph index.

CLI (solo-github-flow):

    python3 scripts/trellis_codegraph.py prepare \\
        --base-worktree <base-path> --worktree <task-path>
    python3 scripts/trellis_codegraph.py sync --worktree <task-path>

Verified against @colbymchenry/codegraph 1.6.0 `status --json`. Required field:
`initialized`. Optional fields, when present: `projectPath`, `indexPath`,
`pendingChanges` (`added`/`modified`/`removed`), `index.state`,
`index.pendingRefs`, `index.reindexRecommended`, `worktreeMismatch`. Process
exit 0 is not index health. A `.codegraph` symlink or `indexPath` outside the
task worktree is rejected before sync. This helper never upgrades CodeGraph
and never indexes the caller merely because it is the current repository.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


IGNORE_RULE = "/.codegraph/"
PINNED_CLI = "@colbymchenry/codegraph@1.6.0"


class HelperError(Exception):
    pass


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run(args: Sequence[str], cwd: str | Path | None = None) -> CommandResult:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return CommandResult(result.returncode, result.stdout.strip(), result.stderr.strip())


def fail(message: str) -> int:
    print(f"[ERROR] {message}", file=sys.stderr)
    return 1


def resolve_dir(label: str, value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise HelperError(f"{label} is not a directory: {path}")
    return path


def enabled(worktree: Path) -> bool:
    path = worktree / ".codegraph"
    return path.is_dir() or path.is_symlink()


def expected_index_dir(worktree: Path) -> Path:
    return worktree.resolve() / ".codegraph"


def assert_local_index(worktree: Path) -> None:
    index_dir = worktree / ".codegraph"
    if not index_dir.exists() and not index_dir.is_symlink():
        return
    expected = expected_index_dir(worktree)
    if index_dir.is_symlink():
        target = index_dir.resolve()
        raise HelperError(
            f".codegraph/ in {worktree} is a symlink to {target}; "
            "refuse this cross-worktree index before sync so the other "
            "worktree is not refreshed. Remove the symlink and run prepare "
            f"to create a local index at {expected}."
        )
    if index_dir.resolve() != expected:
        raise HelperError(
            f".codegraph/ in {worktree} resolves to {index_dir.resolve()}, "
            f"not {expected}; refuse this cross-worktree index before sync."
        )


def require_cli() -> str:
    path = shutil.which("codegraph")
    if path is None:
        raise HelperError(
            "CodeGraph CLI is unavailable. Install "
            f"{PINNED_CLI} and retry; do not auto-upgrade. "
            "codegraph init -y <absolute-task-worktree>"
        )
    return path


def codegraph(cli: str, *args: str) -> CommandResult:
    return run([cli, *args])


def parse_status_json(raw: str, worktree: Path) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HelperError(
            f"codegraph status --json {worktree} did not print JSON "
            f"({exc.msg}); repair the CLI output and retry"
        ) from exc
    if not isinstance(payload, dict):
        raise HelperError(
            f"codegraph status --json {worktree} must print a JSON object"
        )
    return payload


def _int_field(label: str, value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError) as exc:
        raise HelperError(f"{label} is not a number: {value!r}") from exc


def pending_count(status: Mapping[str, Any]) -> int | None:
    total = 0
    found = False
    pending = status.get("pendingChanges")
    if isinstance(pending, dict):
        found = True
        for key in ("added", "modified", "removed"):
            total += _int_field(f"CodeGraph pendingChanges.{key}", pending.get(key))
    index = status.get("index")
    if isinstance(index, dict) and "pendingRefs" in index:
        found = True
        total += _int_field("CodeGraph index.pendingRefs", index.get("pendingRefs"))
    return total if found else None


def verify_status(status: Mapping[str, Any], worktree: Path) -> None:
    if "initialized" not in status:
        raise HelperError(
            f"codegraph status --json {worktree} is missing initialized; "
            "treat the index as unhealthy"
        )
    if not status.get("initialized"):
        raise HelperError(
            f"CodeGraph is not initialized at {worktree} "
            "(status --json initialized=false; exit 0 is not healthy). "
            f"Run: codegraph init -y {worktree}"
        )
    project = status.get("projectPath")
    if project:
        if Path(str(project)).expanduser().resolve() != worktree:
            raise HelperError(
                f"CodeGraph project root {project} does not match worktree "
                f"{worktree}; pass the task worktree absolute path to CLI/MCP"
            )
    index_path = status.get("indexPath")
    if index_path:
        resolved_index = Path(str(index_path)).expanduser().resolve()
        expected = expected_index_dir(worktree)
        if resolved_index != expected:
            raise HelperError(
                f"CodeGraph indexPath {index_path} points at another worktree "
                f"(expected {expected}); refuse this index before sync."
            )
    mismatch = status.get("worktreeMismatch")
    if mismatch:
        raise HelperError(
            f"CodeGraph worktreeMismatch={mismatch!r} at {worktree}; "
            f"re-run prepare in this worktree: codegraph sync {worktree}"
        )
    index = status.get("index")
    if isinstance(index, dict):
        state = index.get("state")
        if state is not None and state != "complete":
            raise HelperError(
                f"CodeGraph index state is {state!r}, not complete, at "
                f"{worktree}; run codegraph sync {worktree} or init -y after repair"
            )
        if index.get("reindexRecommended"):
            raise HelperError(
                f"CodeGraph index.reindexRecommended is true at {worktree}; "
                "rebuild the local index with codegraph index --force "
                f"{worktree} or codegraph init -y {worktree}. "
                "Do not auto-upgrade CodeGraph."
            )


def read_status(cli: str, worktree: Path) -> dict[str, Any]:
    result = codegraph(cli, "status", "--json", str(worktree))
    if result.returncode != 0:
        detail = result.stderr or result.stdout or f"exit {result.returncode}"
        raise HelperError(
            f"codegraph status --json {worktree} failed: {detail}. "
            "Exit 0 is required before JSON health checks."
        )
    if not result.stdout:
        raise HelperError(
            f"codegraph status --json {worktree} printed no JSON; "
            "cannot treat the index as healthy"
        )
    return parse_status_json(result.stdout, worktree)


def sync_index(cli: str, worktree: Path) -> None:
    result = codegraph(cli, "sync", str(worktree))
    if result.returncode != 0:
        detail = result.stderr or result.stdout or f"exit {result.returncode}"
        raise HelperError(
            f"codegraph sync {worktree} failed: {detail}. "
            "Keep the task worktree and retry after the index is writable."
        )


def init_index(cli: str, worktree: Path) -> None:
    result = codegraph(cli, "init", "-y", str(worktree))
    if result.returncode != 0:
        detail = result.stderr or result.stdout or f"exit {result.returncode}"
        raise HelperError(
            f"codegraph init -y {worktree} failed: {detail}. "
            "Keep the task worktree; do not copy or symlink the base .codegraph/"
        )


def verify_and_refresh(cli: str, worktree: Path, *, allow_sync: bool) -> None:
    assert_local_index(worktree)
    status = read_status(cli, worktree)
    verify_status(status, worktree)
    pending = pending_count(status)
    if pending and pending > 0:
        if not allow_sync:
            raise HelperError(
                f"CodeGraph still has {pending} pending change(s) at {worktree} "
                f"after sync; run codegraph sync {worktree}"
            )
        sync_index(cli, worktree)
        assert_local_index(worktree)
        status = read_status(cli, worktree)
        verify_status(status, worktree)
        leftover = pending_count(status)
        if leftover and leftover > 0:
            raise HelperError(
                f"CodeGraph still has {leftover} pending change(s) at {worktree} "
                f"after sync; run codegraph sync {worktree}"
            )


def tracked_codegraph(worktree: Path) -> list[str]:
    result = run(["git", "-C", str(worktree), "ls-files", "-z", "--", ".codegraph"])
    if result.returncode != 0:
        detail = result.stderr or result.stdout or f"exit {result.returncode}"
        raise HelperError(
            f"git ls-files failed in {worktree} while checking .codegraph/: {detail}"
        )
    raw = result.stdout
    if not raw:
        return []
    return [item for item in raw.split("\0") if item]


def ensure_task_ignore(worktree: Path) -> None:
    tracked = tracked_codegraph(worktree)
    if tracked:
        raise HelperError(
            f".codegraph/ is tracked by Git in {worktree} "
            f"({tracked[0]}) and cannot be treated as local tool state. "
            "Do not delete user files automatically; untrack the index first."
        )
    gitignore = worktree / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    lines = existing.splitlines()
    if IGNORE_RULE in lines:
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    gitignore.write_text(existing + prefix + IGNORE_RULE + "\n", encoding="utf-8")


def cmd_prepare(base: Path, task: Path) -> int:
    base_on = enabled(base)
    task_on = enabled(task)
    if not base_on and not task_on:
        print("[SKIP] skipped: CodeGraph is not enabled in the base or task worktree")
        return 0
    cli = require_cli()
    ensure_task_ignore(task)
    assert_local_index(task)
    if task_on:
        verify_and_refresh(cli, task, allow_sync=True)
        print("[OK] CodeGraph task index verified")
        return 0
    init_index(cli, task)
    verify_and_refresh(cli, task, allow_sync=True)
    print("[OK] CodeGraph task index initialized")
    return 0


def cmd_sync(task: Path) -> int:
    if not enabled(task):
        print("[SKIP] skipped: CodeGraph is not enabled in the task worktree")
        return 0
    cli = require_cli()
    assert_local_index(task)
    sync_index(cli, task)
    verify_and_refresh(cli, task, allow_sync=False)
    print("[OK] CodeGraph task index synced")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare or refresh a task-worktree CodeGraph index."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="init or verify the task index")
    prepare.add_argument("--base-worktree", required=True)
    prepare.add_argument("--worktree", required=True)
    sync = sub.add_parser("sync", help="refresh the task index")
    sync.add_argument("--worktree", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            return cmd_prepare(
                resolve_dir("base worktree", args.base_worktree),
                resolve_dir("task worktree", args.worktree),
            )
        if args.command == "sync":
            return cmd_sync(resolve_dir("task worktree", args.worktree))
    except HelperError as exc:
        return fail(str(exc))
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
