#!/usr/bin/env python3
"""test_breakpoint_coordination.py — the hero breakpoint lives in three places
that nothing links.

PR #235 fixed the ornament's own breakpoint (base state hidden, one min-width
unlock). It did NOT fix the seam BETWEEN sheets, which was the other half of
the review finding:

    orpho-home.css   max-width: 1040px  ×5  ─┐
    orpho-primitives.css max-width: 1040px  ─┼─► "the hero is narrow"
                                             │
    home-layout.css  min-width: 1041px   ×1  ─► "the ornament is allowed"
                                             ▲
    probe_home_layout.py  W <= 1040      ×3  ─┘  (expectations)

Every existing gate is single-sheet, so none of them can see a divergence:

  * test_css_cascade_order.py reads orpho-home.css only, and never inspects
    the media CONDITION -- only whether a declaration is media-scoped at all.
  * test_home_layout_fullbleed.py reads home-layout.css only, and deliberately
    asserts structure rather than the literal.
  * test_css_contrast.py opens both sheets but reads only :root --warn.
  * probe_home_layout.py would catch it at runtime, but WIDTHS jumps straight
    from 1024 to 1280, so no sampled width is near the boundary.

Move the hero collapse to 1100 and six rules move, one does not, and the whole
suite plus the probe stays green while the ornament renders over a one-column
hero in production.

A TEST THAT WAS WRITTEN AND THEN DELETED, recorded so it is not re-attempted.
A `test_no_disjoint_breakpoint_pair_anywhere` looked obvious: flag any
`max-width: N` paired with a `min-width` just above it, since the band between
matches neither rule. It cannot work, and mutation testing showed why. With the
range written as `0 < hi - lo < 1` it matched nothing in 107 stylesheets --
a vacuous pass that no mutant could break. Corrected to `<= 1` it fired on
1040/1041, the very pair `test_ornament_unlock_is_adjacent_to_the_hero_collapse`
below REQUIRES. Adjacent integer breakpoints and "disjoint" breakpoints are the
same thing; there is no version of that test that is both non-vacuous and
consistent.

What actually makes the uncovered band harmless is the ornament's BASE state
being the hidden state, which
test_home_layout_fullbleed.py::test_phase_c_ornament_hides_by_default_with_one_breakpoint
already pins. Duplicating it here would add a second copy to drift, so this
file asserts the seam between sheets and leaves the base state to that test.

WHY A CROSS-CHECK AND NOT A SHARED CONSTANT. The obvious fix is one
HERO_BREAKPOINT imported everywhere. This repo has already taken the opposite
position deliberately: --orpho-rail is defined once in home-layout.css:22 and
scripts/probe_home_layout.py:37 re-derives the identical clamp in JS rather
than reading it back, because (docstring, :13-14) "a sheet that silently drops
the custom property fails". Independent restatement plus a cross-check is the
house pattern. This test is that cross-check.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from _css import WEB, media_widths, rules_mentioning, strip_comments

ROOT = Path(__file__).resolve().parent.parent
HOME = WEB / "css" / "orpho-home.css"
LAYOUT = WEB / "home-layout.css"
PROBE = ROOT / "scripts" / "probe_home_layout.py"

# The selector whose collapse defines "the hero is narrow", and the two the
# ornament is built from. Named here so a rename breaks this test loudly
# rather than silently emptying it.
HERO = ".orpho-hero__inner"
ORNAMENT = (".orpho-corners", ".orpho-connector")


class TestBreakpointCoordination(unittest.TestCase):
    def test_ornament_unlock_is_adjacent_to_the_hero_collapse(self):
        """min-width on the ornament == max-width on the hero + 1.

        Adjacent literals leave no width uncovered. Equal literals would double
        up at exactly N; a wider split leaves a band where the hero is two
        columns and the ornament is hidden, or worse.
        """
        hero_maxes = [px for kind, px in rules_mentioning(HOME.read_text(), HERO)
                      if kind == "max-width"]
        self.assertTrue(hero_maxes, f"no max-width governs {HERO} in orpho-home.css")
        collapse = max(hero_maxes)

        unlocks = set()
        for sel in ORNAMENT:
            for kind, px in rules_mentioning(LAYOUT.read_text(), sel):
                unlocks.add((kind, px))
        self.assertEqual(
            len(unlocks), 1,
            f"the ornament must be governed by exactly one breakpoint, found {unlocks}")
        kind, unlock = unlocks.pop()
        self.assertEqual(kind, "min-width",
                         "the ornament unlocks on min-width; a max-width partner "
                         "reopens the uncovered band PR #235 closed")
        self.assertEqual(
            unlock, collapse + 1,
            f"hero collapses at max-width:{collapse:g} but the ornament unlocks at "
            f"min-width:{unlock:g}. They must stay adjacent: move one and you must "
            f"move the other, and no existing gate would have told you.")

    def test_probe_expects_the_same_collapse_literal(self):
        """The runtime probe restates the literal; it must restate THIS one."""
        hero_maxes = [px for kind, px in rules_mentioning(HOME.read_text(), HERO)
                      if kind == "max-width"]
        collapse = max(hero_maxes)
        src = PROBE.read_text()
        # Strip the module docstring so prose about "above 1040px" is not read
        # as an expectation -- the same class of error as CSS comments.
        code = re.sub(r'^\s*""".*?"""', "", src, count=1, flags=re.S)
        literals = {float(m) for m in re.findall(r"W\s*<=\s*([\d.]+)", code)}
        self.assertTrue(literals, "the probe no longer compares W to a literal")
        self.assertEqual(
            literals, {collapse},
            f"probe compares W to {literals} but the sheets collapse at {collapse:g}")

    def test_no_other_stylesheet_governs_the_ornament(self):
        """The adjacency check above reads one sheet, so it is only sound if no
        other sheet can make the ornament visible."""
        others = []
        for css in sorted(WEB.rglob("*.css")):
            if css == LAYOUT:
                continue
            body = strip_comments(css.read_text(errors="replace"))
            if any(sel in body for sel in ORNAMENT):
                others.append(str(css.relative_to(ROOT)))
        self.assertEqual(
            others, [],
            f"the ornament selectors also appear in {others}; the adjacency "
            "check reads home-layout.css only and would no longer be sound")

    def test_comment_stripping_is_load_bearing(self):
        """home-layout.css quotes BOTH retired literals in a review comment.

        Without stripping, that prose reads as two live breakpoints and every
        assertion above changes answer. Negative control for the stripper, in
        the idiom of test_css_contrast.py:132.
        """
        raw = LAYOUT.read_text()
        self.assertIn("max-width: 1040px", raw,
                      "the review comment citing the retired pair was removed; "
                      "this control no longer proves anything -- re-point it")
        self.assertNotIn("max-width: 1040px", strip_comments(raw),
                         "stripper missed a comment")
        kinds = {k for k, _, _ in media_widths(raw)}
        self.assertNotIn("max-width", {k for k, px, _ in media_widths(raw) if px == 1040},
                         "a 1040 max-width survived stripping in home-layout.css")
        self.assertIn("min-width", kinds)


if __name__ == "__main__":
    unittest.main()
