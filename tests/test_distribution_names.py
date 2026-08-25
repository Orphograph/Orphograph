"""test_distribution_names.py

One published name must mean exactly one library (2026-08-25).

Found while preparing the first `orphograph` release: BOTH `sdk/` and
`sdk-python/` declare `name = "orphograph"` at `version = "0.1.0"`, and their
public surfaces share exactly one symbol (`OrphographError`). They are not two
versions of one library — they are two libraries:

    sdk/         anchor_file, anchor_bytes, anchor_text, get_receipt
    sdk-python/  anchor_folder, verify_folder, inclusion_proof, verify_inclusion + CLI

`pip install orphograph` can only ever resolve to one of them, and PyPI burns a
version number PERMANENTLY — a wrong `python3 -m build` in the wrong directory
followed by `twine upload` spends 0.1.0 on the wrong API, forever. Nothing in
the repo prevented that: publishing is manual, there is no CI publish path, and
there was no guard.

This module does not decide which tree wins — that changes the public API and
is the founder's call. It makes shipping the collision by accident impossible,
and it will fail until the decision is recorded.
"""
from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Trees that intentionally declare a distribution name but are NOT candidates
# for publication under it. Empty by design: adding an entry here IS the
# decision, and it should be made deliberately, in a commit that says so.
NOT_PUBLISHED: frozenset[str] = frozenset()


def _tracked(pattern: str) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", pattern], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout
    return [REPO_ROOT / line for line in out.splitlines() if line.strip()]


def _pypi_names() -> dict[str, list[str]]:
    """{distribution name: [pyproject paths declaring it]}"""
    found: dict[str, list[str]] = defaultdict(list)
    for p in _tracked("*pyproject.toml"):
        rel = p.relative_to(REPO_ROOT).as_posix()
        if rel in NOT_PUBLISHED:
            continue
        m = re.search(r'^name\s*=\s*["\']([^"\']+)["\']', p.read_text(encoding="utf-8"), re.M)
        if m:
            found[m.group(1)].append(rel)
    return found


def _npm_names() -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    for p in _tracked("*package.json"):
        rel = p.relative_to(REPO_ROOT).as_posix()
        if "node_modules" in rel or rel in NOT_PUBLISHED:
            continue
        try:
            name = json.loads(p.read_text(encoding="utf-8")).get("name")
        except json.JSONDecodeError:
            continue
        if name:
            found[name].append(rel)
    return found


def test_no_two_trees_claim_the_same_pypi_name() -> None:
    """THE GUARD. A duplicate here is one `python3 -m build` in the wrong
    directory away from permanently spending a version number on the wrong
    library."""
    dupes = {n: paths for n, paths in _pypi_names().items() if len(paths) > 1}
    assert not dupes, (
        "Two or more trees declare the same PyPI distribution name. "
        "`pip install <name>` can only be one of them, and a published version "
        "number can never be reused. Decide which tree owns the name, then "
        "either change the other's name or add it to NOT_PUBLISHED in this "
        "module (in a commit that records the decision):\n"
        + "\n".join(f"  {n}: {', '.join(p)}" for n, p in sorted(dupes.items()))
    )


def test_no_two_trees_claim_the_same_npm_name() -> None:
    """Same rule for npm, which burns versions the same way."""
    dupes = {n: paths for n, paths in _npm_names().items() if len(paths) > 1}
    assert not dupes, "\n".join(f"  {n}: {', '.join(p)}" for n, p in sorted(dupes.items()))


def test_the_scanner_actually_finds_names() -> None:
    """NEGATIVE CONTROL. If the git-ls-files patterns or the regex broke, both
    guards above would pass on an empty dict — reporting CLEAN for a repo it
    never read. The repo does declare names, so zero means the scan died."""
    pypi, npm = _pypi_names(), _npm_names()
    assert pypi, "no pyproject.toml names found at all — the scanner is broken"
    assert npm, "no package.json names found at all — the scanner is broken"
    assert "orphograph-mcp" in pypi, f"known-good name missing; scanner drifted: {sorted(pypi)}"
