#!/usr/bin/env python3
"""Safely remove local worktrees and branches for finished Trellis tasks."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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
    pr_url: str | None
    children: tuple[str, ...]


@dataclass(frozen=True)
class ArchiveCandidate:
    name: str
    age_days: int
    evidence: str
    proof: str


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
    pr_url = data.get("pr_url")
    if not isinstance(pr_url, str) or not pr_url.strip():
        pr_url = None
    children: list[str] = []
    raw_children = data.get("children") or []
    if isinstance(raw_children, list):
        for item in raw_children:
            if isinstance(item, str):
                children.append(item)
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                children.append(item["name"])
    return TaskRecord(
        name=name,
        directory=path.parent,
        archived=archived,
        status=status if isinstance(status, str) else "",
        branch=branch,
        pr_url=pr_url,
        children=tuple(children),
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


def session_auto_commit_enabled(main_checkout: str) -> bool:
    path = Path(main_checkout) / ".trellis" / "config.yaml"
    if not path.is_file():
        return True
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return True
    for raw in lines:
        if not raw or raw[0].isspace() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("session_auto_commit:"):
            value = raw.split(":", 1)[1].strip().lower()
            return value not in {"false", "no", "0", "off"}
    return True


def task_age_days(main_checkout: str, name: str) -> int | None:
    result = run(
        ["git", "log", "-1", "--format=%ct", "--", f".trellis/tasks/{name}"],
        cwd=main_checkout,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    try:
        committed = int(result.stdout)
    except ValueError:
        return None
    return int((time.time() - committed) // 86400)


def child_is_settled(records: Sequence[TaskRecord], name: str) -> bool:
    matches = [record for record in records if record.name == name]
    if not matches:
        return False
    if any(not record.archived and record.status not in {"completed", "archived"} for record in matches):
        return False
    return any(record.archived or record.status in {"completed", "archived"} for record in matches)


def guard_matches_task(markers: Sequence[dict[str, object]], name: str, branch: str | None) -> bool:
    for marker in markers:
        if marker.get("task") == name:
            return True
        if branch and marker.get("branch") == branch:
            return True
    return False


def pr_number(pr_url: str) -> str | None:
    tail = pr_url.rstrip("/").split("/")[-1]
    return tail if tail.isdigit() else None


def commit_exists(root: str, oid: str) -> bool:
    result = run(["git", "cat-file", "-e", f"{oid}^{{commit}}"], cwd=root)
    return result.returncode == 0


def ensure_pr_head(root: str, oid: str, pr_url: str, *, no_fetch: bool) -> bool:
    if commit_exists(root, oid):
        return True
    if no_fetch:
        return False
    number = pr_number(pr_url)
    if number is None:
        return False
    fetched = run(["git", "fetch", "origin", f"refs/pull/{number}/head"], cwd=root)
    if fetched.returncode != 0:
        return False
    return commit_exists(root, oid)


def prove_sha(root: str, sha: str, default_branch: str, *, merge_tree_ok: bool) -> tuple[str | None, str]:
    target = f"origin/{default_branch}"
    if not commit_exists(root, sha):
        return None, "sha_missing"
    if run(["git", "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}"], cwd=root).returncode != 0:
        return None, "target_missing"
    ancestor = run(["git", "merge-base", "--is-ancestor", sha, target], cwd=root)
    if ancestor.returncode == 0:
        return "ancestor", ""
    if ancestor.returncode != 1:
        return None, "git_error"
    if not merge_tree_ok:
        return None, "merge_tree_unavailable"
    merged = run(["git", "merge-tree", "--write-tree", target, sha], cwd=root)
    if merged.returncode == 1:
        return None, "conflict"
    if merged.returncode != 0:
        return None, "git_error"
    merged_tree = _merged_tree_oid(merged.stdout)
    target_tree = run(["git", "rev-parse", f"{target}^{{tree}}"], cwd=root)
    if target_tree.returncode != 0 or not merged_tree or not target_tree.stdout:
        return None, "git_error"
    if merged_tree == target_tree.stdout:
        return "tree_equal", ""
    return None, "tree_differs"


def read_pr_json(target: str, root: str, fields: str) -> tuple[CommandResult, dict[str, object] | None]:
    result = run(["gh", "pr", "view", target, "--json", fields], cwd=root)
    if result.returncode != 0:
        return result, None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return result, None
    return result, payload if isinstance(payload, dict) else None


def classify_commit(root: str, sha: str) -> str | None:
    listed = run(["git", "rev-list", "--parents", "-n1", sha], cwd=root)
    if listed.returncode != 0:
        return "unexpected_commit_type"
    parts = listed.stdout.split()
    if len(parts) != 2:
        return "unexpected_commit_type"
    diff = run(["git", "diff-tree", "-r", "--name-status", "-M", f"{sha}^", sha], cwd=root)
    if diff.returncode != 0:
        return "unexpected_commit_type"
    rows = [line for line in diff.stdout.splitlines() if line.strip()]
    if not rows:
        return "unexpected_commit_type"
    for line in rows:
        columns = line.split("\t")
        if len(columns) < 2:
            return "unexpected_commit_type"
        for path in columns[1:]:
            if not path.startswith(".trellis/tasks/"):
                return "unexpected_commit_paths"
    return None


def pending_file(common: Path) -> Path:
    return common / "trellis-gc" / "pending-push.json"


def read_pending(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    commits = data.get("commits") if isinstance(data, dict) else None
    if not isinstance(commits, list):
        return []
    return [item for item in commits if isinstance(item, str)]


def write_pending(path: Path, default_branch: str, commits: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "default_branch": default_branch,
        "commits": list(commits),
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def clear_pending(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        return


def upstream_tracking(root: str, branch: str) -> tuple[str, str]:
    result = run(
        [
            "git",
            "for-each-ref",
            f"refs/heads/{branch}",
            "--format=%(upstream:short)%09%(upstream:track)",
        ],
        cwd=root,
    )
    if result.returncode != 0 or not result.stdout:
        return "", ""
    parts = result.stdout.split("\t")
    upstream = parts[0] if parts else ""
    tracking = parts[1] if len(parts) > 1 else ""
    return upstream, tracking


def select_archive_candidates(
    *,
    main_checkout: str,
    root: str,
    default_branch: str,
    records: Sequence[TaskRecord],
    markers: Sequence[dict[str, object]],
    worktree_by_branch: dict[str, WorktreeInfo],
    idle_days: int,
    has_gh: bool,
    no_fetch: bool,
    merge_tree_ok: bool,
) -> tuple[list[ArchiveCandidate], list[tuple[str, str]]]:
    chosen: list[ArchiveCandidate] = []
    skipped: list[tuple[str, str]] = []
    active = sorted((record for record in records if not record.archived), key=lambda record: record.name)
    for record in active:
        if record.status == "planning":
            skipped.append((record.name, "planning"))
            continue
        if not record.branch:
            skipped.append((record.name, "branch_missing"))
            continue
        if record.branch == default_branch:
            skipped.append((record.name, "branch_is_default"))
            continue
        evidence = ""
        proof = ""
        if record.status == "completed":
            evidence = "completed"
            if run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{record.branch}"], cwd=root).returncode != 0:
                skipped.append((record.name, "branch_missing"))
                continue
            upstream, tracking = upstream_tracking(root, record.branch)
            found, detail = prove_content(
                root,
                record.branch,
                default_branch,
                upstream=upstream,
                tracking=tracking,
                has_gh=has_gh,
                force_gone=False,
                legacy_only=False,
                merge_tree_ok=merge_tree_ok,
            )
            if found not in {"pr", "ancestor", "tree_equal"}:
                skipped.append((record.name, f"not_landed {detail}".strip()))
                continue
            proof = found
        elif record.status == "in_progress":
            if not record.pr_url:
                skipped.append((record.name, "no_pr"))
                continue
            if not has_gh:
                skipped.append((record.name, "pr_not_merged"))
                continue
            _view, payload = read_pr_json(record.pr_url, root, "state,headRefName,headRefOid")
            state = payload.get("state") if payload else None
            head_name = payload.get("headRefName") if payload else None
            head_oid = payload.get("headRefOid") if payload else None
            if state != "MERGED" or not isinstance(head_oid, str):
                skipped.append((record.name, "pr_not_merged"))
                continue
            if head_name != record.branch:
                skipped.append((record.name, "pr_branch_mismatch"))
                continue
            if not ensure_pr_head(root, head_oid, record.pr_url, no_fetch=no_fetch):
                skipped.append((record.name, "not_landed head_unavailable"))
                continue
            upstream, tracking = upstream_tracking(root, record.branch)
            local = run(["git", "rev-parse", f"refs/heads/{record.branch}"], cwd=root)
            if upstream and tracking == "[gone]" and local.returncode == 0 and local.stdout == head_oid:
                proof = "pr"
            else:
                found, detail = prove_sha(root, head_oid, default_branch, merge_tree_ok=merge_tree_ok)
                if found not in {"ancestor", "tree_equal"}:
                    skipped.append((record.name, f"not_landed {detail}".strip()))
                    continue
                proof = found
            evidence = "pr_merged"
        else:
            skipped.append((record.name, "status_unsupported"))
            continue
        if any(not child_is_settled(records, child) for child in record.children):
            skipped.append((record.name, "children_active"))
            continue
        age = task_age_days(main_checkout, record.name)
        if age is None:
            skipped.append((record.name, "no_history"))
            continue
        if age < idle_days:
            skipped.append((record.name, "recent"))
            continue
        if record.branch in worktree_by_branch:
            skipped.append((record.name, "worktree_active"))
            continue
        status = run(
            ["git", "status", "--porcelain", "--", f".trellis/tasks/{record.name}"],
            cwd=main_checkout,
        )
        if status.returncode != 0 or status.stdout:
            skipped.append((record.name, "dirty_task_dir"))
            continue
        if guard_matches_task(markers, record.name, record.branch):
            skipped.append((record.name, "guard_active"))
            continue
        chosen.append(ArchiveCandidate(record.name, age, evidence, proof))
    return chosen, skipped


def sync_precondition(
    root: str,
    default_branch: str,
    pending: Sequence[str],
    *,
    no_fetch: bool,
) -> tuple[str | None, list[str]]:
    git_dir = run(["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-dir"])
    common = run(["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"])
    if git_dir.returncode != 0 or common.returncode != 0:
        return "not_main_checkout", []
    if os.path.realpath(git_dir.stdout) != os.path.realpath(common.stdout):
        return "not_main_checkout", []
    head = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    if head.returncode != 0 or head.stdout != default_branch:
        return "not_default_branch", []
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root)
    if status.returncode != 0 or status.stdout:
        return "dirty", []
    target = f"origin/{default_branch}"
    if not no_fetch:
        fetched = run(["git", "fetch", "origin", default_branch], cwd=root)
        if fetched.returncode != 0:
            return "fetch_failed", []
    if run(["git", "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}"], cwd=root).returncode != 0:
        return "target_missing", []
    origin_ancestor = run(["git", "merge-base", "--is-ancestor", target, "HEAD"], cwd=root)
    if origin_ancestor.returncode != 0:
        ahead = run(["git", "rev-list", f"{target}..HEAD"], cwd=root).stdout.split()
        behind = run(["git", "rev-list", f"HEAD..{target}"], cwd=root).stdout.split()
        suspicious = [sha for sha in ahead + behind if sha]
        head_ancestor = run(["git", "merge-base", "--is-ancestor", "HEAD", target], cwd=root)
        if head_ancestor.returncode == 0:
            return "behind_remote", suspicious
        return "diverged", suspicious
    ahead_shas = [sha for sha in run(["git", "rev-list", f"{target}..HEAD"], cwd=root).stdout.split() if sha]
    pending_set = set(pending)
    unknown = [sha for sha in ahead_shas if sha not in pending_set]
    if unknown:
        return "unknown_local_commits", unknown
    for sha in ahead_shas:
        reason = classify_commit(root, sha)
        if reason:
            return reason, [sha]
    return None, []


def print_sync_failure(reason: str, shas: Sequence[str]) -> None:
    print(f"[WARN] sync precondition failed: {reason}")
    print("[WARN] recovery: inspect with git log -p <sha>; this script will not reset")
    for sha in shas:
        print(f"[WARN] suspicious {sha}")


def push_default(root: str, default_branch: str) -> CommandResult:
    return run(["git", "push", "origin", default_branch], cwd=root)


def run_archive(main_checkout: str, name: str) -> CommandResult:
    return run(
        ["python3", ".trellis/scripts/task.py", "archive", name, "--skip-branch-validation"],
        cwd=main_checkout,
    )


def apply_idle_archives(
    root: str,
    main_checkout: str,
    default_branch: str,
    plans: Sequence[ArchiveCandidate],
    *,
    no_fetch: bool,
) -> tuple[int, int, list[tuple[str, str]]]:
    """Push any pending archive commits, then archive and push new ones.

    Returns ``(archived_count, exit_code, extra_skips)``. A failed precondition
    or push archives nothing further and leaves the pending file in place.
    """
    common = git_common_dir(root)
    path = pending_file(common) if common is not None else None
    pending = read_pending(path) if path is not None else []
    skips: list[tuple[str, str]] = []
    if not plans and not pending:
        return 0, 0, skips
    reason, shas = sync_precondition(root, default_branch, pending, no_fetch=no_fetch)
    if reason:
        print_sync_failure(reason, shas)
        return 0, 1, skips
    if pending and path is not None:
        pushed = push_default(root, default_branch)
        if pushed.returncode != 0:
            detail = pushed.stderr or pushed.stdout or "push failed"
            print(f"[WARN] pending push: {detail}")
            return 0, 1, skips
        clear_pending(path)
        pending = []
    if plans and not session_auto_commit_enabled(main_checkout):
        for item in plans:
            skips.append((item.name, "auto_commit_disabled"))
        return 0, 0, skips
    archived = 0
    for item in plans:
        before = run(["git", "rev-parse", "HEAD"], cwd=root)
        if before.returncode != 0 or not before.stdout:
            print("[WARN] unexpected_archive_commits")
            return archived, 1, skips
        result = run_archive(main_checkout, item.name)
        if result.returncode != 0:
            detail = result.stderr or result.stdout or "archive failed"
            print(f"[WARN] archive {item.name} failed: {detail}")
            return archived, 1, skips
        listed = run(["git", "rev-list", f"{before.stdout}..HEAD"], cwd=root)
        created = [sha for sha in listed.stdout.split() if sha] if listed.returncode == 0 else []
        if len(created) != 1:
            print("[WARN] unexpected_archive_commits")
            return archived, 1, skips
        illegal = classify_commit(root, created[0])
        if illegal:
            print(f"[WARN] {illegal} {created[0]}")
            return archived, 1, skips
        pending.append(created[0])
        if path is not None:
            write_pending(path, default_branch, pending)
        archived += 1
        print(
            f"[DONE] archived {item.name} "
            f"(evidence={item.evidence}, proof={item.proof})"
        )
    if pending and path is not None:
        pushed = push_default(root, default_branch)
        if pushed.returncode != 0:
            detail = pushed.stderr or pushed.stdout or "push failed"
            print(f"[WARN] pending push: {detail}")
            return archived, 1, skips
        clear_pending(path)
    return archived, 0, skips


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
    parser.add_argument(
        "--archive-idle-days",
        type=int,
        default=7,
        help="archive idle finished tasks older than N days; 0 disables (default: 7)",
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

    checkout = main_worktree or root
    archive_plans: list[ArchiveCandidate] = []
    if args.archive_idle_days > 0:
        archive_plans, archive_skips = select_archive_candidates(
            main_checkout=checkout,
            root=root,
            default_branch=default_branch,
            records=records,
            markers=markers,
            worktree_by_branch=worktree_by_branch,
            idle_days=args.archive_idle_days,
            has_gh=has_gh,
            no_fetch=args.no_fetch,
            merge_tree_ok=merge_tree_ok,
        )
        skipped.extend(archive_skips)

    removed = 0
    for branch in sorted(managed):
        info = worktree_by_branch.get(branch)
        path = info.path if info else None
        print(f"[INFO managed_by=herdr-dispatch run cleanup] {branch} {path if path else '-'}")

    if not planned and not archive_plans:
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

    archived = 0
    archive_exit = 0
    if args.apply:
        prune = run(["git", "worktree", "prune"], cwd=root)
        if prune.returncode != 0:
            print(f"[WARN] git worktree prune failed: {prune.stderr}")
        if main_worktree:
            remove_empty_wt_directories(main_worktree)
        # Idle days <= 0 skips new candidates. A pending push is still retried.
        archived, archive_exit, archive_more = apply_idle_archives(
            root,
            checkout,
            default_branch,
            archive_plans,
            no_fetch=args.no_fetch,
        )
        skipped.extend(archive_more)
    else:
        for item in archive_plans:
            print(
                f"[PLAN] would archive {item.name} "
                f"(idle {item.age_days}d, evidence={item.evidence}, proof={item.proof})"
            )

    for branch, reason in skipped:
        print(f"[SKIP] {branch}: {reason}")
    if (planned or archive_plans) and not args.apply:
        print("[PLAN] dry-run only; re-run with --apply to execute")
    common = git_common_dir(root)
    pending_push = len(read_pending(pending_file(common))) if common is not None else 0
    print(
        f"removed={removed} archived={archived} skipped={len(skipped)} "
        f"managed_by_dispatch={len(managed)} pending_push={pending_push}"
    )
    return archive_exit


if __name__ == "__main__":
    raise SystemExit(main())
