#!/usr/bin/env python3
"""test_certificate_presentation.py — the receipt page reads as a certificate.

Audit P3 (2026-08-12): the receipt is the most shareable artifact and must
present as an archival certificate — while the honesty box stays. A
certificate that lost 'What it does not prove' would be a worse product
than the plain page it replaced.
"""
import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "web" / "receipt.html").read_text()


class TestCertificatePresentation(unittest.TestCase):
    def test_certificate_titling(self):
        self.assertIn('class="cert-title">Certificate of Existence<', HTML)
        self.assertIn("Empirical Notary", HTML)

    def test_evidence_package_button_kept_its_hook(self):
        i = HTML.find('id="download-zip"')
        self.assertGreater(i, -1, "download hook lost")
        self.assertIn("evidence package", HTML[i:i + 300])

    def test_honesty_box_survives_the_certificate(self):
        self.assertIn("What it does not prove", HTML)
        self.assertIn("not a claim of authorship and not a legal determination",
                      HTML)


if __name__ == "__main__":
    unittest.main()
