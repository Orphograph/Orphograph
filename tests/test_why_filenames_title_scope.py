"""The method page's title said "Why filenames are not stored", a universal
claim, while its own lede scopes it to a single file anchored in the browser:
an API caller may attach a label and a folder anchor sends each relative path
(@why-filenames-title). Every statement of the title (title tag, og/twitter
titles, JSON-LD breadcrumb, h1, the method index link) is held to the scope."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "web" / "method" / "why-filenames-are-not-stored.html"
INDEX = ROOT / "web" / "method" / "index.html"
SCOPED = "Why a single-file receipt carries no filename"


def _title_statements(html):
    pats = [r"<title>([^<]*)</title>",
            r'<meta property="og:title" content="([^"]*)"',
            r'<meta name="twitter:title" content="([^"]*)"',
            r'"name": "([^"]*filename[^"]*)"',
            r"<h1>([^<]*)</h1>"]
    return [m for p in pats for m in re.findall(p, html)]


def test_every_title_statement_is_scoped_to_a_single_file():
    found = _title_statements(PAGE.read_text())
    assert len(found) == 5, found
    for t in found:
        assert SCOPED in t, t


def test_no_page_states_the_universal_title():
    for path in (ROOT / "web").rglob("*"):
        if path.suffix in (".html", ".md", ".xml", ".txt", ".json") and path.is_file():
            assert "Why filenames are not stored" not in path.read_text(errors="replace"), path


def test_the_method_index_links_the_scoped_title():
    assert f'href="/method/why-filenames-are-not-stored">{SCOPED}</a>' in INDEX.read_text()
