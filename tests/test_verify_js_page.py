"""Regression guards for the /verify-js page's inline verifier (AUDIT D1/D5).

Two layers:

1. Static guards (always run): the page's verifier script must compare the
   STORED receipt hash verbatim — no ``.toLowerCase()`` on the receipt side,
   no alias hash fields. The engine is canon
   (``server/engine.py:verify_hash_against_receipt`` lowercases only the
   supplied side); a page that lowercases the stored hash calls a tampered,
   byte-for-byte-different receipt "verified".

2. Behavioural conformance (when node is available): drives the page's REAL
   verifier script through ``tests/js/verify_js_page.test.mjs`` and the
   standalone module through ``tests/js/verifier_module.test.mjs`` with a
   tampered-fixture suite (uppercase / mixed-case / alias-only receipts).
   Skipped with an explicit message when node is absent — that skip is the
   documented JS coverage gap for such environments.

The script used to live in an inline ``<script>`` block in
``web/verify-js.html``. Under the site CSP (``script-src 'self'``) that block
never executed, so it was externalized to ``web/verify-js.js`` — which is now
the source of truth these guards read.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAGE_SCRIPT = REPO / "web" / "verify-js.js"
MODULE = REPO / "verifier-js" / "orphograph_verify.js"
JS_TESTS = [
    REPO / "tests" / "js" / "verify_js_page.test.mjs",
    REPO / "tests" / "js" / "verifier_module.test.mjs",
]


class TestVerifyJsPageStaticGuards(unittest.TestCase):
    def setUp(self):
        if not PAGE_SCRIPT.is_file():
            raise AssertionError(f"verifier script missing: {PAGE_SCRIPT}")
        self.script = PAGE_SCRIPT.read_text()

    def test_receipt_side_is_never_lowercased(self):
        # The exact regression that shipped: receiptSha256/512 lowercased
        # before comparison, so an uppercase-tampered receipt "verified".
        self.assertNotRegex(
            self.script,
            r"receiptSha(256|512)\s*=\s*\(?receiptSha(256|512)[^;]*toLowerCase",
            "the stored receipt hash must be compared verbatim (AUDIT D1)",
        )

    def test_no_alias_hash_fields(self):
        # Canonical fields only (VERIFIER_SPEC §3.3): hash_hex / sha512_hex.
        self.assertNotIn("sha256_hex ||", self.script)
        self.assertNotIn("r.sha256)", self.script)
        self.assertNotIn("receipt.sha256 ", self.script)

    def test_module_receipt_side_verbatim(self):
        src = MODULE.read_text()
        self.assertIn('String(receipt.hash_hex || "")', src)
        self.assertNotRegex(
            src, r"receiptSha(256|512)\s*=[^;]*toLowerCase",
            "verifier-js must compare the stored hash verbatim (AUDIT D1)",
        )


class TestVerifyJsBehaviouralConformance(unittest.TestCase):
    def test_node_suite_tampered_fixtures(self):
        node = shutil.which("node")
        if not node:
            self.skipTest(
                "node not available: the JS tampered-fixture conformance suite "
                "(tests/js/*.test.mjs) was not executed in this environment — "
                "the static guards above still pin the comparison semantics."
            )
        proc = subprocess.run(
            [node, "--test", *[str(p) for p in JS_TESTS]],
            capture_output=True,
            text=True,
            cwd=str(REPO),
            timeout=120,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"node --test failed:\n{proc.stdout}\n{proc.stderr}",
        )
        # Reporter-agnostic: spec reporter prints "fail 0", tap prints "# fail 0".
        self.assertIn("fail 0", proc.stdout)


if __name__ == "__main__":
    unittest.main()
