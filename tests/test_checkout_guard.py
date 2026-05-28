"""Guards for the revenue path.

Two failures this locks down, both seen live on 2026-05-25:
  1. /api/config served the stale $7 "Pack of Ten" price while the homepage
     showed the canonical $19 Writer Pack — displayed price could mismatch the
     actual Stripe charge.
  2. checkout was enabled while every Stripe Payment Link URL was empty, so the
     buy buttons led nowhere and no one could pay — silently.
"""
import importlib.util
import os
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
PUBLIC_CONFIG = REPO / "server" / "public_config.py"


def _load():
    spec = importlib.util.spec_from_file_location("public_config", PUBLIC_CONFIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCanonicalPrice(unittest.TestCase):
    def test_pack_default_is_19_writer_pack(self):
        """Entry SKU is the Writer Pack: 10 anchors, $19. Default must not
        regress to the stale $7/$29 values without an explicit env override."""
        mod = _load()
        for var in ("PACK_PRICE_USD", "PACK_CREDIT_COUNT"):
            os.environ.pop(var, None)
        pricing = mod.snapshot()["pricing"]
        self.assertEqual(pricing["pack_usd"], 19, "Writer Pack price drifted")
        self.assertEqual(pricing["pack_credits"], 10, "Writer Pack credit count drifted")


class TestCheckoutGuard(unittest.TestCase):
    def test_warns_when_checkout_live_but_no_pack_url(self):
        cfg = {
            "stripe": {"pack_url": ""},
            "toggles": {"checkout_disabled": False},
            "features": {"nowpayments_enabled": False, "btc_payments": False},
        }
        warnings = _load().config_warnings(cfg)
        self.assertTrue(any("no one can purchase" in w for w in warnings),
                        f"dead checkout not flagged: {warnings}")

    def test_silent_when_checkout_properly_configured(self):
        cfg = {
            "stripe": {"pack_url": "https://buy.stripe.com/test_abc"},
            "toggles": {"checkout_disabled": False},
            "features": {"nowpayments_enabled": False, "btc_payments": False},
        }
        self.assertEqual(_load().config_warnings(cfg), [])

    def test_silent_when_checkout_disabled(self):
        cfg = {
            "stripe": {"pack_url": ""},
            "toggles": {"checkout_disabled": True},
            "features": {"nowpayments_enabled": False, "btc_payments": False},
        }
        self.assertEqual(_load().config_warnings(cfg), [])


if __name__ == "__main__":
    unittest.main()
