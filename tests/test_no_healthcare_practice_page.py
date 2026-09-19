"""Guards that the withdrawn /practice/ page cannot silently return.

Founder decision 2026-09-19: the company excludes medical/healthcare as a
vertical. web/practice/ was a complete, LIVE, sitemap-listed landing page
addressing medical/healthcare practices (parent-verified: orphograph.com/
practice/ and the Fly origin both answered 200 before this change) — a
second, separate surface from config/verticals/healthcare.yml (no link or
slug between them; see tests/test_no_healthcare_vertical_source.py for that
guard). It is withdrawn here the same way web/inspection/ was withdrawn on
2026-09-18 (commits 730ebe0 + 0c0a607): the directory leaves the tracked
tree, its only copy now lives at
outreach/dormant_verticals/practice/{index.html,index.css} (gitignored,
local-only), the sitemap entry and asset-version pin go with it, and the
withdrawn URL answers 410 Gone (not 404) because it sat in a public sitemap
and a crawler drops a Gone page and its cached snippet far sooner than a
Not Found.

Scope note, same as the healthcare-source guard: this file does NOT scan
tracked text for medical/clinical/patient vocabulary. That vocabulary
legitimately remains in disclaimer boilerplate elsewhere ("not a regulated
medical-records system" on web/press.html, web/what-is-this.html,
web/workpapers/, web/listings/, web/construction/, web/matters/) and in
ordinary English ("bookkeeping practices" on web/workpapers/). This guard
binds only to the withdrawn page's SOURCE, its sitemap listing, and its
route.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import _srv

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

# A phrase from the withdrawn page's body that must never appear again in a
# live response once the route answers 410.
OLD_PAGE_MARKER = "Patient-identifying information"


def test_the_practice_directory_is_not_in_the_tree() -> None:
    assert not (WEB / "practice").exists()


def test_the_static_sitemap_does_not_list_it() -> None:
    sitemap = (WEB / "sitemap.xml").read_text(encoding="utf-8")
    assert "orphograph.com/practice/<" not in sitemap
    assert "<loc>https://orphograph.com/practice/</loc>" not in sitemap


def test_the_asset_manifest_does_not_pin_its_stylesheet() -> None:
    import json
    pins = json.loads((WEB / "asset_versions.json").read_text(encoding="utf-8"))
    assert "/practice/index.css" not in pins


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    yield from _srv.server_processes(tmp_path_factory.mktemp("no-practice"), stub_calendars=True)


@pytest.mark.parametrize("method", ("GET", "HEAD"))
@pytest.mark.parametrize("path", ("/practice/", "/practice", "/practice/index.html"))
def test_the_withdrawn_page_says_gone(base, path, method) -> None:
    """410, not 404: it sat in the sitemap, and a crawler drops a Gone page
    and its cached snippet far sooner than a 404."""
    status, body, headers = _srv.request(base, path, method)
    assert status == 410, (method, path, status)
    assert headers.get("Strict-Transport-Security"), "the error path lost its security headers"
    assert headers.get("Content-Security-Policy"), "the error path lost its CSP header"
    assert OLD_PAGE_MARKER.encode() not in body, "the withdrawn page's own copy leaked into the 410 body"


def test_the_generated_sitemap_does_not_list_it(base) -> None:
    status, body, _ = _srv.request(base, "/sitemap.xml")
    assert status == 200
    assert b"orphograph.com/practice/<" not in body
    assert b"<loc>https://orphograph.com/practice/</loc>" not in body
