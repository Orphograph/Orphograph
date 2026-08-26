"""test_no_phantom_env_knobs.py

A test may not set an env var the product never reads (2026-08-26).

Found while benchmarking the anchoring hot path. tests/_srv.py set
`ORPHO_OFFLINE_CALENDARS=1`, and every fixture built on it therefore believed
its anchors were offline. NOTHING in the shipped tree reads that name:
`engine.CALENDARS` is a hardcoded list of five real OpenTimestamps URLs with no
env override. So every anchoring test was SUBMITTING OVER THE NETWORK to
third-party public calendars, at roughly three seconds per anchor.

Two costs, both paid silently:
  * a throughput measurement taken through that fixture measured the internet,
    not the aggregator, and had to be discarded;
  * the suite carried a network dependency nobody had signed up for, which is
    a strong candidate for the fixture timeouts chased earlier the same day.

A phantom knob is worse than a missing one. A missing setting fails loudly the
first time you need it. A phantom setting reads as configured forever.

LEGACY is frozen and may only SHRINK. Removing a name from a test is the fix;
adding one here requires a human to type it, which is the point.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS = REPO_ROOT / "tests"

# Everything the repo actually ships. A key read by ANY of these is real.
PRODUCT_GLOBS = (
    "server/*.py", "capture/*.py", "mcp/*.py", "web/mcp/*.py", "tools/*.py",
    "sdk-python/orphograph/*.py", "sdk/orphograph/*.py", "zk-provenance/*.py",
    "scripts/*.py", "dist/orphograph-verify/*.py", "web/*.js", "sdk-node/src/*.ts",
)

# Names set by tests that the product does not read, frozen 2026-08-26.
# ORPHO_OFFLINE_CALENDARS is deliberately NOT here: it was removed, and it must
# never come back, because its whole effect was to make a network dependency
# look configured away.
LEGACY_PHANTOM = frozenset({
    "ORPHO_FILE", "ORPHO_LABEL", "ORPHO_SERVER_DIR",
    "ORPHO_TEST_EVENT_HEX", "ORPHO_TEXT",
})

_SET_PATTERNS = (
    re.compile(r'["\'](ORPHO_[A-Z0-9_]+|RATE_LIMIT_[A-Z_]+|MIN_[A-Z_]+)["\']\s*:'),
    re.compile(r'\b(ORPHO_[A-Z0-9_]+|RATE_LIMIT_[A-Z_]+)\s*='),
)


def _code_only(src: str) -> str:
    """Source with docstrings and comments stripped.

    Scanning raw text made this guard flag its OWN explanatory comment in
    _srv.py. That is the third scanner in one day to match prose instead of
    code (the _srv DEVNULL docstring and the todo scanner's bare OPEN were the
    others), so the rule is now structural: a scanner that judges code reads
    code.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    return ast.unparse(tree)          # comments never survive unparse


def _keys_set_by_tests() -> set[str]:
    keys: set[str] = set()
    for p in sorted(TESTS.rglob("*.py")):
        if p.name == Path(__file__).name:
            continue
        for pat in _SET_PATTERNS:
            keys.update(pat.findall(_code_only(
                p.read_text(encoding="utf-8", errors="replace"))))
    return keys


def _product_source() -> str:
    parts = []
    for glob in PRODUCT_GLOBS:
        for p in REPO_ROOT.glob(glob):
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def test_no_new_phantom_env_knob() -> None:
    """THE GUARD. A knob the product never reads is configuration theatre."""
    product = _product_source()
    phantom = sorted(k for k in _keys_set_by_tests()
                     if k not in product and k not in LEGACY_PHANTOM)
    assert not phantom, (
        "These env vars are set by tests but read NOWHERE in the shipped tree, "
        "so they configure nothing and make the tests look controlled when they "
        "are not:\n  " + "\n  ".join(phantom)
    )


def test_the_offline_calendar_knob_stays_dead() -> None:
    """It read as 'anchors are offline' while every anchor went to five real
    public calendars over the network. If a fixture needs that behaviour, the
    override belongs in the product behind DOCTRINE's five-calendar invariant,
    which is a founder decision, not a test-side env var."""
    offenders = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in sorted(TESTS.rglob("*.py"))
        if p.name != Path(__file__).name
        and re.search(r'["\']ORPHO_OFFLINE_CALENDARS["\']\s*:',
                      _code_only(p.read_text(errors="replace")))
    ]
    assert not offenders, (
        "ORPHO_OFFLINE_CALENDARS is back in: " + ", ".join(offenders) +
        ". Nothing reads it. Setting it hides a live network dependency."
    )


def test_legacy_list_only_shrinks() -> None:
    """A frozen name that no test sets any more must be trimmed, so the debt
    stays honest instead of accumulating."""
    still_set = _keys_set_by_tests()
    product = _product_source()
    stale = sorted(k for k in LEGACY_PHANTOM
                   if k not in still_set or k in product)
    assert not stale, (
        "LEGACY_PHANTOM is out of date — these are no longer phantom or no "
        "longer set: " + ", ".join(stale)
    )


def test_the_scanner_can_see_a_real_knob() -> None:
    """NEGATIVE CONTROL. ORPHO_DATA_DIR is set by tests AND read by the product.
    If the scanner stopped finding either side, every assertion above would pass
    over an empty set."""
    assert "ORPHO_DATA_DIR" in _keys_set_by_tests(), "scanner sees no test-set keys"
    assert "ORPHO_DATA_DIR" in _product_source(), "scanner sees no product source"
