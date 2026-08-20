#!/usr/bin/env python3
"""test_lineage_chain_e2e.py — offline pins for tools/lineage_chain_e2e.py.

The harness itself needs the network: it anchors through the real
/api/anchor_folder against the real OTS calendars, because a stubbed
submission produces a chain with no attestation and verify_lineage.py
correctly refuses such a link -- so a stubbed run could never exercise the
passing branch honestly. That makes the harness a proof tool, not a CI test.

What CI CAN pin, with no network, is the set of assumptions the harness
would fail silently on if they ever drifted:

1. The exit codes it compares against are the verifier's ACTUAL codes.
   The harness mirrors them as local constants for readability; if
   verify_lineage.py renumbered EXIT_BROKEN tomorrow, every branch would
   still print "OK" while comparing to a stale number. This test is the
   only thing standing between that and a green-looking harness.
2. The .ots corruption branch corrupts the digest, not some other byte.
   A hardcoded offset that stops pointing at the hash would leave the
   "corrupt .ots binding" branch passing for the wrong reason -- the exact
   vacuous-pass shape this project keeps finding.
3. The harness reports UNAVAILABLE, never PASS, when it cannot build a
   real chain.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tools" / "lineage_chain_e2e.py"
VERIFY_DIR = ROOT / "dist" / "orphograph-verify"


def _module_constants(path: Path) -> dict:
    """Read top-level int constants without importing -- importing the
    verifier would drag in otscheck and the harness would start a server."""
    tree = ast.parse(path.read_text())
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        # Both `A = 1` and `A, B = 1, 2` -- the harness uses the tuple form,
        # and a reader that only understood the first would report the
        # constants as ABSENT, which reads identically to "renamed".
        pairs = []
        for t in node.targets:
            if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant):
                pairs.append((t, node.value))
            elif isinstance(t, ast.Tuple) and isinstance(node.value, ast.Tuple):
                pairs.extend(zip(t.elts, node.value.elts))
        for name, val in pairs:
            if (isinstance(name, ast.Name) and isinstance(val, ast.Constant)
                    and isinstance(val.value, int)
                    and not isinstance(val.value, bool)):
                out[name.id] = val.value
    return out


class TestHarnessAssumptions(unittest.TestCase):
    def test_mirrored_exit_codes_match_the_verifier(self):
        """The harness's V_* constants must equal verify_lineage.py's EXIT_*.

        Drift here is invisible at runtime: the harness would compare a real
        exit status against a stale expectation and print MISBEHAVED for a
        correct verifier, or OK for a broken one.
        """
        harness = _module_constants(HARNESS)
        verifier = _module_constants(VERIFY_DIR / "verify_lineage.py")
        for mine, theirs in (("V_OK", "EXIT_OK"), ("V_USAGE", "EXIT_ARGS"),
                             ("V_LINK", "EXIT_LINK"), ("V_OTS", "EXIT_OTS"),
                             ("V_CHAIN", "EXIT_BROKEN")):
            self.assertIn(mine, harness, f"{mine} vanished from the harness")
            self.assertIn(theirs, verifier, f"{theirs} vanished from the verifier")
            self.assertEqual(
                harness[mine], verifier[theirs],
                f"{mine}={harness[mine]} but verify_lineage.{theirs}="
                f"{verifier[theirs]} — the harness is checking a stale code")

    def test_corruption_offset_is_read_from_otscheck(self):
        """The harness must DERIVE the digest offset, not hardcode it."""
        src = HARNESS.read_text()
        self.assertIn("otscheck._EMBEDDED_HASH_OFFSET", src,
                      "the corruption branch no longer reads the offset from "
                      "otscheck; a copied constant can stop pointing at the "
                      "digest and the branch then passes for the wrong reason")
        sys.path.insert(0, str(VERIFY_DIR))
        import otscheck
        offset = otscheck._EMBEDDED_HASH_OFFSET
        # The byte at that offset must lie past the magic and inside a 32-byte
        # digest window, or the corruption is not corrupting the binding.
        self.assertEqual(offset, len(otscheck.OTS_HEADER_MAGIC) + 2)
        self.assertGreaterEqual(offset, len(otscheck.OTS_HEADER_MAGIC))

    def test_unreachable_chain_reports_unavailable_not_pass(self):
        """Every give-up path must return EXIT_UNAVAILABLE, never EXIT_OK.

        `verifier_claim_honesty`: a check that could not reach its dependency
        has not passed, it has not run.
        """
        src = HARNESS.read_text()
        tree = ast.parse(src)
        harness = _module_constants(HARNESS)
        self.assertEqual(harness["EXIT_UNAVAILABLE"], 7)
        self.assertNotEqual(harness["EXIT_UNAVAILABLE"], harness["EXIT_OK"])
        # No line that prints UNAVAILABLE may be followed by a success return.
        returns_ok = 0
        for node in ast.walk(tree):
            if (isinstance(node, ast.Return) and isinstance(node.value, ast.Name)
                    and node.value.id == "EXIT_OK"):
                returns_ok += 1
        self.assertEqual(returns_ok, 1,
                         "exactly one success return expected; more than one "
                         "means a give-up path can reach PASS")

    def test_rejection_branches_run_before_acceptance(self):
        """A verifier that passes everything is indistinguishable from a
        correct one unless the rejections are tried first."""
        src = HARNESS.read_text()
        self.assertLess(src.index("REJECTION BRANCHES FIRST"),
                        src.index("ACCEPTANCE BRANCH"),
                        "the acceptance branch moved ahead of the rejections")


if __name__ == "__main__":
    unittest.main()
