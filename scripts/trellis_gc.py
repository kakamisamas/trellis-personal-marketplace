#!/usr/bin/env python3
"""Safely remove local worktrees and branches for merged Trellis tasks."""

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


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Candidate:
    branch: str
    worktree: str | None
    verified: bool


def run(args: Sequence[str], cwd: str | Path | None = None) -> CommandResult:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return CommandResult(result.returncode, result.stdout.strip(), result.stderr.strip())


def fail(message: str) -> int:
    print(f"[ERROR] {message}", file=sys.stderr)
    return 1


def parse_worktrees(output: str) -> tuple[dict[str, str], str | None]:
    worktree_by_branch: dict[str, str] = {}
    main_worktree: str | None = None
    for block in output.split("\n\n"):
        path: str | None = None
        branch: str | None = None
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = line.removeprefix("worktree ")
            elif line.startswith("branch refs/heads/"):
                branch = line.removeprefix("branch refs/heads/")
        if path and main_worktree is None:
            main_worktree = path
        if path and branch:
            worktree_by_branch[branch] = path
    return worktree_by_branch, main_worktree


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="GC merged task branches: remove leftover worktrees and local branches."
    )
    parser.add_argument("--apply", action="store_true", help="execute removals; default is dry-run")
    parser.add_argument("--prefix", default="task/", help="local branch prefix (default: task/)")
    parser.add_argument("--no-fetch", action="store_true", help="use cached remote state")
    parser.add_argument(
        "--force-gone",
        action="store_true",
        help="allow deletion from upstream [gone] without a matching merged PR head",
    )
    args = parser.parse_args(argv)

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

    current_result = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    current_branch = current_result.stdout if current_result.returncode == 0 else ""

    worktree_result = run(["git", "worktree", "list", "--porcelain"], cwd=root)
    if worktree_result.returncode != 0:
        return fail(f"git worktree list failed: {worktree_result.stderr}")
    worktree_by_branch, main_worktree = parse_worktrees(worktree_result.stdout)
    current_path = os.path.realpath(root)

    refs_result = run(
        [
            "git",
            "for-each-ref",
            f"refs/heads/{args.prefix}",
            "--format=%(refname:short)%09%(upstream:short)%09%(upstream:track)",
        ],
        cwd=root,
    )
    if refs_result.returncode != 0:
        return fail(f"git for-each-ref failed: {refs_result.stderr}")

    has_gh = shutil.which("gh") is not None
    planned: list[Candidate] = []
    skipped: list[tuple[str, str]] = []

    for line in (line for line in refs_result.stdout.splitlines() if line.strip()):
        parts = line.split("\t")
        branch = parts[0]
        upstream = parts[1] if len(parts) > 1 else ""
        tracking = parts[2] if len(parts) > 2 else ""

        if branch == current_branch:
            skipped.append((branch, "current branch"))
            continue
        if not upstream:
            skipped.append((branch, "no upstream (never pushed or still local-only)"))
            continue
        if tracking != "[gone]":
            skipped.append((branch, f"upstream still exists ({upstream})"))
            continue

        verified = False
        reason: str | None = None
        if has_gh:
            pr_result, payload = read_pr(branch, root)
            state = payload.get("state") if payload else None
            pr_head = payload.get("headRefOid") if payload else None
            local_head_result = run(["git", "rev-parse", f"refs/heads/{branch}"], cwd=root)
            local_head = local_head_result.stdout if local_head_result.returncode == 0 else None
            if state == "MERGED" and isinstance(pr_head, str) and local_head == pr_head:
                verified = True
            elif state == "MERGED" and isinstance(pr_head, str) and local_head:
                reason = f"local HEAD {local_head} differs from merged PR head {pr_head}"
            elif payload is not None:
                reason = f"PR state={state or 'unknown'}"
            else:
                reason = f"PR lookup failed ({pr_result.stderr or 'invalid response'})"
        else:
            reason = "gh unavailable to verify merged PR and head SHA"

        if not verified and not args.force_gone:
            skipped.append((branch, f"upstream gone but {reason}; use --force-gone to override"))
            continue

        worktree = worktree_by_branch.get(branch)
        if worktree:
            real_worktree = os.path.realpath(worktree)
            if main_worktree and real_worktree == os.path.realpath(main_worktree):
                skipped.append((branch, "checked out in main worktree"))
                continue
            if real_worktree == current_path:
                skipped.append((branch, "checked out in the worktree running GC"))
                continue
            status = run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=worktree)
            if status.returncode != 0 or status.stdout:
                skipped.append((branch, f"worktree dirty or unreadable: {worktree}"))
                continue

        planned.append(Candidate(branch, worktree, verified))

    if not planned:
        print("[DONE] nothing to clean")

    for candidate in planned:
        proof = "merged PR and head SHA verified" if candidate.verified else "upstream gone (forced)"
        target = (
            f"worktree {candidate.worktree} and local branch {candidate.branch}"
            if candidate.worktree
            else f"local branch {candidate.branch}"
        )
        if not args.apply:
            print(f"[PLAN] would remove {target} ({proof})")
            continue

        if candidate.worktree:
            removed = run(["git", "worktree", "remove", candidate.worktree], cwd=root)
            if removed.returncode != 0:
                print(f"[WARN] failed to remove worktree {candidate.worktree}: {removed.stderr}")
                continue
        deleted = run(["git", "branch", "-D", candidate.branch], cwd=root)
        if deleted.returncode != 0:
            print(f"[WARN] failed to delete branch {candidate.branch}: {deleted.stderr}")
        else:
            print(f"[DONE] removed {target} ({proof})")

    if args.apply:
        prune = run(["git", "worktree", "prune"], cwd=root)
        if prune.returncode != 0:
            print(f"[WARN] git worktree prune failed: {prune.stderr}")

    for branch, reason in skipped:
        print(f"[SKIP] {branch}: {reason}")
    if planned and not args.apply:
        print("[PLAN] dry-run only; re-run with --apply to execute")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
