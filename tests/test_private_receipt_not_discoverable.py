#!/usr/bin/env python3
"""test_private_receipt_not_discoverable.py — private means unconfirmable.

DEFECT (2026-08-07 Stage 3e, wire-path probe of the access-control surface)
---------------------------------------------------------------------------
/api/verify/<id> returns 404 for a private receipt viewed by a non-owner,
deliberately: the privacy-toggle handler says "Don't reveal whether receipt
exists for another owner". The badge endpoint did not follow that rule.

    private receipt      /api/badge/<id>.svg  ->  200   (badge rendered)
    non-existent id      /api/badge/<id>.svg  ->  404

So a stranger holding a receipt id could confirm a PRIVATE receipt exists
from the status code alone. Found by probing every public surface for one
private receipt, not by reading the handler.

The handler's comment shows how it was missed. It reasons carefully about
what the badge SHOWS — "reads only receipt_id + created_at — no filename,
no email, no hash bytes" — and concludes the SVG is safe to serve without
authentication. That reasoning is correct and answers a different question
than the one the status code was answering.

WHY PRIVATE BADGES 404 FOR THE OWNER TOO
A badge exists to be embedded on the public web, which is exactly what
"private" withdraws. And the response is cached `public, max-age=3600`, so
an owner-specific 200 could be stored by a CDN and replayed to strangers —
a per-viewer answer here would reintroduce the leak through the cache. One
answer for every caller is both the right policy and the only cache-safe one.

The rest of the surface was probed at the same time and was clean: no
surface leaked the client_label, and /certificate/<id> returns 200 for a
non-existent id too, so it is a static shell rather than an oracle.
"""
from __future__ import annotations

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
    "app", "engine", "auth", "rate_limit", "credits", "stats", "health",
    "subscriptions", "teams", "stripe_webhook", "mailer", "api_keys",
    "affiliate", "newsletter", "waitlist", "blog", "unsubscribe", "gdpr",
    "public_config", "receipt_export", "btc_price", "btc_payments",
    "stripe_api", "og_svg", "qrcode_svg", "badge_svg", "analytics",
    "support_tools", "onboarding", "referrals", "file_lock", "merkle",
    "lightning", "webhooks",
)

SECRET_LABEL = "SECRET-PROJECT-NAME"
NONEXISTENT = "AAAAAAAAAAAAAAAA"


class TestPrivateReceiptNotDiscoverable(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_mods = {m: sys.modules[m] for m in _POLLUTED if m in sys.modules}
        cls._keys = ("ORPHO_DATA_DIR", "HOST", "PORT", "ORPHO_COOKIE_SECURE",
                     "RATE_LIMIT_PER_DAY")
        cls._old_env = {k: os.environ.get(k) for k in cls._keys}
        os.environ.update({
            "ORPHO_DATA_DIR": cls._tmp.name, "HOST": "127.0.0.1", "PORT": "0",
            "ORPHO_COOKIE_SECURE": "0", "RATE_LIMIT_PER_DAY": "100000"})
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        for m in _POLLUTED:
            sys.modules.pop(m, None)
        import app, engine, auth
        from http.server import ThreadingHTTPServer
        cls._orig_submit = engine._submit
        engine._submit = lambda cal, h: (False, "stubbed: test mode")
        cls.engine, cls.auth = engine, auth
        owner = "owner@example.com"
        cls.private_rid = engine.anchor_hash(
            "f" * 64, client_label=SECRET_LABEL,
            source="sub:" + auth.email_id(owner),
            private=True, owner_id=auth.email_id(owner))["receipt_id"]
        cls.public_rid = engine.anchor_hash(
            "e" * 64, client_label="public-thing")["receipt_id"]
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
        for m, mod in cls._old_mods.items():
            sys.modules[m] = mod
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _get(self, path):
        try:
            with urllib.request.urlopen(self._base + path, timeout=15) as r:
                return r.getcode(), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    # ── the defect ────────────────────────────────────────────────────────
    def test_badge_cannot_confirm_a_private_receipt_exists(self):
        priv, _ = self._get(f"/api/badge/{self.private_rid}.svg")
        gone, _ = self._get(f"/api/badge/{NONEXISTENT}.svg")
        self.assertEqual(
            priv, gone,
            f"the badge endpoint distinguishes a PRIVATE receipt ({priv}) "
            f"from a non-existent one ({gone}), so a stranger can confirm a "
            f"private receipt exists — the thing /api/verify 404s to prevent")
        self.assertEqual(priv, 404)

    def test_the_badge_still_works_for_public_receipts(self):
        """The fix must not break the feature it is protecting."""
        code, body = self._get(f"/api/badge/{self.public_rid}.svg")
        self.assertEqual(code, 200, "public badges stopped rendering")
        self.assertIn("<svg", body)

    def test_verify_still_hides_private_receipts(self):
        """The behaviour the badge was supposed to match."""
        priv, _ = self._get(f"/api/verify/{self.private_rid}")
        gone, _ = self._get(f"/api/verify/{NONEXISTENT}")
        self.assertEqual(priv, gone)
        self.assertEqual(priv, 404)

    # ── the broader sweep, kept as a standing probe ───────────────────────
    def test_no_public_surface_leaks_a_private_receipts_label(self):
        """client_label is customer-chosen text and can name a project, a
        client, or an unannounced product."""
        surfaces = [
            f"/api/verify/{self.private_rid}",
            f"/r/{self.private_rid}",
            f"/api/receipt/{self.private_rid}",
            f"/api/verify_folder/{self.private_rid}",
            f"/api/badge/{self.private_rid}.svg",
            f"/api/receipt/{self.private_rid}/export",
            f"/certificate/{self.private_rid}",
            f"/api/inclusion_proof?receipt_id={self.private_rid}&path=x",
        ]
        leaked = [p for p in surfaces if SECRET_LABEL in self._get(p)[1]]
        self.assertEqual(leaked, [],
                         f"these surfaces leak a private receipt's label to "
                         f"an anonymous caller: {leaked}")


if __name__ == "__main__":
    unittest.main()
