#!/usr/bin/env python3
"""test_identity_header_coherence.py — one masthead, every public page.

Founder coherence audit (2026-08-10): pages like Terms, Privacy, sign-in,
verify, the lp/ landers and the pay/ flows still carried the pre-archival
bare header — a lowercase "orphograph" text link — because the 2026-08-08
migration matched three header shapes and this family used a fourth. A
visitor moving from the homepage to any of those pages fell out of the
visual system entirely.

The rule this pins: the OLD masthead may not exist on any customer-facing
page. founder/ is excluded deliberately — internal admin surfaces are not
part of the public identity and the migration script excludes them too.
"""
from __future__ import annotations

import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
OLD_BRAND = '<div class="brand"><a href="/">orphograph</a></div>'
EXCLUDED = ("founder/", "_mockups/", "dist/", "construction/")


class TestHeaderCoherence(unittest.TestCase):
    def test_no_public_page_keeps_the_old_masthead(self):
        offenders = []
        for page in sorted(WEB.rglob("*.html")):
            rel = str(page.relative_to(WEB))
            if rel.startswith(EXCLUDED):
                continue
            if OLD_BRAND in page.read_text(errors="replace"):
                offenders.append(rel)
        self.assertEqual(offenders, [],
                         "pages still carrying the pre-archival masthead: "
                         + ", ".join(offenders))

    def test_retrofitted_pages_carry_the_lockup(self):
        """Spot-pin the pages the audit named: they must now carry the
        crest + wordmark lockup, not merely have lost the old header."""
        for rel in ("terms.html", "privacy.html", "verify/index.html",
                    "signin.html", "docs/api.html"):
            html = (WEB / rel).read_text()
            self.assertIn("orpho-brand__name", html,
                          f"{rel} lost its old header but gained no lockup")


if __name__ == "__main__":
    unittest.main()
