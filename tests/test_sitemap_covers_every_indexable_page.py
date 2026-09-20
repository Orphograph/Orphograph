"""The sitemap must list every page we ask search engines to index.

Found 2026-09-19: `/pricing` served 200, declared itself canonical, carried no
`noindex`, was linked from 76 pages, and was not in the sitemap. Neither were
14 others, including three `/docs/` pages and four `/legal/` pages.

Why nothing caught it: the sitemap was guarded by three hand-kept lists pinned
to EACH OTHER (the generator's URL list, the committed `web/sitemap.xml`, and
`EXPECTED_URLS` in test_seo.py). A loop of lists is consistent and can still be
wrong, because none of them is compared with the pages that exist. This test
reads the pages.

The rule: every HTML file under `web/` that is indexable (no `noindex`) names a
canonical URL, and that URL's path is either in the sitemap or in
`DELIBERATELY_UNLISTED` with a reason. An exemption that stops being true fails
too, so the set cannot rot into a second unchecked list.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
sys.path.insert(0, str(ROOT / "server"))

# Indexable pages kept out of the sitemap on purpose. Path -> why.
DELIBERATELY_UNLISTED = {
    "/one-pager": "no page on the site links to it; promote or noindex is an open founder decision (2026-09-19)",
    "/vs/c2pa": "no page on the site links to it; promote or noindex is an open founder decision (2026-09-19)",
}

_NOINDEX = re.compile(r"<meta[^>]+name=[\"']robots[\"'][^>]+noindex", re.I)
_CANONICAL = re.compile(r"<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"']([^\"']+)", re.I)


def _norm(path: str) -> str:
    return path.rstrip("/") or "/"


def _path_of(url: str) -> str:
    return _norm(re.sub(r"^https?://[^/]+", "", url) or "/")


def canonical_targets(web_dir: Path) -> dict[str, str]:
    """{canonical path: first file that declares it} over indexable pages."""
    out: dict[str, str] = {}
    for f in sorted(web_dir.rglob("*.html")):
        text = f.read_text(encoding="utf-8", errors="replace")
        if _NOINDEX.search(text):
            continue
        m = _CANONICAL.search(text)
        if m:
            out.setdefault(_path_of(m.group(1)), str(f.relative_to(web_dir)))
    return out


def sitemap_paths(xml_text: str) -> set[str]:
    return {_path_of(e.text.strip()) for e in ET.fromstring(xml_text).iter()
            if e.tag.endswith("loc") and e.text}


def unlisted(web_dir: Path, xml_text: str) -> dict[str, str]:
    listed = sitemap_paths(xml_text)
    return {p: f for p, f in canonical_targets(web_dir).items() if p not in listed}


def _served_sitemap() -> str:
    import app as _app
    return _app._build_sitemap()


def test_every_indexable_page_is_in_the_served_sitemap():
    missing = {p: f for p, f in unlisted(WEB, _served_sitemap()).items()
               if p not in DELIBERATELY_UNLISTED}
    assert missing == {}, (
        "indexable pages the sitemap does not list (add them to _build_sitemap, "
        f"or mark them noindex): {missing}")


def test_every_exemption_is_still_true():
    still_unlisted = unlisted(WEB, _served_sitemap())
    stale = sorted(p for p in DELIBERATELY_UNLISTED if p not in still_unlisted)
    assert stale == [], f"exempt paths that are now listed, noindex, or gone: {stale}"


def test_the_scan_reads_a_real_domain():
    """Guards the guard: a regex that stopped matching would report nothing
    missing forever."""
    targets = canonical_targets(WEB)
    assert len(targets) >= 80, f"only {len(targets)} canonical pages found"
    assert "/" in targets and "/pricing" in targets


def test_control_a_planted_page_is_caught(tmp_path):
    (tmp_path / "listed.html").write_text(
        '<link rel="canonical" href="https://orphograph.com/listed">')
    (tmp_path / "forgotten.html").write_text(
        '<link rel="canonical" href="https://orphograph.com/forgotten">')
    (tmp_path / "private.html").write_text(
        '<meta name="robots" content="noindex">'
        '<link rel="canonical" href="https://orphograph.com/private">')
    xml = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           "<url><loc>https://orphograph.com/listed</loc></url></urlset>")
    assert unlisted(tmp_path, xml) == {"/forgotten": "forgotten.html"}
