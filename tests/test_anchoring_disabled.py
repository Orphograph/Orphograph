#!/usr/bin/env python3
"""test_anchoring_disabled.py — ORPHO_DISABLE_ANCHORING must yield 503, not 500.

Found 2026-08-20 by the review gate. `/api/anchor_folder` called `_reject(503,
...)` in its disable branch, but `_reject` is defined as a LOCAL function 11
lines further down the same handler. Python binds a name assigned anywhere in a
function body as local for the ENTIRE body, so the call raised
UnboundLocalError and the caller got a 500 with no detail -- exactly when an
operator had deliberately paused anchoring and most needed the 503's retry
guidance. `/api/anchor` did it correctly with `_json_response`; the two arms
had drifted.

Both endpoints are pinned here so they cannot drift apart again.
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
    "merkle", "expire_worker", "upgrade_worker", "renewal",
)


class TestAnchoringDisabled(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_env = {k: os.environ.get(k) for k in
                        ("ORPHO_DATA_DIR", "HOST", "PORT",
                         "ORPHO_COOKIE_SECURE", "RATE_LIMIT_PER_DAY",
                         "ORPHO_DISABLE_ANCHORING")}
        cls._old_modules = {m: sys.modules[m] for m in _POLLUTED
                            if m in sys.modules}
        os.environ.update({
            "ORPHO_DATA_DIR": cls._tmp.name,
            "HOST": "127.0.0.1", "PORT": "0",
            "ORPHO_COOKIE_SECURE": "0",
            "RATE_LIMIT_PER_DAY": "100000",
            # Read at import time, so it must be set BEFORE `import app`.
            "ORPHO_DISABLE_ANCHORING": "1",
        })
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        for m in _POLLUTED:
            sys.modules.pop(m, None)
        import app
        from http.server import ThreadingHTTPServer
        cls._server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=cls._server.serve_forever, daemon=True).start()
        cls._base = f"http://127.0.0.1:{cls._server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._server.server_close()
        cls._tmp.cleanup()
        for m in _POLLUTED:
            sys.modules.pop(m, None)
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for m, mod in cls._old_modules.items():
            sys.modules[m] = mod

    def _post(self, path: str, payload: dict):
        req = urllib.request.Request(
            f"{self._base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            r = urllib.request.urlopen(req, timeout=10)
            return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def test_anchor_folder_returns_503_not_500(self):
        """Pre-fix this was 500 (UnboundLocalError), which tells an operator
        nothing and looks like a crash rather than a deliberate pause."""
        status, body = self._post("/api/anchor_folder", {
            "algorithm": "sha256", "version": 1, "root_hex": "a" * 64,
            "leaves": [],
        })
        self.assertEqual(status, 503, f"got {status}: {body[:300]}")
        self.assertIn("temporarily unavailable", body)

    def test_anchor_file_returns_503_too(self):
        """The sibling arm, pinned so the two cannot drift apart again."""
        status, body = self._post("/api/anchor", {"hash_hex": "b" * 64})
        self.assertEqual(status, 503, f"got {status}: {body[:300]}")
        self.assertIn("temporarily unavailable", body)


if __name__ == "__main__":
    unittest.main()
