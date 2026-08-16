#!/usr/bin/env python3
"""test_receipt_pdf_seal.py — the SENT receipt must carry the real medallion.

Founder requirement (2026-08-16): the receipt that leaves the office — the
PDF attached to the receipt email — MUST include the medallion logo, the
same mark shown in the site header.

The happy path was corroborated by execution: render_receipt_pdf() on a real
receipt embeds a raster image XObject built from web/seal.png. But
receipt_pdf._try_load_seal() has a SILENT fallback: if the PNG cannot be
loaded, it draws a vector placeholder instead — a receipt that LOOKS sealed
to code but does not carry the actual medallion. Nothing logged, nothing
failed. These tests make that fallback loud:

  * the repo seal must exist at the exact path the PDF resolves,
  * a rendered PDF must contain a raster /Image XObject (the placeholder
    path draws only vector strokes, so its absence is detectable),
  * the email side must reference the seal as its first inline image.

The email HTML hotlinks {SITE_URL}/seal.png. A separate live finding
(2026-08-16): Cloudflare edge colos held STALE generations of seal.png and
ignore query strings on images, so ?v= cannot bust them — only a cache purge
can. That is founder-gated (no CF token); this file guards the repo half.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import receipt_pdf  # noqa: E402


def _receipt() -> dict:
    p = ROOT / "data" / "receipts" / "-kIGMjku317gkCSB" / "receipt.json"
    if p.exists():
        return json.loads(p.read_text())
    return {  # minimal synthetic receipt when local data is absent (CI)
        "receipt_id": "TESTRECEIPT00001",
        "created_at": "2026-08-16T00:00:00+00:00",
        "hash_hex": "a" * 64,
        "calendars_ok": 5, "calendars_total": 5,
    }


class TestSentReceiptCarriesTheMedallion(unittest.TestCase):

    def test_seal_exists_at_the_resolved_path(self):
        p = Path(receipt_pdf.SEAL_PATH_DEFAULT)
        self.assertTrue(
            p.is_file(),
            f"receipt_pdf resolves the seal to {p} and it does not exist — "
            f"every emailed PDF would silently fall back to the vector "
            f"placeholder instead of the medallion")
        self.assertEqual(p.read_bytes()[:8], b"\x89PNG\r\n\x1a\n",
                         "seal file is not a PNG")

    def test_rendered_pdf_embeds_a_raster_seal_not_the_placeholder(self):
        pdf = receipt_pdf.render_receipt_pdf(_receipt())
        self.assertGreater(len(pdf), 10_000, "suspiciously small PDF")
        self.assertTrue(
            (b"/Subtype /Image" in pdf) or (b"/Subtype/Image" in pdf),
            "rendered PDF contains no raster image XObject — the seal load "
            "failed and the SILENT vector-placeholder fallback fired. The "
            "sent receipt would not carry the medallion.")

    def test_seal_loader_does_not_return_none_on_the_repo_seal(self):
        loaded = receipt_pdf._try_load_seal(receipt_pdf.SEAL_PATH_DEFAULT)
        self.assertIsNotNone(
            loaded,
            "_try_load_seal() returned None for the repo seal — decoder no "
            "longer handles this PNG; the fallback would fire silently")

    def test_receipt_email_leads_with_the_seal_image(self):
        src = (ROOT / "server" / "mailer.py").read_text()
        self.assertIn('seal_url = f"{SITE_URL}/seal.png"', src,
                      "the receipt email no longer references the seal")
        self.assertRegex(
            src, r'<img src="\{seal_url\}" alt="Orphograph"',
            "the seal <img> is gone from the email HTML — inbox preview "
            "tiles and the body would render without the medallion")


if __name__ == "__main__":
    unittest.main()
