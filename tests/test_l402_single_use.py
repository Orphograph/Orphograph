#!/usr/bin/env python3
"""test_l402_single_use.py — one Lightning payment buys exactly one anchor.

DEFECT (2026-08-07 Stage 3e, vacuous-pass sweep of the auth primitives)
-----------------------------------------------------------------------
/api/anchor enforced L402 single-use as check-then-act:

    line 2202   if lightning.is_spent(ln_payment_hash): reject
    ...         (176 lines, including submission to five OTS calendars)
    line 2378   lightning.mark_spent(...)

No lock, on a ThreadingHTTPServer, with network I/O inside the window. Eight
concurrent requests carrying ONE paid credential produced eight receipts and
zero rejections. Reproduced before the fix; this file pins it.

verify_l402's own docstring said "the caller is responsible for the spent
check + mark (so spend is atomic with the anchor)". The caller never made it
atomic — a contract asserted in a docstring and not implemented anywhere.

Second defect, same area: is_spent() swallowed OSError and returned False —
"I could not read the ledger, so it isn't spent." On this system that is not
hypothetical; root-owned files under /data have twice broken server-side
reads (api_keys.jsonl 2026-07-27, webhooks.jsonl 2026-07-28). An unreadable
spent-set now raises and the request 503s.

The fix takes the claim atomically under an fcntl lock, as late as possible
but strictly before the irreversible anchor, and releases it if the anchor
fails or lands on zero calendars — preserving the pre-existing fairness rule
that a worthless anchor does not consume the credential.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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
    "lightning", "webhooks",
)


def _post(url, body, headers=None):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.getcode(), json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


class TestL402SingleUse(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_modules = {m: sys.modules[m] for m in _POLLUTED if m in sys.modules}
        cls._keys = ("ORPHO_DATA_DIR", "HOST", "PORT", "ORPHO_COOKIE_SECURE",
                     "RATE_LIMIT_PER_DAY", "ORPHO_LN_BACKEND",
                     "ORPHO_LN_ALLOW_MOCK")
        cls._old_env = {k: os.environ.get(k) for k in cls._keys}
        os.environ.update({
            "ORPHO_DATA_DIR": cls._tmp.name, "HOST": "127.0.0.1", "PORT": "0",
            "ORPHO_COOKIE_SECURE": "0", "RATE_LIMIT_PER_DAY": "100000",
            "ORPHO_LN_BACKEND": "mock", "ORPHO_LN_ALLOW_MOCK": "1",
        })
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        for m in _POLLUTED:
            sys.modules.pop(m, None)
        import app, engine, lightning
        from http.server import ThreadingHTTPServer
        cls._orig_submit = engine._submit
        # Succeed by default: that is the normal case, and it is the only one
        # in which the credential is genuinely consumed. A stub that always
        # FAILS makes calendars_ok == 0, which correctly RELEASES the
        # credential under the fairness rule — so a single-use test written
        # on a failing stub proves nothing. (It briefly did; that is why this
        # comment exists.)
        engine._submit = lambda cal, h: (True, b"\x00" * 32)
        cls.engine, cls.lightning = engine, lightning
        cls._server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=cls._server.serve_forever, daemon=True).start()
        cls._base = f"http://127.0.0.1:{cls._server.server_address[1]}"

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

    def _paid_credential(self):
        """A settled invoice and the L402 header that spends it."""
        preimage = secrets.token_bytes(32)
        ph = hashlib.sha256(preimage).hexdigest()
        self.lightning._MOCK_INVOICES[ph] = {
            "settled": True, "bolt11": "lnbc", "payment_hash": ph}
        mac = self.lightning.mint_macaroon(ph, self.lightning.PRICE_SATS)
        return ph, f"L402 {mac}:{preimage.hex()}"

    # ── the defect ────────────────────────────────────────────────────────
    def test_concurrent_requests_cannot_double_spend(self):
        """THE bug: 8 threads, 1 paid credential, 8 receipts, 0 rejections."""
        _, auth = self._paid_credential()

        def anchor(i):
            return _post(f"{self._base}/api/anchor",
                         {"hash_hex": f"{i:064x}"}, {"Authorization": auth})

        N = 8
        with ThreadPoolExecutor(max_workers=N) as ex:
            results = list(ex.map(anchor, range(N)))
        issued = [b for c, b in results if c == 200]
        self.assertEqual(
            len(issued), 1,
            f"one paid credential bought {len(issued)} anchors. Single-use is "
            f"not enforced atomically.")
        self.assertEqual(
            sum(1 for c, _ in results if c == 401), N - 1,
            "the losing requests were not rejected as already-spent")

    def test_a_worthless_zero_calendar_anchor_releases_the_credential(self):
        """Fairness rule that predates the fix and had to survive it: an
        anchor no calendar accepted has no Bitcoin commitment and can never
        upgrade, so it must not consume the payment. The atomic claim is
        taken BEFORE the anchor, so this is now an explicit release."""
        ph, auth = self._paid_credential()
        self.engine._submit = lambda cal, h: (False, "stubbed outage")
        try:
            code, body = _post(f"{self._base}/api/anchor",
                               {"hash_hex": "c" * 64}, {"Authorization": auth})
            self.assertEqual(code, 200, body)
            self.assertEqual(body["calendars_ok"], 0)
        finally:
            self.engine._submit = lambda cal, h: (True, b"\x00" * 32)
        self.assertFalse(
            self.lightning.is_spent(ph),
            "a 0-calendar anchor ate the Lightning payment; the agent cannot "
            "retry and got nothing for it")

    def test_sequential_reuse_is_rejected(self):
        _, auth = self._paid_credential()
        c1, b1 = _post(f"{self._base}/api/anchor",
                       {"hash_hex": "a" * 64}, {"Authorization": auth})
        self.assertEqual(c1, 200, b1)
        c2, b2 = _post(f"{self._base}/api/anchor",
                       {"hash_hex": "b" * 64}, {"Authorization": auth})
        self.assertEqual(c2, 401, b2)
        self.assertIn("spent", json.dumps(b2).lower())

    # ── the fail-open read ────────────────────────────────────────────────
    def test_unreadable_ledger_is_not_read_as_unspent(self):
        """'I could not check' must never mean 'not spent'. Root-owned files
        under /data have twice broken reads on this system."""
        ph, _ = self._paid_credential()
        self.assertTrue(self.lightning.claim(ph))
        path = self.lightning._spent_path()
        mode = path.stat().st_mode
        os.chmod(path, 0o000)
        try:
            if os.access(path, os.R_OK):
                self.skipTest("running as root; chmod cannot deny read")
            with self.assertRaises(self.lightning.SpentSetUnavailable):
                self.lightning.is_spent(ph)
        finally:
            os.chmod(path, mode)

    # ── release semantics ─────────────────────────────────────────────────
    def test_release_makes_a_credential_claimable_again(self):
        """A release tombstone must undo the claim. Scanning for ANY mention
        of the hash would permanently burn a credential we just refunded."""
        ph, _ = self._paid_credential()
        self.assertTrue(self.lightning.claim(ph))
        self.assertFalse(self.lightning.claim(ph), "double claim allowed")
        self.lightning.release(ph)
        self.assertFalse(self.lightning.is_spent(ph))
        self.assertTrue(self.lightning.claim(ph),
                        "a released credential could not be re-claimed")

    def test_claim_is_atomic_under_thread_contention(self):
        """Direct pressure on the primitive, independent of HTTP."""
        ph, _ = self._paid_credential()
        with ThreadPoolExecutor(max_workers=16) as ex:
            wins = list(ex.map(lambda _: self.lightning.claim(ph), range(16)))
        self.assertEqual(sum(wins), 1,
                         f"{sum(wins)} threads claimed the same credential")


if __name__ == "__main__":
    unittest.main()
