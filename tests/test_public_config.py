#!/usr/bin/env python3
"""test_public_config.py — pin the public config endpoint's secret-non-leak
and bad-env-var resilience.

The endpoint is fetched on every page load. If it 500s, the entire site
appears broken. If it ever leaks a secret, that's a credential-exposure
incident on every visitor.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))


class TestPublicConfig(unittest.TestCase):
    def setUp(self):
        # Snapshot env so each test starts clean
        self._env_keys = [
            "STRIPE_PACK_URL", "STRIPE_PERSONAL_MONTHLY_URL",
            "STRIPE_PERSONAL_ANNUAL_URL", "STRIPE_CREATOR_MONTHLY_URL",
            "PACK_PRICE_USD", "PACK_CREDIT_COUNT",
            "PERSONAL_MONTHLY_USD", "PERSONAL_ANNUAL_USD", "CREATOR_MONTHLY_USD",
            "ORPHO_MAINTENANCE_MODE", "ORPHO_DISABLE_CHECKOUT",
            "ORPHO_DISABLE_ANCHORING",
            "BTC_PAYMENTS_ENABLED", "CREATOR_TIER_LIVE",
            # Things that must NEVER appear in the snapshot:
            "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
            "ORPHO_FOUNDER_TOKEN", "RESEND_API_KEY",
        ]
        self._saved = {k: os.environ.get(k) for k in self._env_keys}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _snapshot(self) -> dict:
        # Re-import each time so env changes take effect
        sys.modules.pop("public_config", None)
        import public_config
        return public_config.snapshot()

    def test_snapshot_returns_required_top_level_keys(self):
        snap = self._snapshot()
        for key in ("stripe", "pricing", "toggles", "features"):
            self.assertIn(key, snap)

    def test_snapshot_never_returns_stripe_secret(self):
        """Stripe secret keys, webhook secrets, founder tokens, and Resend keys
        must NEVER appear in the public snapshot — even if accidentally added."""
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_DO_NOT_LEAK"
        os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_DO_NOT_LEAK"
        os.environ["ORPHO_FOUNDER_TOKEN"] = "founder_DO_NOT_LEAK"
        os.environ["RESEND_API_KEY"] = "re_live_DO_NOT_LEAK"
        snap = self._snapshot()
        import json
        snap_text = json.dumps(snap)
        for forbidden in ("sk_test_DO_NOT_LEAK", "whsec_DO_NOT_LEAK",
                          "founder_DO_NOT_LEAK", "re_live_DO_NOT_LEAK"):
            self.assertNotIn(forbidden, snap_text,
                f"public_config snapshot leaked {forbidden}")

    def test_bad_integer_env_falls_back_to_default(self):
        """A typo in pricing env vars must NOT crash /api/config — that endpoint
        is fetched on every page load."""
        os.environ["PACK_PRICE_USD"] = "$7"  # typo
        snap = self._snapshot()
        self.assertEqual(snap["pricing"]["pack_usd"], 7)

    def test_bad_integer_env_falls_back_for_all_pricing_fields(self):
        os.environ["PACK_PRICE_USD"] = "not_a_number"
        os.environ["PERSONAL_MONTHLY_USD"] = ""
        os.environ["CREATOR_MONTHLY_USD"] = "nineteen"
        snap = self._snapshot()
        self.assertEqual(snap["pricing"]["pack_usd"], 7)
        self.assertEqual(snap["pricing"]["personal_monthly_usd"], 5)
        self.assertEqual(snap["pricing"]["creator_monthly_usd"], 19)

    def test_empty_stripe_urls_default_to_empty_string(self):
        # Unset everything
        for k in ("STRIPE_PACK_URL", "STRIPE_PERSONAL_MONTHLY_URL",
                  "STRIPE_PERSONAL_ANNUAL_URL", "STRIPE_CREATOR_MONTHLY_URL"):
            os.environ.pop(k, None)
        snap = self._snapshot()
        for url_key in ("pack_url", "personal_monthly_url",
                        "personal_annual_url", "creator_monthly_url"):
            self.assertEqual(snap["stripe"][url_key], "")

    def test_toggles_parse_truthy_values(self):
        os.environ["ORPHO_MAINTENANCE_MODE"] = "1"
        os.environ["ORPHO_DISABLE_CHECKOUT"] = "0"
        os.environ["ORPHO_DISABLE_ANCHORING"] = ""
        snap = self._snapshot()
        self.assertTrue(snap["toggles"]["maintenance_mode"])
        self.assertFalse(snap["toggles"]["checkout_disabled"])
        self.assertFalse(snap["toggles"]["anchoring_disabled"])

    def test_toggles_only_treat_exact_1_as_true(self):
        """Defensive: 'true', 'yes', 'on' should NOT enable a toggle.
        Only '1' should, matching the rest of the server's convention."""
        os.environ["ORPHO_MAINTENANCE_MODE"] = "true"
        snap = self._snapshot()
        self.assertFalse(snap["toggles"]["maintenance_mode"])

    def test_features_block_is_present(self):
        snap = self._snapshot()
        for f in ("btc_payments", "creator_tier_live",
                  "private_receipts", "receipt_vault"):
            self.assertIn(f, snap["features"])


if __name__ == "__main__":
    unittest.main()
