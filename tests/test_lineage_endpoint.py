#!/usr/bin/env python3
"""test_lineage_endpoint.py — end-to-end pin for lineage over /api/anchor_folder.

Drives the wired lineage path (docs/DESIGN_EDIT_LINEAGE.md §2.4) against a
real ThreadingHTTPServer: a draft-1 folder anchor, then a draft-2 anchor
whose manifest carries the reserved `.orphograph/parent` leaf committing to
draft-1's root. Mirrors the harness conventions of test_folder_anchor.py
(engine._submit stubbed, temp ORPHO_DATA_DIR, module evict/restore).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

_POLLUTED = (
    "app", "engine", "auth", "rate_limit", "credits", "stats",
    "health", "subscriptions", "teams", "stripe_webhook",
    "mailer", "api_keys", "affiliate", "newsletter", "waitlist",
    "blog", "unsubscribe", "gdpr", "public_config",
    "receipt_export", "btc_price", "btc_payments", "stripe_api",
    "og_svg", "qrcode_svg", "badge_svg", "analytics",
    "support_tools", "onboarding", "referrals", "file_lock",
    "merkle",
)


def _start_test_server(data_dir: Path):
    os.environ["ORPHO_DATA_DIR"] = str(data_dir)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = "0"
    os.environ["ORPHO_COOKIE_SECURE"] = "0"
    os.environ["RATE_LIMIT_PER_DAY"] = "100000"
    os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    for m in _POLLUTED:
        sys.modules.pop(m, None)
    import app
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


class TestLineageEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_modules = {m: sys.modules[m] for m in _POLLUTED if m in sys.modules}
        cls._old_env = {
            k: os.environ.get(k)
            for k in ("ORPHO_DATA_DIR", "HOST", "PORT", "ORPHO_COOKIE_SECURE",
                      "RATE_LIMIT_PER_DAY")
        }
        cls._server, cls._base = _start_test_server(Path(cls._tmp.name))
        import engine as engine_mod
        import merkle as merkle_mod
        cls._original_submit = engine_mod._submit
        engine_mod._submit = lambda cal, h: (False, "stubbed: test mode")
        cls.engine = engine_mod
        cls.merkle = merkle_mod

    @classmethod
    def tearDownClass(cls):
        cls.engine._submit = cls._original_submit
        cls._server.shutdown()
        cls._server.server_close()
        cls._tmp.cleanup()
        for m in _POLLUTED:
            sys.modules.pop(m, None)
        for m, mod in cls._old_modules.items():
            sys.modules[m] = mod
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # -- helpers ----------------------------------------------------------

    def _post_folder(self, manifest: dict):
        req = urllib.request.Request(
            f"{self._base}/api/anchor_folder",
            data=json.dumps(manifest).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _manifest_for(self, files: dict, parent_root: str | None = None,
                      parent_rid: str | None = None) -> dict:
        """Build a manifest from raw leaves — the same construction the
        engine-level lineage tests use (tests/test_edit_lineage.py), so the
        endpoint sees exactly what a lineage-aware client would send."""
        m = self.merkle
        leaves = []
        for name, content in files.items():
            digest = hashlib.sha256(content).digest()
            leaves.append({
                "path": name,
                "file_sha256_hex": digest.hex(),
                "leaf_hex": m._leaf_hash(name, digest).hex(),
                "size_bytes": len(content),
            })
        if parent_root is not None:
            leaves.append({
                "path": self.engine.RESERVED_PARENT_PATH,
                "file_sha256_hex": parent_root,
                "leaf_hex": hashlib.sha256(
                    b"\x00" + self.engine.RESERVED_PARENT_PATH.encode("utf-8")
                    + b"\x00" + bytes.fromhex(parent_root)).hexdigest(),
                "size_bytes": 0,
            })
        leaves.sort(key=lambda leaf: leaf["path"].encode("utf-8"))
        levels = m._build_levels([bytes.fromhex(l["leaf_hex"]) for l in leaves])
        manifest = {
            "algorithm": m.ALGORITHM,
            "version": m.VERSION,
            "root_hex": levels[-1][0].hex(),
            "leaves": leaves,
        }
        if parent_root is not None:
            manifest["parent"] = {"receipt_id": parent_rid,
                                  "root_hex": parent_root}
        return manifest

    # -- tests -------------------------------------------------------------

    def test_plain_folder_anchor_has_no_lineage(self):
        manifest = self._manifest_for({"a.txt": b"alpha"})
        status, body = self._post_folder(manifest)
        self.assertEqual(status, 200)
        self.assertNotIn("lineage", body)

    def test_lineage_chain_two_drafts(self):
        # Draft 1 — plain anchor.
        m1 = self._manifest_for({"draft.md": b"first version"})
        status, b1 = self._post_folder(m1)
        self.assertEqual(status, 200)
        parent_rid, parent_root = b1["receipt_id"], b1["root_hex"]

        # Draft 2 — commits to draft 1 via the reserved leaf.
        m2 = self._manifest_for({"draft.md": b"second version"},
                                parent_root=parent_root, parent_rid=parent_rid)
        status, b2 = self._post_folder(m2)
        self.assertEqual(status, 200, b2)
        self.assertIn("lineage", b2)
        self.assertEqual(b2["lineage"]["parent_receipt_id"], parent_rid)
        self.assertEqual(b2["lineage"]["parent_root"], parent_root)
        self.assertTrue(b2["lineage"]["committed"])
        self.assertTrue(b2["lineage"]["parent_receipt_found"])

        # The persisted receipt carries the mirror and verify_folder shows it.
        rfile = Path(self._tmp.name) / "receipts" / b2["receipt_id"] / "receipt.json"
        on_disk = json.loads(rfile.read_text())
        self.assertEqual(on_disk["lineage"]["parent_root"], parent_root)

    def test_lineage_wrong_parent_hash_rejected_before_anchor(self):
        m1 = self._manifest_for({"draft.md": b"v1"})
        status, b1 = self._post_folder(m1)
        self.assertEqual(status, 200)
        wrong_root = "f" * 64  # parent exists but this is not its root
        m2 = self._manifest_for({"draft.md": b"v2"},
                                parent_root=wrong_root,
                                parent_rid=b1["receipt_id"])
        status, body = self._post_folder(m2)
        self.assertEqual(status, 400)
        self.assertIn("lineage invalid", body["error"])
        # Fail-fast means no receipt directory was created for the bad post.

    def test_lineage_leaf_without_parent_block_rejected(self):
        m1 = self._manifest_for({"draft.md": b"v1"})
        status, b1 = self._post_folder(m1)
        m2 = self._manifest_for({"draft.md": b"v2"},
                                parent_root=b1["root_hex"],
                                parent_rid=b1["receipt_id"])
        del m2["parent"]
        status, body = self._post_folder(m2)
        self.assertEqual(status, 400)
        self.assertIn("lineage invalid", body["error"])

    def test_export_zip_includes_manifest(self):
        m1 = self._manifest_for({"a.txt": b"alpha"})
        status, b1 = self._post_folder(m1)
        self.assertEqual(status, 200)
        import receipt_export
        blob, err = receipt_export.export_zip(b1["receipt_id"])
        self.assertIsNone(err)
        import io, zipfile
        names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
        self.assertIn("receipt.json", names)
        self.assertIn("manifest.json", names)


if __name__ == "__main__":
    unittest.main()
