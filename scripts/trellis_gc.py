#!/usr/bin/env python3
"""Safely remove local worktrees and branches for finished Trellis tasks."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DISPATCH_PREFIXES = ("card/", "run/")
MERGE_TREE_VERSION = (2, 38)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Candidate:
    branch: str
    worktree: str | None
    proof: str
    force_remove: bool = False


@dataclass(frozen=True)
class WorktreeInfo:
    path: str
    locked: bool


@dataclass(frozen=True)
class TaskRecord:
    name: str
    directory: Path
    archived: bool
    status: str
    branch: str | None


def run(args: Sequence[str], cwd: str | Path | None = None) -> CommandResult:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return CommandResult(result.returncode, result.stdout.strip(), result.stderr.strip())


def fail(message: str) -> int:
    print(f"[ERROR] {message}", file=sys.stderr)
    return 1


def preview_status(output: str, *, max_lines: int = 12) -> str:
    lines = output.splitlines()
    if not lines:
        return output
    preview = lines[:max_lines]
    text = "\n".join(preview)
    extra = len(lines) - len(preview)
    if extra > 0:
        text += f"\n... ({extra} more lines)"
    return text


def dirty_reason(status: CommandResult) -> str:
    detail = status.stdout or status.stderr
    if not detail:
        return "dirty"
    return "dirty\n" + preview_status(detail)


def _inside_wt_root(path: str) -> bool:
    base = os.path.basename(path)
    parent = os.path.basename(os.path.dirname(path))
    return base.endswith("-wt") or parent.endswith("-wt")


def remove_empty_worktree_parent(worktree: str) -> None:
    """Remove an empty ``<repo>-wt/<run_id>/`` and, if that empties it, ``<repo>-wt``."""
    current = os.path.dirname(os.path.abspath(worktree.rstrip(os.sep)))
    for _ in range(2):
        if not _inside_wt_root(current):
            return
        try:
            os.rmdir(current)
        except OSError:
            return
        if os.path.basename(current).endswith("-wt"):
            return
        current = os.path.dirname(current)


def remove_empty_wt_directories(main_checkout: str) -> None:
    main = Path(main_checkout)
    wt_root = main.parent / f"{main.name}-wt"
    if not wt_root.is_dir():
        return
    for child in list(wt_root.iterdir()):
        if not child.is_dir():
            continue
        try:
            next(child.iterdir())
        except StopIteration:
            try:
                child.rmdir()
            except OSError:
                pass
    try:
        next(wt_root.iterdir())
    except StopIteration:
        try:
            wt_root.rmdir()
        except OSError:
            pass


def parse_worktrees(output: str) -> tuple[dict[str, WorktreeInfo], str | None]:
    worktree_by_branch: dict[str, WorktreeInfo] = {}
    main_worktree: str | None = None
    for block in output.split("\n\n"):
        path: str | None = None
        branch: str | None = None
        locked = False
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = line.removeprefix("worktree ")
            elif line.startswith("branch refs/heads/"):
                branch = line.removeprefix("branch refs/heads/")
            elif line == "locked" or line.startswith("locked "):
                locked = True
        if path and main_worktree is None:
            main_worktree = path
        if path and branch:
            worktree_by_branch[branch] = WorktreeInfo(path, locked)
    return worktree_by_branch, main_worktree


def list_local_branches(root: str, prefix: str) -> tuple[list[tuple[str, str, str]] | None, str]:
    refs_result = run(
        [
            "git",
            "for-each-ref",
            f"refs/heads/{prefix}",
            "--format=%(refname:short)%09%(upstream:short)%09%(upstream:track)",
        ],
        cwd=root,
    )
    if refs_result.returncode != 0:
        return None, refs_result.stderr
    rows: list[tuple[str, str, str]] = []
    for line in (line for line in refs_result.stdout.splitlines() if line.strip()):
        parts = line.split("\t")
        branch = parts[0]
        upstream = parts[1] if len(parts) > 1 else ""
        tracking = parts[2] if len(parts) > 2 else ""
        rows.append((branch, upstream, tracking))
    return rows, ""


def is_dispatch_managed(branch: str) -> bool:
    return branch.startswith(DISPATCH_PREFIXES)


def read_pr(branch: str, root: str) -> tuple[CommandResult, dict[str, object] | None]:
    result = run(
        ["gh", "pr", "view", branch, "--json", "state,headRefOid"],
        cwd=root,
    )
    if result.returncode != 0:
        return result, None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return result, None
    return result, payload if isinstance(payload, dict) else None


def version_at_least(text: str, required: tuple[int, int]) -> bool:
    numbers: list[int] = []
    for part in text.removeprefix("git version ").strip().split("."):
        digits = ""
        for char in part:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        numbers.append(int(digits))
    if len(numbers) < 2:
        return False
    return (numbers[0], numbers[1]) >= required


def merge_tree_supported() -> bool:
    result = run(["git", "--version"])
    if result.returncode != 0:
        return False
    return version_at_least(result.stdout, MERGE_TREE_VERSION)


def detect_default_branch(root: str) -> str:
    result = run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=root)
    if result.returncode != 0:
        return "main"
    name = result.stdout.strip()
    if name.startswith("origin/"):
        return name.removeprefix("origin/")
    return "main"


def git_common_dir(root: str) -> Path | None:
    result = run(
        ["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"]
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return Path(result.stdout)


def _read_task(path: Path, *, archived: bool) -> TaskRecord | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    branch = data.get("branch")
    if not isinstance(branch, str):
        branch = None
    status = data.get("status")
    name = data.get("name")
    if not isinstance(name, str) or not name:
        name = path.parent.name
    return TaskRecord(
        name=name,
        directory=path.parent,
        archived=archived,
        status=status if isinstance(status, str) else "",
        branch=branch,
    )


def load_task_records(main_checkout: str) -> list[TaskRecord]:
    tasks_root = Path(main_checkout) / ".trellis" / "tasks"
    if not tasks_root.is_dir():
        return []
    records: list[TaskRecord] = []
    for path in sorted(tasks_root.glob("*/task.json")):
        if path.parent.name == "archive":
            continue
        record = _read_task(path, archived=False)
        if record is not None:
            records.append(record)
    for path in sorted(tasks_root.glob("archive/*/*/task.json")):
        record = _read_task(path, archived=True)
        if record is not None:
            records.append(record)
    return records


def branch_lifecycle(records: Sequence[TaskRecord], branch: str) -> str:
    """Return ``active``, ``archived``, or ``missing`` for ``branch``."""
    if any(record.branch == branch and not record.archived for record in records):
        return "active"
    if any(record.branch == branch and record.archived for record in records):
        return "archived"
    return "missing"


def load_guard_markers(common: Path | None) -> list[dict[str, object]]:
    if common is None:
        return []
    directory = common / "trellis-guard"
    if not directory.is_dir():
        return []
    markers: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            markers.append(data)
    return markers


def guard_matches_branch(markers: Sequence[dict[str, object]], branch: str) -> bool:
    return any(marker.get("branch") == branch for marker in markers)


def index_lock_state(worktree: str) -> str:
    result = run(
        [
            "git",
            "-C",
            worktree,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "index.lock",
        ]
    )
    if result.returncode != 0 or not result.stdout:
        return "lock_unknown"
    if Path(result.stdout).exists():
        return "git_locked"
    return "clear"


def location_skip(
    branch: str,
    worktree: WorktreeInfo | None,
    *,
    main_worktree: str | None,
    current_path: str,
    current_branch: str,
) -> str | None:
    if worktree is not None:
        real_worktree = os.path.realpath(worktree.path)
        if main_worktree and real_worktree == os.path.realpath(main_worktree):
            return "main_worktree"
        if real_worktree == current_path or branch == current_branch:
            return "current_worktree"
        if worktree.locked:
            return "locked"
        state = index_lock_state(worktree.path)
        if state != "clear":
            return state
        return None
    if branch == current_branch:
        return "current_worktree"
    return None


def _merged_tree_oid(stdout: str) -> str | None:
    for line in stdout.splitlines():
        token = line.strip()
        if len(token) == 40 and all(char in "0123456789abcdef" for char in token):
            return token
    return None


def prove_content(
    root: str,
    branch: str,
    default_branch: str,
    *,
    upstream: str,
    tracking: str,
    has_gh: bool,
    force_gone: bool,
    legacy_only: bool,
    merge_tree_ok: bool,
) -> tuple[str | None, str]:
    """Return ``(proof, reason)``. ``proof`` is set when the branch may be removed."""
    upstream_gone = bool(upstream) and tracking == "[gone]"
    local = run(["git", "rev-parse", f"refs/heads/{branch}"], cwd=root)
    local_head = local.stdout if local.returncode == 0 else ""
    if upstream_gone and has_gh:
        _pr_result, payload = read_pr(branch, root)
        state = payload.get("state") if payload else None
        pr_head = payload.get("headRefOid") if payload else None
        if state == "MERGED" and isinstance(pr_head, str) and local_head == pr_head:
            return "pr", ""
    if legacy_only:
        return None, "task_unknown"

    target = f"origin/{default_branch}"
    target_commit = run(["git", "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}"], cwd=root)
    if target_commit.returncode != 0:
        return None, "target_missing"
    ancestor = run(["git", "merge-base", "--is-ancestor", branch, target], cwd=root)
    if ancestor.returncode == 0:
        return "ancestor", ""
    if ancestor.returncode != 1:
        return None, "git_error"
    if not merge_tree_ok:
        tree_reason = "merge_tree_unavailable"
    else:
        merged = run(["git", "merge-tree", "--write-tree", target, branch], cwd=root)
        if merged.returncode == 1:
            tree_reason = "conflict"
        elif merged.returncode != 0:
            tree_reason = "git_error"
        else:
            merged_tree = _merged_tree_oid(merged.stdout)
            target_tree = run(["git", "rev-parse", f"{target}^{{tree}}"], cwd=root)
            if target_tree.returncode != 0 or not merged_tree or not target_tree.stdout:
                tree_reason = "git_error"
            elif merged_tree == target_tree.stdout:
                return "tree_equal", ""
            else:
                tree_reason = "tree_differs"
    if force_gone and upstream_gone:
        return "forced", ""
    return None, tree_reason


def removal_target(branch: str, worktree: str | None) -> str:
    if worktree:
        return f"worktree {worktree} and local branch {branch}"
    return f"local branch {branch}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="GC finished task branches: remove leftover worktrees and local branches."
    )
    parser.add_argument("--apply", action="store_true", help="execute removals; default is dry-run")
    parser.add_argument(
        "--prefix",
        action="append",
        default=None,
        help="local branch prefix to clean; repeatable (default: task/)",
    )
    parser.add_argument("--no-fetch", action="store_true", help="use cached remote state")
    parser.add_argument(
        "--force-gone",
        action="store_true",
        help="allow deletion from upstream [gone] without a landed-content proof",
    )
    parser.add_argument(
        "--force-dirty",
        action="store_true",
        help="remove a dirty worktree when a landed-content proof matches",
    )
    args = parser.parse_args(argv)
    prefixes = args.prefix if args.prefix else ["task/"]

    root_result = run(["git", "rev-parse", "--show-toplevel"])
    if root_result.returncode != 0:
        return fail(f"not inside a git repository: {root_result.stderr}")
    root = root_result.stdout

    if not args.no_fetch:
        fetch = run(["git", "fetch", "--prune", "--quiet"], cwd=root)
        if fetch.returncode != 0:
            detail = fetch.stderr or "unknown error"
            if args.apply:
                return fail(
                    "git fetch --prune failed; refusing --apply with stale remote state "
                    f"({detail}). Retry the fetch or explicitly pass --no-fetch."
                )
            print(f"[WARN] git fetch --prune failed ({detail}); dry-run uses cached remote state")

    default_branch = detect_default_branch(root)
    print(f"[INFO] default branch: origin/{default_branch}")

    current_result = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    current_branch = current_result.stdout if current_result.returncode == 0 else ""

    worktree_result = run(["git", "worktree", "list", "--porcelain"], cwd=root)
    if worktree_result.returncode != 0:
        return fail(f"git worktree list failed: {worktree_result.stderr}")
    worktree_by_branch, main_worktree = parse_worktrees(worktree_result.stdout)
    current_path = os.path.realpath(root)
    records = load_task_records(main_worktree or root)
    markers = load_guard_markers(git_common_dir(root))
    merge_tree_ok = merge_tree_supported()

    scan_prefixes: list[str] = []
    for prefix in list(prefixes) + list(DISPATCH_PREFIXES):
        if prefix not in scan_prefixes:
            scan_prefixes.append(prefix)
    seen: set[str] = set()
    managed: list[str] = []
    branch_rows: list[tuple[str, str, str]] = []
    for prefix in scan_prefixes:
        rows, error = list_local_branches(root, prefix)
        if rows is None:
            return fail(f"git for-each-ref failed: {error}")
        for branch, upstream, tracking in rows:
            if branch in seen:
                continue
            seen.add(branch)
            if is_dispatch_managed(branch):
                managed.append(branch)
                continue
            if any(branch.startswith(item) for item in prefixes):
                branch_rows.append((branch, upstream, tracking))

    has_gh = shutil.which("gh") is not None
    planned: list[Candidate] = []
    skipped: list[tuple[str, str]] = []

    for branch, upstream, tracking in branch_rows:
        if branch == default_branch:
            skipped.append((branch, "branch_is_default"))
            continue
        worktree = worktree_by_branch.get(branch)
        located = location_skip(
            branch,
            worktree,
            main_worktree=main_worktree,
            current_path=current_path,
            current_branch=current_branch,
        )
        if located:
            skipped.append((branch, located))
            continue
        lifecycle = branch_lifecycle(records, branch)
        if lifecycle == "active":
            skipped.append((branch, "task_active"))
            continue
        if guard_matches_branch(markers, branch):
            skipped.append((branch, "guard_active"))
            continue
        proof, reason = prove_content(
            root,
            branch,
            default_branch,
            upstream=upstream,
            tracking=tracking,
            has_gh=has_gh,
            force_gone=args.force_gone,
            legacy_only=lifecycle != "archived",
            merge_tree_ok=merge_tree_ok,
        )
        if proof is None:
            skipped.append((branch, reason))
            continue
        worktree_path = worktree.path if worktree else None
        if worktree_path:
            status = run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=worktree_path)
            dirty = status.returncode != 0 or bool(status.stdout)
            if dirty:
                # --force-dirty overrides a real landed proof, not --force-gone alone.
                if args.force_dirty and proof != "forced":
                    planned.append(Candidate(branch, worktree_path, proof, force_remove=True))
                    continue
                skipped.append((branch, dirty_reason(status)))
                continue
        planned.append(Candidate(branch, worktree_path, proof))

    removed = 0
    for branch in sorted(managed):
        info = worktree_by_branch.get(branch)
        path = info.path if info else None
        print(f"[INFO managed_by=herdr-dispatch run cleanup] {branch} {path if path else '-'}")

    if not planned:
        print("[DONE] nothing to clean")

    for candidate in planned:
        target = removal_target(candidate.branch, candidate.worktree)
        if not args.apply:
            print(f"[PLAN] would remove {target} (proof={candidate.proof})")
            continue
        if candidate.worktree:
            rechecked = index_lock_state(candidate.worktree)
            if rechecked != "clear":
                skipped.append((candidate.branch, rechecked))
                continue
            remove_args = ["git", "worktree", "remove"]
            if candidate.force_remove:
                remove_args.append("--force")
            remove_args.append(candidate.worktree)
            removed_wt = run(remove_args, cwd=root)
            if removed_wt.returncode != 0:
                print(f"[WARN] failed to remove worktree {candidate.worktree}: {removed_wt.stderr}")
                continue
            remove_empty_worktree_parent(candidate.worktree)
        deleted = run(["git", "branch", "-D", candidate.branch], cwd=root)
        if deleted.returncode != 0:
            print(f"[WARN] failed to delete branch {candidate.branch}: {deleted.stderr}")
        else:
            print(f"[DONE] removed {target} (proof={candidate.proof})")
            removed += 1

    if args.apply:
        prune = run(["git", "worktree", "prune"], cwd=root)
        if prune.returncode != 0:
            print(f"[WARN] git worktree prune failed: {prune.stderr}")
        if main_worktree:
            remove_empty_wt_directories(main_worktree)

    for branch, reason in skipped:
        print(f"[SKIP] {branch}: {reason}")
    if planned and not args.apply:
        print("[PLAN] dry-run only; re-run with --apply to execute")
    print(
        f"removed={removed} archived=0 skipped={len(skipped)} "
        f"managed_by_dispatch={len(managed)} pending_push=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
