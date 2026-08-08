#!/usr/bin/env python3
"""test_notary_claim_is_disclaimed.py — if a page's header calls Orphograph an
"Empirical Notary", that page must also carry the disclaimer.

WHY THIS EXISTS SEPARATELY FROM test_regulated_term_scan
--------------------------------------------------------
The regulated-term scanner is baseline-grandfathered: it fires only when a
page introduces a regulated term it did not previously contain. That is the
right design for prose — a blog post titled "digital notary vs cryptographic
timestamp" has always contained the word and should not trip a gate forever.

But it does not cover the case this test covers. On 2026-08-08 the archival
design system added a brand lockup reading

    Orphograph
    EMPIRICAL NOTARY

to the header of 56 pages. On 22 of them the word "notary" was ALREADY in the
baseline (blog posts about notarisation, the legal/ pages, method/ essays), so
the scanner passed every one — while the site had quietly started *asserting*,
in its own masthead, that it is a notary, on pages carrying no disclaimer.

A term appearing in prose and a brand claiming it as identity are different
claims. The scanner measures the first. This measures the second.

Orphograph is not a notary. Wherever the masthead says otherwise, the
correction must be on the same page.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

CLAIM = 'orpho-brand__sub">Empirical Notary'
DISCLAIMER = "not a law firm"

# Not customer-facing, or not served.
EXCLUDE_PREFIXES = ("_mockups/", "dist/", "founder/", "construction/")


def _pages():
    for f in sorted(WEB.rglob("*.html")):
        rel = f.relative_to(WEB).as_posix()
        if rel.startswith(EXCLUDE_PREFIXES):
            continue
        yield rel, f.read_text(encoding="utf-8")


class TestNotaryClaimIsDisclaimed(unittest.TestCase):
    def test_masthead_notary_claim_always_carries_the_disclaimer(self):
        offenders = [
            rel for rel, html in _pages()
            if CLAIM in html and DISCLAIMER not in html
        ]
        self.assertEqual(
            offenders, [],
            f"{len(offenders)} page(s) assert 'Empirical Notary' in the "
            f"masthead with no disclaimer on the page: {offenders}. "
            f"Either add the disclaimer to the footer or drop the subtitle "
            f"from that page's header — do not leave the claim bare.")

    def test_the_check_can_actually_fail(self):
        """Guard against the invariant silently measuring nothing.

        If the brand markup is ever renamed, `CLAIM` stops matching and the
        test above passes vacuously across the whole site. Assert that the
        claim string is present somewhere, so a rename breaks loudly here
        instead of disarming the gate everywhere.
        """
        claiming = [rel for rel, html in _pages() if CLAIM in html]
        self.assertGreater(
            len(claiming), 0,
            "No page carries the masthead claim string — either the brand "
            "markup was renamed (update CLAIM) or the lockup was removed. "
            "Until this is resolved the disclaimer invariant is vacuous.")


if __name__ == "__main__":
    unittest.main()
