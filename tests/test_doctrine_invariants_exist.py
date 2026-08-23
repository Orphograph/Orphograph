#!/usr/bin/env python3
"""DOCTRINE.md's "Code invariant" lines cite implementation files by path. The
doctrine's own thesis is that it "cannot drift from the implementation without
the implementation breaking first" — but nothing checked that the files it
cites actually exist, so a phantom citation slipped in undetected:
`tools/regulatory_self_audit.py` was cited as the copy-guard and did not exist
anywhere in the repo (the real guards are scripts/regulated_term_scan.py +
scripts/compliance_scan.py + the CI-gated claim tests).

This test closes that class: every repo-relative source path DOCTRINE.md cites
in backticks must exist. Served routes (leading `/`), the deliberately
gitignored CLAUDE.md, and the documented post-anchor artifacts under
deploy/genesis/ are out of scope by design.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCTRINE = ROOT / "DOCTRINE.md"

# A cited path is "a code invariant that must exist" when it lives under one of
# the source trees. This is deliberately narrow: it will not flag a served
# route (/learn.html), CLAUDE.md (gitignored by design), or a genesis artifact
# that the doctrine itself says is created after the first anchor.
_SOURCE_PREFIXES = ("tools/", "scripts/", "server/", "tests/", "web/")
_BACKTICKED = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|js|html|txt|json))`")


def _cited_source_paths(text: str) -> list[str]:
    out = []
    for m in _BACKTICKED.finditer(text):
        path = m.group(1)
        if path.startswith(_SOURCE_PREFIXES) and not path.startswith("deploy/genesis/"):
            out.append(path)
    return sorted(set(out))


class TestDoctrineInvariantsExist(unittest.TestCase):
    def test_doctrine_file_present(self):
        self.assertTrue(DOCTRINE.is_file(), "DOCTRINE.md is missing")

    def test_every_cited_source_file_exists(self):
        cited = _cited_source_paths(DOCTRINE.read_text(encoding="utf-8"))
        self.assertTrue(cited, "extractor found no cited source paths — the regex or "
                               "doctrine changed shape; investigate before trusting a pass")
        missing = [p for p in cited if not (ROOT / p).exists()]
        self.assertEqual(missing, [], f"DOCTRINE.md cites source files that do not exist: {missing}")

    def test_negative_control_the_extractor_catches_a_phantom(self):
        # The exact drift this test exists to catch: the pre-fix doctrine text.
        phantom = "> The self-audit (`tools/regulatory_self_audit.py`) blocks deploys.\n"
        cited = _cited_source_paths(phantom)
        self.assertIn("tools/regulatory_self_audit.py", cited,
                      "extractor failed to pick up a backticked source path")
        self.assertFalse((ROOT / "tools/regulatory_self_audit.py").exists(),
                         "control assumes this phantom path is absent")


if __name__ == "__main__":
    unittest.main()
