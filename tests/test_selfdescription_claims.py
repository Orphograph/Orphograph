"""test_selfdescription_claims.py — guard the site's claims about itself.

The browser verifier at /verify-js.html used to be one HTML document with its
JavaScript inline. The strict site CSP blocks inline <script>, so the verifier
was externalised to /verify-js.js. The page still runs entirely client-side and
still survives an offline save, but it is no longer a single file, and several
other pages described it as one.

A provenance product's claims about how its own verification works are a trust
surface. These tests fail the build if a served page describes the browser
verifier as self-contained in a way the shipped page no longer is.

Pure stdlib + pytest. Text-only assertions; nothing here executes site code.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SERVER = ROOT / "server"

# The page that owns the claim. Read (never asserted against) as the source of
# truth for whether the verifier still loads an external script at all.
VERIFY_JS_HTML = WEB / "verify-js.html"


def _served_html_files() -> list[Path]:
    """Every web/**/*.html excluding non-deployed artifacts."""
    out: list[Path] = []
    for p in WEB.rglob("*.html"):
        rel = p.relative_to(WEB).as_posix()
        if rel.startswith("_mockups/"):
            continue
        if rel == "index-legacy.html":
            continue
        out.append(p)
    return sorted(out)


def _server_templates() -> list[Path]:
    """server/*.py modules that emit user-visible HTML."""
    return sorted(SERVER.glob("*.py"))


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")


def _format_hits(hits: list[tuple[Path, int, str]]) -> str:
    lines = ["", "Stale self-description claims found:"]
    for path, lineno, snippet in hits:
        lines.append(f"  {path.relative_to(ROOT)}:{lineno}  {snippet.strip()[:160]}")
    return "\n".join(lines)


def _scan(paths: list[Path], pattern: re.Pattern[str]) -> list[tuple[Path, int, str]]:
    hits: list[tuple[Path, int, str]] = []
    for p in paths:
        for i, line in enumerate(_read(p).splitlines(), start=1):
            if pattern.search(line):
                hits.append((p, i, line))
    return hits


# ─────────────────────────────────────────────────────────────────────────────
# Premise: the browser verifier does load an external script.
# If this ever stops being true — e.g. a build step inlines verify-js.js back
# into the page — the guards below become over-strict and should be revisited
# rather than silently kept.
# ─────────────────────────────────────────────────────────────────────────────

EXTERNAL_VERIFIER_SCRIPT = re.compile(r"<script[^>]+src=[\"']/verify-js\.js")


def test_browser_verifier_loads_an_external_script() -> None:
    assert VERIFY_JS_HTML.is_file(), "web/verify-js.html is missing"
    assert EXTERNAL_VERIFIER_SCRIPT.search(_read(VERIFY_JS_HTML)), (
        "web/verify-js.html no longer loads /verify-js.js. The single-file "
        "claims guarded by this module may have become true again — re-check "
        "them before relaxing these tests."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Rule: no served page may call the browser verifier a single HTML file.
# The offline and no-call-home claims are unaffected and remain permitted;
# only the file-count / self-containment phrasing is pinned here.
# ─────────────────────────────────────────────────────────────────────────────

SINGLE_FILE_CLAIM = re.compile(
    r"single[\s-](?:html\s+(?:file|document|page)|file\s+browser\s+verifier)",
    re.IGNORECASE,
)


def test_no_page_calls_the_browser_verifier_a_single_html_file() -> None:
    hits = _scan(_served_html_files(), SINGLE_FILE_CLAIM)
    assert not hits, _format_hits(hits)


def test_no_server_template_calls_the_browser_verifier_a_single_html_file() -> None:
    hits = _scan(_server_templates(), SINGLE_FILE_CLAIM)
    assert not hits, _format_hits(hits)


# ─────────────────────────────────────────────────────────────────────────────
# Rule: the downloadable offline kit is Python. It ships no HTML file, so no
# page may advertise one inside it. Guarded against the archive's real manifest
# rather than a hardcoded list.
# ─────────────────────────────────────────────────────────────────────────────

def test_offline_verifier_kit_ships_no_html() -> None:
    import zipfile

    kit = WEB / "dist" / "orphograph-verify.zip"
    assert kit.is_file(), "web/dist/orphograph-verify.zip is missing"
    with zipfile.ZipFile(kit) as zf:
        names = zf.namelist()
    html = [n for n in names if n.lower().endswith((".html", ".htm"))]
    assert not html, (
        f"the offline verifier kit now ships HTML ({html}); pages describing it "
        "as Python-only need updating"
    )


KIT_HTML_CLAIM = re.compile(
    r"verifier kit \([^)]*html[^)]*\)",
    re.IGNORECASE,
)


def test_no_page_claims_the_offline_kit_contains_html() -> None:
    hits = _scan(_served_html_files(), KIT_HTML_CLAIM)
    assert not hits, _format_hits(hits)
