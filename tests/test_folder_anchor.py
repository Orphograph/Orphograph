#!/usr/bin/env python3
"""test_folder_anchor.py — end-to-end pin for the folder-Merkle endpoints.

Drives the three new endpoints (`/api/anchor_folder`,
`/api/verify_folder/<id>`, `/api/inclusion_proof`) against a real
ThreadingHTTPServer instance, with the OTS network call stubbed via
``engine._submit`` so the test does not depend on calendar reachability.
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


def _start_test_server(data_dir: Path):
    os.environ["ORPHO_DATA_DIR"] = str(data_dir)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = "0"
    os.environ["ORPHO_COOKIE_SECURE"] = "0"
    # Prod free-tier caps anchors at 3/day per IP; the whole test class shares
    # one IP bucket, so lift the cap to keep multi-anchor tests independent.
    os.environ["RATE_LIMIT_PER_DAY"] = "100000"
    os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    for m in list(sys.modules.keys()):
        if m in (
            "app", "engine", "auth", "rate_limit", "credits", "stats",
            "health", "subscriptions", "teams", "stripe_webhook",
            "mailer", "api_keys", "affiliate", "newsletter",
            "waitlist", "blog", "unsubscribe", "gdpr", "public_config",
            "receipt_export", "btc_price", "btc_payments", "stripe_api",
            "og_svg", "qrcode_svg", "badge_svg", "analytics",
            "support_tools", "onboarding", "referrals", "file_lock",
            "merkle",
        ):
            sys.modules.pop(m, None)
    import app
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


class TestFolderAnchorFlow(unittest.TestCase):
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

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        # Snapshot every module we are about to evict so tearDownClass can
        # restore them; otherwise subsequent test files re-import them against
        # the temp data dir and get spurious ImportErrors.
        cls._old_modules = {
            m: sys.modules[m] for m in cls._POLLUTED if m in sys.modules
        }
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
        # Restore the original modules + env so this test file does not
        # poison later files in the same pytest run.
        for m in cls._POLLUTED:
            sys.modules.pop(m, None)
        for m, mod in cls._old_modules.items():
            sys.modules[m] = mod
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _build_folder(self) -> tuple[Path, object]:
        folder = Path(tempfile.mkdtemp())
        (folder / "a.txt").write_bytes(b"alpha")
        (folder / "b.txt").write_bytes(b"beta")
        (folder / "sub").mkdir()
        (folder / "sub" / "c.txt").write_bytes(b"gamma")
        tree = self.merkle.MerkleTree.from_folder(folder)
        return folder, tree

    def test_anchor_then_verify_then_inclusion(self):
        folder, tree = self._build_folder()
        manifest = tree.manifest()
        # POST /api/anchor_folder
        req = urllib.request.Request(
            f"{self._base}/api/anchor_folder",
            data=json.dumps(manifest).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read())
        rid = body["receipt_id"]
        self.assertEqual(body["root_hex"], tree.root_hex())
        self.assertEqual(body["leaf_count"], 3)
        self.assertEqual(body["kind"], "folder")
        self.assertEqual(body["merkle_algorithm"], "orphograph-merkle-v1-rfc6962")

        # GET /api/verify_folder/<rid>
        resp = urllib.request.urlopen(f"{self._base}/api/verify_folder/{rid}", timeout=5)
        self.assertEqual(resp.status, 200)
        vbody = json.loads(resp.read())
        self.assertEqual(vbody["receipt"]["receipt_id"], rid)
        self.assertEqual(vbody["receipt"]["kind"], "folder")
        self.assertEqual(vbody["manifest"]["root_hex"], tree.root_hex())
        self.assertEqual(len(vbody["manifest"]["leaves"]), 3)

        # GET /api/inclusion_proof for sub/c.txt
        from urllib.parse import quote
        url = f"{self._base}/api/inclusion_proof?receipt_id={rid}&path={quote('sub/c.txt')}"
        resp = urllib.request.urlopen(url, timeout=5)
        self.assertEqual(resp.status, 200)
        ipbody = json.loads(resp.read())
        self.assertEqual(ipbody["receipt_id"], rid)
        self.assertEqual(ipbody["path"], "sub/c.txt")
        self.assertEqual(ipbody["root_hex"], tree.root_hex())
        proof = [tuple(step) for step in ipbody["proof"]]
        # Verify locally as any third party would
        file_hash = hashlib.sha256(b"gamma").digest()
        ok = self.merkle.MerkleTree.verify_inclusion(
            file_hash, "sub/c.txt", proof, bytes.fromhex(tree.root_hex())
        )
        self.assertTrue(ok)

    def test_certificate_view_renders(self):
        # Anchor a folder, then confirm the hosted /certificate/<id> page
        # renders with the id templated in and the renderer wired up.
        folder, tree = self._build_folder()
        req = urllib.request.Request(
            f"{self._base}/api/anchor_folder",
            data=json.dumps(tree.manifest()).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        rid = json.loads(urllib.request.urlopen(req, timeout=10).read())["receipt_id"]

        resp = urllib.request.urlopen(f"{self._base}/certificate/{rid}", timeout=5)
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))
        html = resp.read().decode("utf-8")
        self.assertIn(rid, html)                          # id templated in
        self.assertNotIn("{{RECEIPT_ID}}", html)          # placeholder gone
        self.assertIn("/certificate.js", html)            # renderer loaded
        self.assertIn("Dataset Provenance Certificate", html)
        # Strict-CSP contract: the page must carry no inline styles or scripts
        # (style-src/script-src are 'self' only — inline would be dropped).
        import re as _re
        self.assertNotIn("style=", html)
        self.assertNotIn("<style", html)
        self.assertEqual(_re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html), [])
        # Accessibility contract: manifest headers are scoped and the dynamic
        # result/error regions announce to screen readers.
        self.assertIn('scope="col"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn('role="alert"', html)

        # The single-file verify API must expose kind=folder so /r/<id>
        # can client-side redirect folder anchors to the certificate view.
        vresp = urllib.request.urlopen(f"{self._base}/api/verify/{rid}", timeout=5)
        self.assertEqual(json.loads(vresp.read()).get("kind"), "folder")

    def test_certificate_bad_id_rejected(self):
        try:
            urllib.request.urlopen(f"{self._base}/certificate/bad.id", timeout=5)
            self.fail("expected HTTP 400 for an invalid receipt id")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_sample_folder_receipt_seeds_and_renders_full(self):
        # The permanent dataset-provenance sample seeds from web/sample-folder/
        # and renders a full (non-redacted) certificate publicly via paths_public.
        import app as _app
        _app._seed_sample_folder_receipt()
        rid = "DatasetProvenanceSample"

        resp = urllib.request.urlopen(f"{self._base}/certificate/{rid}", timeout=5)
        self.assertEqual(resp.status, 200)
        self.assertIn(rid, resp.read().decode("utf-8"))

        resp = urllib.request.urlopen(f"{self._base}/api/verify_folder/{rid}", timeout=5)
        d = json.loads(resp.read())
        self.assertEqual(d["receipt"]["kind"], "folder")
        self.assertTrue(d["receipt"].get("paths_public"))
        self.assertEqual(d["receipt"]["leaf_count"], 8)
        # paths_public => the manifest is NOT redacted; real paths are visible.
        self.assertNotIn("paths_redacted", d["manifest"])
        self.assertEqual(len(d["manifest"]["leaves"]), 8)
        self.assertIsNotNone(d["manifest"]["leaves"][0].get("path"))
        # The committed .ots proofs validate against the anchored root.
        self.assertEqual(d["manifest"]["root_hex"], d["receipt"]["hash_hex"])
        self.assertTrue(all(c["ok"] for c in d["receipt"]["checks"]))

    def test_paths_public_opt_in_at_anchor_time(self):
        # Default anchor: a non-owner sees redacted leaf paths.
        folder_a = Path(tempfile.mkdtemp())
        (folder_a / "private-a.txt").write_bytes(b"default redacted content")
        (folder_a / "private-b.txt").write_bytes(b"more default content")
        tree_a = self.merkle.MerkleTree.from_folder(folder_a)
        req = urllib.request.Request(
            f"{self._base}/api/anchor_folder",
            data=json.dumps(tree_a.manifest()).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        rid_a = json.loads(urllib.request.urlopen(req, timeout=10).read())["receipt_id"]
        vf_a = json.loads(urllib.request.urlopen(
            f"{self._base}/api/verify_folder/{rid_a}", timeout=5).read())
        self.assertTrue(vf_a["manifest"].get("paths_redacted"))

        # paths_public anchor (wrapped body): NOT redacted, flag echoed + stored.
        folder_b = Path(tempfile.mkdtemp())
        (folder_b / "public-x.txt").write_bytes(b"published path content")
        (folder_b / "public-y.txt").write_bytes(b"second published file")
        tree_b = self.merkle.MerkleTree.from_folder(folder_b)
        body = {"manifest": tree_b.manifest(), "paths_public": True}
        req = urllib.request.Request(
            f"{self._base}/api/anchor_folder",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        resp_b = json.loads(urllib.request.urlopen(req, timeout=10).read())
        self.assertTrue(resp_b.get("paths_public"))
        rid_b = resp_b["receipt_id"]
        vf_b = json.loads(urllib.request.urlopen(
            f"{self._base}/api/verify_folder/{rid_b}", timeout=5).read())
        self.assertNotIn("paths_redacted", vf_b["manifest"])
        self.assertTrue(vf_b["receipt"].get("paths_public"))
        self.assertIsNotNone(vf_b["manifest"]["leaves"][0].get("path"))

    def test_folder_js_emits_server_format_manifest(self):
        # Regression guard (#24): web/folder.js must build a server-format
        # {leaves:[{path,file_sha256_hex,leaf_hex,size_bytes}], version}
        # manifest — not the {files:[...]} summary the server rejects.
        src = (ROOT / "web" / "folder.js").read_text()
        self.assertIn("leaves:", src)
        self.assertIn("file_sha256_hex:", src)
        self.assertIn("leaf_hex:", src)
        self.assertIn("size_bytes:", src)
        self.assertIn("version: 1", src)
        self.assertNotIn("files: hashed.map", src)  # the old broken shape

    def test_server_rejects_files_shape_accepts_leaves_shape(self):
        # The /api/anchor_folder contract the folder.js fix depends on.
        folder, tree = self._build_folder()
        man = tree.manifest()
        files_shape = {
            "algorithm": man["algorithm"], "root_hex": man["root_hex"],
            "file_count": len(man["leaves"]),
            "files": [{"path": l["path"], "file_sha256": l["file_sha256_hex"]}
                      for l in man["leaves"]],
        }
        req = urllib.request.Request(
            f"{self._base}/api/anchor_folder",
            data=json.dumps(files_shape).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("expected 400 for the {files} shape")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
        req = urllib.request.Request(
            f"{self._base}/api/anchor_folder",
            data=json.dumps(man).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        self.assertEqual(urllib.request.urlopen(req, timeout=10).status, 200)

    def test_bad_manifest_rejected(self):
        bad = {"algorithm": "wrong-algo", "version": 1, "root_hex": "00" * 32, "leaves": []}
        req = urllib.request.Request(
            f"{self._base}/api/anchor_folder",
            data=json.dumps(bad).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 400)

    def test_inclusion_proof_unknown_path_404(self):
        folder, tree = self._build_folder()
        req = urllib.request.Request(
            f"{self._base}/api/anchor_folder",
            data=json.dumps(tree.manifest()).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        rid = json.loads(urllib.request.urlopen(req, timeout=10).read())["receipt_id"]
        from urllib.parse import quote
        url = f"{self._base}/api/inclusion_proof?receipt_id={rid}&path={quote('does/not/exist.txt')}"
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(url, timeout=5)
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
