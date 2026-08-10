#!/usr/bin/env python3
"""test_terms_disclosure_surfacing.py — the Terms must cover what is SOLD,
and the disclosure must be REACHABLE at the moments that matter.

Found 2026-08-10 by the disclosure-map audit. Three gaps, one per test:

  * terms.html §4 was written when the catalog was one SKU ("Writer Pack
    ($19)"). The live pricing page sells THREE: Writer Pack ($19, 10
    credits), Pack of Fifty ($29, 50 credits), and the Standing Order
    ($9/month subscription). Two of the three — including the only
    RECURRING charge — appeared nowhere in the contract governing them.
  * No purchase-assent line existed at any buy CTA. Terms a buyer never
    sees at the moment of payment are terms nobody agreed to.
  * 48 pages with footers (status, press, security, every lp/ landing
    page…) had no path to /terms at all.

The SKU test reads prices from server/public_config.py's declared defaults
rather than pinning literals, so a price change that forgets the contract
fails here instead of shipping silently.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
TERMS = (WEB / "terms.html").read_text()
PRICING = (WEB / "pricing.html").read_text()
CONFIG = (ROOT / "server" / "public_config.py").read_text()


def _default(env_name: str) -> int:
    m = re.search(r'_int_env\("%s",\s*(\d+)\)' % env_name, CONFIG)
    assert m, f"{env_name} default not found in public_config.py"
    return int(m.group(1))


class TestTermsCoverTheCatalog(unittest.TestCase):
    def test_every_sold_sku_appears_in_terms(self):
        pack = _default("PACK_PRICE_USD")
        pack50 = _default("PACK50_PRICE_USD")
        monthly = _default("PERSONAL_MONTHLY_USD")
        for label, pattern in [
            (f"Writer Pack price ${pack}", r"(?<!\d)\$%d\b" % pack),
            (f"Pack of Fifty price ${pack50}", r"(?<!\d)\$%d\b" % pack50),
            (f"Standing Order price ${monthly}", r"(?<!\d)\$%d\b" % monthly),
            ("Pack of Fifty by name", r"Pack of Fifty"),
            ("Standing Order by name", r"Standing Order"),
        ]:
            self.assertTrue(re.search(pattern, TERMS),
                            f"terms.html does not mention {label} — the "
                            f"contract lags the catalog it governs")

    def test_credit_counts_match_config(self):
        for env, name in [("PACK_CREDIT_COUNT", "Writer Pack"),
                          ("PACK50_CREDIT_COUNT", "Pack of Fifty")]:
            n = _default(env)
            self.assertTrue(re.search(r"(?<!\d)%d\b" % n, TERMS),
                            f"terms.html never states the {name}'s "
                            f"{n}-credit grant")


class TestAssentAtTheMomentOfPayment(unittest.TestCase):
    """Every buy CTA must have assent language in its immediate vicinity."""

    def test_each_buy_cta_carries_assent(self):
        for cta in ("buy-pack", "buy-pack50", "buy-personal"):
            pos = PRICING.find(f'id="{cta}"')
            self.assertGreater(pos, -1, f"{cta} CTA missing from pricing.html")
            window = PRICING[pos:pos + 1800]
            self.assertIn("agree to the", window,
                          f"no purchase-assent language near {cta}")
            self.assertIn('href="/terms"', window,
                          f"assent near {cta} does not link the Terms")


class TestTermsReachableFromEveryFooter(unittest.TestCase):
    def test_every_footer_links_terms(self):
        missing = []
        for page in sorted(WEB.rglob("*.html")):
            html = page.read_text(errors="replace")
            if "<footer" in html and 'href="/terms"' not in html:
                missing.append(str(page.relative_to(WEB)))
        self.assertEqual(missing, [],
                         "pages with footers but no path to the Terms: "
                         + ", ".join(missing))


if __name__ == "__main__":
    unittest.main()
