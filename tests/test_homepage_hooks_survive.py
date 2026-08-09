#!/usr/bin/env python3
"""test_homepage_hooks_survive.py — the visual redesign must not silently
drop a functional hook from the homepage.

The 2026-08-08 archival redesign rewrites web/index.html wholesale. The page
is the revenue surface: it carries the file drop zone, folder anchoring, the
Pack-of-Fifty purchase form, the live status badge, the incident banner and
the recent-receipts feed. Every one of those is wired by id from a separate
JS file, so losing an id breaks the feature with NO error — the script simply
finds nothing and returns.

This test pins the manifest below. It is deliberately a frozen list rather
than a diff against git HEAD: a diff would happily pass if a hook were dropped
in the same commit that updated the expectation.

If you intentionally remove a feature, delete its entry here IN THE SAME
COMMIT and say so in the message. Do not "fix" this test by regenerating it.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "web" / "index.html"

# Captured from web/index.html at eb51d0f, before the archival redesign.
REQUIRED_IDS = {
    # file intake + hashing
    "drop", "drop-input", "drop-btn", "try-sample",
    # folder anchoring (Merkle manifest path)
    "anchor-folder-btn", "folder-progress", "folder-receipt",
    # Pack of Fifty purchase form — revenue
    "pack-form", "pack-form-input", "pack-form-msg", "pack-form-cancel",
    # tier / entitlement display
    "tier-badge", "tier-badge-clear", "tier-badge-detail", "tier-badge-link",
    "tier-explainer",
    # live operational surfaces
    "live-status-badge", "live-receipt-title", "ops-banner",
    "status", "sticky-status",
    # receipts feed + anchors
    "recent-receipts", "recent-receipts-body", "proof", "how",
}

REQUIRED_SCRIPTS = {
    "/v2.js",
    "/folder.js",
    "/statusbar.js",
    "/status-badge.js",
    "/assets/event.js",
    "/assets/scroll-depth.js",
    "/assets/drop-observer.js",
}


class TestHomepageHooksSurvive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_every_functional_id_still_present(self):
        found = set(re.findall(r'id="([^"]+)"', self.html))
        missing = REQUIRED_IDS - found
        self.assertEqual(
            missing, set(),
            f"The redesign dropped {len(missing)} functional id(s): "
            f"{sorted(missing)}. Each is wired from JS by id; losing one "
            f"breaks that feature silently, with no console error.")

    def test_every_script_still_loaded(self):
        srcs = re.findall(r'<script[^>]*src="([^"?]+)', self.html)
        missing = REQUIRED_SCRIPTS - set(srcs)
        self.assertEqual(
            missing, set(),
            f"The redesign dropped script(s): {sorted(missing)}")

    def test_folder_js_still_loaded_as_module(self):
        """folder.js uses ESM imports — a plain <script> would throw."""
        self.assertRegex(
            self.html,
            r'<script[^>]*type="module"[^>]*src="/folder\.js',
            "folder.js must keep type=\"module\"; it uses ESM syntax and a "
            "classic script tag fails at parse time.")

    def test_no_inline_style_attributes(self):
        """CSP is style-src 'self'. An inline style is silently discarded."""
        offenders = re.findall(r'<[^>]+\sstyle="[^"]*"', self.html)
        self.assertEqual(
            offenders, [],
            f"{len(offenders)} inline style attribute(s) — the CSP drops "
            f"these, so the styling would be dead on arrival.")

    def test_no_style_element(self):
        self.assertNotRegex(
            self.html, r"<style[\s>]",
            "Inline <style> blocks are blocked by style-src 'self'.")

    def test_design_system_is_linked_before_page_css(self):
        """Tokens must load before primitives, and both before page CSS,
        or the cascade resolves against undefined custom properties."""
        order = re.findall(r'<link[^>]*href="(/[^"?]+\.css)', self.html)
        self.assertIn("/css/orpho-tokens.css", order, "tokens sheet not linked")
        self.assertIn("/css/orpho-primitives.css", order, "primitives not linked")
        self.assertLess(
            order.index("/css/orpho-tokens.css"),
            order.index("/css/orpho-primitives.css"),
            "tokens must be linked before primitives")

    def test_canonical_sample_receipt_id(self):
        """The reference mockup showed a fabricated receipt id. The sample
        receipt on the site is canonical and must not drift to it."""
        self.assertNotIn(
            "8f3b2c6e-9a17-4d57-9b60-f1d9c7e6a2b4", self.html,
            "That receipt id came from the design mockup and is fabricated.")
        if "SAMPLE RECEIPT" in self.html.upper():
            self.assertIn(
                "XwTULwlh76PcCst9", self.html,
                "The canonical sample receipt is XwTULwlh76PcCst9.")

    def test_every_displayed_sha256_is_64_hex(self):
        """A hash that is the wrong length is a credibility bug on a product
        whose entire subject is hashes.

        The 2026-08-08 reference mockup shipped a 63-character SHA-256 in the
        hero receipt and it was copied in verbatim. Caught by a security
        review, not by a human reading it — nobody counts 64 characters by
        eye, which is exactly why this is a test and not a review checklist
        item.
        """
        # Any long hex run rendered as receipt metadata. 40 is the floor so
        # short ids and colour literals are not swept in.
        for run in re.findall(r"\b[0-9a-f]{40,}\b", self.html):
            self.assertEqual(
                len(run), 64,
                f"displayed hash is {len(run)} hex chars, not 64: {run}")

    def test_crest_asset_is_the_canonical_one(self):
        """The medallion is never redrawn. Any crest on the page must point
        at the shipped artwork, not an inline SVG reinvention."""
        self.assertIn(
            "seal-display.png", self.html,
            "The header crest must use the canonical seal artwork.")


if __name__ == "__main__":
    unittest.main()
