#!/usr/bin/env python3
"""test_lifecycle_contract.py — docs/LIFECYCLE.md must match the code.

The drift this guards: a document that names verifier states and exit codes
goes stale the moment someone renames a constant or changes an exit code
(2026-08-22: the dist README, verify.py docstring AND the public
/docs/verify page still described exit 4 as a stdout-scrape, months after
otscheck replaced that logic — in four places, three of them shipped). So the
vocabulary is read FROM the document and compared TO the code in both
directions, and the PROSE in every shipped copy is checked for the old
scrape semantics, not just for two literal phrases.
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "LIFECYCLE.md"
DIST = ROOT / "dist" / "orphograph-verify"
WEB_DOC = ROOT / "web" / "docs" / "verify.html"
sys.path.insert(0, str(DIST))

import otscheck  # noqa: E402

_NON_PASS = ("PENDING", "FAILED", "UNAVAILABLE", "UNBOUND", "INDETERMINATE")

# Wordings of the pre-otscheck design (decide by scanning stdout for the hash,
# or "exit 4 = hash absent"). None may appear in any shipped copy.
_SCRAPE_PHRASES = (
    "absent from ots output",
    "not present in `ots verify` output",
    "anywhere in the ots output",
    "present root indicates",
    "inspects the output",
    "inspected for the",
    "searched for the hash as evidence of",      # allowed only when negated — checked below
    "ots verify <file>",
    "on a match and <code>1</code> on a mismatch",
    "1</code> on a mismatch",
)
_NEGATED_OK = ("never searched for the hash as evidence of", "never scanned for the hash")


def _fenced(doc: str, tag: str) -> str:
    m = re.search(rf"```{tag}\n(.*?)```", doc, re.S)
    assert m, f"docs/LIFECYCLE.md has no ```{tag} block"
    return m.group(1)


def _literal_returns(src: str) -> set[int]:
    """Every `return <int literal>` in the module, via the AST (any width)."""
    out: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, int) \
                and not isinstance(node.value.value, bool):
            out.add(node.value.value)
    return out


def _exit_constants(src: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id.startswith("EXIT_") and isinstance(node.value, ast.Constant):
            out[node.targets[0].id] = node.value.value
    return out


def _no_scrape_prose(text: str, label: str, tc: unittest.TestCase) -> None:
    low = " ".join(text.lower().split())  # collapse line breaks so wrapped prose still matches
    for phrase in _SCRAPE_PHRASES:
        if phrase in _NEGATED_OK or phrase.lower() not in low:
            continue
        if phrase == "searched for the hash as evidence of" and any(n in low for n in _NEGATED_OK):
            continue
        tc.fail(f"{label} still carries pre-otscheck wording: {phrase!r}")


class TestLifecycleContract(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.doc = DOC.read_text()
        cls.verify_src = (DIST / "verify.py").read_text()
        cls.verify_docstring = ast.get_docstring(ast.parse(cls.verify_src)) or ""
        cls.readme = (DIST / "README.md").read_text()
        cls.lineage_src = (DIST / "verify_lineage.py").read_text()
        cls.web_doc = WEB_DOC.read_text()

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

    # ---- exit codes: doc == verify.py literal returns == docstring == README ----

    def test_exit_codes_documented_match_verify_py(self):
        documented = {int(line.split()[0]) for line in _fenced(self.doc, "exits").strip().splitlines()}
        self.assertEqual(documented, _literal_returns(self.verify_src))
        # and the shipped docstring lists the same codes in its "Exit codes" block
        in_docstring = {int(m) for m in re.findall(r"^\s*(\d+)\s{2,}", self.verify_docstring, re.M)}
        self.assertEqual(documented, in_docstring, "verify.py docstring exit-code block drifted")
        for code in documented:
            self.assertRegex(self.readme, rf"- `{code}` —", f"README.md exit-code list lacks {code}")

    def test_exit_4_explained_as_non_pass_states_in_prose_not_just_code(self):
        """Each shipped copy must NAME the non-pass states in its PROSE (the
        docstring / README / LIFECYCLE / public page), not merely reference
        `otscheck.UNAVAILABLE` in code."""
        for label, text in (("verify.py docstring", self.verify_docstring),
                            ("dist README.md", self.readme),
                            ("docs/LIFECYCLE.md", self.doc),
                            ("web/docs/verify.html", self.web_doc)):
            for state in _NON_PASS:
                self.assertIn(state, text, f"{label} does not mention {state}")

    def test_no_shipped_copy_describes_the_old_stdout_scrape(self):
        for label, text in (("verify.py source", self.verify_src),
                            ("dist README.md", self.readme),
                            ("docs/LIFECYCLE.md", self.doc),
                            ("web/docs/verify.html", self.web_doc),
                            ("dist QUICKSTART.txt", (DIST / "QUICKSTART.txt").read_text())):
            _no_scrape_prose(text, label, self)

    def test_readme_and_docstring_state_the_real_client_argv(self):
        for label, text in (("verify.py docstring", self.verify_docstring), ("dist README.md", self.readme)):
            self.assertIn("ots verify -d", text, f"{label} does not state the `-d` binding argv")

    # ---- lineage exit constants: every EXIT_* documented with its value ------

    def test_every_lineage_exit_constant_is_documented(self):
        consts = _exit_constants(self.lineage_src)
        self.assertTrue(consts, "verify_lineage.py defines EXIT_* constants")
        block = _fenced(self.doc, "lineage_exits")
        documented = {}
        for line in block.strip().splitlines():
            code, name = line.split()[:2]
            documented[name] = int(code)
        self.assertEqual(documented, consts)

    # ---- every matrix row named in the doc exists as a test -------------------

    def test_every_matrix_row_names_an_existing_test(self):
        matrix_src = (ROOT / "tests" / "test_independent_verification_matrix.py").read_text()
        defined = set(re.findall(r"def (test_\w+)\(", matrix_src))
        named = set(re.findall(r"`(test_[a-z0-9_]+)`", self.doc))
        prefixes = set(re.findall(r"`(test_[a-z0-9_]+)_\*`", self.doc))
        exact = {n for n in named if not n.endswith("_")}
        missing = {n for n in exact if n not in defined}
        self.assertFalse(missing, f"doc names tests that do not exist: {sorted(missing)}")
        for p in prefixes:
            self.assertTrue(any(d.startswith(p) for d in defined), f"no test matches {p}_*")
        undocumented = {d for d in defined if d not in exact and not any(d.startswith(p) for p in prefixes)}
        self.assertFalse(undocumented, f"matrix tests missing from docs/LIFECYCLE.md §4: {sorted(undocumented)}")

    # ---- interface table names real keys -------------------------------------

    def test_mcp_lineage_keys_in_doc_exist_in_code(self):
        mcp_src = (ROOT / "mcp" / "orphograph_mcp.py").read_text()
        for key in ("ok", "tip", "chain", "depth", "broken_at", "forks_seen", "note", "depth_capped"):
            self.assertIn(f'"{key}"', mcp_src)
            self.assertIn(key, self.doc)
        self.assertNotIn("`all_ok`", self.doc, "doc names a key the MCP server never emits")

    def test_doc_does_not_reference_unshipped_sdk_modules(self):
        pkg = ROOT / "sdk-python" / "orphograph"
        for m in re.findall(r"`_(\w+)\.", self.doc):
            if m in ("client", "cli", "merkle"):
                self.assertTrue((pkg / f"_{m}.py").is_file())
            else:
                self.assertTrue((pkg / f"_{m}.py").is_file() or f"_{m}" == "_ed25519",
                                f"doc references sdk module _{m} which is not on this branch")

    # ---- no dead pointers; honest-claims framing is present ---------------------

    def test_no_dead_repo_pointer(self):
        self.assertNotIn("tracked in\n`ORPHOGRAPH_PRODUCTIZATION.md`", self.doc)
        self.assertIn("## 6. Staged follow-ups", self.doc)

    def test_doc_states_what_is_not_proven(self):
        for phrase in ("no identity assurance", "no legal admissibility", "no AI-detection", "no authorship"):
            self.assertIn(phrase, self.doc)


if __name__ == "__main__":
    unittest.main()
