#!/usr/bin/env python3
"""test_qr_scannable.py — a QR below camera size is a decorative lie.

DEFECT (2026-08-16, founder-reported) and the SECOND defect it hid
------------------------------------------------------------------
The founder pointed a camera at the homepage QRs and they did not scan.
Every QR FILE decoded perfectly (repo = origin = edge, OpenCV reads the right
URL). The threshold was MEASURED, not guessed — the real /qr-receipt.svg
rasterized at candidate sizes and machine-decoded:

    76 fails · 96 fails · 112 fails · 128 fails · 144 fails · 160 DECODES

The first version of this file pinned that floor by GREPPING THE STYLESHEET,
and passed 4/4 while the live page still drew 146px symbols. `style.css` sets
`* { box-sizing: border-box }` site-wide, so a wrapper declaring `width:160px`
while carrying `padding: 6px` and a `1px` border rendered 160-12-2 = 146.
Production was worse: 74px. Four green tests, one broken product — the exact
vacuous-pass shape the defect hunt exists to catch, committed by the test
that was supposed to prevent it.

So the assertion moved to where the truth is: `tools/qr_geometry.py` renders
the real page in a real engine and reads each QR's bounding box out of the
DOM in CSS pixels. The static checks below are kept as a cheap backstop, but
they are NOT the gate — the rendered geometry is.

A missing browser reports UNAVAILABLE (skip with the reason stated), never a
pass: see feedback_verifier_claim_honesty.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import qr_geometry  # noqa: E402

MIN_PX = 160  # measured camera threshold for the 33-module symbol

# The widths a visitor actually arrives at. Phone matters most: it is the
# device holding the camera. 500 is the floor headless Brave will honour on
# this machine (feedback_headless_chromium_500px_min) — asking for 390 
# silently yields 500, so 500 is what gets asserted and claimed.
VIEWPORTS = (1440, 900, 500)


class TestRenderedQRGeometry(unittest.TestCase):
    """The gate. Everything else in this file is a backstop."""

    @classmethod
    def setUpClass(cls):
        if not qr_geometry.browser_available():
            raise unittest.SkipTest(
                f"UNAVAILABLE, not passed: no headless browser at "
                f"{qr_geometry.BRAVE}, so rendered QR size could not be "
                f"measured. The static checks below still ran.")
        cls.geo = {}
        for page in ("index.html", "receipt.html"):
            for vw in VIEWPORTS:
                cls.geo[(page, vw)] = qr_geometry.measure(page, width=vw)

    def test_every_rendered_qr_meets_the_measured_threshold(self):
        small = []
        for (page, vw), g in self.geo.items():
            for q in g["qrs"]:
                # sym_*, not w/h: the bounding box includes padding+border and
                # reports 160 for a 146px symbol. Mutation-checked — putting
                # `padding: 6px` back left the box at 160 and this assertion
                # green until it measured the content box instead.
                if min(q["sym_w"], q["sym_h"]) < MIN_PX:
                    small.append(f"{page} @{vw}px viewport: symbol "
                                 f"{q['sym_w']}x{q['sym_h']} inside a "
                                 f"{q['w']}x{q['h']} box ({q['sel'] or 'img'})")
        self.assertEqual(
            small, [],
            f"QR drawn below the measured {MIN_PX}px camera threshold. The "
            f"stylesheet can say 160 and the browser still draw less — that "
            f"is what happened on 2026-08-16 (declared 160, drawn 146):\n  "
            + "\n  ".join(small))

    def test_no_qr_is_clipped_away_by_an_ancestor(self):
        """A QR at the right size that an `overflow: hidden` ancestor cuts in
        half is not scannable either. The hero plate did exactly this in
        production at every width measured, hiding all but ~24px of it."""
        clipped = []
        for (page, vw), g in self.geo.items():
            for q in g["qrs"]:
                if q.get("clipped"):
                    clipped.append(f"{page} @{vw}px: {q['clipped']['hidden_px']}px "
                                   f"cut off by .{q['clipped']['by']}")
        self.assertEqual(clipped, [],
                         "QR partly outside a clipping ancestor:\n  "
                         + "\n  ".join(clipped))

    def test_a_qr_is_actually_present_where_we_promise_one(self):
        """Guards the lazy fix: deleting the QR also makes the two tests
        above pass. The sample-receipt section must still carry one."""
        for vw in VIEWPORTS:
            g = self.geo[("index.html", vw)]
            self.assertGreaterEqual(
                len(g["qrs"]), 1,
                f"homepage @{vw}px renders no QR at all — the page promises "
                f"a scannable sample receipt")
        for vw in VIEWPORTS:
            g = self.geo[("receipt.html", vw)]
            self.assertGreaterEqual(
                len(g["qrs"]), 1,
                f"receipt page @{vw}px renders no QR — the caption says "
                f"'Scan to open this receipt'")


class TestStaticBackstops(unittest.TestCase):
    """Cheap, run anywhere, and individually insufficient — each of these
    passed while the product was broken."""

    def test_no_inner_padding_shrinks_the_qr(self):
        """The SVG carries its own quiet zone; CSS padding inside the img box
        only shrinks modules."""
        for rel in ("web/css/orpho-home.css", "web/receipt.css"):
            css = (ROOT / rel).read_text()
            for m in re.finditer(r"\.[\w-]*qr[\w-]*(?:\s+img)?\s*\{[^}]*\}",
                                 css, re.S):
                block = m.group(0)
                pm = re.search(r"padding:\s*(\d+)px", block)
                if pm and int(pm.group(1)) > 0 and " img" in block[:80]:
                    self.fail(f"{rel}: QR img has inner padding "
                              f"{pm.group(1)}px — shrinks modules below the "
                              f"measured size")

    def test_qr_imgs_are_sized_in_content_box(self):
        """`* { box-sizing: border-box }` in style.css is what turned a
        declared 160 into a drawn 146. Sizing the img in content-box is the
        root fix; this pins it so a later refactor cannot quietly undo it."""
        for rel in ("web/css/orpho-home.css", "web/receipt.css"):
            css = (ROOT / rel).read_text()
            blocks = [m.group(0) for m in
                      re.finditer(r"\.[\w-]*qr[\w-]*\s+img[^{]*\{[^}]*\}",
                                  css, re.S)]
            self.assertTrue(blocks, f"{rel}: no QR img rule found")
            for b in blocks:
                if re.search(r"width:\s*\d+px", b):
                    self.assertIn(
                        "box-sizing: content-box", b,
                        f"{rel}: QR img sets a pixel width without "
                        f"content-box, so the site-wide border-box rule "
                        f"subtracts the border from the symbol:\n{b}")

    def test_html_width_attributes_meet_threshold(self):
        for rel in ("web/index.html", "web/receipt.html"):
            html = (ROOT / rel).read_text()
            for m in re.finditer(r'src="[^"]*qr[^"]*"[^>]*width="(\d+)"', html):
                self.assertGreaterEqual(
                    int(m.group(1)), MIN_PX,
                    f"{rel}: QR <img width={m.group(1)}> below {MIN_PX}px")

    def test_the_svg_still_carries_its_own_quiet_zone(self):
        """The padding removal is only safe while this holds."""
        svg = (ROOT / "web" / "qr-receipt.svg").read_text()
        vb = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
        self.assertIsNotNone(vb)
        rects = re.findall(r'<rect x="(\d+)" y="(\d+)"', svg)
        min_x = min(int(x) for x, _ in rects)
        self.assertGreaterEqual(
            min_x * 41 // int(vb.group(1)), 4,
            "qr-receipt.svg no longer carries a >=4-module quiet zone; "
            "removing CSS padding is no longer safe")


if __name__ == "__main__":
    unittest.main()
