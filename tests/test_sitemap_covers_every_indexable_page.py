"""The sitemap must list every page we ask search engines to index, and only those.

Found 2026-09-19: `/pricing` served 200, declared itself canonical, carried no
`noindex`, was linked from 76 pages, and was not in the sitemap. Neither were
14 others, including three `/docs/` pages and four `/legal/` pages.

Why nothing caught it: the sitemap was guarded by three hand-kept lists pinned
to EACH OTHER (the generator's URL list, the committed `web/sitemap.xml`, and
`EXPECTED_URLS` in test_seo.py). A loop of lists is consistent and can still be
wrong, because none of them is compared with the pages that exist. This test
reads the pages.

The rules, all read from the pages themselves:

1. Every HTML page that is indexable (no `noindex`) names a target path, its
   canonical when it has one and the path it is served at when it does not.
   That path is in the sitemap or in `UNLINKED` with a reason.
2. The reverse: every sitemap entry that is a page on disk is indexable, and is
   not a path the server answers Gone. A sitemap that lists a `noindex` page
   contradicts itself (Search Console reports "submitted URL marked noindex");
   `/account` was exactly that.
3. An exemption's reason is CHECKED, not asserted: "no page links to it" fails
   the moment a page does, so the set cannot rot into a second unchecked list.

Reviewed by /code-review high 260 (2026-09-20): the first version read the
robots meta and the canonical link with order-sensitive regexes, skipped every
page with no canonical (so `/lp/start` and the four `/founder/` admin shells
passed while indexable), checked one direction only, and asserted its
exemption reasons in prose.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
sys.path.insert(0, str(ROOT / "server"))

# Indexable pages kept out of the sitemap on purpose. Path -> why. Every one of
# these is verified to have NO inbound link (see `inbound_links`); adding a link
# to one of them is what turns this red, and the answer is then to list it.
UNLINKED = {
    "/one-pager": "no page on the site links to it; promote or noindex is an open founder decision (2026-09-19)",
    "/vs/c2pa": "no page on the site links to it; promote or noindex is an open founder decision (2026-09-19)",
    "/lp/start": "a paid-traffic landing page no other page links to; list it or noindex it is a founder decision (2026-09-20)",
    "/press-kit/orphograph-brand-guide": "no page links to it; list it or give it a canonical is a founder decision (2026-09-20)",
}


class _Page(HTMLParser):
    """What a crawler reads from a page: whether it is a document, the robots
    directive, the canonical link, and the links out. A parser, not a regex, so
    attribute order, quoting and commented-out tags cannot change the answer."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.is_document = False
        self.noindex = False
        self.canonical: str | None = None
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in {"html", "head", "title", "body"}:
            self.is_document = True
        if tag == "meta" and a.get("name", "").lower() in {"robots", "googlebot"}:
            if "noindex" in a.get("content", "").lower():
                self.noindex = True
        if tag == "link" and "canonical" in a.get("rel", "").lower().split():
            if self.canonical is None:
                self.canonical = a.get("href", "")
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])


def _parse(text: str) -> _Page:
    page = _Page()
    page.feed(text)
    page.close()
    return page


def _path_of(url: str) -> str:
    """Site path of a URL: no scheme or host, no query or fragment, no trailing
    slash, no `.html`. A canonical of `/d?utm=1` names `/d`."""
    path = re.sub(r"^https?://[^/]+", "", url.strip())
    path = path.split("#", 1)[0].split("?", 1)[0]
    if path.endswith(".html"):
        path = path[: -len(".html")]
    path = path.rstrip("/") or "/"
    return "/" if path == "/index" else path


def _served_path(rel: str) -> str:
    """The URL a file under web/ is served at."""
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[: -len("/index.html")]
    return "/" + rel[: -len(".html")]


def _server():
    import app as _app
    return _app


def _not_served(rel: str, served: str) -> bool:
    """Paths the server answers 404 or Gone, whatever the file says."""
    a = _server()
    return bool(a._is_private_path(rel) or a._is_withdrawn_path(served)
                or a._is_retired_btc_path(served))


def _pages(web_dir: Path):
    for f in sorted(web_dir.rglob("*.html")):
        rel = f.relative_to(web_dir).as_posix()
        page = _parse(f.read_text(encoding="utf-8", errors="replace"))
        if page.is_document:
            yield rel, _served_path(rel), page


def indexable_targets(web_dir: Path) -> dict[str, str]:
    """{path a crawler is asked to index: first file that names it}."""
    out: dict[str, str] = {}
    for rel, served, page in _pages(web_dir):
        if page.noindex or _not_served(rel, served):
            continue
        target = _path_of(page.canonical) if page.canonical else served
        out.setdefault(target, rel)
    return out


def sitemap_paths(xml_text: str) -> set[str]:
    return {_path_of(e.text) for e in ET.fromstring(xml_text).iter()
            if e.tag.endswith("loc") and e.text}


def unlisted(web_dir: Path, xml_text: str) -> dict[str, str]:
    listed = sitemap_paths(xml_text)
    return {p: f for p, f in indexable_targets(web_dir).items() if p not in listed}


def contradictions(web_dir: Path, xml_text: str) -> tuple[dict[str, str], int]:
    """Sitemap entries that are pages on disk yet must not be listed: noindex, or
    a path the server answers Gone. Returns (offenders, how many were checked),
    so a guard that resolved nothing cannot pass."""
    by_path = {}
    for rel, served, page in _pages(web_dir):
        by_path.setdefault(served, (rel, page))
    offenders: dict[str, str] = {}
    checked = 0
    for path in sorted(sitemap_paths(xml_text)):
        hit = by_path.get(path)
        if hit is None:
            continue  # served by a route, not a file: feeds, txt, zip, rendered posts
        rel, page = hit
        checked += 1
        if page.noindex:
            offenders[path] = "its page says noindex"
        elif _not_served(rel, path):
            offenders[path] = "the server answers it Gone or not found"
    return offenders, checked


def inbound_links(web_dir: Path, server_dir: Path, path: str) -> list[str]:
    """Files that link to `path`, other than the page itself: HTML under web/ and
    hrefs written in the server's own templates."""
    found: list[str] = []
    for rel, served, page in _pages(web_dir):
        if served == path:
            continue
        if any(_path_of(h) == path for h in page.links if h.startswith(("/", "http"))):
            found.append(f"web/{rel}")
    literal = re.compile(r"href=[\"'](?:https?://[^/\"']+)?(" + re.escape(path) + r")(?:\.html)?[/\"'?#]")
    for f in sorted(server_dir.rglob("*.py")):
        if literal.search(f.read_text(encoding="utf-8", errors="replace")):
            found.append(f"server/{f.relative_to(server_dir).as_posix()}")
    return found


def _served_sitemap() -> str:
    return _server()._build_sitemap()


def _static_sitemap() -> str:
    return (WEB / "sitemap.xml").read_text(encoding="utf-8")


LISTS = pytest.mark.parametrize("read", [_served_sitemap, _static_sitemap],
                                ids=["served-by-the-server", "committed-static-file"])


# --- 1. every indexable page is listed ----------------------------------------

@LISTS
def test_every_indexable_page_is_in_the_sitemap(read):
    missing = {p: f for p, f in unlisted(WEB, read()).items() if p not in UNLINKED}
    assert missing == {}, (
        "indexable pages the sitemap does not list (add them to _build_sitemap and "
        f"web/sitemap.xml, or mark them noindex): {missing}")


# --- 2. and nothing listed contradicts itself ---------------------------------

@LISTS
def test_no_sitemap_entry_is_a_noindex_or_gone_page(read):
    offenders, checked = contradictions(WEB, read())
    assert offenders == {}, f"the sitemap lists pages that must not be listed: {offenders}"
    assert checked >= 70, f"only {checked} sitemap entries resolved to a page: the check is not looking"


# --- 3. the exemptions are true -----------------------------------------------

def test_every_exemption_is_still_true():
    still_unlisted = unlisted(WEB, _served_sitemap())
    stale = sorted(p for p in UNLINKED if p not in still_unlisted)
    assert stale == [], f"exempt paths that are now listed, noindex, or gone: {stale}"


@pytest.mark.parametrize("path", sorted(UNLINKED))
def test_an_exempt_page_really_has_no_inbound_link(path):
    """"No page links to it" is the stated reason. When one does, the reason is
    false and the page is a linked, indexable, unlisted page: the /pricing defect."""
    linkers = inbound_links(WEB, ROOT / "server", path)
    assert linkers == [], f"{path} is linked from {linkers}: list it in the sitemap"


# --- the guard is looking at something ----------------------------------------

def test_the_scan_reads_a_real_domain():
    """A parser that stopped matching would report nothing missing forever."""
    targets = indexable_targets(WEB)
    assert len(targets) >= 80, f"only {len(targets)} indexable pages found"
    assert "/" in targets and "/pricing" in targets
    assert "/lp/start" in targets, "a page with no canonical must be read by its served path"
    assert "/founder/admin" not in targets and "/account" not in targets, "noindex pages are excluded"


# --- controls: plant the defect, see it caught ---------------------------------

def _write(dirpath: Path, name: str, html: str) -> None:
    f = dirpath / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("<!doctype html><html><head>" + html + "</head><body></body></html>")


_XML = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://orphograph.com/listed</loc></url></urlset>")


def test_control_a_planted_page_is_caught(tmp_path):
    _write(tmp_path, "listed.html", '<link rel="canonical" href="https://orphograph.com/listed">')
    _write(tmp_path, "forgotten.html", '<link rel="canonical" href="https://orphograph.com/forgotten">')
    _write(tmp_path, "private.html", '<meta name="robots" content="noindex">'
                                     '<link rel="canonical" href="https://orphograph.com/private">')
    assert unlisted(tmp_path, _XML) == {"/forgotten": "forgotten.html"}


def test_control_attribute_order_comments_and_queries_do_not_fool_the_reader(tmp_path):
    # Reversed attribute order: still a canonical, still unlisted.
    _write(tmp_path, "reversed.html", '<link href="https://orphograph.com/reversed" rel="canonical">')
    # Reversed order noindex: NOT indexable, so not reported.
    _write(tmp_path, "hidden.html", '<meta content="noindex, follow" name="robots">'
                                    '<link rel="canonical" href="https://orphograph.com/hidden">')
    # A noindex that only lives in a comment does not make the page noindex.
    _write(tmp_path, "commented.html", '<!-- <meta name="robots" content="noindex"> -->'
                                       '<link rel="canonical" href="https://orphograph.com/commented">')
    # A canonical carrying a query names the path, not the query.
    _write(tmp_path, "tracked.html", '<link rel="canonical" href="https://orphograph.com/tracked?utm=1#top">')
    assert unlisted(tmp_path, _XML) == {"/reversed": "reversed.html",
                                        "/commented": "commented.html",
                                        "/tracked": "tracked.html"}


def test_control_a_page_with_no_canonical_is_read_by_the_path_it_is_served_at(tmp_path):
    _write(tmp_path, "no-canonical.html", "<title>x</title>")
    _write(tmp_path, "deep/index.html", "<title>y</title>")
    _write(tmp_path, "index.html", "<title>home</title>")
    assert unlisted(tmp_path, _XML) == {
        "/no-canonical": "no-canonical.html", "/deep": "deep/index.html", "/": "index.html"}


def test_control_a_listed_noindex_page_is_a_contradiction(tmp_path):
    _write(tmp_path, "account.html", '<meta name="robots" content="noindex">')
    _write(tmp_path, "listed.html", '<link rel="canonical" href="https://orphograph.com/listed">')
    xml = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           "<url><loc>https://orphograph.com/account</loc></url>"
           "<url><loc>https://orphograph.com/listed</loc></url>"
           "<url><loc>https://orphograph.com/feed.xml</loc></url></urlset>")
    offenders, checked = contradictions(tmp_path, xml)
    assert offenders == {"/account": "its page says noindex"}
    assert checked == 2, "the route-served entry is skipped, the two pages are read"


def test_control_a_link_to_an_exempt_page_is_found(tmp_path):
    _write(tmp_path, "target.html", "<title>t</title>")
    _write(tmp_path, "hub.html", '<a href="/target">t</a><a href="https://orphograph.com/other?x=1">o</a>')
    server = tmp_path / "srv"
    server.mkdir()
    (server / "tpl.py").write_text('HTML = \'<a href="/from-python">x</a>\'\n')
    assert inbound_links(tmp_path, server, "/target") == ["web/hub.html"]
    assert inbound_links(tmp_path, server, "/from-python") == ["server/tpl.py"]
    assert inbound_links(tmp_path, server, "/other") == ["web/hub.html"]
    assert inbound_links(tmp_path, server, "/nobody") == []
