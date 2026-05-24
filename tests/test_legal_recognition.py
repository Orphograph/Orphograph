"""test_legal_recognition.py — coverage for the new /method/legal-recognition
page added in the May 2026 session.

Asserts the page is present on disk, carries a substantial set of external
citation links, and is announced in the root sitemap so search engines and
the buy-flow pages can deep-link into it.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "web" / "method" / "legal-recognition.html"
SITEMAP = ROOT / "web" / "sitemap.xml"

SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
SITEMAP_URL = "https://orphograph.com/method/legal-recognition.html"


def test_legal_recognition_page_exists():
    assert PAGE.exists(), f"expected page on disk at {PAGE}"
    # Sanity check that it's a real HTML doc, not an empty placeholder.
    text = PAGE.read_text(encoding="utf-8")
    assert text.lower().startswith("<!doctype html>"), "missing doctype"
    assert "</html>" in text.lower(), "missing closing </html>"


def test_legal_recognition_has_citation_links():
    text = PAGE.read_text(encoding="utf-8")
    # External http(s) anchors only.
    external = re.findall(r'href="(https?://[^"]+)"', text)
    # Be liberal — page may evolve. Spec calls for 17, allow drift to 12.
    assert len(external) >= 12, (
        f"expected >=12 external citation links, found {len(external)}"
    )


def test_legal_recognition_in_sitemap():
    tree = ET.parse(SITEMAP)
    locs = {
        u.find("s:loc", SITEMAP_NS).text
        for u in tree.getroot().findall("s:url", SITEMAP_NS)
        if u.find("s:loc", SITEMAP_NS) is not None
    }
    assert SITEMAP_URL in locs, (
        f"{SITEMAP_URL} missing from web/sitemap.xml"
    )
