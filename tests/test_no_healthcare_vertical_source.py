"""Guards that the withdrawn healthcare vertical SOURCE cannot silently return.

Founder decision 2026-09-19: the company excludes medical/healthcare as a
vertical. config/verticals/healthcare.yml — a finished but unserved landing-
page source — left the tracked tree the same day.

This is narrower than tests/test_no_insurance_vertical_on_site.py BY DESIGN.
That guard scans every tracked text file because "insurance" left the SERVED
tree too (web/inspection/ was withdrawn and now answers 410). Healthcare and
medical/clinical/patient vocabulary legitimately remains in served copy —
several pages' disclaimers state the office is NOT a regulated medical-records
system, which stays true (and becomes more true) after this removal. Scanning
all tracked text for that vocabulary would fail on content the founder has not
asked to remove. This guard instead binds to the vertical-config SOURCE:
config/verticals/*.yml and the withdrawn web/practice/ page.

web/practice/ WAS a served, sitemap-listed page addressing the same audience
— and WAS linked to this scaffold: the deleted healthcare.yml declared
`route: /practice/`, the same pattern every surviving config uses for its
own already-served page (accounting -> /workpapers/, legal -> /matters/,
realestate -> /listings/, construction -> /construction/). web/practice/ was
withdrawn on the same branch, same day; see
tests/test_no_healthcare_practice_page.py for its source-tree guard and
tests/test_no_insurance_vertical_on_site.py for the shared wire-level check
that /practice/ (and /inspection/, and anything under either) answers 410,
plus the /verticals/healthcare(.html) 404 check on that same shared server.

Two INDEPENDENT guards below, deliberately not one:
  - VOCABULARY (SLUG_VOCAB): scans `slug`, `nav_label`, `title`, `route` and
    `audience` — never `disclaimer`, which legitimately carries "not a
    regulated medical-records system" in every surviving config. A re-added
    vertical can dodge this by rewording (slug "practice", nav_label
    "Practices" — "practice" is deliberately NOT vocabulary, since it is
    ordinary English; see accounting.yml's audience: "...bookkeeping
    practices...").
  - WITHDRAWN-PREFIX (route only, via server.app's real prefix-match
    function): independent of wording entirely — catches a reworded config
    that still points its `route` at a withdrawn URL. This is the guard the
    vocabulary check cannot do and must not try to do by adding "practice"
    as a trigger word.
Both are necessary; neither substitutes for the other. The negative control
below plants a config that evades one and is caught by the other, naming
which.
"""
from __future__ import annotations

import re
from pathlib import Path

import verticals  # tests/conftest.py puts server/ on sys.path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config" / "verticals"
HEALTHCARE_YML = CONFIG_DIR / "healthcare.yml"

# Widened 2026-09-19 (code review finding 2, PR #255): the original
# `health.?care|\bmedical\b|\bclinical\b|\bhipaa\b|\bpatients?\b` missed
# "clinic"/"clinics" (a different word from "clinical"), "dental",
# "physician(s)", and bare "health" entirely -- a config re-added as
# slug "practice" / nav_label "Practices" with an audience field naming
# "clinics" passed the old regex outright. See test_the_scan_can_see_what_it_hunts
# for the negative control that would have caught this gap.
SLUG_VOCAB = re.compile(
    r"health.?care|\bhealth\b|\bmedical\b|\bclinical\b|\bclinics?\b|\bdental\b|"
    r"\bphysicians?\b|\bhipaa\b|\bpatients?\b",
    re.I,
)

# Fields scanned by SLUG_VOCAB. `disclaimer` is deliberately excluded: every
# surviving config's disclaimer field legitimately contains "not a regulated
# medical-records system" (the office disclaiming healthcare, not marketing
# to it) and scanning it would fail on content nobody has asked to remove.
SCANNED_FIELDS = ("nav_label", "title", "route", "audience")


def test_healthcare_yaml_source_does_not_exist_on_disk():
    """Filesystem check, not `git ls-files` — a re-added, unstaged file must
    still be caught before it is ever committed."""
    assert not HEALTHCARE_YML.exists(), (
        f"{HEALTHCARE_YML} exists — the withdrawn healthcare vertical source "
        "returned to the tracked tree"
    )


def test_no_config_verticals_entry_names_healthcare_by_any_name():
    """A `*.yml` glob alone misses `healthcare.yaml`, `healthcare.yml.bak`,
    or `healthcare.yml.disabled` sitting right next to the guarded exact
    path (code review finding 6, PR #255) — scan every directory ENTRY's
    name, any extension, any suffix, not just the ones ending in `.yml`."""
    assert CONFIG_DIR.is_dir(), f"{CONFIG_DIR} is missing — scan is not seeing the directory"
    bad = [e.name for e in CONFIG_DIR.iterdir() if SLUG_VOCAB.search(e.name)]
    assert not bad, f"an entry under config/verticals/ names healthcare/medical: {bad}"


def test_no_vertical_config_declares_a_healthcare_slug():
    """Load through the real module (server/verticals.py), not a bare YAML
    parse — that is the surface /verticals/<slug>.html actually serves from.
    Scans SCANNED_FIELDS only (never `disclaimer` — see module docstring)."""
    verticals.reload()
    slugs = verticals.all_slugs()
    bad_slugs = [s for s in slugs if SLUG_VOCAB.search(s)]
    assert not bad_slugs, f"a loaded vertical slug names healthcare/medical: {bad_slugs}"
    bad_fields: dict[str, list[tuple[str, str]]] = {}
    for slug in slugs:
        cfg = verticals.get(slug) or {}
        for field in SCANNED_FIELDS:
            value = str(cfg.get(field, ""))
            if SLUG_VOCAB.search(value):
                bad_fields.setdefault(slug, []).append((field, value))
    assert not bad_fields, f"a loaded vertical's field names healthcare/medical: {bad_fields}"


def test_no_vertical_config_route_names_a_withdrawn_prefix():
    """Independent of vocabulary entirely (code review finding 3, PR #255):
    a vertical can be reworded to dodge SLUG_VOCAB completely (ordinary
    slug, ordinary nav_label, ordinary audience) and still reclaim a
    withdrawn URL through `route` alone. Uses the real prefix-match
    function from server/app.py — the same one do_GET runs — not a
    second, hand-typed comparison.

    Deliberately does NOT add "practice" to SLUG_VOCAB to catch this: every
    surviving config's `route` follows the identical pattern (accounting's
    route is /workpapers/, legal's is /matters/, ...) and "practice" is
    ordinary English (accounting.yml's audience: "...bookkeeping
    practices..."). The withdrawn-PATH check, not a vocabulary word, is
    what has to catch a route collision.
    """
    import app  # local import — server/app.py's DATA_DIR binds at import;
    # irrelevant here (only a pure function is used), but this suite's
    # convention keeps `import app` out of module scope. See
    # tests/test_no_insurance_vertical_on_site.py for the longer version
    # of this rationale.
    verticals.reload()
    bad: dict[str, str] = {}
    for slug in verticals.all_slugs():
        cfg = verticals.get(slug) or {}
        route = str(cfg.get("route", "")).rstrip("/")
        if route and app._is_withdrawn_path(route):
            bad[slug] = route
    assert not bad, f"a vertical config's route names a withdrawn prefix: {bad}"


def test_the_scan_can_see_what_it_hunts():
    """NEGATIVE CONTROL. Plants exactly the words the OLD regex missed (not
    words it already caught — "medical practice" would match the old
    `\\bmedical\\b` and prove nothing about the widening), then plants the
    concrete evasion this finding described: a config re-added under an
    unrelated slug/nav_label, using the ORIGINAL config's own audience
    wording. Two different guards have to catch two different fields —
    neither is SLUG_VOCAB alone."""
    for planted in ("clinic", "clinics", "Clinics", "dental", "physician",
                    "physicians", "health system administrators", "health"):
        assert SLUG_VOCAB.search(planted), planted

    # The reintroduction this finding verified: slug: healthcare,
    # nav_label: Healthcare, route: /practice/, audience naming "practice
    # administrators... at small clinics and offices" -- reworded here to
    # the slug/nav_label an attacker (or an honest mistake) would actually
    # use to dodge the old vocabulary scan.
    reintroduced = {
        "slug": "practice",
        "nav_label": "Practices",
        "route": "/practice/",
        "audience": "Practice administrators, office managers, and billing "
                     "staff at small clinics and offices.",
    }
    # slug/nav_label/route evade vocabulary BY DESIGN -- "practice" must
    # never become a trigger word (see the false-positive check below).
    assert not SLUG_VOCAB.search(reintroduced["slug"])
    assert not SLUG_VOCAB.search(reintroduced["nav_label"])
    assert not SLUG_VOCAB.search(reintroduced["route"])
    # audience is where the WIDENED vocabulary has to land -- "clinics" is
    # exactly the word the old regex missed.
    assert SLUG_VOCAB.search(reintroduced["audience"]), (
        "the widened vocabulary must catch 'clinics' in an audience field; "
        "the old regex (clinical only, not clinic/clinics) would have missed it"
    )
    # route is where the WITHDRAWN-PREFIX guard (not vocabulary) has to
    # land -- unit-check the function this file's route guard calls.
    import app
    assert app._is_withdrawn_path(reintroduced["route"].rstrip("/"))

    # False-positive control: none of the four surviving configs' SCANNED
    # fields (never disclaimer) trip the widened vocabulary.
    verticals.reload()
    for slug in ("construction", "legal", "realestate", "accounting"):
        cfg = verticals.get(slug)
        assert cfg is not None, f"{slug} failed to load"
        assert not SLUG_VOCAB.search(slug), slug
        for field in SCANNED_FIELDS:
            value = str(cfg.get(field, ""))
            assert not SLUG_VOCAB.search(value), (slug, field, value)
        # the withdrawn-prefix guard must not flag any surviving route either
        route = str(cfg.get("route", "")).rstrip("/")
        assert not app._is_withdrawn_path(route), (slug, route)
