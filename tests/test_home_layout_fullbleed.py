#!/usr/bin/env python3
"""test_home_layout_fullbleed.py — the last-loaded homepage sheet must not
re-cage the page or re-break the phone.

web/home-layout.css loads AFTER index.css on the homepage, which makes it the
last word in the cascade. That is the point of it — and the hazard. Two
classes of regression it can ship that no existing gate sees:

* test_css_cascade_order.py pins the phone collapse of .orpho-hero__inner,
  .orpho-sample__grid and .orpho-pair, but it reads orpho-home.css ONLY. A
  grid-template-columns (or a display that is not grid) on one of those
  selectors HERE overrides the terminal mobile block at every width and the
  gate stays green. v1 of this sheet did exactly that (display:flex on the
  hero), deliberately; v2 must not do it by accident.
* The plan's grep gates (no raw hex, no 100vw, no overflow-x:hidden on the
  root, no transform on an ancestor of #hero-envelope) were review notes.
  Review notes drift. These are the same checks as a test, with a negative
  control so a checker that matches nothing cannot report CLEAN.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHEET = ROOT / "web" / "home-layout.css"
CSS = SHEET.read_text()

COLLAPSING = (".orpho-hero__inner", ".orpho-sample__grid", ".orpho-pair")
ENVELOPE_ANCESTORS = ("html", "body", ".orpho-home", ".orpho-hero", ".orpho-hero.hero",
                      ".orpho-hero__inner")


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _split_selectors(sel: str) -> list[str]:
    """Split a selector list on top-level commas only — the comma inside
    `:is(.a, .b)` is part of one selector, not a list separator."""
    parts, depth, cur = [], 0, []
    for ch in sel:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip()); cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def _rules(css: str):
    """Yield (selector_text, declaration_block) for every rule, media blocks
    flattened. Good enough for a hand-written sheet; not a CSS parser."""
    css = _strip_comments(css)
    css = re.sub(r"@keyframes[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)  # whole keyframes blocks
    css = re.sub(r"@media[^{]*\{", "", css)          # open media blocks
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel = " ".join(m.group(1).split())
        if sel:
            yield sel, m.group(2)


def _declares(css: str, selector_suffix: str, prop: str) -> list[str]:
    """Selectors ending in `selector_suffix` (as a compound, e.g.
    '.orpho-home .orpho-pair' or '.orpho-pair') that declare `prop`."""
    hits = []
    for sel, block in _rules(css):
        for part in _split_selectors(sel):
            last = part.split()[-1] if part.split() else ""
            if last == selector_suffix and re.search(rf"(^|;)\s*{re.escape(prop)}\s*:", block):
                hits.append(part)
    return hits


class TestFullBleedSheet(unittest.TestCase):
    def test_sheet_is_the_homepage_layout_boundary(self):
        self.assertIn("--orpho-rail", CSS, "the rail custom property is the contract")
        for sel, _ in _rules(CSS):
            for part in _split_selectors(sel):
                self.assertTrue(part.startswith(".orpho-home"),
                                f"unscoped rule leaks to other pages: {part!r}")
        # The splitter itself: a comma inside :is() is not a list separator.
        self.assertEqual(_split_selectors(".a :is(.b, .c), .d"), [".a :is(.b, .c)", ".d"])

    def test_collapsing_grids_are_not_redeclared_here(self):
        for sel in COLLAPSING:
            self.assertEqual(_declares(CSS, sel, "grid-template-columns"), [],
                             f"{sel}: columns belong in orpho-home.css, where the "
                             f"phone collapse is gated; declared here they win at every width")
            for part in _declares(CSS, sel, "display"):
                block = next(b for s, b in _rules(CSS) if part in s)
                self.assertRegex(block, r"display\s*:\s*grid",
                                 f"{part}: display must stay grid or the mobile collapse is moot")

    def test_no_raw_hex_no_100vw_no_root_overflow(self):
        code = _strip_comments(CSS)
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", code), [],
                         "colours come from orpho-tokens.css, never raw hex")
        self.assertNotIn("100vw", code, "100vw includes the scrollbar")
        for sel, block in _rules(CSS):
            if sel.split()[-1] in ("html", "body", "body.orpho-home"):
                self.assertNotRegex(block, r"overflow(-x)?\s*:\s*hidden",
                                    "root overflow-x:hidden defeats #sticky-status")

    def test_no_transform_on_envelope_ancestors(self):
        for anc in ENVELOPE_ANCESTORS:
            self.assertEqual(_declares(CSS, anc, "transform"), [],
                             f"transform on {anc} flattens the #hero-envelope z-stack")

    def test_phase_c_ornament_is_decorative_and_respects_reduced_motion(self):
        """Corners, ledger ground and connector: absolute, pointer-events none,
        and every animation this sheet declares is switched off under
        prefers-reduced-motion. A keyframes name that is animated but never
        stilled is the regression this pins."""
        code = _strip_comments(CSS)
        for sel in (".orpho-corners", ".orpho-hero::before", ".orpho-connector"):
            blocks = [b for s, b in _rules(CSS) if any(part.endswith(sel) for part in _split_selectors(s))]
            self.assertTrue(blocks, f"{sel} missing")
            joined = " ".join(blocks)
            self.assertRegex(joined, r"position\s*:\s*absolute", f"{sel} must be absolutely positioned")
            self.assertRegex(joined, r"pointer-events\s*:\s*none", f"{sel} must not take the pointer")
        for sel in (".orpho-hero::before", ".orpho-connector"):
            joined = " ".join(b for s, b in _rules(CSS) if s.endswith(sel))
            self.assertRegex(joined, r"z-index\s*:\s*0", f"{sel} sits under .orpho-hero__inner (z 1)")
        animated = set(re.findall(r"animation\s*:\s*([a-zA-Z][\w-]*)", code)) - {"none"}
        defined = set(re.findall(r"@keyframes\s+([\w-]+)", code))
        self.assertTrue(animated, "Phase C declares at least one animation")
        self.assertEqual(animated - defined, set(), "animation names must be defined in this sheet")
        rm = re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{(.*?)\}\s*$", code, flags=re.S)
        self.assertIsNotNone(rm, "a prefers-reduced-motion block must close the sheet")
        for sel, block in _rules(CSS):
            if re.search(r"animation\s*:\s*orpho-", block):
                self.assertRegex(rm.group(1), re.escape(sel.split()[-1]) + r"[^{]*\{[^}]*animation\s*:\s*none",
                                 f"{sel} animates but is not stilled under reduced motion")

    def test_negative_control_checker_sees_a_planted_defect(self):
        planted = (".orpho-home .orpho-hero__inner { grid-template-columns: 1fr 1fr; }\n"
                   ".orpho-home .orpho-hero { transform: translateX(0); }\n"
                   "body { overflow-x: hidden; color: #fff; width: 100vw; }\n")
        self.assertEqual(_declares(planted, ".orpho-hero__inner", "grid-template-columns"),
                         [".orpho-home .orpho-hero__inner"])
        self.assertEqual(_declares(planted, ".orpho-hero", "transform"),
                         [".orpho-home .orpho-hero"])
        self.assertTrue(re.findall(r"#[0-9a-fA-F]{3,8}\b", planted))
        self.assertIn("100vw", planted)
        kf = "@keyframes x { 0% { left: 0 } 100% { left: 10px } }\n.orpho-home .a { color: red; }\n"
        self.assertEqual([s for s, _ in _rules(kf)], [".orpho-home .a"], "keyframes steps are not rules")


if __name__ == "__main__":
    unittest.main()
