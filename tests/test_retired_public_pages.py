"""Retired public pages answer 410 Gone, and a page kept out of search says
so, through a real server.

Founder decisions of 2026-09-28 about public pages, each landed on its own:

  A. /verticals/<slug>.html was rendered from config/verticals/*.yml. In
     production it answered 404 only because the image ships without
     config/; any tree that had config/ served the full pages. Retired: the
     whole /verticals subtree answers 410.
  B. /lp/start is a paid-traffic landing page nothing links to. It keeps
     working, but asks not to be indexed twice over: a robots meta tag for
     crawlers that read the page and an X-Robots-Tag header for those that
     only read headers (and for HEAD, which has no page to read).
  C. /one-pager and /vs/c2pa: nothing linked to either. Retired, their files
     deleted; the page, its trailing-slash and .html spellings and its
     stylesheet all answer 410.
  D. /press-kit/orphograph-brand-guide was indexable and linked from
     nothing. The press kit page links it and the sitemap lists it.

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

RETIRED_PREFIXES = ("/verticals", "/one-pager", "/vs/c2pa")

RETIRED = [
    "/verticals",
    "/verticals/",
    # The rendered pages themselves: config/ is present in this test tree, so
    # before the retirement these answered 200 with a full page.
    "/verticals/legal.html",
    "/verticals/accounting.html",
    "/verticals/legal",
    "/verticals/deep/nested/path",
    # Each page in every spelling the static handler used to answer: the
    # clean URL, a trailing slash, the .html form (it 301ed to the clean URL)
    # and the stylesheet that sat beside it with a ?v= pin.
    "/one-pager", "/one-pager/", "/one-pager.html", "/one-pager.css",
    "/vs/c2pa", "/vs/c2pa/", "/vs/c2pa.html", "/vs/c2pa.css",
]

# /lp/c2pa-alternative shares characters with a retired page and is a live,
# listed page: a match that was too loose would retire it too.
LIVE = ("/pricing", "/press-kit", "/lp/c2pa-alternative")

# Served, but not to be indexed. The trailing-slash spelling resolves to the
# same file, so it must carry the same answer.
NOINDEX = ("/lp/start", "/lp/start/")
NOINDEX_META = re.compile(r'<meta\s+name="robots"\s+content="noindex"\s*/?>', re.I)


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


@pytest.mark.parametrize("method", ("GET", "HEAD"))
@pytest.mark.parametrize("path", NOINDEX)
def test_the_paid_traffic_landing_page_asks_not_to_be_indexed(base, path, method) -> None:
    status, body, headers = _srv.request(base, path, method)
    assert status == 200, (method, path, status)
    assert headers.get("X-Robots-Tag") == "noindex", (method, path, headers.get("X-Robots-Tag"))
    assert _missing_security_headers(headers) == [], (method, path)
    if method == "GET":
        assert NOINDEX_META.search(body.decode("utf-8")), "the page lost its robots meta tag"


def test_a_revalidated_landing_page_keeps_the_header(base) -> None:
    """A crawler that cached the page revalidates with If-None-Match and gets
    a 304. The 304 must not drop the directive the 200 carried."""
    status, _body, headers = _srv.request(base, "/lp/start")
    assert status == 200 and headers.get("ETag")
    status, _body, headers = _srv.request(base, "/lp/start",
                                          headers={"If-None-Match": headers["ETag"]})
    assert status == 304, status
    assert headers.get("X-Robots-Tag") == "noindex"


def test_control_an_indexable_landing_page_carries_no_robots_header(base) -> None:
    """The header belongs to /lp/start alone. Sent anywhere else it would
    quietly pull an indexed page out of search."""
    for path in ("/lp/agent-receipts", "/pricing", "/"):
        status, _body, headers = _srv.request(base, path)
        assert status == 200, (path, status)
        assert headers.get("X-Robots-Tag") is None, (path, headers.get("X-Robots-Tag"))


def _sitemap_paths(base: str) -> set[str]:
    status, body, _ = _srv.request(base, "/sitemap.xml")
    assert status == 200
    paths = {re.sub(r"^https?://[^/]+", "", loc)
             for loc in re.findall(r"<loc>([^<]+)</loc>", body.decode("utf-8"))}
    # A reader that matched nothing would find no retired path forever.
    assert len(paths) >= 70, f"only {len(paths)} sitemap entries were read"
    return paths


BRAND_GUIDE = "/press-kit/orphograph-brand-guide"


def test_the_press_kit_page_links_the_brand_guide(base) -> None:
    status, body, _ = _srv.request(base, "/press-kit")
    assert status == 200
    hrefs = re.findall(r'<a\b[^>]*\bhref="([^"]+)"', body.decode("utf-8"))
    assert BRAND_GUIDE in hrefs, "the press kit page does not link the brand guide"
    status, _body, headers = _srv.request(base, BRAND_GUIDE)
    assert status == 200, status
    assert headers.get("X-Robots-Tag") is None


def test_the_served_sitemap_lists_the_brand_guide(base) -> None:
    assert BRAND_GUIDE in _sitemap_paths(base)


def test_the_served_sitemap_lists_no_retired_page(base) -> None:
    listed = _sitemap_paths(base)
    retired = sorted(p for p in listed
                     if any(p == r or p.startswith((r + "/", r + ".")) for r in RETIRED_PREFIXES))
    assert retired == [], f"the sitemap still lists retired pages: {retired}"
    noindexed = sorted(p for p in listed if p.rstrip("/") in {n.rstrip("/") for n in NOINDEX})
    assert noindexed == [], f"the sitemap lists a noindex page: {noindexed}"
