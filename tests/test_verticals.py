"""Tests for the vertical-packaging scaffold.

The six YAMLs at ``config/verticals/`` are loaded by ``server/verticals.py``
and rendered into landing pages under ``/verticals/<slug>.html``. The pages
are NOT linked from the homepage; they are reachable by direct URL only.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import verticals  # provided to sys.path by tests/conftest.py


ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config" / "verticals"
WEB_INDEX = ROOT / "web" / "index.html"

EXPECTED_SLUGS = {
    "construction",
    "inspection",
    "legal",
    "realestate",
    "healthcare",
    "accounting",
}

COMPETITOR_PATTERN = re.compile(
    r"\b(companycam|spectora|jobnimbus|procore|buildertrend|verisk|corelogic|"
    r"truepic|clio|vlex|filevine|tebra|dentrix|kareo|patientpop|henry schein|"
    r"tyler|opengov|costar|matterport|moxiworks|lone wolf|stone point|"
    r"insight partners|adobe content|content credentials|stampery|c2pa)\b",
    re.IGNORECASE,
)
DOLLAR_PATTERN = re.compile(
    r"\$[0-9]|\$\$|valuation|acquired for|raised \$|series [A-Z]"
)


# ---------------------------------------------------------------------------
# Module-level
# ---------------------------------------------------------------------------

def test_all_six_yamls_present_on_disk():
    files = sorted(p.stem for p in CONFIG_DIR.glob("*.yml"))
    assert set(files) == EXPECTED_SLUGS, files


def test_module_loads_all_six_slugs():
    verticals.reload()
    assert set(verticals.all_slugs()) == EXPECTED_SLUGS


def test_unknown_slug_returns_none():
    assert verticals.get("nonexistent-vertical") is None
    assert verticals.render_html("nonexistent-vertical") is None


# ---------------------------------------------------------------------------
# Per-vertical schema + rendering
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("slug", sorted(EXPECTED_SLUGS))
def test_yaml_schema_fields_present(slug):
    cfg = verticals.get(slug)
    assert cfg is not None, f"missing config for {slug}"
    assert cfg.get("slug") == slug
    assert isinstance(cfg.get("title"), str) and cfg["title"]
    assert isinstance(cfg.get("nav_label"), str) and cfg["nav_label"]

    hero = cfg.get("hero")
    assert isinstance(hero, dict)
    assert hero.get("headline")
    assert hero.get("subhead")

    assert isinstance(cfg.get("faq"), list) and cfg["faq"], f"{slug} has no faq entries"
    for entry in cfg["faq"]:
        assert isinstance(entry, dict)
        assert entry.get("q")
        assert entry.get("a")

    placeholder = cfg.get("pricing_placeholder", "")
    assert placeholder.startswith("STRIPE_PRICE_"), placeholder

    disclaimer = cfg.get("disclaimer", "")
    assert "evidence of existence" in disclaimer
    assert "not certify authorship" in disclaimer


@pytest.mark.parametrize("slug", sorted(EXPECTED_SLUGS))
def test_disclaimer_appears_in_rendered_html(slug):
    cfg = verticals.get(slug)
    body = verticals.render_html(slug)
    assert body is not None
    # The disclaimer is rendered as escaped paragraphs; check the canonical
    # opening clause appears.
    assert "evidence of existence" in body
    assert "not certify authorship" in body
    # The "not a law firm" / "not a regulated medical-records system" /
    # "not a qualified electronic trust service" / "not a legal or financial
    # advisor" block must also be present per the task contract.
    assert "not a law firm" in body
    assert "not a regulated medical-records system" in body
    assert "not a qualified electronic trust service" in body
    assert "not a legal or financial advisor" in body


@pytest.mark.parametrize("slug", sorted(EXPECTED_SLUGS))
def test_rendered_html_uses_cream_background(slug):
    body = verticals.render_html(slug)
    assert body is not None
    # The /method/folder-merkle.html surface uses the cream paper background;
    # vertical pages assert it inline so the brand surface is consistent even
    # if the global stylesheet changes.
    assert "#fdfaf3" in body


@pytest.mark.parametrize("slug", sorted(EXPECTED_SLUGS))
def test_no_competitor_names_in_yaml_or_html(slug):
    cfg_path = CONFIG_DIR / f"{slug}.yml"
    yaml_text = cfg_path.read_text(encoding="utf-8")
    body = verticals.render_html(slug)
    assert body is not None
    assert not COMPETITOR_PATTERN.search(yaml_text), f"competitor name in {slug}.yml"
    assert not COMPETITOR_PATTERN.search(body), f"competitor name in rendered {slug}"


@pytest.mark.parametrize("slug", sorted(EXPECTED_SLUGS))
def test_no_dollar_or_valuation_tokens(slug):
    cfg_path = CONFIG_DIR / f"{slug}.yml"
    yaml_text = cfg_path.read_text(encoding="utf-8")
    body = verticals.render_html(slug)
    assert body is not None
    assert not DOLLAR_PATTERN.search(yaml_text), f"$ or valuation token in {slug}.yml"
    assert not DOLLAR_PATTERN.search(body), f"$ or valuation token in rendered {slug}"


@pytest.mark.parametrize("slug", sorted(EXPECTED_SLUGS))
def test_no_exclamation_marks(slug):
    cfg_path = CONFIG_DIR / f"{slug}.yml"
    yaml_text = cfg_path.read_text(encoding="utf-8")
    body = verticals.render_html(slug)
    assert body is not None
    assert "!" not in yaml_text, f"exclamation mark in {slug}.yml"
    # In rendered HTML, exclamation marks should not appear in content;
    # the DOCTYPE declaration contains '<!DOCTYPE html>' which is an SGML
    # declaration, not punctuation — we strip the head and check the body.
    # The page template uses '<!DOCTYPE', '<!--' is not present. We use a
    # narrow check: every '!' that is not part of '<!' (DOCTYPE/comments)
    # is a content exclamation mark.
    content_exclamations = re.findall(r"(?<!<)!", body)
    assert not content_exclamations, f"exclamation in rendered {slug}: {content_exclamations[:3]}"


@pytest.mark.parametrize("slug", sorted(EXPECTED_SLUGS))
def test_rendered_html_contains_hero_and_faq(slug):
    cfg = verticals.get(slug)
    body = verticals.render_html(slug)
    assert body is not None
    # Hero headline appears
    headline = cfg["hero"]["headline"]
    # The headline is HTML-escaped; we check a stable substring.
    needle = headline.split(".")[0].strip()[:32]
    assert needle in body, f"headline fragment missing in {slug}: {needle!r}"
    # At least one FAQ question appears
    first_q = cfg["faq"][0]["q"]
    assert first_q in body, f"first FAQ question missing in {slug}"


# ---------------------------------------------------------------------------
# Anti-link guard — the homepage MUST NOT link to /verticals/.
# ---------------------------------------------------------------------------

def test_homepage_has_no_link_to_verticals():
    text = WEB_INDEX.read_text(encoding="utf-8")
    assert "/verticals/" not in text, (
        "web/index.html must not link to /verticals/<slug>; pages are "
        "founder-private until manually published."
    )
