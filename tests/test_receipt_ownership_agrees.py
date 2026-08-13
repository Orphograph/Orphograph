#!/usr/bin/env python3
"""test_receipt_ownership_agrees.py — one answer to "is this receipt theirs?"

DEFECT (2026-08-07 Stage 3e, drift sweep of the access-control surface)
-----------------------------------------------------------------------
A receipt's `source` records HOW it was paid for, not who owns it:

    session-anchored   sub:<email_id>
    API-key-anchored   api:<key[:10]>

Three places asked "does this receipt belong to `email`?" and each answered
independently. Only ONE — the vault listing — knew about `api:` tags. Its
own comment spelled the rule out ("Both belong in the owner's vault,
including receipts anchored under a since-rotated key") while the other two
compared against `sub:<email_id>` alone.

Reproduced for a subscriber who anchors through the API:

    vault LIST shows it : True   (len=1)
    vault COUNT says    : 0          <- same page, contradicting numbers
    privacy toggle      : "receipt not found"   <- about their own receipt

The counter feeds /api/me, so the customer saw a vault listing their anchors
above a count that said zero. And the privacy toggle told them a receipt
they were looking at did not exist — the fail-closed direction, so not a
disclosure bug, but a paying subscriber could not make their own receipt
private or public again.

Fixed at the root: `_owned_sources_for_email()` is the single source of
truth and `_receipt_belongs_to()` the single test. A new payment path is
added in one place.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
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

OWNER = "owner@example.com"
STRANGER = "stranger@example.com"


class TestOwnershipTestsAgree(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_mods = {m: sys.modules[m] for m in _POLLUTED if m in sys.modules}
        cls._old_env = {k: os.environ.get(k)
                        for k in ("ORPHO_DATA_DIR", "RATE_LIMIT_PER_DAY")}
        os.environ["ORPHO_DATA_DIR"] = cls._tmp.name
        os.environ["RATE_LIMIT_PER_DAY"] = "100000"
        for m in _POLLUTED:
            sys.modules.pop(m, None)
        import app, engine, auth, api_keys
        cls.app, cls.engine, cls.auth, cls.api_keys = app, engine, auth, api_keys
        cls._orig_submit = engine._submit
        engine._submit = lambda cal, h: (False, "stubbed: test mode")

    @classmethod
    def tearDownClass(cls):
        cls.engine._submit = cls._orig_submit
        for m in _POLLUTED:
            sys.modules.pop(m, None)
        for m, mod in cls._old_mods.items():
            sys.modules[m] = mod
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls._tmp.cleanup()

    def _api_key_receipt(self, email=OWNER):
        """A receipt anchored the way /api/anchor tags an API-key subscriber."""
        key = self.api_keys.issue(email)
        return self.engine.anchor_hash(
            os.urandom(32).hex(), source=f"api:{key[:10]}",
            private=False, owner_id=self.auth.email_id(email)), key

    def _session_receipt(self, email=OWNER):
        return self.engine.anchor_hash(
            os.urandom(32).hex(),
            source="sub:" + self.auth.email_id(email),
            private=False, owner_id=self.auth.email_id(email))

    # ── the defect ────────────────────────────────────────────────────────
    def test_count_and_list_agree_for_api_key_anchors(self):
        """They are rendered on the same page. Disagreeing is the defect."""
        rec, _ = self._api_key_receipt()
        listed, _ = self.app._list_anchors_for_email(OWNER, with_more_flag=True)
        count = self.app._count_anchors_for_email(OWNER)
        in_list = any(r["receipt_id"] == rec["receipt_id"] for r in listed)
        self.assertTrue(in_list, "the vault listing lost an owned receipt")
        self.assertEqual(
            count, len(listed),
            f"vault count ({count}) disagrees with the vault listing "
            f"({len(listed)}) — the customer sees both numbers at once")

    def test_owner_passes_the_ownership_test_on_an_api_key_receipt(self):
        """The privacy toggle used this; the owner was told 'not found'."""
        rec, _ = self._api_key_receipt()
        self.assertTrue(
            self.app._receipt_belongs_to(rec, OWNER),
            "a subscriber does not own the receipt they anchored with their "
            "own API key, so they cannot change its privacy")

    def test_a_rotated_key_still_grants_ownership(self):
        """Receipts anchored under a since-rotated key stay the owner's."""
        rec, _ = self._api_key_receipt()
        self.api_keys.issue(OWNER)  # rotate: issue a second key
        self.assertTrue(self.app._receipt_belongs_to(rec, OWNER),
                        "rotating a key orphaned the receipts it anchored")

    # ── it must still refuse everyone else ────────────────────────────────
    def test_a_stranger_owns_nothing(self):
        rec_api, _ = self._api_key_receipt()
        rec_sess = self._session_receipt()
        for rec in (rec_api, rec_sess):
            self.assertFalse(self.app._receipt_belongs_to(rec, STRANGER))
        self.assertEqual(self.app._count_anchors_for_email(STRANGER), 0)

    def test_an_empty_email_owns_nothing(self):
        """Guard the anonymous case: no email must never match a tag."""
        rec, _ = self._api_key_receipt()
        self.assertFalse(self.app._receipt_belongs_to(rec, ""))
        self.assertEqual(self.app._owned_sources_for_email(""), set())

    def test_free_and_pack_anchors_are_owned_by_nobody(self):
        """A free or pack anchor has no signed-in identity to gate by, so it
        must not fall into anyone's vault."""
        for src in ("free", "pack:abcd1234"):
            rec = self.engine.anchor_hash(os.urandom(32).hex(), source=src)
            self.assertFalse(self.app._receipt_belongs_to(rec, OWNER),
                             f"{src!r} anchor was claimed by a subscriber")

    # ── root-cause guard ──────────────────────────────────────────────────
    def test_no_call_site_hand_rolls_the_ownership_test(self):
        """The whole defect was three independent answers. If a fourth
        appears, it will be wrong the same way."""
        src = (ROOT / "server" / "app.py").read_text()
        # Everything except the body of _owned_sources_for_email itself, which
        # is where the rule is ALLOWED to be spelled out.
        start = src.index("def _owned_sources_for_email")
        end = src.index("def _receipt_belongs_to")
        outside = src[:start] + src[end:]
        stray = [ln.strip() for ln in outside.splitlines()
                 if '"sub:" + auth.email_id' in ln
                 and "source =" not in ln]         # anchor-time TAGGING is fine
        self.assertEqual(
            stray, [],
            "these lines rebuild the ownership test by hand instead of "
            f"calling _receipt_belongs_to / _owned_sources_for_email: {stray}")


if __name__ == "__main__":
    unittest.main()
