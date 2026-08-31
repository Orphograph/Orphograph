"""The gate command exists in exactly three places — test.yml, deploy.yml,
and scripts/run_gate_tests.sh (the local entry point) — and must be
byte-identical in all three. A hand-derived local variant (wrong paths,
an invented flag, exit code laundered by a pipe) reported green while CI
failed on three shipped defects (2026-08-31); this pin makes the drift a
test failure instead of a false memory."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SOURCES = {
    "test.yml": ROOT / ".github" / "workflows" / "test.yml",
    "deploy.yml": ROOT / ".github" / "workflows" / "deploy.yml",
    "run_gate_tests.sh": ROOT / "scripts" / "run_gate_tests.sh",
}

_LINE = re.compile(r"python3 -m pytest [^\n]+")


def _pytest_lines(path: Path) -> list[str]:
    return _LINE.findall(path.read_text(encoding="utf-8"))


def test_gate_and_sdk_lines_identical_in_all_three_sources():
    lines = {name: _pytest_lines(p) for name, p in SOURCES.items()}
    for name, found in lines.items():
        # Negative control: an extractor that matches nothing would make the
        # identity assertions below pass vacuously.
        assert len(found) == 2, f"{name}: expected 2 pytest lines, found {found}"
    ref_gate, ref_sdk = lines["run_gate_tests.sh"]
    for name in ("test.yml", "deploy.yml"):
        gate, sdk = lines[name]
        assert gate == ref_gate, f"{name} gate line drifted from the script"
        assert sdk == ref_sdk, f"{name} sdk line drifted from the script"


def test_the_local_entry_point_is_executable():
    sh = SOURCES["run_gate_tests.sh"]
    assert sh.stat().st_mode & 0o111, "scripts/run_gate_tests.sh is not executable"
