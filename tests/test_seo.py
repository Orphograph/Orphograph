"""test_seo.py — SEO scaffolding (sitemap, robots, head meta) integrity.

Verifies the static SEO surface area:
  - web/sitemap.xml parses as XML and covers every listed public page
  - web/robots.txt has the Sitemap line and disallows the private prefixes
  - every web/method/*.html has the four required head meta tags
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

# Canonical public-page set. Kept in lockstep with web/sitemap.xml.
EXPECTED_URLS = [
    "https://orphograph.com/",
    "https://orphograph.com/verify/",
    "https://orphograph.com/blog/",
    "https://orphograph.com/learn",
    "https://orphograph.com/dataset-provenance",
    "https://orphograph.com/integrations",
    "https://orphograph.com/accept",
    "https://orphograph.com/standing-record",
    "https://orphograph.com/blog/prove-what-was-in-your-training-set",
    "https://orphograph.com/buy",
    "https://orphograph.com/lp/",
    "https://orphograph.com/about",
    "https://orphograph.com/faq",
    "https://orphograph.com/verify-js",
    "https://orphograph.com/lp/prove-photo-pre-ai",
    "https://orphograph.com/lp/bitcoin-timestamp-file",
    "https://orphograph.com/lp/c2pa-alternative",
    "https://orphograph.com/lp/opentimestamps-explained",
    "https://orphograph.com/lp/wedding-photographer-proof",
    "https://orphograph.com/lp/manuscript-priority-date",
    "https://orphograph.com/lp/screenshot-evidence-timestamp",
    "https://orphograph.com/lp/ai-image-detector-vs-provenance",
    "https://orphograph.com/lp/eu-ai-act-training-data",
    "https://orphograph.com/lp/agent-receipts",
    "https://orphograph.com/method/architecture",
    "https://orphograph.com/method/bitcoin-attestation",
    "https://orphograph.com/method/evidence-law",
    "https://orphograph.com/method/folder-merkle",
    "https://orphograph.com/method/legal-recognition",
    "https://orphograph.com/method/the-mit-verifier-annotated",
    "https://orphograph.com/method/whitepaper",
    "https://orphograph.com/method/why-filenames-are-not-stored",
    "https://orphograph.com/docs",
    "https://orphograph.com/docs/agents",
    "https://orphograph.com/docs/api",
    "https://orphograph.com/docs/webhooks",
    "https://orphograph.com/docs/cli",
    "https://orphograph.com/docs/sdk",
    "https://orphograph.com/stats",
    "https://orphograph.com/gift",
    "https://orphograph.com/status",
    "https://orphograph.com/security",
    "https://orphograph.com/continuity",
    "https://orphograph.com/ios",
    "https://orphograph.com/mcp",
    "https://orphograph.com/badge",
    "https://orphograph.com/badge-demo",
    "https://orphograph.com/press",
    "https://orphograph.com/press-kit",
    "https://orphograph.com/roadmap",
    "https://orphograph.com/changelog",
    "https://orphograph.com/account",
    "https://orphograph.com/access",
    "https://orphograph.com/pack",
    "https://orphograph.com/construction/",
    "https://orphograph.com/inspection/",
    "https://orphograph.com/listings/",
    "https://orphograph.com/matters/",
    "https://orphograph.com/practice/",
    "https://orphograph.com/workpapers/",
    "https://orphograph.com/blog/atom.xml",
    "https://orphograph.com/blog/rss.xml",
    "https://orphograph.com/signin",
    "https://orphograph.com/recover",
    "https://orphograph.com/terms",
    "https://orphograph.com/privacy",
    "https://orphograph.com/legal/",
    "https://orphograph.com/.well-known/security.txt",
    "https://orphograph.com/humans.txt",
    "https://orphograph.com/sitemap-image.xml",
    "https://orphograph.com/press-kit/orphograph-press-kit.zip",
    "https://orphograph.com/blog/bitcoin-block-height-as-source-of-truth",
    "https://orphograph.com/blog/bitcoin-merkle-roots-unforgeable-timestamps",
    "https://orphograph.com/blog/date-stamp-a-document-permanently",
    "https://orphograph.com/blog/digital-notary-vs-cryptographic-timestamp",
    "https://orphograph.com/blog/how-to-prove-photo-existed-before-ai-model-released",
    "https://orphograph.com/blog/opentimestamps-for-non-developers",
    "https://orphograph.com/blog/prove-a-contract-existed-before-a-date",
    "https://orphograph.com/blog/prove-a-photo-was-not-edited",
    "https://orphograph.com/blog/prove-code-existed-before-a-competitors-commit",
    "https://orphograph.com/blog/prove-i-created-something-before-someone-else",
    "https://orphograph.com/blog/prove-photo-existed-before-ai",
    "https://orphograph.com/blog/prove-you-wrote-it-not-ai",
    "https://orphograph.com/blog/reading-ots-file-by-hand",
    "https://orphograph.com/blog/what-makes-a-digital-timestamp-legally-defensible",
    "https://orphograph.com/blog/why-5-opentimestamps-calendars-not-1",
    "https://orphograph.com/blog/why-domain-dying-doesnt-kill-your-timestamp",
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


def test_sitemap_static_file_matches_generator() -> None:
    """web/sitemap.xml is a committed snapshot of the SERVED sitemap, which the
    server generates via _build_sitemap(). Locs and priorities must match
    exactly (lastmod is file-mtime-derived and excluded). If this fails, the
    generator's URL list changed: regenerate the static file from /sitemap.xml.
    """
    import sys as _sys
    server_dir = ROOT / "server"
    if str(server_dir) not in _sys.path:
        _sys.path.insert(0, str(server_dir))
    import app as _app

    def _pairs(xml: str) -> list[tuple[str, str]]:
        return re.findall(r"<loc>([^<]+)</loc>\s*(?:<lastmod>[^<]*</lastmod>\s*)?<priority>([^<]+)</priority>", xml)

    static_pairs = _pairs((WEB / "sitemap.xml").read_text(encoding="utf-8"))
    generated_pairs = _pairs(_app._build_sitemap())
    assert static_pairs == generated_pairs, (
        "web/sitemap.xml drifted from server _build_sitemap() — regenerate it"
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


# start.html is an ad-landing variant: deliberately non-canonical and absent
# from the sitemap, so it is exempt from the meta requirements below.
LP_FILES = sorted(p for p in (WEB / "lp").glob("*.html") if p.name != "start.html")


@pytest.mark.parametrize("html_path", LP_FILES, ids=lambda p: p.name)
def test_lp_pages_have_required_meta(html_path: Path) -> None:
    """Each lp/*.html page must carry canonical, og:title, and description."""
    head = html_path.read_text(encoding="utf-8").split("</head>", 1)[0]
    for fragment in ('<link rel="canonical"', '<meta property="og:title"', '<meta name="description"'):
        assert fragment in head, f"{html_path.name} head is missing: {fragment}"


# CSP is style-src 'self': inline <style> blocks and style= attributes are
# silently dropped by the browser. Every lp page (start.html included) is now
# clean — pin the whole directory so new pages can never regress to inline
# styles. Note: even CSP-legal SVG presentation attributes like font-style=
# must be expressed as classes here, since this check matches the substring.
CSP_CLEAN_LP = sorted((WEB / "lp").glob("*.html"))


@pytest.mark.parametrize("html_path", CSP_CLEAN_LP, ids=lambda p: p.name)
def test_lp_pages_no_inline_styles(html_path: Path) -> None:
    """CSP-clean lp pages must not regress to inline styles."""
    body = html_path.read_text(encoding="utf-8")
    assert "<style" not in body, f"{html_path.name} has an inline <style> block"
    assert 'style="' not in body, f"{html_path.name} has inline style= attributes"


# All blog pages were cleaned in one pass (fix/blog-inline-css), so the
# whole directory is enforced by glob — a new post with inline styles
# fails here on arrival instead of shipping unstyled under the CSP.
CSP_CLEAN_BLOG = sorted((WEB / "blog").glob("*.html"))


def test_blog_dir_has_pages() -> None:
    """Guard the glob above: an empty list would silently skip enforcement."""
    assert len(CSP_CLEAN_BLOG) >= 17, "web/blog/*.html glob came back near-empty"


@pytest.mark.parametrize("html_path", CSP_CLEAN_BLOG, ids=lambda p: p.name)
def test_blog_pages_no_inline_styles(html_path: Path) -> None:
    """CSP-clean blog pages must not regress to inline styles."""
    body = html_path.read_text(encoding="utf-8")
    assert "<style" not in body, f"{html_path.name} has an inline <style> block"
    assert 'style="' not in body, f"{html_path.name} has inline style= attributes"


# The pay pages are the money surface: under style-src 'self' an inline style
# renders the checkout unstyled, which reads as a broken payment page. #121
# cleaned web/pay/ but could not add a guard (this file was owned by #122 at
# the time). Every page in the directory — btc, crypto and success — was
# re-checked as clean, so the whole directory is pinned by glob, matching the
# lp/ and blog/ pattern above: a new pay page with inline styles fails here on
# arrival instead of shipping broken.
CSP_CLEAN_PAY = sorted((WEB / "pay").glob("*.html"))


def test_pay_dir_has_pages() -> None:
    """Guard the glob above: an empty list would silently skip enforcement."""
    assert len(CSP_CLEAN_PAY) >= 3, "web/pay/*.html glob came back near-empty"


@pytest.mark.parametrize("html_path", CSP_CLEAN_PAY, ids=lambda p: p.name)
def test_pay_pages_no_inline_styles(html_path: Path) -> None:
    """CSP-clean pay pages must not regress to inline styles."""
    body = html_path.read_text(encoding="utf-8")
    assert "<style" not in body, f"{html_path.name} has an inline <style> block"
    assert 'style="' not in body, f"{html_path.name} has inline style= attributes"


# CSP is script-src 'self': an inline <script> block is silently discarded
# by the browser, so any page whose behaviour depends on one is dead in
# production. /recover is the payment-recovery surface (its form handler was
# inline and never ran); /verify-js is the public "verify your receipt in
# your browser" trust surface (its entire verifier was inline and never ran).
# Pages listed here are externalized; the check fails any regression back to
# an inline block. Add a page here once it is clean (a page still carrying an
# inline block must not be listed).
# Unlike the style checks above this one is NOT deliberately over-strict:
# a <script type="application/ld+json"> data block is never executed and is
# not subject to script-src, so structured data (FAQPage markup on the blog,
# /lp/, index.html, faq.html, dataset-provenance.html, and the question list
# on /recover) stays legal and is excluded below.
# The seven paths below were externalized by fix/inline-script-sweep (#119)
# and fix/pay-pages-csp (#121) but landed while this file was owned by #122,
# so they shipped clean and unguarded. Each was re-checked as clean before
# being listed here, per the rule in the paragraph above.
CSP_CLEAN_SCRIPT = [
    "recover.html",
    "verify-js.html",
    "team/join.html",
    "founder/admin.html",
    "founder/funnel.html",
    "founder/metrics.html",
    "founder/support.html",
    "pay/btc.html",
    "pay/crypto.html",
]

_INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\ssrc=)[^>]*>", re.IGNORECASE)


@pytest.mark.parametrize("page_name", CSP_CLEAN_SCRIPT)
def test_pages_no_inline_script(page_name: str) -> None:
    """CSP-clean pages must not regress to an inline <script> block."""
    path = WEB / page_name
    assert path.is_file(), f"{page_name} is missing from web/"
    body = path.read_text(encoding="utf-8")
    hits = [h for h in _INLINE_SCRIPT_RE.findall(body) if "ld+json" not in h.lower()]
    assert not hits, f"{page_name} has an inline <script> block: {hits}"


def test_inline_script_check_catches_a_real_inline_block() -> None:
    """Guard the pin above: prove the matcher fires on the pattern this
    PR removed, so the check can never degrade into a silent no-op."""
    assert _INLINE_SCRIPT_RE.findall('<script>\n(function(){})();\n</script>')
    assert _INLINE_SCRIPT_RE.findall('<script type="text/javascript">x=1</script>')
    assert not _INLINE_SCRIPT_RE.findall('<script src="/recover.js?v=1"></script>')
    assert not _INLINE_SCRIPT_RE.findall('<script src="/verify-js.js?v=1"></script>')
    # ld+json is data, not script-src-governed: the filter must let it pass.
    assert not [
        h for h in _INLINE_SCRIPT_RE.findall(
            '<script type="application/ld+json">{"@type":"FAQPage"}</script>'
        )
        if "ld+json" not in h.lower()
    ]


def test_recover_assets_are_pinned() -> None:
    """The externalized recover assets must carry ?v= pins (Cloudflare
    serves an unpinned asset stale for 24h)."""
    body = (WEB / "recover.html").read_text(encoding="utf-8")
    assert '<script src="/recover.js?v=' in body, "recover.js reference is missing or unpinned"
    assert '<link rel="stylesheet" href="/recover.css?v=' in body, "recover.css reference is unpinned"
    assert (WEB / "recover.js").is_file(), "web/recover.js is missing"


def test_verify_js_assets_are_pinned() -> None:
    """The externalized verify-js assets must carry ?v= pins (Cloudflare
    serves an unpinned asset stale for 24h)."""
    body = (WEB / "verify-js.html").read_text(encoding="utf-8")
    assert '<script src="/verify-js.js?v=' in body, "verify-js.js reference is missing or unpinned"
    assert '<link rel="stylesheet" href="/verify-js.css?v=' in body, "verify-js.css reference is unpinned"
    assert (WEB / "verify-js.js").is_file(), "web/verify-js.js is missing"


# ── launch LP: full social-card pin ────────────────────────────────────
# /lp/agent-receipts is a primary share target (HN / Reddit / X). Reddit
# renders og:*; X requires twitter:card to render any card at all. Pin the
# complete set so a head edit cannot silently degrade the share preview.

AGENT_RECEIPTS_LP = WEB / "lp" / "agent-receipts.html"


def test_agent_receipts_lp_full_social_card() -> None:
    head = AGENT_RECEIPTS_LP.read_text(encoding="utf-8").split("</head>", 1)[0]
    required = [
        '<link rel="canonical" href="https://orphograph.com/lp/agent-receipts">',
        '<meta property="og:title"',
        '<meta property="og:description"',
        '<meta property="og:image" content="https://orphograph.com/og-image.png',
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="twitter:title"',
        '<meta name="twitter:description"',
        '<meta name="twitter:image" content="https://orphograph.com/og-image.png',
    ]
    for fragment in required:
        assert fragment in head, f"agent-receipts.html head is missing: {fragment}"


def test_agent_receipts_lp_twitter_title_matches_title() -> None:
    """Same convention as method pages: twitter:title echoes <title>."""
    head = AGENT_RECEIPTS_LP.read_text(encoding="utf-8").split("</head>", 1)[0]
    start = head.find("<title>")
    end = head.find("</title>", start)
    assert start != -1 and end != -1, "agent-receipts.html has no <title>"
    page_title = head[start + len("<title>"):end].strip()
    needle = f'<meta name="twitter:title" content="{page_title}">'
    assert needle in head, f"twitter:title does not match page <title> {page_title!r}"
