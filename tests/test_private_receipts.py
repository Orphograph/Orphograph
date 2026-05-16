#!/usr/bin/env python3
"""test_private_receipts.py — pin private-receipt access gate across all three
response shapes (.zip, /summary, JSON).

A regression where the gate was missing on .zip and /summary was found and
fixed; this test makes sure it stays fixed.

Strategy: drive engine.anchor_hash directly to materialize a receipt with
private=True + owner_id, then read it back through the public app paths via
a minimal HTTP test fixture (BaseHTTPRequestHandler instantiated against
test sockets).
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))


def _start_test_server(data_dir: Path) -> tuple[object, str]:
    """Boot app.py against a temp data dir on a random port. Returns
    (server_thread, base_url). Caller is responsible for shutdown."""
    os.environ["ORPHO_DATA_DIR"] = str(data_dir)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = "0"  # ephemeral
    os.environ["ORPHO_COOKIE_SECURE"] = "0"  # localhost
    os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    # Fresh module imports against the new data dir
    for m in list(sys.modules.keys()):
        if m in (
            "app", "engine", "auth", "rate_limit", "credits", "stats",
            "health", "subscriptions", "teams", "stripe_webhook",
            "mailer", "subscriptions", "api_keys", "affiliate", "newsletter",
            "waitlist", "blog", "unsubscribe", "gdpr", "public_config",
            "receipt_export", "btc_price", "btc_payments", "stripe_api",
            "og_svg", "qrcode_svg", "badge_svg", "analytics", "support_tools",
            "onboarding", "referrals", "file_lock",
        ):
            sys.modules.pop(m, None)
    import app
    from http.server import ThreadingHTTPServer

    # Use a private port; ThreadingHTTPServer with port 0 returns the chosen port.
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"


def _stop(server) -> None:
    server.shutdown()
    server.server_close()


class TestPrivateReceiptGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        # Snapshot the env + module state so we can restore in tearDownClass
        # and avoid leaking module eviction to later test files.
        cls._old_env = {
            k: os.environ.get(k)
            for k in ("ORPHO_DATA_DIR", "HOST", "PORT", "ORPHO_COOKIE_SECURE")
        }
        cls._old_modules = {
            m: sys.modules[m] for m in list(sys.modules.keys())
            if m in (
                "app", "engine", "auth", "rate_limit", "credits", "stats",
                "health", "subscriptions", "teams", "stripe_webhook",
                "mailer", "api_keys", "affiliate", "newsletter", "waitlist",
                "blog", "unsubscribe", "gdpr", "public_config",
                "receipt_export", "btc_price", "btc_payments", "stripe_api",
                "og_svg", "qrcode_svg", "badge_svg", "analytics",
                "support_tools", "onboarding", "referrals", "file_lock",
            )
        }
        cls._server, cls._base = _start_test_server(Path(cls._tmp.name))
        # Import the fresh engine for direct anchor calls
        import engine as engine_mod
        import auth as auth_mod
        cls.engine = engine_mod
        cls.auth = auth_mod
        # Synthesize a private receipt directly via the engine.
        owner_email = "owner@example.com"
        cls.owner_email = owner_email
        cls.owner_id = auth_mod.email_id(owner_email)
        # SHA-256 of an arbitrary string — engine validates hex shape.
        import hashlib
        digest = hashlib.sha256(b"private-test").hexdigest()
        # Anchor without actually hitting OTS calendars by stubbing _submit.
        original_submit = engine_mod._submit
        engine_mod._submit = lambda cal, h: (False, "stubbed: no network")
        try:
            rec = engine_mod.anchor_hash(
                digest,
                source=f"sub:{cls.owner_id}",
                private=True,
                owner_id=cls.owner_id,
            )
        finally:
            engine_mod._submit = original_submit
        cls.private_rid = rec["receipt_id"]
        # Also create a public receipt for the negative side.
        digest_pub = hashlib.sha256(b"public-test").hexdigest()
        engine_mod._submit = lambda cal, h: (False, "stubbed: no network")
        try:
            rec_pub = engine_mod.anchor_hash(digest_pub, source="free")
        finally:
            engine_mod._submit = original_submit
        cls.public_rid = rec_pub["receipt_id"]

    @classmethod
    def tearDownClass(cls):
        _stop(cls._server)
        cls._tmp.cleanup()
        # Restore module + env state so later test files import against the
        # real data dir, not the now-deleted temp dir.
        for m in list(sys.modules.keys()):
            if m in cls._old_modules or m in (
                "app", "engine", "auth", "rate_limit", "credits", "stats",
                "health", "subscriptions", "teams", "stripe_webhook",
                "mailer", "api_keys", "affiliate", "newsletter", "waitlist",
                "blog", "unsubscribe", "gdpr", "public_config",
                "receipt_export", "btc_price", "btc_payments", "stripe_api",
                "og_svg", "qrcode_svg", "badge_svg", "analytics",
                "support_tools", "onboarding", "referrals", "file_lock",
            ):
                sys.modules.pop(m, None)
        for m, mod in cls._old_modules.items():
            sys.modules[m] = mod
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _get(self, path: str, cookie: str | None = None) -> tuple[int, bytes, dict]:
        req = urllib.request.Request(self._base + path)
        if cookie:
            req.add_header("Cookie", cookie)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read() if hasattr(e, "read") else b"", dict(e.headers) if hasattr(e, "headers") else {}

    # ── JSON shape ──────────────────────────────────────────────────────

    def test_private_json_anonymous_gets_404(self):
        status, body, _ = self._get(f"/api/receipt/{self.private_rid}")
        self.assertEqual(status, 404, "anonymous viewer must not see private receipt")
        data = json.loads(body)
        self.assertFalse(data.get("found"))
        # Don't leak the existence — error message should be identical to a
        # truly-missing receipt.
        self.assertEqual(data.get("error"), "receipt not found")

    # ── ZIP shape (the critical regression path) ────────────────────────

    def test_private_zip_anonymous_gets_404(self):
        status, body, headers = self._get(f"/api/receipt/{self.private_rid}.zip")
        self.assertEqual(status, 404,
            "anonymous viewer must not download a private receipt's ZIP. "
            "This is the auth bypass the gate was fixed for.")
        # Body must NOT be a ZIP — verify it's the JSON error envelope
        self.assertNotEqual(body[:2], b"PK", "must not return zip bytes to non-owner")
        self.assertNotIn(b"application/zip", headers.get("Content-Type", "").encode())

    # ── /summary shape (other regression path) ──────────────────────────

    def test_private_summary_anonymous_gets_404(self):
        status, body, _ = self._get(f"/api/receipt/{self.private_rid}/summary")
        self.assertEqual(status, 404,
            "anonymous viewer must not see private receipt summary. "
            "This is the second auth-bypass surface the gate was fixed for.")
        data = json.loads(body)
        self.assertFalse(data.get("found"))

    # ── Public receipts still work for everyone ─────────────────────────

    def test_public_json_anonymous_works(self):
        status, body, _ = self._get(f"/api/receipt/{self.public_rid}")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data.get("found"))
        self.assertFalse(data.get("private"))

    def test_public_json_does_not_leak_owner_id(self):
        """Public receipts must not surface owner_id — otherwise an external
        observer can cluster all of a subscriber's public receipts by HMAC."""
        status, body, _ = self._get(f"/api/receipt/{self.public_rid}")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertNotIn("owner_id", data,
            "public receipt response must not include owner_id (HMAC clustering surface)")

    def test_public_summary_does_not_leak_owner_id(self):
        status, body, _ = self._get(f"/api/receipt/{self.public_rid}/summary")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertNotIn("owner_id", data)

    # ── Invalid ids ─────────────────────────────────────────────────────

    def test_invalid_id_zip(self):
        # Use only URL-safe chars that the receipt-id regex will reject —
        # avoid raw spaces (URL parser may reject before our app sees the request).
        status, _, _ = self._get("/api/receipt/has$$$dollar.zip")
        self.assertIn(status, (400, 404))

    def test_invalid_id_summary(self):
        status, _, _ = self._get("/api/receipt/notavalid$$$/summary")
        self.assertIn(status, (400, 404))


if __name__ == "__main__":
    unittest.main()
