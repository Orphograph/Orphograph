#!/usr/bin/env python3
"""The offline kit's QUICKSTART must cover every evidence shape we ship.

Found 2026-08-09 by walking the stranger path for R12: the evidence ZIP for
a single-file receipt (receipt.json + five .ots — the most common package)
had NO documented recipe. QUICKSTART covered only the folder-receipt modes,
so a stranger's first attempt fed receipt.json to `verify.py file` and got
"missing required fields", exit 2. The rejection is correct; the missing
recipe was the defect — on the acceptance surface, where confusion costs
trust.
"""
import unittest
from pathlib import Path

QS = (Path(__file__).resolve().parent.parent /
      "dist" / "orphograph-verify" / "QUICKSTART.txt").read_text()


class TestQuickstartCoversEvidenceShapes(unittest.TestCase):
    def test_single_file_recipe_present(self):
        for needle in ("receipt.json", "shasum -a 256", "hash_hex",
                       "ots verify -f"):
            self.assertIn(needle, QS,
                          f"QUICKSTART lost the single-file recipe ({needle})")

    def test_folder_modes_still_documented(self):
        self.assertIn("verify.py file", QS)
        self.assertIn("verify.py folder", QS)

    def test_unavailable_semantics_still_documented(self):
        self.assertIn("UNAVAILABLE", QS)
