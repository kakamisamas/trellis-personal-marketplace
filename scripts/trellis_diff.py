#!/usr/bin/env python3
"""Count net changed lines from merge-base to HEAD or the working tree.

CLI:

    python3 scripts/trellis_diff.py --base <base-ref>
    python3 scripts/trellis_diff.py --base <base-sha> --head <head-sha> --check

Local default counts merge-base(base, HEAD) through the current worktree,
including unignored untracked files, without changing the Git index.
``--head`` counts only committed trees. 2500 is the planning budget; 3500 is
the CI hard limit. ``--check`` fails when the hard limit is exceeded.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping, Sequence


PLANNING_BUDGET = 2500
HARD_LIMIT = 3500
PATHSPECS = (
    ".",
    ":(exclude)*.lock",
    ":(exclude)*-lock.*",
    ":(exclude)dist/**",
    ":(exclude)*.min.*",
)
MISSING_SHA = (
    "PR base/head commit is unavailable; checkout must use fetch-depth: 0"
)


class DiffError(Exception):
    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def git(
    repo: Path,
    *args: str,
    binary: bool = False,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]:
    merged = None
    if env is not None:
        merged = os.environ.copy()
        merged.update(env)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=not binary,
        env=merged,
    )


def fail(message: str, *, code: int = 1) -> int:
    print(f"[ERROR] {message}", file=sys.stderr)
    if "fetch-depth: 0" in message:
        print(f"::error::{message}")
    return code


def repo_from_cwd() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DiffError(result.stderr.strip() or "run trellis_diff.py inside a git repository")
    return Path(result.stdout.strip()).resolve()


def require_commit(repo: Path, ref: str) -> None:
    result = git(repo, "cat-file", "-e", f"{ref}^{{commit}}")
    if result.returncode != 0:
        raise DiffError(MISSING_SHA)


def merge_base(repo: Path, base: str, head: str) -> str:
    result = git(repo, "merge-base", base, head)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise DiffError(detail or f"git merge-base {base} {head} failed")
    sha = result.stdout.strip()
    if not sha:
        raise DiffError(f"git merge-base {base} {head} produced no SHA")
    return sha


def parse_numstat_z(blob: bytes) -> int:
    total = 0
    rest = blob
    while rest and rest != b"\0":
        first = rest.find(b"\t")
        second = rest.find(b"\t", first + 1) if first >= 0 else -1
        if first < 0 or second < 0:
            text = rest.decode("utf-8", "replace").strip()
            if not text:
                break
            raise DiffError(f"unreadable git numstat -z output: {text[:200]}")
        added = rest[:first]
        deleted = rest[first + 1 : second]
        rest = rest[second + 1 :]
        if rest.startswith(b"\0"):
            rest = rest[1:]
            if b"\0" not in rest:
                raise DiffError("truncated rename in git numstat -z output")
            _, rest = rest.split(b"\0", 1)
            if b"\0" not in rest:
                raise DiffError("truncated rename target in git numstat -z output")
            _, rest = rest.split(b"\0", 1)
        else:
            if b"\0" not in rest:
                raise DiffError("truncated path in git numstat -z output")
            _, rest = rest.split(b"\0", 1)
        if added == b"-" or deleted == b"-":
            continue
        try:
            total += int(added) + int(deleted)
        except ValueError as exc:
            raise DiffError(f"non-numeric numstat counts: {added!r} {deleted!r}") from exc
    return total


def numstat(repo: Path, *args: str) -> int:
    result = git(
        repo,
        "diff",
        "--numstat",
        "-z",
        "--find-renames",
        *args,
        "--",
        *PATHSPECS,
        binary=True,
    )
    assert isinstance(result.stdout, bytes)
    if result.returncode not in (0, 1):
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise DiffError(detail or "git diff --numstat failed")
    return parse_numstat_z(result.stdout)


def real_index_path(repo: Path) -> Path:
    result = git(repo, "rev-parse", "--git-path", "index")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise DiffError(detail or "git rev-parse --git-path index failed")
    raw = result.stdout.strip()
    path = Path(raw)
    return path if path.is_absolute() else (repo / path)


def _decode(detail: str | bytes) -> str:
    if isinstance(detail, bytes):
        return detail.decode("utf-8", "replace").strip()
    return detail.strip()


def workspace_numstat(repo: Path, merge_base_sha: str) -> int:
    """Diff merge-base against a temp index of the final worktree snapshot."""
    real_index = real_index_path(repo)
    fd, tmp = tempfile.mkstemp(prefix="trellis-diff-", suffix=".index")
    os.close(fd)
    leftover = [tmp, f"{tmp}.lock"]
    try:
        if real_index.is_file():
            shutil.copyfile(real_index, tmp)
        else:
            os.remove(tmp)
            seeded = git(repo, "read-tree", "HEAD", env={"GIT_INDEX_FILE": tmp})
            if seeded.returncode != 0:
                raise DiffError(
                    _decode(seeded.stderr or seeded.stdout)
                    or "failed to seed temporary index from HEAD"
                )
        added = git(repo, "add", "-A", "--", ".", env={"GIT_INDEX_FILE": tmp})
        if added.returncode != 0:
            raise DiffError(
                _decode(added.stderr or added.stdout)
                or "failed to snapshot the worktree into a temporary index"
            )
        result = git(
            repo,
            "diff",
            "--cached",
            "--numstat",
            "-z",
            "--find-renames",
            merge_base_sha,
            "--",
            *PATHSPECS,
            binary=True,
            env={"GIT_INDEX_FILE": tmp},
        )
        assert isinstance(result.stdout, bytes)
        if result.returncode not in (0, 1):
            raise DiffError(
                _decode(result.stderr)
                or "git diff --cached --numstat failed against the snapshot"
            )
        return parse_numstat_z(result.stdout)
    finally:
        for path in leftover:
            try:
                os.unlink(path)
            except FileNotFoundError:
                continue


def report(lines: int) -> None:
    print(f"changed lines: {lines}")
    print(f"planning budget: {PLANNING_BUDGET}")
    print(f"hard limit: {HARD_LIMIT}")


def count_lines(repo: Path, base: str, head: str | None) -> int:
    require_commit(repo, base)
    if head is None:
        require_commit(repo, "HEAD")
        mid = merge_base(repo, base, "HEAD")
        return workspace_numstat(repo, mid)
    require_commit(repo, head)
    mid = merge_base(repo, base, head)
    return numstat(repo, mid, head)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Count net changed lines for Trellis tasks.")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        repo = repo_from_cwd()
        lines = count_lines(repo, args.base, args.head)
    except DiffError as exc:
        return fail(str(exc), code=exc.code)
    report(lines)
    if args.check and lines > HARD_LIMIT:
        message = (
            f"PR changes {lines} lines, above the {HARD_LIMIT}-line limit; "
            "split the task and resubmit"
        )
        print(f"::error::{message}")
        print(message)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
