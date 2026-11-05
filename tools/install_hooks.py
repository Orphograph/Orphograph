#!/usr/bin/env python3
"""Install the tracked pre-push secret guard into this clone's hooks directory.

The guard lives in ``scripts/git-hooks/pre-push`` so every clone can get the
same copy. Until 2026-11-05 it existed only as an untracked file in one
checkout: a fresh clone pushed with no guard at all, and nothing said so.

``tools/start_branch.py`` calls :func:`hook_problem` and refuses to start a
branch while the guard is missing or differs from the tracked copy.

Exit codes: 0 installed or already current, 1 refused (a different hook is in
place; rerun with ``--force`` after reading it), 2 not a git checkout.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

TRACKED = Path("scripts/git-hooks/pre-push")


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True)
    return out.stdout.strip()


def hooks_dir(repo: Path) -> Path:
    """Where git looks for hooks: core.hooksPath if set, else the common dir.

    A linked worktree shares the main checkout's hooks, so the common dir is
    the right place, not the worktree's private git dir.
    """
    custom = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=repo,
                            text=True, capture_output=True).stdout.strip()
    if custom:
        path = Path(custom).expanduser()
        return path if path.is_absolute() else (Path(_git(repo, "rev-parse", "--show-toplevel")) / path)
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (repo / common).resolve()
    return common / "hooks"


def hook_problem(repo: Path, expected: bytes) -> str | None:
    """Why the installed pre-push guard is not ``expected``, or None when it is."""
    installed = hooks_dir(repo) / "pre-push"
    if not installed.exists():
        return f"pre-push secret guard is not installed at {installed}"
    if installed.read_bytes() != expected:
        return f"pre-push hook at {installed} differs from {TRACKED}"
    if not os.access(installed, os.X_OK):
        return f"pre-push hook at {installed} is not executable"
    return None


def install(repo: Path, *, force: bool = False) -> tuple[bool, str]:
    root = Path(_git(repo, "rev-parse", "--show-toplevel"))
    expected = (root / TRACKED).read_bytes()
    problem = hook_problem(repo, expected)
    if problem is None:
        return True, "pre-push secret guard already current"
    target = hooks_dir(repo) / "pre-push"
    if target.exists() and target.read_bytes() != expected and not force:
        return False, (f"refused: {target} holds a different hook. Read it, then rerun "
                       "with --force to replace it with the tracked guard.")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name("pre-push.tmp")
    tmp.write_bytes(expected)
    tmp.chmod(0o755)
    tmp.replace(target)
    return True, f"installed pre-push secret guard at {target}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--force", action="store_true",
                        help="replace a different existing pre-push hook")
    args = parser.parse_args(argv)
    try:
        ok, message = install(args.repo, force=args.force)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"not a git checkout with {TRACKED}: {exc}", file=sys.stderr)
        return 2
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
