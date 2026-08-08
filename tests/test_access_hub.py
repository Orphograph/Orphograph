"""test_access_hub.py

Covers the discoverable "Sign in / access your account" front door added
2026-07-23:

  * GET /access                      — page route serves 200            [http]
  * /access routes BOTH customer types: links to /pack AND /signin      [static]
  * header "Sign in" -> /access is present on the homepage(s) and a
    couple of other shared-header pages                                 [static]
  * /access in sitemap.xml + footer                                     [http/static]
  * CSP-clean: no inline <script>/<style> or on*= handlers on /access   [static]

The static assertions read the shipped HTML directly (fast, no server); the
route/sitemap assertions exercise a real server instance the same way the
pack-access suite does.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"


# ─────────────────────────────── static checks ────────────────────────────

def _read(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


def _header_block(html: str) -> str:
    m = re.search(r'<header[^>]*>.*?</header>|<nav aria-label="Primary">.*?</nav>',
                  html, re.S)
    assert m, "no header/primary-nav block found"
    return m.group(0)


def test_access_page_links_to_both_doors():
    html = _read("access.html")
    assert 'href="/pack"' in html, "access hub must route pack buyers to /pack"
    assert 'href="/signin"' in html, "access hub must route subscribers to /signin"
    # Newcomer path to pricing.
    assert 'href="/pricing"' in html


def test_access_page_is_csp_clean():
    html = _read("access.html")
    # No inline script/style blocks and no inline event handlers (style-src /
    # script-src are 'self'; anything inline is dropped by CSP).
    assert not re.search(r'<script(?![^>]*\bsrc=)[^>]*>', html), "inline <script> present"
    assert "<style" not in html.lower(), "inline <style> present"
    assert not re.search(r'\son\w+\s*=', html), "inline on*= handler present"
    assert 'style="' not in html, "inline style attribute present"


def test_access_page_header_is_brand_lockup_one_cta():
    """2026-08-08: the wordmark-only rule is SUPERSEDED.

    It was introduced in b776d2b as "wordmark-only header and one CTA
    preserved" — a convention carried forward while adding /access, not a
    constraint with a technical reason. The archival design system puts the
    canonical crest at top-left beside the wordmark, sitewide, so the header
    now carries exactly one <img> and that image must be the shipped seal
    artwork rather than a redrawn emblem.

    The one-CTA rule is NOT superseded and is still asserted below: a header
    with two primary buttons has no primary.
    """
    header = _header_block(_read("access.html"))
    assert 'class="orpho-brand__name">Orphograph' in header, "wordmark missing"
    assert 'class="orpho-brand__sub">Empirical Notary' in header, "subtitle missing"
    assert header.count("<img") == 1, "header carries exactly one mark"
    assert "seal-display.png" in header, (
        "the header mark must be the canonical seal artwork, never a redraw")
    # Exactly one primary CTA in the header. Matched on the cta-btn class
    # token rather than an exact class string: the design system prepends
    # orpho-btn utilities, so the attribute is no longer literally
    # class="cta cta-btn".
    assert header.count("cta-btn") == 1


HOMEPAGES = ["index.html", "v2/index.html"]
OTHER_PAGES = ["pricing.html", "pack.html", "faq.html"]


@pytest.mark.parametrize("page", HOMEPAGES + OTHER_PAGES)
def test_header_has_signin_link_to_access(page):
    header = _header_block(_read(page))
    assert 'href="/access"' in header, f"{page} header missing Sign in -> /access"
    # It is a subordinate text link, not a second big CTA button.
    assert 'class="cta cta-btn"' not in _signin_anchor(header), \
        f"{page} Sign in link must not be a primary CTA button"


def _signin_anchor(header: str) -> str:
    m = re.search(r'<a href="/access"[^>]*>.*?</a>', header, re.S)
    return m.group(0) if m else ""


def test_homepage_signin_is_text_link_not_stacked_button():
    # The homepage(s) must keep exactly ONE primary Anchor CTA in the header —
    # the Sign in link must not introduce a second stacked button.
    for page in HOMEPAGES:
        header = _header_block(_read(page))
        anchor = _signin_anchor(header)
        assert anchor, f"{page} missing /access link"
        assert "nav-signin" in anchor, f"{page} Sign in should use nav-signin styling"


def test_footer_links_to_access():
    assert 'href="/access"' in _read("index.html")
    assert 'href="/access"' in _read("v2/index.html")


# ─────────────────────────────── http: route + sitemap ────────────────────

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("access_hub_data")
    port = _free_port()
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "ORPHO_DATA_DIR": str(data_dir),
        "ORPHO_COOKIE_SECURE": "0",
    }
    env.pop("RESEND_API_KEY", None)
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "server" / "app.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def test_access_route_200(server):
    status, body = _get(server + "/access")
    assert status == 200
    assert "Access your account" in body
    assert 'href="/pack"' in body and 'href="/signin"' in body


def test_access_in_sitemap(server):
    status, body = _get(server + "/sitemap.xml")
    assert status == 200
    assert "/access</loc>" in body
