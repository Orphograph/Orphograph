#!/usr/bin/env python3
"""test_lifecycle_contract.py — docs/LIFECYCLE.md must match the code.

The drift this guards: a document that names verifier states and exit codes
goes stale the moment someone renames a constant or changes an exit code
(2026-08-22: the dist README and verify.py docstring still described exit 4
as "root_hex absent from ots output" months after otscheck replaced that
logic). So the vocabulary is read FROM the document and compared TO the code,
in both directions.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "LIFECYCLE.md"
DIST = ROOT / "dist" / "orphograph-verify"
sys.path.insert(0, str(DIST))

import otscheck  # noqa: E402

_NON_PASS = ("PENDING", "FAILED", "UNAVAILABLE", "UNBOUND", "INDETERMINATE")


def _fenced(doc: str, tag: str) -> str:
    m = re.search(rf"```{tag}\n(.*?)```", doc, re.S)
    assert m, f"docs/LIFECYCLE.md has no ```{tag} block"
    return m.group(1)


class TestLifecycleContract(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.doc = DOC.read_text()

    # ---- state vocabulary: doc == otscheck, both directions ----------------

    def test_state_vocabulary_matches_otscheck_exactly(self):
        documented = set(_fenced(self.doc, "states").split())
        in_code = {
            name for name, val in vars(otscheck).items()
            if name.isupper() and isinstance(val, str) and val == name
        }
        self.assertEqual(documented, in_code)

    def test_only_verified_is_a_pass(self):
        self.assertEqual(tuple(otscheck.PASSING), ("VERIFIED",))
        self.assertIn("Only `VERIFIED` is a pass", self.doc)

    # ---- exit codes: doc == verify.py docstring == README -------------------

    def test_exit_codes_documented_match_verify_py(self):
        documented = {int(line.split()[0]) for line in _fenced(self.doc, "exits").strip().splitlines()}
        src = (DIST / "verify.py").read_text()
        returned = {int(m) for m in re.findall(r"^\s+return (\d)\b", src, re.M)}
        # main() may also return the sub-check code directly (`return sub`);
        # the literal returns are the contract surface.
        self.assertEqual(documented, returned)

    def test_exit_4_explained_as_non_pass_states_everywhere(self):
        """The stale wording this file exists to catch: exit 4 is NOT
        'root_hex absent from ots output'. All three places must name the
        non-pass states."""
        for path in (DIST / "verify.py", DIST / "README.md", DOC):
            text = path.read_text()
            self.assertNotIn("absent from ots output", text, path.name)
            self.assertNotIn("not present in `ots verify` output", text, path.name)
            for state in _NON_PASS:
                self.assertIn(state, text, f"{path.name} does not mention {state}")

    def test_lineage_exit_codes_documented(self):
        src = (DIST / "verify_lineage.py").read_text()
        m = re.search(r"EXIT_BROKEN\s*=\s*(\d+)", src)
        self.assertIsNotNone(m, "verify_lineage.py defines EXIT_BROKEN")
        self.assertIn(f"`{m.group(1)}` chain broken", self.doc)

    # ---- every matrix row named in the doc exists as a test -------------------

    def test_every_matrix_row_names_an_existing_test(self):
        matrix_src = (ROOT / "tests" / "test_independent_verification_matrix.py").read_text()
        defined = set(re.findall(r"def (test_\w+)\(", matrix_src))
        named = set(re.findall(r"`(test_[a-z0-9_]+)`", self.doc))
        # wildcard references like `test_missing_*` are prefixes
        prefixes = set(re.findall(r"`(test_[a-z0-9_]+)_\*`", self.doc))
        exact = {n for n in named if not n.endswith("_")}
        missing = {n for n in exact if n not in defined}
        self.assertFalse(missing, f"doc names tests that do not exist: {sorted(missing)}")
        for p in prefixes:
            self.assertTrue(any(d.startswith(p) for d in defined), f"no test matches {p}_*")
        # and the reverse: no matrix test is undocumented
        undocumented = {d for d in defined if d not in exact and not any(d.startswith(p) for p in prefixes)}
        self.assertFalse(undocumented, f"matrix tests missing from docs/LIFECYCLE.md §4: {sorted(undocumented)}")

    # ---- honest-claims framing is present ------------------------------------

    def test_doc_states_what_is_not_proven(self):
        for phrase in ("no identity assurance", "no legal admissibility", "no AI-detection", "no authorship"):
            self.assertIn(phrase, self.doc)


if __name__ == "__main__":
    unittest.main()
