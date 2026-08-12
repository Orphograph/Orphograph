#!/usr/bin/env python3
"""test_trust_architecture_section.py — the trust-architecture section holds.

Audit item 10 (2026-08-12): About's strongest reasoning — Orphograph issues,
OpenTimestamps constructs, Bitcoin provides permanence; the office may
disappear, the evidence does not — promoted onto the homepage. This pins it
against silent loss in a future migration pass, the exact failure mode the
homepage-hooks test exists for on the functional side.
"""
import unittest
from pathlib import Path

INDEX = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text()


class TestTrustArchitecture(unittest.TestCase):
    def test_three_layers_present_in_order(self):
        i = INDEX.find('class="orpho-card orpho-arch"')
        self.assertGreater(i, -1, "architecture section missing")
        section = INDEX[i:INDEX.find("</section>", i)]
        positions = [section.find(a) for a in
                     ("Orphograph", "OpenTimestamps", "Bitcoin")]
        self.assertTrue(all(p > -1 for p in positions), positions)
        self.assertEqual(positions, sorted(positions),
                         "layers out of order — the chain reads top-down")

    def test_kicker_survives(self):
        self.assertIn("The office may disappear.", INDEX)
        self.assertIn("The evidence does not.", INDEX)


if __name__ == "__main__":
    unittest.main()
