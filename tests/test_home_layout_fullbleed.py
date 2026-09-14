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

from _css import rules_mentioning

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
        # The reduced-motion block is located by a BRACE SCAN, not by a
        # `\}\s*$` regex. The regex form (shipped 2026-09-05) could not fail:
        # `.*?` with re.S simply grew until the LAST `}` in the file matched
        # `$`, so a rule appended AFTER the block was swallowed into
        # `rm.group(1)` and the "is it stilled" assertion still found the
        # original `animation: none`. Proven by mutation on 2026-09-07:
        # appending `.orpho-connector__dot { animation: orpho-connector-travel
        # 10s infinite }` — which re-animates the dot for reduced-motion users
        # at equal specificity and later in the cascade — left the suite green.
        start = code.find("@media (prefers-reduced-motion: reduce)")
        self.assertNotEqual(start, -1, "the sheet must carry a prefers-reduced-motion block")
        open_brace = code.index("{", start)
        depth, end = 0, None
        for i in range(open_brace, len(code)):
            if code[i] == "{":
                depth += 1
            elif code[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        self.assertIsNotNone(end, "the prefers-reduced-motion block is unbalanced")
        rm_body = code[open_brace + 1:end]
        # Nothing may re-animate after the block: it is the sheet's last word.
        self.assertNotRegex(code[end + 1:], r"animation(-name)?\s*:\s*orpho-",
                            "a rule after the reduced-motion block re-animates; "
                            "the block must close the sheet to hold")
        for sel, block in _rules(CSS):
            if re.search(r"animation(-name)?\s*:\s*(?:[^;]*\s)?orpho-", block):
                self.assertRegex(rm_body, re.escape(sel.split()[-1]) + r"[^{]*\{[^}]*animation\s*:\s*none",
                                 f"{sel} animates but is not stilled under reduced motion")

    def test_phase_c_ornament_hides_by_default_with_one_breakpoint(self):
        """The ornament's BASE state is hidden and exactly one media literal
        unlocks it.

        It shipped (2026-09-05) as an exclusive pair — styled only inside
        `@media (min-width: 1041px)`, hidden only inside
        `@media (max-width: 1040px)` — so the breakpoint was written twice in
        one sheet and the two literals did not meet: a viewport width strictly
        between them matched neither rule, leaving the list with neither
        `position: absolute` nor `display: none`. Whether a browser can
        actually present a width in that band was NOT reproduced (Brave
        snapped 1040.5 to 1041), so this pins the structure, which is the part
        that is wrong on its own terms."""
        code = _strip_comments(CSS)
        for sel in (".orpho-corners", ".orpho-connector"):
            base = [b for s_, b in _rules(code)
                    if any(part.endswith(sel) for part in _split_selectors(s_))
                    and re.search(r"display\s*:\s*none", b)]
            self.assertTrue(base, f"{sel} must be display:none in the base state")
        # Brace-matched, not regex-nested. The regex form shipped 2026-09-05
        # tolerated exactly ONE level of nesting, so a rule any deeper dropped
        # out of `governing` silently and this assertion passed on a partial
        # set. rules_mentioning walks braces, so depth cannot hide a rule.
        governing = set()
        for sel in (".orpho-corners", ".orpho-connector"):
            for kind, px in rules_mentioning(CSS, sel):
                governing.add(f"{kind}: {px:g}px")
        self.assertEqual(
            len(governing), 1,
            f"exactly one width breakpoint may govern the ornament, found {governing}")
        self.assertNotIn(
            "max-width", " ".join(governing),
            "the ornament unlocks on min-width; a max-width partner re-opens the gap")

    def test_connector_dot_reaches_the_block(self):
        """The travelling point must touch the block glyph, not stop short.

        Measured 2026-09-07 in Brave at 1440px: the dot's right edge finished
        3.22px (0.2rem) shy of the block's left edge, in both the animated end
        keyframe and the reduced-motion rest pose. This asserts the arithmetic
        rather than the literal, so a change to the block or dot size has to
        move the end pose with it."""
        code = _strip_comments(CSS)

        def px(pattern, where):
            m = re.search(pattern, code)
            self.assertIsNotNone(m, f"could not read {where}")
            return float(m.group(1))

        block_right = px(r"\.orpho-connector__block\s*\{[^}]*right\s*:\s*(-?[\d.]+)rem", "block right")
        block_w = px(r"\.orpho-connector__block\s*\{[^}]*width\s*:\s*([\d.]+)rem", "block width")
        dot_w = px(r"\.orpho-connector__dot\s*\{[^}]*width\s*:\s*([\d.]+)rem", "dot width")
        # The connector's right edge is 100%. block_right is a negative inset,
        # so the glyph's left edge sits at 100% + block_right - ... no: at
        # 100% - block_right_inset - width, with the inset measured inward.
        block_left = -block_right - block_w          # rem offset from 100%, negative = inside
        # The dot moves on transform (2026-09-13): its end pose is stated as a
        # translateX of the connector's own width minus the end offset.
        ends = re.findall(r"translateX\(calc\(var\(--orpho-connector-w\)\s*-\s*([\d.]+)rem\)\)", code)
        self.assertEqual(len(ends), 2,
                         "both the keyframe end and the reduced-motion rest pose "
                         f"must state an end offset, found {ends}")
        for end in {float(e) for e in ends}:
            dot_right = -end + dot_w                 # same frame of reference
            self.assertAlmostEqual(
                dot_right, block_left, places=3,
                msg=f"dot right edge at 100%{dot_right:+}rem does not meet the "
                    f"block's left edge at 100%{block_left:+}rem")

    def test_connector_dot_animates_off_the_layout_thread(self):
        """Measured 2026-09-13 in headless Chromium at 1440x900 over 4s: the
        `left` keyframes forced 234 layouts (one per frame, ~16ms/s of layout
        + pre-paint on the main thread); with the dot static, 1 layout. So the
        travel keyframes may move the dot only through transform and opacity,
        and the reduced-motion rest pose parks it the same way."""
        code = _strip_comments(CSS)
        start = code.find("@keyframes orpho-connector-travel")
        self.assertNotEqual(start, -1, "the travel keyframes must exist")
        stop = code.find("@media", start)
        body = code[start:stop if stop != -1 else None]
        layout = re.findall(r"\b(left|right|top|bottom|width|height|margin[\w-]*|inset)\s*:", body)
        self.assertEqual(layout, [], f"layout property animated in orpho-connector-travel: {layout}")
        self.assertIn("transform", body, "the dot travels on transform")
        rm = code[code.find("@media (prefers-reduced-motion: reduce)"):]
        self.assertRegex(rm, r"\.orpho-connector__dot\s*\{[^}]*transform\s*:\s*translateX\(",
                         "the reduced-motion rest pose must park the dot on transform too")

    def test_gold_hairlines_fade_on_one_token(self):
        """The corner hairlines faded at 40% and the connector at 30%: two
        curves for one ornament (review of #235). One token, every gradient."""
        code = _strip_comments(CSS)
        grads = re.findall(r"linear-gradient\((?:[^()]|\([^()]*\))*\)", code)
        gold = [g for g in grads if "var(--orpho-gold)" in g]
        self.assertGreaterEqual(len(gold), 3, gold)
        for g in gold:
            self.assertIn("var(--orpho-hairline-fade)", g, g)
            self.assertNotRegex(g, r"\d+%", f"literal stop in {g}")
        self.assertRegex(code, r"--orpho-hairline-fade\s*:\s*\d+%", "the fade token must be declared once")
        self.assertEqual(len(re.findall(r"--orpho-hairline-fade\s*:", code)), 1)

    def test_corner_title_reuses_the_label_primitive(self):
        """`.orpho-corner strong` re-declared the letterspaced-uppercase recipe
        that `.orpho-label` already owns (review of #235). The markup carries
        the primitive; the corner rule states only what differs."""
        html = (ROOT / "web" / "index.html").read_text()
        strongs = re.findall(r'<li class="orpho-corner[^"]*"><strong([^>]*)>', html)
        self.assertEqual(len(strongs), 4, "four corner titles expected")
        for attrs in strongs:
            self.assertRegex(attrs, r'class="[^"]*\borpho-label\b', f"corner title lacks orpho-label: {attrs!r}")
        code = _strip_comments(CSS)
        m = re.search(r"\.orpho-home \.orpho-corner strong\s*\{([^}]*)\}", code)
        self.assertIsNotNone(m, "the corner title rule must exist")
        for dup in ("text-transform", "letter-spacing", "font-family"):
            self.assertNotIn(dup, m.group(1), f"{dup} is the .orpho-label primitive's job")

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
