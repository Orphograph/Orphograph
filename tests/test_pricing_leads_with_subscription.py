"""test_pricing_leads_with_subscription.py — pin the 2026-09-11 pricing order.

Free (3 anchors a day, about 90 a month) out-supplies both packs, so a page
that leads with anchor counts sells the one thing free already gives away.
What free lacks is private receipts, the vault and API access, which only the
Standing Order carries. The founder chose to lead with it, so it comes first
and is the only featured card. The page must also make no popularity claim:
no pack has an external sale to back one.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PRICING = WEB / "pricing.html"

POPULARITY = re.compile(r"(?i)most popular|best[- ]?sell(?:er|ing)|customer favou?rite")


def _html() -> str:
    return PRICING.read_text(encoding="utf-8")


def test_standing_order_is_the_first_and_only_featured_tier() -> None:
    html = _html()
    names = re.findall(r'class="t-name">([^<]+)<', html)
    assert names[0] == "Standing Order", names
    featured = re.findall(
        r'<div class="tier featured">\s*(?:<span[^>]*>[^<]*</span>\s*)?'
        r'<div class="t-name">([^<]+)<', html)
    assert featured == ["Standing Order"], featured


def test_price_strip_leads_with_the_standing_order() -> None:
    items = re.findall(r'<span class="pa-item"><strong>([^<]+)</strong>', _html())
    assert items and items[0] == "Standing Order", items


def test_no_page_claims_popularity() -> None:
    pages = [p for p in WEB.rglob("*.html") if not p.relative_to(WEB).as_posix().startswith("_mockups/")]
    hits = [p.relative_to(ROOT).as_posix() for p in pages
            if POPULARITY.search(re.sub(r"<!--.*?-->", "", p.read_text(encoding="utf-8", errors="ignore"), flags=re.S))]
    assert not hits, f"popularity claim with no external sales behind it: {hits}"
    assert POPULARITY.search('<span class="ribbon">Most Popular</span>')
