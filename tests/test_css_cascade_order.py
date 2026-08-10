#!/usr/bin/env python3
"""test_css_cascade_order.py — layout appended after the responsive blocks
re-breaks every phone.

Found 2026-08-10 via founder's iPhone screenshots: eight append-only
"correction passes" put unscoped `grid-template-columns` on
.orpho-hero__inner AFTER the max-width media query that collapses it, so
the two-column hero rendered at 390pt — text squeezed to ~15ch, receipt
clipped mid-word. The cascade has no warning for this: same specificity,
later wins, at every width.

The contract this pins: for each collapsing selector, the LAST
grid-template-columns declaration in file order must live inside a
max-width media block. Appending a desktop-layout override at the file
end fails here before it reaches a phone.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "web" / "css" /
       "orpho-home.css").read_text()

SELECTORS = (".orpho-hero__inner", ".orpho-sample__grid", ".orpho-pair")


def _last_decl_scope(selector: str) -> str | None:
    """Return 'media' or 'unscoped' for the LAST grid-template-columns
    declaration on `selector`, tracking media blocks with a brace count."""
    last = None
    depth = 0          # brace depth relative to an open @media block
    in_media = False
    i = 0
    for m in re.finditer(r"@media[^{]*\{|\{|\}", CSS):
        tok = m.group(0)
        if tok.startswith("@media"):
            in_media = True; depth = 1
        elif tok == "{":
            if in_media: depth += 1
        elif tok == "}":
            if in_media:
                depth -= 1
                if depth == 0: in_media = False
    # Second pass: walk rule-by-rule with positions.
    pos = 0
    media_ranges = []
    for m in re.finditer(r"@media[^{]*\{", CSS):
        start = m.end(); depth = 1; j = start
        while depth and j < len(CSS):
            if CSS[j] == "{": depth += 1
            elif CSS[j] == "}": depth -= 1
            j += 1
        media_ranges.append((m.start(), j))
    for m in re.finditer(
            re.escape(selector) + r"[^{}]*\{[^}]*grid-template-columns", CSS):
        scoped = any(a <= m.start() < b for a, b in media_ranges)
        last = "media" if scoped else "unscoped"
    return last


class TestCascadeOrder(unittest.TestCase):
    def test_last_grid_decl_is_media_scoped(self):
        for sel in SELECTORS:
            scope = _last_decl_scope(sel)
            if scope is None:
                continue
            self.assertEqual(
                scope, "media",
                f"{sel}: the LAST grid-template-columns in orpho-home.css is "
                f"UNSCOPED — it overrides the phone collapse at every width. "
                f"Move the correction above the terminal mobile block.")

    def test_terminal_block_present(self):
        tail = CSS[-4000:]
        self.assertIn("TERMINAL BLOCK", tail,
                      "the terminal mobile-reassert block must stay last")


if __name__ == "__main__":
    unittest.main()
