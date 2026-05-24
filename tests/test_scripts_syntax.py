"""test_scripts_syntax.py — paranoid syntax guard.

Every *.py under scripts/ must parse cleanly with ast.parse(). Catches
typos and accidental merge-marker leakage long before deploy.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _python_scripts() -> list[Path]:
    out: list[Path] = []
    for p in SCRIPTS.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        out.append(p)
    return sorted(out)


SCRIPT_FILES = _python_scripts()


@pytest.mark.parametrize("path", SCRIPT_FILES,
                         ids=[p.name for p in SCRIPT_FILES])
def test_script_parses(path: Path):
    src = path.read_text(encoding="utf-8")
    try:
        ast.parse(src, filename=str(path))
    except SyntaxError as e:
        raise AssertionError(
            f"SyntaxError in {path.relative_to(ROOT)}:{e.lineno}: {e.msg}"
        )
