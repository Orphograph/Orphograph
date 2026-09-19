#!/usr/bin/env python3
"""update_explainer_manifest.py — regenerate explainer/HOW_IT_WORKS.manifest.json.

Run this AFTER re-rendering explainer/HOW_IT_WORKS.pdf from its HTML source.
It records the current sha256 of both tracked files so
tests/test_explainer_pdf_drift.py can catch drift between the HTML and the
PDF without ever parsing PDF content — the PDF's text is font-encoded and
pypdf/pdfminer/etc. are not CI dependencies, so byte-identity via sha256 is
the pin instead of text extraction.

explainer/HOW_IT_WORKS.html is fully self-contained (inline <style>, no
external CSS/font/image files, no <img>/<link>/<iframe> — verified
2026-09-19), so only the HTML file itself and the PDF need a sha256 here. If
that ever changes (an external asset gets pulled in), add its sha256 to this
manifest and to the drift test.

Full regenerate procedure:
  1. Edit explainer/HOW_IT_WORKS.html.
  2. Render the PDF from the HTML with headless Chrome/Brave — the same
     method the current PDF was built with (see commit 0c0a607):

       "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" \\
         --headless --disable-gpu --no-pdf-header-footer --hide-scrollbars \\
         --print-to-pdf=explainer/HOW_IT_WORKS.pdf \\
         "file://$(pwd)/explainer/HOW_IT_WORKS.html"

     Google Chrome or Chromium work identically if Brave isn't installed;
     swap the binary path only.
  3. Regenerate this manifest:

       python3 scripts/update_explainer_manifest.py

  4. Verify the pin is now green:

       PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_explainer_pdf_drift.py -q

  5. Commit HOW_IT_WORKS.html, HOW_IT_WORKS.pdf and
     HOW_IT_WORKS.manifest.json together, in the same commit.
"""
from __future__ import annotations

import hashlib
import json
import sys
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


def main() -> int:
    missing = [p for p in (HTML_PATH, PDF_PATH) if not p.is_file()]
    if missing:
        for p in missing:
            print(f"error: missing {p}", file=sys.stderr)
        return 1

    manifest = {
        "_doc": (
            "Drift pin for explainer/HOW_IT_WORKS.{html,pdf}. The sha256 "
            "values here must match the tracked files byte-for-byte or "
            "tests/test_explainer_pdf_drift.py fails CI (hard fail, never a "
            "skip). Regenerate this file with "
            "'python3 scripts/update_explainer_manifest.py' after "
            "re-rendering the PDF from the HTML — see this script's "
            "docstring for the full procedure. No PDF text parsing is "
            "involved anywhere in this pin."
        ),
        "html": {
            "path": "explainer/HOW_IT_WORKS.html",
            "sha256": _sha256(HTML_PATH),
        },
        "pdf": {
            "path": "explainer/HOW_IT_WORKS.pdf",
            "sha256": _sha256(PDF_PATH),
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
