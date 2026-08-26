#!/usr/bin/env python3
"""Fail when the current commit does not contain the required remote base.

This is deliberately small enough to run from a pre-push hook and CI.  It
does not fetch, merge, rebase, push, or modify the worktree.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False
    )


def is_fresh(repo: Path, base: str) -> tuple[bool, str]:
    base_sha = _git(repo, "rev-parse", "--verify", base)
    if base_sha.returncode:
        return False, f"cannot resolve {base}: {base_sha.stderr.strip()}"
    head_sha = _git(repo, "rev-parse", "--verify", "HEAD")
    if head_sha.returncode:
        return False, f"cannot resolve HEAD: {head_sha.stderr.strip()}"
    check = _git(repo, "merge-base", "--is-ancestor", base, "HEAD")
    if check.returncode == 0:
        return True, f"fresh: {base_sha.stdout.strip()} is an ancestor of HEAD"
    if check.returncode == 1:
        counts = _git(repo, "rev-list", "--left-right", "--count", f"HEAD...{base}")
        detail = counts.stdout.strip() or "unknown divergence"
        return False, f"stale: HEAD...{base} (ahead/behind) = {detail}"
    return False, f"merge-base check failed: {check.stderr.strip()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", default="origin/master")
    args = parser.parse_args(argv)
    ok, message = is_fresh(args.repo.resolve(), args.base)
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
