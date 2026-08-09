#!/usr/bin/env python3
"""test_privacy_matches_wire.py — the privacy page must describe exactly what
crosses the network, derived from the code that sends it.

Found 2026-08-08 (external audit, then verified against v2.js): privacy.html
said "Only the 32-byte SHA-256 digest is sent to us" while the anchoring page
computed BOTH digests and submitted `sha512_hex` alongside `hash_hex`. On a
trust product a privacy statement that understates the wire is a false claim,
not a nuance.

These pins are CONDITIONAL on the sending code, so they cannot rot in either
direction: if v2.js stops sending sha512_hex, the privacy mention becomes the
stale side and the test flips to demand its removal.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVACY = (ROOT / "web" / "privacy.html").read_text(encoding="utf-8")
V2JS = (ROOT / "web" / "v2.js").read_text(encoding="utf-8")
API_DOC = (ROOT / "web" / "docs" / "api.html").read_text(encoding="utf-8")


class TestPrivacyMatchesWire(unittest.TestCase):
    def test_sha512_disclosure_tracks_the_sender(self):
        sends_512 = "sha512_hex" in V2JS
        mentions_512 = "SHA-512" in PRIVACY
        if sends_512:
            self.assertTrue(
                mentions_512,
                "v2.js submits sha512_hex but privacy.html never mentions "
                "SHA-512 — the privacy page understates what crosses the wire.")
        else:
            self.assertFalse(
                mentions_512,
                "privacy.html discusses SHA-512 but nothing sends it — the "
                "privacy page overstates the wire; remove the stale mention.")

    def test_no_sha256_only_absolute(self):
        self.assertNotRegex(
            PRIVACY, r"[Oo]nly the 32-byte SHA-256 digest is\s+sent",
            "The 'only SHA-256 is sent' absolute is false while sha512_hex "
            "is on the wire.")

    def test_private_receipts_are_described(self):
        """The product ships owner-only private receipts (fail-closed).
        A privacy page describing receipts as public-by-design without the
        private posture misdescribes the product."""
        self.assertIn("Private receipt", PRIVACY)
        self.assertIn("fails\n      closed", PRIVACY.replace("fails closed", "fails\n      closed"),)

    def test_api_docs_free_tier_is_daily_everywhere(self):
        """The server enforces RATE_LIMIT_PER_DAY; docs said '10 anchors/hour'
        in one row and '3 per 24 hours' in two others."""
        self.assertNotRegex(
            API_DOC, r"\d+\s+anchors/hour",
            "api.html states an hourly free-tier limit; enforcement is per-day.")
        self.assertGreaterEqual(
            len(re.findall(r"3 anchors per 24 hours", API_DOC)), 2)


if __name__ == "__main__":
    unittest.main()
