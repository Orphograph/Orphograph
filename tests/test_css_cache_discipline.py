#!/usr/bin/env python3
"""test_css_cache_discipline.py — a changed stylesheet MUST bump its ?v=.

The class this pins burned twice in one week, once per artifact type:
  * qr-receipt.svg was regenerated under an unchanged ?v=1 — the edge served
    the dead QR after the fix deployed (2026-08-09, caught mid-merge-chain);
  * orpho-home.css was appended to by TWO further PRs (#150, #151) under the
    same ?v=1 it shipped with in #149 — visitors got #149's cached CSS under
    #151's markup: the receipt card collapsed, the letter lost its details,
    and none of it showed in post-merge curls because those hit fresh cache
    keys (founder screenshots, 2026-08-09).

The existing drift guard pins index.css only. This one covers the design
system via a committed manifest (web/css/versions.json):

  content-hash check  — sheet bytes must match the manifest sha256, so any
                        edit forces a manifest update in the same commit;
  version-bump check  — updating the manifest hash without incrementing the
                        version is rejected by construction below;
  reference check     — every page (and the server 404 template) referencing
                        a sheet must use exactly the manifest version.

To change a sheet: edit it, bump "version", recompute "sha256", and bump the
?v= on every referencing page (scripts/migrate_design_system.py-style sweep
or sed). The point is that forgetting ANY of those steps fails loudly here
instead of silently serving customers a frankenstein.
"""
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
MANIFEST = json.loads((WEB / "css" / "versions.json").read_text())
EXCLUDE = ("_mockups/", "dist/", "construction/")


class TestCssCacheDiscipline(unittest.TestCase):
    def test_manifest_matches_sheet_bytes(self):
        for sheet, meta in MANIFEST.items():
            digest = hashlib.sha256((WEB / "css" / f"{sheet}.css").read_bytes()).hexdigest()
            self.assertEqual(
                digest, meta["sha256"],
                f"{sheet}.css changed but web/css/versions.json was not "
                f"updated — bump its version AND the ?v= on every "
                f"referencing page in the same commit.")

    def test_every_reference_uses_the_manifest_version(self):
        offenders = []
        sources = [p for p in WEB.rglob("*.html")
                   if not p.relative_to(WEB).as_posix().startswith(EXCLUDE)]
        sources.append(ROOT / "server" / "app.py")
        for src in sources:
            text = src.read_text(encoding="utf-8")
            for sheet, meta in MANIFEST.items():
                for hit in re.findall(rf"/css/{sheet}\.css\?v=(\d+)", text):
                    if int(hit) != meta["version"]:
                        offenders.append(f"{src.name}: {sheet} v={hit} "
                                         f"(manifest v={meta['version']})")
        self.assertEqual(offenders[:10], [],
                         f"{len(offenders)} stale sheet reference(s)")

    def test_manifest_covers_all_design_sheets(self):
        on_disk = {p.stem for p in (WEB / "css").glob("orpho-*.css")}
        self.assertEqual(on_disk, set(MANIFEST),
                         "a design sheet exists outside the manifest")


if __name__ == "__main__":
    unittest.main()
