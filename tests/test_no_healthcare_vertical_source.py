"""Guards that the withdrawn healthcare vertical SOURCE cannot silently return.

Founder decision 2026-09-19: the company excludes medical/healthcare as a
vertical. config/verticals/healthcare.yml — a finished but unserved landing-
page source — left the tracked tree the same day; its only copy now lives at
outreach/dormant_verticals/healthcare/healthcare.yml (gitignored, local-only).

This is narrower than tests/test_no_insurance_vertical_on_site.py BY DESIGN.
That guard scans every tracked text file because "insurance" left the SERVED
tree too (web/inspection/ was withdrawn and now answers 410). Healthcare and
medical/clinical/patient vocabulary legitimately remains in served copy —
several pages' disclaimers state the office is NOT a regulated medical-records
system, which stays true (and becomes more true) after this removal. Scanning
all tracked text for that vocabulary would fail on content the founder has not
asked to remove.

(web/practice/ WAS a second, separate healthcare-audience landing page —
unrelated to this config/verticals/ scaffold, no link or slug between them —
also withdrawn on this branch, same day, once the founder confirmed it was
live. See tests/test_no_healthcare_practice_page.py for that guard: same
narrow-source rationale, applied to a served page + sitemap entry + 410
route instead of a YAML file.)

This guard instead binds ONLY to the vertical-config SOURCE: the file itself,
plus the identity fields (filename stem, `slug`, `nav_label`, `title`) of
every config under config/verticals/ — the same surface server/verticals.py
and server/adapter_spec.py key off to decide what is loaded and routable.
"""
from __future__ import annotations

import re
from pathlib import Path

import verticals  # tests/conftest.py puts server/ on sys.path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config" / "verticals"
HEALTHCARE_YML = CONFIG_DIR / "healthcare.yml"

SLUG_VOCAB = re.compile(r"health.?care|\bmedical\b|\bclinical\b|\bhipaa\b|\bpatients?\b", re.I)


def test_healthcare_yaml_source_does_not_exist_on_disk():
    """Filesystem check, not `git ls-files` — a re-added, unstaged file must
    still be caught before it is ever committed."""
    assert not HEALTHCARE_YML.exists(), (
        f"{HEALTHCARE_YML} exists — the withdrawn healthcare vertical source "
        "returned to the tracked tree"
    )


def test_no_vertical_config_file_name_declares_healthcare():
    files = sorted(CONFIG_DIR.glob("*.yml"))
    assert files, "no vertical configs found under config/verticals/ — scan is not seeing the directory"
    bad = [f.name for f in files if SLUG_VOCAB.search(f.stem)]
    assert not bad, f"a vertical config file name declares a healthcare/medical slug: {bad}"


def test_no_vertical_config_declares_a_healthcare_slug():
    """Load through the real module (server/verticals.py), not a bare YAML
    parse — that is the surface /verticals/<slug>.html actually serves from."""
    verticals.reload()
    slugs = verticals.all_slugs()
    bad_slugs = [s for s in slugs if SLUG_VOCAB.search(s)]
    assert not bad_slugs, f"a loaded vertical slug names healthcare/medical: {bad_slugs}"
    bad_fields: dict[str, list[tuple[str, str]]] = {}
    for slug in slugs:
        cfg = verticals.get(slug) or {}
        for field in ("nav_label", "title"):
            value = str(cfg.get(field, ""))
            if SLUG_VOCAB.search(value):
                bad_fields.setdefault(slug, []).append((field, value))
    assert not bad_fields, f"a loaded vertical's identity field names healthcare/medical: {bad_fields}"


def test_the_scan_can_see_what_it_hunts():
    """NEGATIVE CONTROL for the vocabulary shape, without touching the
    filesystem mid-suite: the regex must catch every spelling a re-added
    config could plausibly use for its slug/nav_label/title, and must not
    false-positive on the four surviving verticals' real identity fields."""
    for planted in (
        "healthcare", "health-care", "health care", "Healthcare",
        "medical", "Medical Records", "clinical", "HIPAA", "patients",
    ):
        assert SLUG_VOCAB.search(planted), planted

    verticals.reload()
    for slug in ("construction", "legal", "realestate", "accounting"):
        cfg = verticals.get(slug)
        assert cfg is not None, f"{slug} failed to load"
        assert not SLUG_VOCAB.search(slug), slug
        for field in ("nav_label", "title"):
            value = str(cfg.get(field, ""))
            assert not SLUG_VOCAB.search(value), (slug, field, value)
