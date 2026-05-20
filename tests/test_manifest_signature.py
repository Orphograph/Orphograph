#!/usr/bin/env python3
"""test_manifest_signature.py — Ed25519 manifest signature pin.

The signature block is OPTIONAL — manifests without it must still anchor.
When present, it MUST verify; a manifest that claims a signature but fails
verification is rejected.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import manifest_signature as ms  # noqa: E402


def _sample_manifest() -> dict:
    """A minimal but shape-valid folder manifest for signing tests."""
    return {
        "algorithm": "orphograph-merkle-v1-rfc6962",
        "version": 1,
        "root_hex": "a" * 64,
        "leaves": [
            {
                "path": "doc.txt",
                "file_sha256_hex": "b" * 64,
                "leaf_hex": "c" * 64,
                "size_bytes": 12,
            }
        ],
    }


class TestManifestSignature(unittest.TestCase):

    def test_sign_then_verify_round_trips(self):
        priv = os.urandom(32)
        signed = ms.sign_manifest(_sample_manifest(), priv)
        self.assertIn("signature", signed)
        block = signed["signature"]
        self.assertEqual(block["alg"], "EdDSA")
        self.assertEqual(block["curve"], "Ed25519")
        self.assertTrue(block["kid"].startswith("did:key:z6Mk"))
        ok, reason = ms.verify_manifest_signature(signed)
        self.assertTrue(ok, reason)

    def test_tampered_leaf_invalidates_signature(self):
        priv = os.urandom(32)
        signed = ms.sign_manifest(_sample_manifest(), priv)
        # Flip a single byte in the committed leaf hash.
        signed["leaves"][0]["leaf_hex"] = "d" + signed["leaves"][0]["leaf_hex"][1:]
        ok, reason = ms.verify_manifest_signature(signed)
        self.assertFalse(ok)
        self.assertIn("signature does not verify", reason)

    def test_tampered_root_hex_invalidates_signature(self):
        priv = os.urandom(32)
        signed = ms.sign_manifest(_sample_manifest(), priv)
        signed["root_hex"] = "e" * 64
        ok, _ = ms.verify_manifest_signature(signed)
        self.assertFalse(ok)

    def test_post_anchor_fields_do_not_break_signature(self):
        """The server appends receipt_id and kind AFTER signing — the
        verifier must strip them before recomputing the canonical bytes."""
        priv = os.urandom(32)
        signed = ms.sign_manifest(_sample_manifest(), priv)
        # Simulate the server side: post-anchor it adds these two fields.
        signed["receipt_id"] = "ABCDEFGHIJKLMNOP"
        signed["kind"] = "folder"
        ok, _ = ms.verify_manifest_signature(signed)
        self.assertTrue(ok)

    def test_did_key_round_trip_recovers_public_key(self):
        priv = os.urandom(32)
        signed = ms.sign_manifest(_sample_manifest(), priv)
        kid = signed["signature"]["kid"]
        pub = ms.public_key_from_did_key(kid)
        self.assertEqual(len(pub), 32)
        self.assertEqual(ms.derive_did_key(pub), kid)

    def test_canonical_bytes_drops_signature_field(self):
        m = _sample_manifest()
        a = ms.canonical_manifest_bytes(m)
        m_with_sig = dict(m)
        m_with_sig["signature"] = {"any": "thing"}
        b = ms.canonical_manifest_bytes(m_with_sig)
        self.assertEqual(a, b)

    def test_canonical_bytes_is_sorted_and_compact(self):
        # sort_keys + tight separators + ascii-only is what made the bytes
        # deterministic across hosts. A loose dump produces extra whitespace.
        m = _sample_manifest()
        canon = ms.canonical_manifest_bytes(m)
        # No spaces after separators.
        self.assertNotIn(b": ", canon)
        self.assertNotIn(b", ", canon)
        # Re-parsing yields the same logical dict (minus signature field).
        parsed = json.loads(canon.decode("ascii"))
        self.assertEqual(parsed["root_hex"], m["root_hex"])

    def test_verify_returns_false_when_signature_absent(self):
        # Callers should branch on the field's presence; the function
        # still defines a deterministic behaviour for the no-signature case.
        ok, reason = ms.verify_manifest_signature(_sample_manifest())
        self.assertFalse(ok)
        self.assertEqual(reason, "no signature present")

    def test_invalid_kid_form_rejected(self):
        priv = os.urandom(32)
        signed = ms.sign_manifest(_sample_manifest(), priv)
        signed["signature"]["kid"] = "did:web:example.com"
        ok, reason = ms.verify_manifest_signature(signed)
        self.assertFalse(ok)
        self.assertIn("kid", reason)

    def test_resign_replaces_block_rather_than_nesting(self):
        priv1 = os.urandom(32)
        priv2 = os.urandom(32)
        signed_once = ms.sign_manifest(_sample_manifest(), priv1)
        signed_twice = ms.sign_manifest(signed_once, priv2)
        # The block was REPLACED, not stacked — the new signature verifies
        # and corresponds to priv2's did:key.
        ok, _ = ms.verify_manifest_signature(signed_twice)
        self.assertTrue(ok)
        self.assertNotEqual(
            signed_once["signature"]["kid"], signed_twice["signature"]["kid"]
        )


if __name__ == "__main__":
    unittest.main()
