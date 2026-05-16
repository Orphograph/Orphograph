#!/usr/bin/env python3
"""test_admin_toggles.py — pin /api/founder/admin/toggles + maintenance,
anchoring, and checkout env toggle enforcement.

The toggles are read at module import time into module-level constants
(ORPHO_MAINTENANCE_MODE, ORPHO_DISABLE_CHECKOUT, ORPHO_DISABLE_ANCHORING),
so each test class sets its env BEFORE booting the server, then evicts the
relevant modules so they re-read os.environ on the fresh import.
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


_MODULES_TO_EVICT = (
    "app", "engine", "auth", "rate_limit", "credits", "stats",
    "health", "subscriptions", "teams", "stripe_webhook",
    "mailer", "api_keys", "affiliate", "newsletter", "waitlist",
    "blog", "unsubscribe", "gdpr", "public_config",
    "receipt_export", "btc_price", "btc_payments", "stripe_api",
    "og_svg", "qrcode_svg", "badge_svg", "analytics",
    "support_tools", "onboarding", "referrals", "file_lock",
)


def _evict_app_modules() -> dict:
    """Pop app+sibling modules so the next `import app` re-reads os.environ.
    Returns a snapshot of the prior module objects for restoration."""
    saved = {m: sys.modules[m] for m in list(sys.modules.keys()) if m in _MODULES_TO_EVICT}
    for m in list(sys.modules.keys()):
        if m in _MODULES_TO_EVICT:
            sys.modules.pop(m, None)
    return saved


def _restore_modules(saved: dict) -> None:
    for m in list(sys.modules.keys()):
        if m in _MODULES_TO_EVICT:
            sys.modules.pop(m, None)
    for m, mod in saved.items():
        sys.modules[m] = mod


def _start_server(data_dir: Path):
    os.environ["ORPHO_DATA_DIR"] = str(data_dir)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = "0"
    os.environ["ORPHO_COOKIE_SECURE"] = "0"
    os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    import app
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}", app


def _stop(server) -> None:
    server.shutdown()
    server.server_close()


def _request(base: str, path: str, method: str = "GET",
             headers: dict | None = None, body: bytes | None = None):
    req = urllib.request.Request(base + path, method=method, data=body)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, (e.read() if hasattr(e, "read") else b""), (dict(e.headers) if hasattr(e, "headers") else {})


# ─────────────────────────────────────────────────────────────────────────────
# Class A: ORPHO_FOUNDER_TOKEN UNSET — toggles endpoint must 404 unconditionally
# ─────────────────────────────────────────────────────────────────────────────


class TestTogglesEndpointTokenUnset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_env = {k: os.environ.get(k) for k in (
            "ORPHO_FOUNDER_TOKEN", "ORPHO_MAINTENANCE_MODE",
            "ORPHO_DISABLE_ANCHORING", "ORPHO_DISABLE_CHECKOUT",
            "ORPHO_DATA_DIR", "HOST", "PORT", "ORPHO_COOKIE_SECURE",
        )}
        os.environ.pop("ORPHO_FOUNDER_TOKEN", None)
        os.environ.pop("ORPHO_MAINTENANCE_MODE", None)
        os.environ.pop("ORPHO_DISABLE_ANCHORING", None)
        os.environ.pop("ORPHO_DISABLE_CHECKOUT", None)
        cls._saved_modules = _evict_app_modules()
        cls._server, cls._base, cls._app = _start_server(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        _stop(cls._server)
        cls._tmp.cleanup()
        _restore_modules(cls._saved_modules)
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_no_token_returns_404_not_401(self):
        # 404 avoids leaking whether the token env var is set (existence oracle).
        status, _, _ = _request(self._base, "/api/founder/admin/toggles")
        self.assertEqual(status, 404)

    def test_no_token_with_random_header_still_404(self):
        status, _, _ = _request(
            self._base, "/api/founder/admin/toggles",
            headers={"X-Orpho-Founder": "anything"},
        )
        self.assertEqual(status, 404)


# ─────────────────────────────────────────────────────────────────────────────
# Class B: ORPHO_FOUNDER_TOKEN SET — wrong header 404, right header 200
# ─────────────────────────────────────────────────────────────────────────────


class TestTogglesEndpointTokenSet(unittest.TestCase):
    TOKEN = "test-founder-token-abc123"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_env = {k: os.environ.get(k) for k in (
            "ORPHO_FOUNDER_TOKEN", "ORPHO_MAINTENANCE_MODE",
            "ORPHO_DISABLE_ANCHORING", "ORPHO_DISABLE_CHECKOUT",
            "ORPHO_DATA_DIR", "HOST", "PORT", "ORPHO_COOKIE_SECURE",
        )}
        os.environ["ORPHO_FOUNDER_TOKEN"] = cls.TOKEN
        os.environ.pop("ORPHO_MAINTENANCE_MODE", None)
        os.environ.pop("ORPHO_DISABLE_ANCHORING", None)
        os.environ.pop("ORPHO_DISABLE_CHECKOUT", None)
        cls._saved_modules = _evict_app_modules()
        cls._server, cls._base, cls._app = _start_server(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        _stop(cls._server)
        cls._tmp.cleanup()
        _restore_modules(cls._saved_modules)
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_wrong_header_returns_404(self):
        status, _, _ = _request(
            self._base, "/api/founder/admin/toggles",
            headers={"X-Orpho-Founder": "wrong-token"},
        )
        self.assertEqual(status, 404)

    def test_missing_header_returns_404(self):
        status, _, _ = _request(self._base, "/api/founder/admin/toggles")
        self.assertEqual(status, 404)

    def test_correct_header_returns_200_with_all_three_flags(self):
        status, body, _ = _request(
            self._base, "/api/founder/admin/toggles",
            headers={"X-Orpho-Founder": self.TOKEN},
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("maintenance_mode", data)
        self.assertIn("checkout_disabled", data)
        self.assertIn("anchoring_disabled", data)
        self.assertIsInstance(data["maintenance_mode"], bool)
        self.assertIsInstance(data["checkout_disabled"], bool)
        self.assertIsInstance(data["anchoring_disabled"], bool)
        # All three should be False since env vars are unset
        self.assertFalse(data["maintenance_mode"])
        self.assertFalse(data["checkout_disabled"])
        self.assertFalse(data["anchoring_disabled"])


# ─────────────────────────────────────────────────────────────────────────────
# Class C: ORPHO_MAINTENANCE_MODE=1 — /api/anchor 503; health/stats exempt
# ─────────────────────────────────────────────────────────────────────────────


class TestMaintenanceMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_env = {k: os.environ.get(k) for k in (
            "ORPHO_MAINTENANCE_MODE", "ORPHO_DISABLE_ANCHORING",
            "ORPHO_DISABLE_CHECKOUT", "ORPHO_DATA_DIR", "HOST", "PORT",
            "ORPHO_COOKIE_SECURE",
        )}
        os.environ["ORPHO_MAINTENANCE_MODE"] = "1"
        os.environ.pop("ORPHO_DISABLE_ANCHORING", None)
        os.environ.pop("ORPHO_DISABLE_CHECKOUT", None)
        cls._saved_modules = _evict_app_modules()
        cls._server, cls._base, cls._app = _start_server(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        _stop(cls._server)
        cls._tmp.cleanup()
        _restore_modules(cls._saved_modules)
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_anchor_post_returns_503(self):
        digest = hashlib.sha256(b"maint-mode-test").hexdigest()
        payload = json.dumps({"hash_hex": digest}).encode()
        status, body, _ = _request(
            self._base, "/api/anchor",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=payload,
        )
        self.assertEqual(status, 503)
        data = json.loads(body)
        self.assertEqual(data.get("error"), "service unavailable")

    def test_health_still_returns_200(self):
        status, body, _ = _request(self._base, "/api/health")
        self.assertEqual(status, 200, "health endpoint must remain reachable in maintenance mode")
        data = json.loads(body)
        # Sanity: it's a health snapshot, not the maintenance error.
        self.assertNotEqual(data.get("error"), "service unavailable")

    def test_stats_still_returns_200(self):
        status, body, _ = _request(self._base, "/api/stats")
        self.assertEqual(status, 200, "stats endpoint must remain reachable in maintenance mode")
        data = json.loads(body)
        self.assertNotEqual(data.get("error"), "service unavailable")


# ─────────────────────────────────────────────────────────────────────────────
# Class D: ORPHO_DISABLE_ANCHORING=1 — /api/anchor 503; receipt reads still work
# ─────────────────────────────────────────────────────────────────────────────


class TestDisableAnchoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_env = {k: os.environ.get(k) for k in (
            "ORPHO_MAINTENANCE_MODE", "ORPHO_DISABLE_ANCHORING",
            "ORPHO_DISABLE_CHECKOUT", "ORPHO_DATA_DIR", "HOST", "PORT",
            "ORPHO_COOKIE_SECURE",
        )}
        os.environ.pop("ORPHO_MAINTENANCE_MODE", None)
        os.environ.pop("ORPHO_DISABLE_CHECKOUT", None)
        # Seed a receipt BEFORE flipping the toggle and booting — we want the
        # receipt to exist so the read-side test exercises the success path.
        # But the toggle is read at app-module import time, so we need to:
        #   1. evict modules, 2. set the env, 3. import engine (no toggle),
        #   4. seed, 5. boot app (reads the toggle).
        os.environ["ORPHO_DATA_DIR"] = str(cls._tmp.name)
        cls._saved_modules = _evict_app_modules()
        os.environ["ORPHO_DISABLE_ANCHORING"] = "1"
        # Seed receipt directly via engine (bypass HTTP — anchor endpoint is now blocked)
        import engine as engine_mod
        original_submit = engine_mod._submit
        engine_mod._submit = lambda cal, h: (False, "stubbed: no network")
        try:
            digest = hashlib.sha256(b"pre-toggle-receipt").hexdigest()
            rec = engine_mod.anchor_hash(digest, source="free")
        finally:
            engine_mod._submit = original_submit
        cls.seeded_rid = rec["receipt_id"]
        # Now boot the server (app.py reads ORPHO_DISABLE_ANCHORING on import)
        cls._server, cls._base, cls._app = _start_server(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        _stop(cls._server)
        cls._tmp.cleanup()
        _restore_modules(cls._saved_modules)
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_anchor_post_returns_503_with_valid_body(self):
        digest = hashlib.sha256(b"disable-anchor-test").hexdigest()
        payload = json.dumps({"hash_hex": digest}).encode()
        status, body, _ = _request(
            self._base, "/api/anchor",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=payload,
        )
        self.assertEqual(status, 503)
        data = json.loads(body)
        self.assertEqual(data.get("error"), "anchoring temporarily unavailable")

    def test_receipt_read_still_works(self):
        # Reads of existing receipts must NOT be blocked by the anchoring kill switch.
        status, body, _ = _request(self._base, f"/api/receipt/{self.seeded_rid}")
        self.assertEqual(status, 200,
            "ORPHO_DISABLE_ANCHORING must not block read of existing receipts")
        data = json.loads(body)
        self.assertTrue(data.get("found"))


if __name__ == "__main__":
    unittest.main()
