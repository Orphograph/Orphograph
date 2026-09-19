"""Guards that the withdrawn /practice/ page cannot silently return, at the
source-tree level (static checks only — no server).

Founder decision 2026-09-19: the company excludes medical/healthcare as a
vertical. web/practice/ was a complete, live, sitemap-listed landing page
addressing medical/healthcare practices, withdrawn the same way web/
inspection/ was withdrawn on 2026-09-18 (commits 730ebe0 + 0c0a607): the
directory leaves the tracked tree, the sitemap entry and asset-version pin
go with it, and the withdrawn URL answers 410 Gone (not 404) because it sat
in a public sitemap and a crawler drops a Gone page and its cached snippet
far sooner than a Not Found.

config/verticals/healthcare.yml — a separate, config-driven scaffold — WAS
linked to this page: its `route` field named /practice/ as where the
vertical would live if launched. See tests/test_no_healthcare_vertical_source.py
for the config-source guard, including the check that no vertical config's
`route` names a withdrawn prefix. The wire-level check that /practice/ (and
every path under it) actually answers 410 lives in
tests/test_no_insurance_vertical_on_site.py, parametrized over
server.app.WITHDRAWN_PATH_PREFIXES on ONE shared server — not duplicated
here as a second server-spinning fixture.

Scope note, same as the healthcare-source guard: this file does NOT scan
tracked text for medical/clinical/patient vocabulary. That vocabulary
legitimately remains in disclaimer boilerplate elsewhere ("not a regulated
medical-records system" on web/press.html, web/what-is-this.html,
web/workpapers/, web/listings/, web/construction/, web/matters/) and in
ordinary English ("bookkeeping practices" on web/workpapers/). This guard
binds only to the withdrawn page's SOURCE: the directory, its sitemap
listing, and its asset-version pin.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def test_the_practice_directory_is_not_in_the_tree() -> None:
    assert not (WEB / "practice").exists()


def test_the_static_sitemap_does_not_list_it() -> None:
    sitemap = (WEB / "sitemap.xml").read_text(encoding="utf-8")
    assert "orphograph.com/practice/<" not in sitemap


def test_the_asset_manifest_does_not_pin_its_stylesheet() -> None:
    pins = json.loads((WEB / "asset_versions.json").read_text(encoding="utf-8"))
    assert "/practice/index.css" not in pins
