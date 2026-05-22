"""test_seo.py — SEO scaffolding (sitemap, robots, head meta) integrity.

Verifies the static SEO surface area:
  - web/sitemap.xml parses as XML and covers every listed public page
  - web/robots.txt has the Sitemap line and disallows the private prefixes
  - every web/method/*.html has the four required head meta tags
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

# Canonical public-page set. Kept in lockstep with web/sitemap.xml.
EXPECTED_URLS = [
    "https://orphograph.com/",
    "https://orphograph.com/about.html",
    "https://orphograph.com/buy.html",
    "https://orphograph.com/account.html",
    "https://orphograph.com/faq.html",
    "https://orphograph.com/learn.html",
    "https://orphograph.com/legal/",
    "https://orphograph.com/changelog.html",
    "https://orphograph.com/continuity.html",
    "https://orphograph.com/recover.html",
    "https://orphograph.com/signin.html",
    "https://orphograph.com/status.html",
    "https://orphograph.com/roadmap.html",
    "https://orphograph.com/ios.html",
    "https://orphograph.com/mcp.html",
    "https://orphograph.com/badge.html",
    "https://orphograph.com/verify-js.html",
    "https://orphograph.com/method/folder-merkle.html",
    "https://orphograph.com/method/evidence-law.html",
    "https://orphograph.com/method/architecture.html",
    "https://orphograph.com/method/bitcoin-attestation.html",
    "https://orphograph.com/method/the-mit-verifier-annotated.html",
    "https://orphograph.com/method/why-filenames-are-not-stored.html",
    "https://orphograph.com/construction/",
    "https://orphograph.com/inspection/",
    "https://orphograph.com/matters/",
    "https://orphograph.com/listings/",
    "https://orphograph.com/practice/",
    "https://orphograph.com/workpapers/",
    "https://orphograph.com/docs/api.html",
    "https://orphograph.com/docs/webhooks.html",
    "https://orphograph.com/blog/",
    "https://orphograph.com/blog/prove-a-photo-was-not-edited.html",
    "https://orphograph.com/blog/date-stamp-a-document-permanently.html",
    "https://orphograph.com/blog/prove-i-created-something-before-someone-else.html",
    "https://orphograph.com/blog/prove-you-wrote-it-not-ai.html",
]

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def _parse_sitemap_locs() -> list[str]:
    tree = ET.parse(WEB / "sitemap.xml")
    root = tree.getroot()
    return [loc.text.strip() for loc in root.findall(f"{SITEMAP_NS}url/{SITEMAP_NS}loc") if loc.text]


def test_sitemap_parses_as_xml() -> None:
    """The sitemap must be well-formed XML — search engines reject otherwise."""
    tree = ET.parse(WEB / "sitemap.xml")
    assert tree.getroot().tag == f"{SITEMAP_NS}urlset"


def test_sitemap_contains_every_expected_url() -> None:
    """Every public page in the EXPECTED_URLS set must appear in the sitemap."""
    locs = _parse_sitemap_locs()
    locs_set = set(locs)
    missing = [u for u in EXPECTED_URLS if u not in locs_set]
    assert not missing, f"Sitemap is missing: {missing}"


def test_sitemap_count_matches_expected() -> None:
    """The sitemap should list exactly the canonical public pages — no more, no less."""
    locs = _parse_sitemap_locs()
    assert len(locs) == len(EXPECTED_URLS), (
        f"Expected {len(EXPECTED_URLS)} URLs, found {len(locs)}"
    )


def test_sitemap_lastmod_present() -> None:
    """Every <url> entry must carry a <lastmod> so crawlers can prioritise."""
    tree = ET.parse(WEB / "sitemap.xml")
    root = tree.getroot()
    for url_node in root.findall(f"{SITEMAP_NS}url"):
        lastmod = url_node.find(f"{SITEMAP_NS}lastmod")
        loc = url_node.find(f"{SITEMAP_NS}loc")
        assert lastmod is not None and lastmod.text, (
            f"Missing <lastmod> for {loc.text if loc is not None else '?'}"
        )


def test_robots_has_sitemap_line() -> None:
    """robots.txt must advertise the sitemap so crawlers can find it."""
    body = (WEB / "robots.txt").read_text(encoding="utf-8")
    assert "Sitemap: https://orphograph.com/sitemap.xml" in body


def test_robots_disallows_private_prefixes() -> None:
    """The private/API prefixes must be excluded from crawling."""
    body = (WEB / "robots.txt").read_text(encoding="utf-8")
    for prefix in ("/api/", "/r/", "/a/", "/data/", "/admin/"):
        assert f"Disallow: {prefix}" in body, f"robots.txt missing Disallow: {prefix}"


METHOD_FILES = sorted((WEB / "method").glob("*.html"))


@pytest.mark.parametrize("html_path", METHOD_FILES, ids=lambda p: p.name)
def test_method_pages_have_required_meta(html_path: Path) -> None:
    """Each method/*.html page must carry the four SEO meta tags."""
    body = html_path.read_text(encoding="utf-8")
    head = body.split("</head>", 1)[0]
    required_fragments = [
        '<meta property="og:type" content="article">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="twitter:title"',
        '<link rel="canonical"',
    ]
    for fragment in required_fragments:
        assert fragment in head, f"{html_path.name} head is missing: {fragment}"


@pytest.mark.parametrize("html_path", METHOD_FILES, ids=lambda p: p.name)
def test_method_pages_twitter_title_matches_title(html_path: Path) -> None:
    """The twitter:title must echo the page <title> — duplicate-title drift causes social-card mismatches."""
    body = html_path.read_text(encoding="utf-8")
    head = body.split("</head>", 1)[0]
    # Extract <title>...</title>
    start = head.find("<title>")
    end = head.find("</title>", start)
    assert start != -1 and end != -1, f"{html_path.name} has no <title>"
    page_title = head[start + len("<title>"):end].strip()
    # twitter:title must contain the same string
    needle = f'<meta name="twitter:title" content="{page_title}">'
    assert needle in head, (
        f"{html_path.name} twitter:title does not match page <title> {page_title!r}"
    )
