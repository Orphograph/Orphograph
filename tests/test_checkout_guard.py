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

    def test_no_false_alarm_when_nowpayments_on_native_btc_off(self):
        """The live prod config (2026-05-28): card live, NOWPayments on,
        native BTC flow off. NOWPayments alone is a complete crypto path —
        this must NOT emit a 'half-wired' / crypto warning."""
        cfg = {
            "stripe": {"pack_url": "https://buy.stripe.com/live_xyz"},
            "toggles": {"checkout_disabled": False},
            "features": {"nowpayments_enabled": True, "btc_payments": False},
        }
        warnings = _load().config_warnings(cfg)
        self.assertEqual(warnings, [], f"false crypto alarm returned: {warnings}")
        self.assertFalse(any("half-wired" in w for w in warnings))

    def test_card_only_config_is_silent(self):
        """Card configured, no crypto backend at all — a valid card-only
        config. Absence of crypto is not a misconfiguration."""
        cfg = {
            "stripe": {"pack_url": "https://buy.stripe.com/live_xyz"},
            "toggles": {"checkout_disabled": False},
            "features": {"nowpayments_enabled": False, "btc_payments": False},
        }
        self.assertEqual(_load().config_warnings(cfg), [])

    def test_warns_when_pack_url_is_placeholder(self):
        """The 2026-05-30 live failure: STRIPE_PACK_URL was the literal
        placeholder 'https://buy.stripe.com/...'. Non-empty, so the old
        emptiness check passed it and the dead button shipped silently."""
        cfg = {
            "stripe": {"pack_url": "https://buy.stripe.com/..."},
            "toggles": {"checkout_disabled": False},
            "features": {"nowpayments_enabled": False, "btc_payments": False},
        }
        warnings = _load().config_warnings(cfg)
        self.assertTrue(any("not a valid Stripe payment link" in w for w in warnings),
                        f"placeholder pack_url not flagged: {warnings}")

    def test_warns_when_monthly_url_is_placeholder(self):
        """Standing Order link placeholder is the same dead-button trap; an
        empty monthly URL is fine (not offered) but a placeholder is not."""
        cfg = {
            "stripe": {
                "pack_url": "https://buy.stripe.com/live_xyz",
                "personal_monthly_url": "https://buy.stripe.com/...",
            },
            "toggles": {"checkout_disabled": False},
            "features": {"nowpayments_enabled": False, "btc_payments": False},
        }
        warnings = _load().config_warnings(cfg)
        self.assertTrue(any("STRIPE_PERSONAL_MONTHLY_URL" in w for w in warnings),
                        f"placeholder monthly_url not flagged: {warnings}")


class TestStripeUrlValidation(unittest.TestCase):
    def test_is_live_stripe_url(self):
        mod = _load()
        live = [
            "https://buy.stripe.com/test_abc",
            "https://buy.stripe.com/live_xyz",
            "https://buy.stripe.com/eVa00j2Qq7byfYQbII",
            "https://buy.stripe.com/8x2cN5abc123?prefilled_email=a@b.co",
            "https://checkout.stripe.com/c/pay/cs_live_abc12345",
        ]
        for url in live:
            self.assertTrue(mod.is_live_stripe_url(url), f"should be live: {url}")
        dead = [
            "",
            "   ",
            "https://buy.stripe.com/...",
            "https://buy.stripe.com/",
            "https://buy.stripe.com/abc",          # too short
            "https://example.com/buy",             # wrong host
            "buy.stripe.com/eVa00j2Qq7byfYQbII",   # no scheme
        ]
        for url in dead:
            self.assertFalse(mod.is_live_stripe_url(url), f"should be dead: {url!r}")


if __name__ == "__main__":
    unittest.main()
