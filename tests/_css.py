"""_css.py — the shared CSS-text helpers, in ONE place.

Written 2026-09-08. `_strip_comments` existed twice already, in
test_css_contrast.py:44 and test_home_layout_fullbleed.py:35, byte-identical.
That is the shape `_srv.py` was written to stop: a helper copied until the
copies drift.

The stripper is LOAD-BEARING, not cosmetic. `web/home-layout.css:172-173`
contains this prose:

    * only inside `@media (min-width: 1041px)` and hidden only inside
    * `@media (max-width: 1040px)`: an exclusive PAIR, ...

which is a review note explaining a breakpoint pair that no longer exists. A
scanner that reads breakpoint literals without stripping comments first finds
those two and reports a coordination failure that is pure prose. Every consumer
here strips first, and test__css.py plants a comment to prove the stripper can
still fail.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def strip_comments(css: str) -> str:
    """Remove /* ... */ blocks. Non-greedy with DOTALL, so nested-looking
    comment text inside one comment is consumed with it."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def media_widths(css: str) -> list[tuple[str, float, int]]:
    """-> [(kind, literal_px, line_no)] for every width condition in a @media
    prelude, comments stripped. kind is 'min-width' or 'max-width'.

    Only the prelude is read, so a `max-width` used as a PROPERTY inside a
    declaration block (`.x { max-width: 40rem }`) is not mistaken for a
    breakpoint. Line numbers are counted on the stripped text, so they are
    reliable for ordering and identity but are NOT source line numbers; the
    callers here use them only to distinguish separate occurrences.
    """
    code = strip_comments(css)
    out: list[tuple[str, float, int]] = []
    for m in re.finditer(r"@media([^{]*)\{", code):
        line_no = code.count("\n", 0, m.start()) + 1
        for w in re.finditer(r"(min|max)-width\s*:\s*([\d.]+)px", m.group(1)):
            out.append((f"{w.group(1)}-width", float(w.group(2)), line_no))
    return out


def rules_mentioning(css: str, selector: str) -> list[tuple[str, float]]:
    """-> [(kind, literal_px)] for every @media whose BODY mentions `selector`.

    Brace-matched rather than regex-nested, so a rule at any nesting depth is
    seen. The regex form this replaces tolerated exactly one level and dropped
    anything deeper out of the result set, silently.
    """
    code = strip_comments(css)
    found: list[tuple[str, float]] = []
    for m in re.finditer(r"@media([^{]*)\{", code):
        depth, end = 0, None
        for i in range(m.end() - 1, len(code)):
            if code[i] == "{":
                depth += 1
            elif code[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            continue
        if selector in code[m.end():end]:
            for w in re.finditer(r"(min|max)-width\s*:\s*([\d.]+)px", m.group(1)):
                found.append((f"{w.group(1)}-width", float(w.group(2))))
    return found
