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

import _srv

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
    """Run the server in a SUBPROCESS, not in this interpreter.

    2026-08-25: this used to pop 32 modules out of sys.modules, re-import
    `app`, and serve from a thread inside the test process. It passed alone and
    FAILED under full-suite load with a socket TimeoutError — three times —
    because a re-imported `app` inherits and adds to whatever global state and
    background threads the rest of the suite has accumulated, and the
    in-process server thread then does not answer in time. Raising the request
    timeout would have hidden that; subprocess isolation removes the coupling.
    Port reservation, startup deadline and server-log capture live in _srv.py.
    """
    bases, procs, logs = _srv.spin(data_dir)
    _srv.wait_ready(bases, procs, logs)
    return (procs, logs), bases[0]


class TestC2paRoundtrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._handle, cls._base = _start(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        procs, logs = cls._handle
        _srv._kill_all(procs, logs)
        cls._tmp.cleanup()

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
