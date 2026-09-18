"""The office does not market to the insurance vertical. Served pages must not
name it, and the inspection vertical page stays out of the served tree.

Scope is what a visitor or a crawler can read: everything under web/ and the
blog sources under content/. One allowance: a court case whose caption happens
to contain the word (a citation on /method/legal-recognition).
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

import _srv

ROOT = Path(__file__).resolve().parent.parent
TEXT_EXT = {".html", ".md", ".txt", ".xml", ".json", ".js", ".svg", ".yml", ".yaml"}

VOCAB = re.compile(
    r"insuranc|insurer|\binsureds?\b|adjusters?\b|underwrit|policyholder|"
    r"property and casualty|\bP&C\b|claims? (?:team|adjust)", re.I)
ALLOWED = re.compile(r"Lorraine[ _]v\.?[ _]Markel[ _]American[ _]Insurance[ _]Co\.?", re.I)

_READABLE_ATTRS = re.compile(
    r"""\b(?:content|alt|title|aria-label|placeholder)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)


def _flat(text: str) -> str:
    attrs = " ".join(a or b for a, b in _READABLE_ATTRS.findall(text))
    body = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(body + " " + attrs))


def _served_text_files() -> list[Path]:
    out = []
    for base in (ROOT / "web", ROOT / "content"):
        out += [p for p in base.rglob("*") if p.is_file() and p.suffix in TEXT_EXT]
    return out


def _hits(text: str) -> list[str]:
    return [m.group(0) for m in VOCAB.finditer(ALLOWED.sub(" ", _flat(text)))]


def test_no_served_page_names_the_insurance_vertical() -> None:
    files = _served_text_files()
    assert len(files) > 150, f"only {len(files)} files read — the scan is not seeing the tree"
    found = {str(p.relative_to(ROOT)): h for p in files
             if (h := _hits(p.read_text(encoding="utf-8", errors="replace")))}
    assert not found, f"served text names the insurance vertical: {found}"


def test_the_scan_can_see_what_it_hunts() -> None:
    """NEGATIVE CONTROL: each shape that was live on 2026-09-18, wrapped or not."""
    for planted in (
        "<strong>Insurance and inspection disputes.</strong>",
        "to a court, an adjuster, an auditor",
        "<h2>For lawyers and\n   adjusters</h2>",
        '<meta property="og:description" content="Cheap insurance against delivery disputes">',
        "For property and casualty inspections",
        "the insured disputes the adjuster&#x27;s assessment",
        "A property underwriting inspection prior to binding",
    ):
        assert _hits(planted), planted
    assert _hits("Lorraine v. Markel American Insurance Co., 241 F.R.D. 534") == []
    assert _hits("an auditor, a regulator, a lender") == []


def test_the_inspection_vertical_is_not_in_the_served_tree() -> None:
    assert not (ROOT / "web" / "inspection").exists()
    assert "/inspection" not in (ROOT / "web" / "sitemap.xml").read_text(encoding="utf-8")
    assert "/inspection" not in (ROOT / "web" / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    yield from _srv.server_processes(tmp_path_factory.mktemp("no-insurance"), stub_calendars=True)


@pytest.mark.parametrize("path", ("/inspection/", "/inspection", "/inspection/index.html"))
def test_the_inspection_url_is_gone_on_the_wire(base, path) -> None:
    status, _body, _headers = _srv.request(base, path)
    assert status == 404, (path, status)


def test_the_generated_sitemap_does_not_list_it(base) -> None:
    status, body, _ = _srv.request(base, "/sitemap.xml")
    assert status == 200
    assert b"/inspection" not in body
