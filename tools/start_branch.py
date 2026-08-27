#!/usr/bin/env python3
"""Create an isolated feature worktree exactly from ``origin/master``.

The command makes the safe path the easy path: fetch first, validate the
remote base, then create a new branch in a new worktree.  It never changes the
caller's checkout and never pushes, merges, rebases, or deletes anything.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


class StartError(RuntimeError):
    pass


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False
    )
    if check and result.returncode:
        raise StartError(result.stderr.strip() or "git command failed")
    return result


def start_branch(
    repo: Path,
    slug: str,
    worktree_root: Path,
    *,
    fetch: bool = True,
) -> tuple[Path, str, str]:
    repo = repo.resolve()
    if not SLUG.fullmatch(slug):
        raise StartError("slug must be 2-63 lowercase letters, digits, or hyphens")
    if fetch:
        _git(repo, "fetch", "--prune", "origin")
    base = _git(repo, "rev-parse", "--verify", "origin/master").stdout.strip()
    branch = f"codex/{slug}"
    exists = _git(repo, "show-ref", "--verify", f"refs/heads/{branch}", check=False)
    if exists.returncode == 0:
        raise StartError(f"branch already exists: {branch}")
    target = (worktree_root / slug).resolve()
    if target.exists():
        raise StartError(f"worktree target already exists: {target}")
    worktree_root.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(target), "origin/master")
    actual = _git(target, "rev-parse", "HEAD").stdout.strip()
    if actual != base:
        raise StartError(f"created worktree at {actual}, expected {base}")
    return target, branch, base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--worktree-root",
        type=Path,
        default=Path.home() / "orphograph-worktrees",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="tests/offline recovery only; normal work must fetch",
    )
    args = parser.parse_args(argv)
    try:
        target, branch, base = start_branch(
            args.repo, args.slug, args.worktree_root, fetch=not args.no_fetch
        )
    except StartError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"created {branch} at {base}")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
