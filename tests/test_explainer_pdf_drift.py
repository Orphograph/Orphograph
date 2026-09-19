#!/usr/bin/env python3
"""test_explainer_pdf_drift.py — pins explainer/HOW_IT_WORKS.pdf to its HTML.

No CI gate previously read explainer/HOW_IT_WORKS.pdf at all: the PDF text
is font-encoded and pypdf (or any PDF-parsing library) is deliberately not a
CI dependency, so text extraction is not an option here. This test pins
byte-identity instead, via sha256, against the tracked sidecar manifest
explainer/HOW_IT_WORKS.manifest.json:

  * sha256(explainer/HOW_IT_WORKS.html) must match manifest["html"]["sha256"]
  * sha256(explainer/HOW_IT_WORKS.pdf)  must match manifest["pdf"]["sha256"]

explainer/HOW_IT_WORKS.html is fully self-contained — inline <style>, no
external CSS/font/image files, no <img>/<link>/<iframe> tags (verified
2026-09-19) — so the HTML file's own sha256 is the complete source pin; there
is no separate CSS/image asset whose hash would also need pinning. If an
external asset is ever inlined-from later, add its sha256 to both the
manifest and this test.

Effect: edit the HTML without regenerating the PDF + manifest, and the HTML
sha256 in the manifest goes stale — CI goes red. Regenerate the PDF without
updating the manifest, and the PDF sha256 goes stale — CI goes red either
way.

Regenerate procedure (after editing explainer/HOW_IT_WORKS.html):
  1. Render the PDF from the HTML with headless Chrome/Brave — the same
     method the current PDF was built with (see commit 0c0a607):

       "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" \\
         --headless --disable-gpu --no-pdf-header-footer --hide-scrollbars \\
         --print-to-pdf=explainer/HOW_IT_WORKS.pdf \\
         "file://$(pwd)/explainer/HOW_IT_WORKS.html"

     Google Chrome or Chromium work identically if Brave isn't installed.
  2. Regenerate the manifest: python3 scripts/update_explainer_manifest.py
  3. Re-run this test file, then commit all three files together:
       explainer/HOW_IT_WORKS.html
       explainer/HOW_IT_WORKS.pdf
       explainer/HOW_IT_WORKS.manifest.json

Every assertion below is a hard failure, never a skip, when an artifact is
missing — a skip on a missing PDF/HTML/manifest would be a vacuous pass on
exactly the case this pin exists to catch (see conftest.py's no-green-by-skip
gate: any skip fails the whole session unless PYTEST_ALLOW_SKIPS=1).
"""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPLAINER_DIR = ROOT / "explainer"
HTML_PATH = EXPLAINER_DIR / "HOW_IT_WORKS.html"
PDF_PATH = EXPLAINER_DIR / "HOW_IT_WORKS.pdf"
MANIFEST_PATH = EXPLAINER_DIR / "HOW_IT_WORKS.manifest.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


class TestExplainerPdfDrift(unittest.TestCase):
    def test_source_html_present(self):
        self.assertTrue(
            HTML_PATH.is_file(),
            f"explainer source missing: {HTML_PATH} — cannot verify the "
            "drift pin (this is a hard failure, not a skip)",
        )

    def test_pdf_present(self):
        self.assertTrue(
            PDF_PATH.is_file(),
            f"explainer PDF missing: {PDF_PATH} — cannot verify the drift "
            "pin (this is a hard failure, not a skip)",
        )

    def test_manifest_present(self):
        self.assertTrue(
            MANIFEST_PATH.is_file(),
            f"drift-pin manifest missing: {MANIFEST_PATH} — regenerate with "
            "'python3 scripts/update_explainer_manifest.py' (this is a hard "
            "failure, not a skip)",
        )

    def test_html_sha256_matches_manifest(self):
        if not HTML_PATH.is_file():
            self.fail(f"explainer source missing: {HTML_PATH}")
        if not MANIFEST_PATH.is_file():
            self.fail(f"drift-pin manifest missing: {MANIFEST_PATH}")
        manifest = json.loads(MANIFEST_PATH.read_text())
        recorded = manifest["html"]["sha256"]
        actual = _sha256(HTML_PATH)
        self.assertEqual(
            actual,
            recorded,
            "explainer/HOW_IT_WORKS.html changed since HOW_IT_WORKS.pdf was "
            "last regenerated from it — the manifest's html sha256 no "
            "longer matches. Re-render the PDF from the HTML and run "
            "'python3 scripts/update_explainer_manifest.py' before "
            "committing.",
        )

    def test_pdf_sha256_matches_manifest(self):
        if not PDF_PATH.is_file():
            self.fail(f"explainer PDF missing: {PDF_PATH}")
        if not MANIFEST_PATH.is_file():
            self.fail(f"drift-pin manifest missing: {MANIFEST_PATH}")
        manifest = json.loads(MANIFEST_PATH.read_text())
        recorded = manifest["pdf"]["sha256"]
        actual = _sha256(PDF_PATH)
        self.assertEqual(
            actual,
            recorded,
            "explainer/HOW_IT_WORKS.pdf bytes changed without updating the "
            "manifest — run 'python3 scripts/update_explainer_manifest.py' "
            "after any legitimate regeneration, before committing.",
        )


if __name__ == "__main__":
    unittest.main()
