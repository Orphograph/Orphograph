"""The office does not market to the insurance vertical, and nothing in this
repository — which is public — names it as an audience.

Scope is every tracked text file outside tests/: served pages, the server's own
templates and email builders, outreach tooling, docs. Two allowances: a court
case whose caption contains the word (a citation on /method/legal-recognition),
and the line in an internal posting guide that states this very rule.

The withdrawn-PAGE wire tests below (test_the_withdrawn_page_says_gone and
its neighbors) are not insurance-specific: they drive server.app's
WITHDRAWN_PATH_PREFIXES constant end to end, which today covers both
/inspection (withdrawn 2026-09-18) and /practice (withdrawn 2026-09-19, a
separate medical/healthcare-audience page unrelated to this file's
vocabulary scan). Both withdrawals answer through the same do_GET prefix
check, so one parametrized test on one shared server covers both rather
than duplicating a second server-spinning file. The vocabulary scan above
stays insurance-only and untouched: medical/clinical/patient vocabulary
legitimately remains in this repo's disclaimer boilerplate, unlike
"insurance" (see tests/test_no_healthcare_vertical_source.py for that
narrower, healthcare-specific guard).
"""
from __future__ import annotations

import re

import pytest

import _sitetext
import _srv

ROOT = _sitetext.ROOT

# Mirrors server.app.WITHDRAWN_PATH_PREFIXES. Written out here rather than
# imported: this suite's convention is `import app` only inside a
# test/fixture, after any per-test env setup (many files do this — grep
# "import app" under tests/), not at module level where it would be
# resolved once at collection time and cached in sys.modules for every
# other file's later `import app`. A drift between this tuple and the real
# constant would show up immediately as a WRONG SET of paths under test,
# which is a visible, honest failure mode — not a silent one.
WITHDRAWN_PREFIXES = ("/inspection", "/practice")
WITHDRAWN_VARIANTS = [
    variant
    for prefix in WITHDRAWN_PREFIXES
    for variant in (
        prefix,
        prefix + "/",
        prefix + "/index.html",
        prefix + "/index",           # clean-URL sibling — was live, missed pre-fix
        prefix + "/index.css",       # asset sibling — was live, missed pre-fix
        prefix + "/deep/nested/path",  # anything under the subtree
    )
]

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


def test_the_prefix_match_does_not_leak_to_a_similar_path() -> None:
    """Unit-level check of app._is_withdrawn_path, isolated from any server:
    a TRAILING double slash (/practice//, an empty path segment under the
    prefix) is still withdrawn; a path that merely SHARES CHARACTERS with
    the prefix is not.

    Not covered here: a LEADING double slash (//practice/) never reaches
    this function as such — stdlib http.server's parse_request() already
    collapses it to a single "/" before self.path is set (gh-87389, an
    open-redirect mitigation), verified end to end over a raw socket
    2026-09-19. _is_withdrawn_path("//practice/") does return False in
    isolation; that input shape is simply unreachable via a real request."""
    import app  # local import — see the WITHDRAWN_PREFIXES comment above
    for prefix in WITHDRAWN_PREFIXES:
        assert app._is_withdrawn_path(prefix)
        assert app._is_withdrawn_path(prefix + "/")
        assert app._is_withdrawn_path(prefix + "//")
        assert app._is_withdrawn_path(prefix + "/index")
        assert app._is_withdrawn_path(prefix + "/index.css")
        assert app._is_withdrawn_path(prefix + "/anything/deep")
        assert not app._is_withdrawn_path(prefix + "x")
        assert not app._is_withdrawn_path(prefix + "s")  # e.g. a plural, different page


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    yield from _srv.server_processes(tmp_path_factory.mktemp("no-insurance"), stub_calendars=True)


@pytest.mark.parametrize("method", ("GET", "HEAD"))
@pytest.mark.parametrize("path", WITHDRAWN_VARIANTS)
def test_the_withdrawn_page_says_gone(base, path, method) -> None:
    """410, not 404, across the WHOLE withdrawn subtree of each prefix in
    WITHDRAWN_PATH_PREFIXES — not just the handful of URLs that were ever
    directly linked. The clean-URL sibling (/<prefix>/index) and any asset
    that lived beside index.html (/<prefix>/index.css) were live, 200-
    answering pages before withdrawal; this is the assertion that can
    actually fail if the route regresses to matching exact strings again."""
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


@pytest.mark.parametrize("path", ("/verticals/healthcare", "/verticals/healthcare.html"))
def test_the_healthcare_config_rendered_vertical_is_not_served(base, path) -> None:
    """Same check as test_the_config_rendered_vertical_is_not_served, for the
    withdrawn healthcare config (tests/test_no_healthcare_vertical_source.py
    owns the source-tree guard for config/verticals/healthcare.yml itself;
    this is the wire-level companion, on the shared server rather than a
    third fixture). Status only — _hits() above is insurance vocabulary and
    would not mean anything applied to a healthcare path.

    Asymmetric by construction, not by oversight: do_GET's /verticals/
    branch only fires when the path ends in .html
    (`path.startswith("/verticals/") and path.endswith(".html")`), so
    "/verticals/healthcare" (no extension) already 404s from the static
    fallback regardless of whether config/verticals/healthcare.yml exists —
    only the .html variant can distinguish "the config is gone" from "this
    URL shape was never routed." Both are asserted for completeness; only
    the .html one is a meaningful regression control."""
    status, _body, _ = _srv.request(base, path)
    assert status == 404, (path, status)


def test_the_generated_sitemap_does_not_list_it(base) -> None:
    status, body, _ = _srv.request(base, "/sitemap.xml")
    assert status == 200
    for prefix in WITHDRAWN_PREFIXES:
        assert f"orphograph.com{prefix}/<".encode() not in body, prefix
