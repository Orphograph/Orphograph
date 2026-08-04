#!/usr/bin/env python3
"""test_lightning_l402.py — L402 pay-per-anchor over the REAL HTTP handler.

Every test drives a live ThreadingHTTPServer (this session's hard lesson:
engine-level green means nothing until the wire path is proven). The mock
Lightning backend is enabled via ORPHO_LN_BACKEND=mock +
ORPHO_LN_ALLOW_MOCK=1 — invoices settle only through lightning.mock_pay(),
so "paid" is always an explicit test action, never a default.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
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
    "merkle", "lightning",
)
_ENV = ("ORPHO_DATA_DIR", "HOST", "PORT", "ORPHO_COOKIE_SECURE",
        "RATE_LIMIT_PER_DAY", "ORPHO_LN_BACKEND", "ORPHO_LN_ALLOW_MOCK",
        "ORPHO_LN_PRICE_SATS")

HASH_A = "aa" * 32
HASH_B = "bb" * 32
HASH_C = "cc" * 32


class TestL402(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_env = {k: os.environ.get(k) for k in _ENV}
        cls._old_modules = {m: sys.modules[m] for m in _POLLUTED if m in sys.modules}
        os.environ["ORPHO_DATA_DIR"] = cls._tmp.name
        os.environ["HOST"] = "127.0.0.1"
        os.environ["PORT"] = "0"
        os.environ["ORPHO_COOKIE_SECURE"] = "0"
        # Free tier effectively OFF so unauthenticated posts hit the paywall
        # deterministically (limit 1: first free anchor passes, rest 402).
        os.environ["RATE_LIMIT_PER_DAY"] = "1"
        os.environ["ORPHO_LN_BACKEND"] = "mock"
        os.environ["ORPHO_LN_ALLOW_MOCK"] = "1"
        for m in _POLLUTED:
            sys.modules.pop(m, None)
        import app
        import engine as engine_mod
        import lightning as lightning_mod
        from http.server import ThreadingHTTPServer
        cls.lightning = lightning_mod
        cls._orig_submit = engine_mod._submit
        engine_mod._submit = lambda cal, h: (True, b"\xf0stub-calendar-body")
        cls._server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls._base = f"http://127.0.0.1:{cls._server.server_address[1]}"
        threading.Thread(target=cls._server.serve_forever, daemon=True).start()
        cls.engine = engine_mod

    @classmethod
    def tearDownClass(cls):
        cls.engine._submit = cls._orig_submit
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

    # ── helpers ──────────────────────────────────────────────────────────

    def _post(self, path, body, headers=None):
        req = urllib.request.Request(
            f"{self._base}{path}", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            return resp.status, dict(resp.headers), json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), json.loads(e.read())

    def _paid_credential(self):
        """Quote → mock-pay → (macaroon, preimage)."""
        s, _, q = self._post("/api/ln/quote", {})
        self.assertEqual(s, 200, q)
        preimage = self.lightning.mock_pay(
            self.lightning.parse_macaroon(q["macaroon"])["payment_hash"])
        self.assertIsNotNone(preimage)
        return q["macaroon"], preimage

    def _exhaust_free_tier(self):
        self._post("/api/anchor", {"hash_hex": HASH_C})

    # ── tests ────────────────────────────────────────────────────────────

    def test_01_free_tier_still_works_without_payment(self):
        s, _, b = self._post("/api/anchor", {"hash_hex": HASH_A})
        self.assertEqual(s, 200, b)
        self.assertTrue(b["receipt_id"])

    def test_02_past_free_tier_returns_402_challenge(self):
        self._exhaust_free_tier()
        s, h, b = self._post("/api/anchor", {"hash_hex": HASH_B})
        self.assertEqual(s, 402, b)
        self.assertIn("L402", h.get("WWW-Authenticate", ""))
        self.assertIn("invoice", b)
        self.assertIn("macaroon", b)
        self.assertEqual(b["price_sats"], self.lightning.PRICE_SATS)

    def test_03_paid_l402_anchors_once_then_replay_rejected(self):
        self._exhaust_free_tier()
        macaroon, preimage = self._paid_credential()
        auth = {"Authorization": f"L402 {macaroon}:{preimage}"}
        s, _, b = self._post("/api/anchor", {"hash_hex": HASH_B}, auth)
        self.assertEqual(s, 200, b)
        rid = b["receipt_id"]
        # Source is tagged as a Lightning anchor on disk.
        on_disk = json.loads(
            (Path(self._tmp.name) / "receipts" / rid / "receipt.json").read_text())
        self.assertTrue(on_disk["source"].startswith("ln:"))
        # Replay: the same credential must never buy a second anchor.
        s, _, b = self._post("/api/anchor", {"hash_hex": HASH_A}, auth)
        self.assertEqual(s, 401)
        self.assertIn("already spent", b["error"])

    def test_04_unpaid_invoice_rejected(self):
        self._exhaust_free_tier()
        s, _, q = self._post("/api/ln/quote", {})
        doc = self.lightning.parse_macaroon(q["macaroon"])
        fake_preimage = "00" * 32
        s, _, b = self._post("/api/anchor", {"hash_hex": HASH_B},
                             {"Authorization": f"L402 {q['macaroon']}:{fake_preimage}"})
        self.assertEqual(s, 401)
        # Wrong preimage fails the hash binding before settlement is asked.
        self.assertIn("preimage", b["error"])
        self.assertFalse(self.lightning.invoice_settled(doc["payment_hash"]))

    def test_05_tampered_macaroon_rejected(self):
        self._exhaust_free_tier()
        macaroon, preimage = self._paid_credential()
        body, sig = macaroon.split(".", 1)
        tampered = body + "." + ("A" + sig[1:] if sig[0] != "A" else "B" + sig[1:])
        s, _, b = self._post("/api/anchor", {"hash_hex": HASH_B},
                             {"Authorization": f"L402 {tampered}:{preimage}"})
        self.assertEqual(s, 401)
        self.assertIn("macaroon", b["error"])

    def test_06_quote_503_when_unconfigured(self):
        old = os.environ.pop("ORPHO_LN_ALLOW_MOCK")
        try:
            s, _, b = self._post("/api/ln/quote", {})
            self.assertEqual(s, 503)
            # And the paywall degrades to the classic 429, never a broken 402.
            self._exhaust_free_tier()
            s, _, b = self._post("/api/anchor", {"hash_hex": HASH_B})
            self.assertEqual(s, 429)
        finally:
            os.environ["ORPHO_LN_ALLOW_MOCK"] = old


if __name__ == "__main__":
    unittest.main()
