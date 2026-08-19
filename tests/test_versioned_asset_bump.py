"""Every versioned asset that changed must have its ?v= bumped (2026-08-18).

Why this exists, and why test_css_cache_discipline.py did not already cover it.

That test enforces bumps for sheets under web/css/ ONLY, via
web/css/versions.json. Everything else referenced with a ?v= query string --
the root-level stylesheets (receipt.css, buy.css), web/pay/btc.css, and every
single .js file -- is hand-bumped with nothing checking it.

The gap shipped a real defect. The QR-removal branch rewrote web/pay-btc.js and
web/buy.js and left both at ?v=1. server/app.py serves .js as
`public, max-age=86400` and .html as `public, max-age=300, must-revalidate`, so
a returning visitor picks up the NEW markup within five minutes while holding
the OLD script for up to a day. The old pay-btc.js did
`document.getElementById("qr").src = ...` with no null guard against markup
that no longer contains #qr, which throws on the price-success path and takes
the refresh interval down with it -- on the Bitcoin payment page.

A stylesheet mismatch is a cosmetic frankenstein. A script mismatch is a dead
page. The narrower asset had the test.

This test is content-addressed rather than diff-addressed: it pins the sha256
of each versioned asset against the ?v= its pages reference. Change the bytes
without changing the version and it fails, whatever branch you are on.
"""
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PINS = WEB / "asset_versions.json"

# ?v= references to a same-origin .js or .css, excluding the web/css/ sheets
# already governed by versions.json + test_css_cache_discipline.
REF = re.compile(r'(?:src|href)="(/(?!css/)[^"?]+\.(?:js|css))\?v=(\d+)"')
EXCLUDE_DIRS = ("_mockups/", "dist/", "construction/", "vendor/", "node_modules/")


def _pages():
    for p in WEB.rglob("*.html"):
        rel = p.relative_to(WEB).as_posix()
        if any(rel.startswith(d) or f"/{d}" in rel for d in EXCLUDE_DIRS):
            continue
        yield rel, p


def _references():
    """{asset_path: {version: [pages]}} across the whole visitor surface."""
    found: dict[str, dict[str, list[str]]] = {}
    for rel, p in _pages():
        for asset, ver in REF.findall(p.read_text(encoding="utf-8")):
            found.setdefault(asset, {}).setdefault(ver, []).append(rel)
    return found


class TestVersionedAssetBump(unittest.TestCase):
    def test_one_version_per_asset_across_all_pages(self):
        """Two pages referencing the same asset at different ?v= means one of
        them is served stale markup against fresh code, or the reverse."""
        split = {a: v for a, v in _references().items() if len(v) > 1}
        self.assertEqual(
            split, {},
            "asset referenced at conflicting versions: "
            + json.dumps(split, indent=2))

    def test_pinned_bytes_match_pinned_version(self):
        """The bump gate. Edit an asset, and its sha256 stops matching the pin;
        the only way green again is to bump the ?v= and re-pin."""
        self.assertTrue(PINS.exists(),
                        "web/asset_versions.json is missing -- run this test's "
                        "docstring instructions to regenerate it")
        pins = json.loads(PINS.read_text())
        refs = _references()
        drift = []
        for asset, versions in sorted(refs.items()):
            ver = next(iter(versions))
            disk = WEB / asset.lstrip("/")
            if not disk.exists():
                drift.append(f"{asset} referenced but missing on disk")
                continue
            digest = hashlib.sha256(disk.read_bytes()).hexdigest()
            pin = pins.get(asset)
            if pin is None:
                drift.append(f"{asset} is versioned but not pinned")
            elif pin["version"] != ver:
                drift.append(
                    f"{asset}: pages say v={ver}, pin says v={pin['version']}")
            elif pin["sha256"] != digest:
                drift.append(
                    f"{asset}: BYTES CHANGED at unchanged v={ver} -- bump the "
                    f"?v= on every referencing page AND re-pin the sha256, in "
                    f"the same commit")
        self.assertEqual(drift, [], "\n  ".join([""] + drift))

    def test_the_scan_actually_finds_assets(self):
        """NEGATIVE CONTROL. Zero references would make both tests above pass
        vacuously, which is precisely how the .js gap survived."""
        refs = _references()
        self.assertGreater(len(refs), 3,
                           "the reference scan found almost nothing -- it is "
                           "not reading pages, so it proves nothing")
        self.assertTrue(any(a.endswith(".js") for a in refs),
                        "no .js references found; this test exists for .js")


if __name__ == "__main__":
    unittest.main()
