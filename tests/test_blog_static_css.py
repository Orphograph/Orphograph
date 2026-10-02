"""test_blog_static_css.py — /blog/<slug>.css must serve, not 400.

Live recon 2026-07-18: every /blog/*.css URL (e.g. /blog/index.css?v=1 and
the 17 per-post stylesheets) returned HTTP 400 "invalid slug". The /blog/
dispatcher only special-cased .html; any other dotted path fell through to
the bare-slug validator, whose charset ([a-z0-9-]) rejects dots. Result:
all 18 blog pages referenced a 400ing stylesheet — console errors and
missing blog-specific styling across the whole SEO surface.

Contract under test (server/app.py /blog/ branch):
  1. /blog/<slug>.css serves the static file from web/blog/ as text/css
     with cache headers when the file exists.
  2. A well-formed .css path with no file behind it 404s (NOT 400).
  3. Traversal shapes (../, encoded ../) never reach the static server —
     they fail the slug-charset regex and are rejected cleanly.
  4. The two pre-existing surfaces still work: static .html posts and
     markdown-rendered bare slugs.

Idiom: browserless live-server subprocess (same pattern as
test_buy_btc_funnel.py). The 200 case uses a throwaway fixture stylesheet
written into web/blog/ and removed on teardown, so the test never depends
on (or invents) real post styling.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import _srv

REPO_ROOT = Path(__file__).resolve().parent.parent
BLOG_WEB_DIR = REPO_ROOT / "web" / "blog"

# Slug-charset name so it exercises exactly the route under test; the zz-
# prefix keeps it obviously synthetic and last in directory listings.
FIXTURE_CSS_NAME = "zz-css-route-fixture.css"
FIXTURE_CSS_BODY = "/* test fixture — safe to delete */ .zz-fixture{color:#000}\n"


@pytest.fixture(scope="module")
def fixture_css():
    """Throwaway stylesheet under web/blog/ for the 200 path."""
    target = BLOG_WEB_DIR / FIXTURE_CSS_NAME
    target.write_text(FIXTURE_CSS_BODY, encoding="utf-8")
    try:
        yield FIXTURE_CSS_NAME
    finally:
        target.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def live_server(tmp_path_factory, fixture_css):
    data_dir = tmp_path_factory.mktemp("data")
    yield from _srv.server_processes(
        data_dir, stub_calendars=True, RATE_LIMIT_PER_DAY="100000")


def _get(base: str, path: str):
    """GET returning (status, headers, body) without raising on 4xx/5xx."""
    status, body, headers = _srv.request(base, path, timeout=5)
    return status, headers, body


def _get_page(base: str, path: str):
    """_get, taking the one 301 that moves a `.html` URL to its extensionless
    path. urlopen took that hop unseen; _srv.request never follows a
    redirect, so the page a reader lands on is fetched here."""
    status, headers, body = _get(base, path)
    if status == 301 and headers.get("Location", "").startswith("/"):
        return _get(base, headers["Location"])
    return status, headers, body


def _raw_get(base: str, raw_path: str):
    """GET with the path sent byte-for-byte (no client-side ../ collapsing)."""
    raw = _srv.raw_request(base, raw_path, timeout=5)
    assert raw, "the server closed the connection without answering"
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), body


# ── 1. Existing stylesheet serves as text/css ──────────────────────────────

def test_blog_css_serves_200_text_css(live_server, fixture_css):
    status, headers, body = _get(live_server, f"/blog/{fixture_css}?v=1")
    assert status == 200, f"expected 200 for existing blog css, got {status}"
    assert headers.get("Content-Type", "").startswith("text/css")
    assert body.decode("utf-8") == FIXTURE_CSS_BODY
    assert "max-age" in headers.get("Cache-Control", ""), "static css must be cacheable"


# ── 2. Well-formed but missing stylesheet → 404, never 400 ─────────────────

def test_blog_css_missing_file_404s(live_server):
    status, _, _ = _get(live_server, "/blog/no-such-stylesheet.css")
    assert status == 404, f"missing blog css must 404 (was the 400 bug), got {status}"


# ── 3. Traversal shapes are rejected before the static server ──────────────

def test_blog_css_dot_dot_traversal_blocked(live_server):
    # Raw socket path — urllib would collapse ../ client-side.
    status, body = _raw_get(live_server, "/blog/../index.css")
    assert status == 400, f"../ shape must fail slug validation, got {status}"
    assert b":root" not in body, "must not leak web/index.css content"


def test_blog_css_encoded_traversal_blocked(live_server):
    status, _ = _raw_get(live_server, "/blog/..%2findex.css")
    assert status == 400, f"encoded ../ must fail slug validation, got {status}"


def test_blog_css_inner_dots_rejected(live_server):
    # Dots anywhere before the suffix fail the charset — no probing games.
    status, _, _ = _get(live_server, "/blog/style.css.css")
    assert status == 400


# ── 4. Pre-existing blog surfaces unchanged ────────────────────────────────

def test_blog_static_html_post_still_serves(live_server):
    status, headers, _ = _get_page(live_server, "/blog/prove-you-wrote-it-not-ai.html")
    assert status == 200
    assert headers.get("Content-Type", "").startswith("text/html")


def test_blog_markdown_slug_still_renders(live_server):
    status, headers, body = _get(live_server, "/blog/reading-ots-file-by-hand")
    assert status == 200
    assert headers.get("Content-Type", "").startswith("text/html")
    assert b"<html" in body.lower()


def test_blog_bad_slug_still_400s(live_server):
    status, _, _ = _get(live_server, "/blog/Not_A_Slug!")
    assert status == 400
