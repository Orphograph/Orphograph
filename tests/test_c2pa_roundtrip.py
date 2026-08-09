#!/usr/bin/env python3
"""test_c2pa_roundtrip.py — a commitment field accepted on anchor MUST be
readable on verify, through the real HTTP entry point.

Found 2026-08-09 (R16): c2pa_manifest_hash was validated, stored, and listed
as CORE by renewal.py — and never surfaced by any response builder. Write-
only on the wire since the field shipped; the receiving side could never
check the binding. Harness conventions from tests/test_agent_discovery.py.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

_POLLUTED = (
    "app", "engine", "auth", "rate_limit", "credits", "stats", "health",
    "subscriptions", "teams", "stripe_webhook", "mailer", "api_keys",
    "affiliate", "newsletter", "waitlist", "blog", "unsubscribe", "gdpr",
    "public_config", "receipt_export", "btc_price", "btc_payments",
    "stripe_api", "og_svg", "qrcode_svg", "badge_svg", "analytics",
    "support_tools", "onboarding", "referrals", "file_lock", "merkle",
)


def _start(data_dir):
    os.environ.update(ORPHO_DATA_DIR=str(data_dir), HOST="127.0.0.1", PORT="0",
                      ORPHO_COOKIE_SECURE="0", RATE_LIMIT_PER_DAY="100000",
                      ORPHO_OFFLINE_CALENDARS="1")
    for m in _POLLUTED: sys.modules.pop(m, None)
    import app
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class TestC2paRoundtrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old = {m: sys.modules[m] for m in _POLLUTED if m in sys.modules}
        cls._srv, cls._base = _start(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._srv.shutdown(); cls._srv.server_close(); cls._tmp.cleanup()
        for m in _POLLUTED: sys.modules.pop(m, None)
        sys.modules.update(cls._old)

    def _post(self, path, body):
        req = urllib.request.Request(self._base + path,
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=15).read())

    def _get(self, path):
        return json.loads(urllib.request.urlopen(self._base + path, timeout=15).read())

    def test_c2pa_survives_the_round_trip(self):
        c2pa = "cd" * 32
        r = self._post("/api/anchor", {"hash_hex": "ab" * 32,
                                       "client_label": "c2pa-rt",
                                       "c2pa_manifest_hash": c2pa})
        rid = r["receipt_id"]
        for path in (f"/api/verify/{rid}", f"/api/receipt/{rid}"):
            got = self._get(path)
            self.assertEqual(got.get("c2pa_manifest_hash"), c2pa,
                             f"{path} dropped the C2PA binding — the "
                             f"commitment is write-only on the wire again")

    def test_shape_stable_without_c2pa(self):
        r = self._post("/api/anchor", {"hash_hex": "ef" * 32,
                                       "client_label": "no-c2pa"})
        got = self._get(f"/api/verify/{r['receipt_id']}")
        self.assertNotIn("c2pa_manifest_hash", got,
                         "absent field must stay absent (shape stability)")


if __name__ == "__main__":
    unittest.main()
