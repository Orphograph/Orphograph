"""The office does not market to the insurance vertical, and nothing in this
repository — which is public — names it as an audience.

Scope is every tracked text file outside tests/: served pages, the server's own
templates and email builders, outreach tooling, docs. Two allowances: a court
case whose caption contains the word (a citation on /method/legal-recognition),
and the line in an internal posting guide that states this very rule.
"""
from __future__ import annotations

import re

import pytest

import _sitetext
import _srv

ROOT = _sitetext.ROOT

VOCAB = re.compile(
    r"insuranc|insurer|\binsure[sd]?\b|\binsuring\b|\binsureds?\b|\badjusters?\b|underwrit|"
    r"policyholder|property and casualty|\bP&C\b|claims? (?:team|adjust)|\bcarriers?\b|xactimate",
    re.I)
ALLOWED = re.compile(
    r"Lorraine[ _]v\.?[ _]Markel[ _]American[ _]Insurance[ _]Co\.?"
    r"|No insurance/medical/FDA/pharma", re.I)


def _hits(text: str, suffix: str = ".html") -> list[str]:
    return [m.group(0) for m in VOCAB.finditer(ALLOWED.sub(" ", _sitetext.flat(text, suffix)))]


def test_no_tracked_text_names_the_insurance_vertical() -> None:
    files = _sitetext.tracked_text_files()
    assert len(files) > 500, f"only {len(files)} files read — the scan is not seeing the tree"
    found = {}
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        if (h := _hits(text, p.suffix)):
            found[str(p.relative_to(ROOT))] = h
    assert not found, f"tracked text names the insurance vertical: {found}"


def test_the_scan_can_see_what_it_hunts() -> None:
    """NEGATIVE CONTROL: every shape that was live on 2026-09-18, wrapped or not."""
    for planted in (
        "<strong>Insurance and inspection disputes.</strong>",
        "to a court, an adjuster, an auditor",
        "<h2>For lawyers and\n   adjusters</h2>",
        '<meta property="og:description" content="Cheap insurance against delivery disputes">',
        "For property and casualty inspections",
        "the insured disputes the adjuster&#x27;s assessment",
        "A property underwriting inspection prior to binding",
        "Carriers, lenders, and homeowners disagree",
        "It is the carrier asking, weeks after the job closed",
        "insure yourself against delivery disputes",
    ):
        assert _hits(planted), planted
    # copy inside JavaScript, where a bare `<` is not a tag
    js = "for (i=0;i<n;i++){ el.textContent='Send to your insurance adjuster'; } cb = () => 1"
    assert _hits(js, ".js"), "source files must not be tag-stripped"
    assert _hits(f"<p>x</p><script>{js}</script>", ".html"), "inline scripts must be read whole"
    assert _hits("Lorraine v. Markel American Insurance Co., 241 F.R.D. 534") == []
    assert _hits("an auditor, a regulator, a lender; make sure it verifies") == []


def test_the_inspection_vertical_is_not_in_the_tree() -> None:
    assert not (ROOT / "web" / "inspection").exists()
    assert not (ROOT / "config" / "verticals" / "inspection.yml").exists()
    sitemap = (ROOT / "web" / "sitemap.xml").read_text(encoding="utf-8")
    assert "orphograph.com/inspection/<" not in sitemap
    assert 'href="/inspection/"' not in (ROOT / "web" / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    yield from _srv.server_processes(tmp_path_factory.mktemp("no-insurance"), stub_calendars=True)


@pytest.mark.parametrize("method", ("GET", "HEAD"))
@pytest.mark.parametrize("path", ("/inspection/", "/inspection", "/inspection/index.html"))
def test_the_withdrawn_page_says_gone(base, path, method) -> None:
    """410, not 404: it was in the sitemap and the homepage footer, and a
    crawler drops a Gone page and its cached snippet far sooner."""
    status, _body, headers = _srv.request(base, path, method)
    assert status == 410, (method, path, status)
    assert headers.get("Strict-Transport-Security"), "the error path lost its security headers"


@pytest.mark.parametrize("path", ("/verticals/inspection", "/verticals/inspection.html"))
def test_the_config_rendered_vertical_is_not_served(base, path) -> None:
    """The server renders /verticals/<slug> from config/ whenever config/ is
    present — it is, in this test server, even though the image omits it."""
    status, body, _ = _srv.request(base, path)
    assert status == 404, (path, status)
    assert not _hits(body.decode("utf-8", "replace"))


def test_the_generated_sitemap_does_not_list_it(base) -> None:
    status, body, _ = _srv.request(base, "/sitemap.xml")
    assert status == 200
    assert b"orphograph.com/inspection/<" not in body
