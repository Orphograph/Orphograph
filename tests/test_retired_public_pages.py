"""Retired public pages answer 410 Gone, through a real server.

Founder decisions of 2026-09-28 about public pages, each landed on its own:

  A. /verticals/<slug>.html was rendered from config/verticals/*.yml. In
     production it answered 404 only because the image ships without
     config/; any tree that had config/ served the full pages. Retired: the
     whole /verticals subtree answers 410.

A crawler drops a Gone page and its cached snippet far sooner than a Not
Found, and a link checker reading 410 knows to stop rather than retry. HEAD
runs the GET routing, so a probe that sends HEAD must read the same status and
the same security headers as GET.

The controls are pages that must keep answering, so a change that retired too
much (a prefix that swallowed a live page) turns this red as well.
"""
from __future__ import annotations

import re

import pytest

import _srv

SECURITY_HEADERS = ("Strict-Transport-Security", "X-Content-Type-Options",
                    "X-Frame-Options", "Content-Security-Policy")

RETIRED_PREFIXES = ("/verticals",)

RETIRED = [
    "/verticals",
    "/verticals/",
    # The rendered pages themselves: config/ is present in this test tree, so
    # before the retirement these answered 200 with a full page.
    "/verticals/legal.html",
    "/verticals/accounting.html",
    "/verticals/legal",
    "/verticals/deep/nested/path",
]

LIVE = ("/pricing", "/press-kit")


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    yield from _srv.server_processes(tmp_path_factory.mktemp("retired-pages"),
                                     stub_calendars=True)


def _missing_security_headers(headers) -> list[str]:
    return [h for h in SECURITY_HEADERS if not headers.get(h)]


@pytest.mark.parametrize("method", ("GET", "HEAD"))
@pytest.mark.parametrize("path", RETIRED)
def test_a_retired_page_answers_gone(base, path, method) -> None:
    status, _body, headers = _srv.request(base, path, method)
    assert status == 410, (method, path, status)
    assert _missing_security_headers(headers) == [], (method, path)


@pytest.mark.parametrize("method", ("GET", "HEAD"))
@pytest.mark.parametrize("path", LIVE)
def test_control_a_live_page_still_answers(base, path, method) -> None:
    status, _body, headers = _srv.request(base, path, method)
    assert status == 200, (method, path, status)
    assert _missing_security_headers(headers) == [], (method, path)


def _sitemap_paths(base: str) -> set[str]:
    status, body, _ = _srv.request(base, "/sitemap.xml")
    assert status == 200
    paths = {re.sub(r"^https?://[^/]+", "", loc)
             for loc in re.findall(r"<loc>([^<]+)</loc>", body.decode("utf-8"))}
    # A reader that matched nothing would find no retired path forever.
    assert len(paths) >= 70, f"only {len(paths)} sitemap entries were read"
    return paths


def test_the_served_sitemap_lists_no_retired_page(base) -> None:
    listed = _sitemap_paths(base)
    retired = sorted(p for p in listed
                     if any(p == r or p.startswith((r + "/", r + ".")) for r in RETIRED_PREFIXES))
    assert retired == [], f"the sitemap still lists retired pages: {retired}"
