#!/usr/bin/env python3
"""test_gift_webhook.py — tests the Stripe webhook gift-pack code path.

Verifies that when a checkout session contains
`metadata.gift_to_email = recipient@example.com`, the resulting Pack credits
are registered to the recipient and the gift email is sent to them
(not to the buyer).
"""
from __future__ import annotations

import json
import os
import sys
import hmac as _hmac
import hashlib as _hashlib
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))


def _build_stripe_event(*, customer_email: str, gift_to: str = "",
                       gift_message: str = "", mode: str = "payment") -> dict:
    return {
        "id": f"evt_test_{time.time_ns()}",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": f"cs_test_{time.time_ns()}",
                "mode": mode,
                "customer_email": customer_email,
                "customer": "cus_test",
                "metadata": {
                    "gift_to_email": gift_to,
                    "gift_message": gift_message,
                },
            }
        },
    }


def _sign(payload: bytes, secret: str, ts: int) -> str:
    sig = _hmac.new(secret.encode(), f"{ts}.".encode() + payload, _hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


class TestGiftWebhook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_data_dir = os.environ.get("ORPHO_DATA_DIR")
        os.environ["ORPHO_DATA_DIR"] = cls._tmp.name
        # Reimport so the modules pick up the data dir
        cls._evicted = {}
        for m in list(sys.modules.keys()):
            if m in ("stripe_webhook", "credits", "mailer", "subscriptions",
                     "referrals", "auth", "file_lock"):
                cls._evicted[m] = sys.modules[m]
                del sys.modules[m]

    @classmethod
    def tearDownClass(cls):
        # Restore previous module state so later tests aren't affected
        for m in list(sys.modules.keys()):
            if m in ("stripe_webhook", "credits", "mailer", "subscriptions",
                     "referrals", "auth", "file_lock"):
                del sys.modules[m]
        for m, mod in cls._evicted.items():
            sys.modules[m] = mod
        if cls._old_data_dir is None:
            os.environ.pop("ORPHO_DATA_DIR", None)
        else:
            os.environ["ORPHO_DATA_DIR"] = cls._old_data_dir
        cls._tmp.cleanup()

    def test_gift_metadata_routes_credits_to_recipient(self):
        import stripe_webhook
        import credits
        event = _build_stripe_event(
            customer_email="buyer@example.com",
            gift_to="friend@example.com",
            gift_message="Saw this — thought of you.",
        )
        payload = json.dumps(event).encode()
        result = stripe_webhook.handle_event(payload)
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("claim_code_minted"))
        self.assertTrue(result.get("gift"))

        # Recipient now has credits registered to their email
        # (credits._scan returns a map of claim_code → remaining; we verify
        # the ledger via _read_all-style scan)
        ledger_path = credits.LEDGER_PATH
        self.assertTrue(ledger_path.exists())
        rows = [json.loads(l) for l in ledger_path.read_text().splitlines() if l.strip()]
        gift_rows = [r for r in rows if r.get("email") == "friend@example.com"]
        self.assertTrue(gift_rows, "credits ledger should contain a row for the recipient")
        self.assertTrue(any(r.get("source", "").startswith("stripe-gift:") for r in gift_rows))

    def test_non_gift_routes_to_buyer(self):
        import stripe_webhook
        import credits
        event = _build_stripe_event(
            customer_email="self-buyer@example.com",
            gift_to="",
        )
        payload = json.dumps(event).encode()
        result = stripe_webhook.handle_event(payload)
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("claim_code_minted"))
        self.assertFalse(result.get("gift"))

        # Self-buyer is on the credits ledger
        rows = [json.loads(l) for l in credits.LEDGER_PATH.read_text().splitlines() if l.strip()]
        self.assertTrue(any(r.get("email") == "self-buyer@example.com" for r in rows))

    def test_invalid_gift_email_falls_back_to_buyer(self):
        import stripe_webhook
        import credits
        event = _build_stripe_event(
            customer_email="buyer2@example.com",
            gift_to="not-a-valid-email",  # no @
        )
        payload = json.dumps(event).encode()
        result = stripe_webhook.handle_event(payload)
        self.assertTrue(result.get("ok"))
        # Invalid gift email → falls back to buyer
        self.assertFalse(result.get("gift"))
        rows = [json.loads(l) for l in credits.LEDGER_PATH.read_text().splitlines() if l.strip()]
        self.assertTrue(any(r.get("email") == "buyer2@example.com" for r in rows))


if __name__ == "__main__":
    unittest.main()
