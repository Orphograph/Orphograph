"""test_stripe_checkout.py — coverage for the live Stripe Checkout flow.

The previous test suite covered the inbound webhook signature path and the
subscription-cancel API helpers; it did not cover the outbound checkout-
session creator or its HTTP handler. This file fills that gap.

We monkeypatch `stripe_api._request` instead of hitting api.stripe.com, so
these tests are deterministic and run offline. The subprocess server fixture
in test_attacks.py cannot mock outbound HTTP from a child process, so we
exercise the handler directly via a stub-handler shim (same pattern as
test_subscriptions.py).
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))


# ──────────────────────────────────────────────────────────────────────────
# Tests for stripe_api.create_checkout_session() — pure function unit tests.
# ──────────────────────────────────────────────────────────────────────────

class TestCreateCheckoutSession(unittest.TestCase):
    """Verify the form payload assembled for POST /v1/checkout/sessions."""

    def setUp(self):
        sys.modules.pop("stripe_api", None)
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_unit_test_key"
        os.environ.pop("STRIPE_AUTOMATIC_TAX", None)
        import stripe_api
        self.stripe_api = stripe_api
        self.calls = []

        def fake_request(method, path, form=None):
            self.calls.append({"method": method, "path": path, "form": form})
            return {"ok": True, "data": {"id": "cs_test_fake", "url": "https://checkout.stripe.com/c/pay/cs_test_fake"}}

        self.fake = fake_request

    def test_pack_payment_mode_payload(self):
        with patch.object(self.stripe_api, "_request", side_effect=self.fake):
            result = self.stripe_api.create_checkout_session(
                price_id="price_PACK",
                mode="payment",
                success_url="https://orphograph.com/buy.html?stripe_session={CHECKOUT_SESSION_ID}",
                cancel_url="https://orphograph.com/?stripe=canceled",
                customer_email="buyer@example.com",
            )
        self.assertTrue(result.get("ok"))
        self.assertEqual(len(self.calls), 1)
        form = self.calls[0]["form"]
        self.assertEqual(form["mode"], "payment")
        self.assertEqual(form["line_items[0][price]"], "price_PACK")
        self.assertEqual(form["line_items[0][quantity]"], "1")
        self.assertEqual(form["customer_email"], "buyer@example.com")
        # mode=payment includes the customer_creation hint
        self.assertEqual(form["customer_creation"], "if_required")
        # automatic_tax is GATED off by default — must NOT be in the form
        self.assertNotIn("automatic_tax[enabled]", form)
        self.assertNotIn("billing_address_collection", form)

    def test_subscription_mode_payload(self):
        with patch.object(self.stripe_api, "_request", side_effect=self.fake):
            self.stripe_api.create_checkout_session(
                price_id="price_SUB",
                mode="subscription",
                success_url="https://orphograph.com/buy.html",
                cancel_url="https://orphograph.com/?stripe=canceled",
            )
        form = self.calls[0]["form"]
        self.assertEqual(form["mode"], "subscription")
        # customer_creation only emitted in payment mode
        self.assertNotIn("customer_creation", form)
        # No email passed → no customer_email field
        self.assertNotIn("customer_email", form)

    def test_automatic_tax_opts_in_via_env(self):
        os.environ["STRIPE_AUTOMATIC_TAX"] = "1"
        sys.modules.pop("stripe_api", None)
        import stripe_api as fresh
        with patch.object(fresh, "_request", side_effect=self.fake):
            fresh.create_checkout_session(
                price_id="price_PACK",
                mode="payment",
                success_url="https://orphograph.com/buy.html",
                cancel_url="https://orphograph.com/?",
            )
        form = self.calls[0]["form"]
        self.assertEqual(form["automatic_tax[enabled]"], "true")
        self.assertEqual(form["billing_address_collection"], "auto")
        del os.environ["STRIPE_AUTOMATIC_TAX"]

    def test_rejects_bad_mode_without_network(self):
        with patch.object(self.stripe_api, "_request", side_effect=self.fake):
            result = self.stripe_api.create_checkout_session(
                price_id="price_x", mode="trial",
                success_url="x", cancel_url="y",
            )
        self.assertFalse(result.get("ok"))
        self.assertIn("mode", result.get("error", "").lower())
        # CRITICAL: must NOT have called _request
        self.assertEqual(len(self.calls), 0)

    def test_rejects_empty_price_id(self):
        with patch.object(self.stripe_api, "_request", side_effect=self.fake):
            result = self.stripe_api.create_checkout_session(
                price_id="", mode="payment",
                success_url="x", cancel_url="y",
            )
        self.assertFalse(result.get("ok"))
        self.assertEqual(len(self.calls), 0)


# ──────────────────────────────────────────────────────────────────────────
# Tests for the error-categorization function — confirms 401/402/429/5xx
# each yield the right category + retryable + operator_alert flags.
# ──────────────────────────────────────────────────────────────────────────

class TestErrorCategorization(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("stripe_api", None)
        import stripe_api
        self.stripe_api = stripe_api

    def test_401_is_auth_error_with_operator_alert(self):
        cat, retry, alert = self.stripe_api._categorize_http_error(401)
        self.assertEqual(cat, "auth_error")
        self.assertFalse(retry)
        self.assertTrue(alert)

    def test_402_is_card_declined_no_retry(self):
        cat, retry, alert = self.stripe_api._categorize_http_error(402)
        self.assertEqual(cat, "card_declined")
        self.assertFalse(retry)
        self.assertFalse(alert)

    def test_429_is_rate_limited_retryable(self):
        cat, retry, _ = self.stripe_api._categorize_http_error(429)
        self.assertEqual(cat, "rate_limited")
        self.assertTrue(retry)

    def test_500_is_stripe_outage_retryable(self):
        cat, retry, _ = self.stripe_api._categorize_http_error(503)
        self.assertEqual(cat, "stripe_outage")
        self.assertTrue(retry)

    def test_400_is_invalid_request_non_retryable(self):
        cat, retry, alert = self.stripe_api._categorize_http_error(400)
        self.assertEqual(cat, "invalid_request")
        self.assertFalse(retry)
        self.assertFalse(alert)


# ──────────────────────────────────────────────────────────────────────────
# Stub-handler tests for _handle_stripe_checkout — exercises plan routing,
# bad-input rejection, missing-env behavior, and Stripe error propagation.
# Uses the same shim pattern as test_subscriptions.py (no live HTTP server).
# ──────────────────────────────────────────────────────────────────────────

class _StubHandler:
    """Minimal stub satisfying the parts of BaseHTTPRequestHandler that
    _handle_stripe_checkout reaches. Captures the response for inspection.
    """

    def __init__(self, body_bytes: bytes, headers: dict | None = None):
        self.rfile = io.BytesIO(body_bytes)
        self.wfile = io.BytesIO()
        self.path = "/api/stripe/checkout"
        self.headers = headers or {"Content-Length": str(len(body_bytes)), "Host": "127.0.0.1:8000"}
        self._status = None
        self._sent_headers = {}

    def send_response(self, code):
        self._status = code

    def send_header(self, k, v):
        self._sent_headers[k] = v

    def end_headers(self):
        pass

    # Helpers used inside app.py
    def _client_key(self):
        return "test-client"

    def _client_ip(self):
        return "127.0.0.1"

    def _session_email(self):
        return None


class TestStripeCheckoutHandler(unittest.TestCase):
    """Drive Handler._handle_stripe_checkout through realistic inputs."""

    def setUp(self):
        # Fresh env for each test
        self._old = {k: os.environ.get(k) for k in (
            "STRIPE_SECRET_KEY", "STRIPE_PRICE_PACK", "STRIPE_PRICE_SUB",
            "ORPHO_DISABLE_CHECKOUT", "ORPHO_ENV", "SITE_URL",
        )}
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_unit_test_key"
        os.environ["STRIPE_PRICE_PACK"] = "price_PACK_TEST"
        os.environ["STRIPE_PRICE_SUB"] = "price_SUB_TEST"
        os.environ.pop("ORPHO_DISABLE_CHECKOUT", None)
        os.environ.pop("ORPHO_ENV", None)
        os.environ.pop("SITE_URL", None)
        os.environ["ORPHO_DATA_DIR"] = tempfile.mkdtemp()
        # Force fresh modules with the new env
        for m in ("app", "stripe_api"):
            sys.modules.pop(m, None)
        import app
        self.app = app

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for m in ("app", "stripe_api"):
            sys.modules.pop(m, None)

    def _read_response(self, handler):
        handler.wfile.seek(0)
        return handler.wfile.read()

    def test_pack_plan_calls_stripe_with_pack_price(self):
        body = b'{"plan":"pack"}'
        handler = _StubHandler(body)
        calls = []

        def fake_create(**kwargs):
            calls.append(kwargs)
            return {"ok": True, "data": {"id": "cs_test_pack", "url": "https://checkout.stripe.com/x"}}

        with patch.object(self.app.stripe_api, "create_checkout_session", side_effect=fake_create):
            self.app.Handler._handle_stripe_checkout(handler)
        self.assertEqual(handler._status, 200)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["price_id"], "price_PACK_TEST")
        self.assertEqual(calls[0]["mode"], "payment")

    def test_pro_plan_calls_stripe_with_sub_price(self):
        body = b'{"plan":"pro"}'
        handler = _StubHandler(body)
        calls = []

        def fake_create(**kwargs):
            calls.append(kwargs)
            return {"ok": True, "data": {"id": "cs_test_sub", "url": "https://checkout.stripe.com/y"}}

        with patch.object(self.app.stripe_api, "create_checkout_session", side_effect=fake_create):
            self.app.Handler._handle_stripe_checkout(handler)
        self.assertEqual(handler._status, 200)
        self.assertEqual(calls[0]["price_id"], "price_SUB_TEST")
        self.assertEqual(calls[0]["mode"], "subscription")

    def test_unknown_plan_is_400(self):
        body = b'{"plan":"bogus"}'
        handler = _StubHandler(body)
        self.app.Handler._handle_stripe_checkout(handler)
        self.assertEqual(handler._status, 400)

    def test_invalid_json_body_is_400(self):
        body = b"not json at all"
        handler = _StubHandler(body)
        self.app.Handler._handle_stripe_checkout(handler)
        self.assertEqual(handler._status, 400)

    def test_missing_price_env_returns_503(self):
        os.environ.pop("STRIPE_PRICE_PACK", None)
        for m in ("app", "stripe_api"):
            sys.modules.pop(m, None)
        import app
        body = b'{"plan":"pack"}'
        handler = _StubHandler(body)
        app.Handler._handle_stripe_checkout(handler)
        self.assertEqual(handler._status, 503)

    def test_stripe_error_propagates_as_503(self):
        # 503, NOT 502: Cloudflare replaces origin 502 bodies with its own
        # opaque error page, so the JSON detail never reaches the client
        # (observed live 2026-07-09). Origin 503 passes through intact.
        body = b'{"plan":"pack"}'
        handler = _StubHandler(body)
        with patch.object(self.app.stripe_api, "create_checkout_session",
                          return_value={"ok": False, "error": "No such price: price_x"}):
            self.app.Handler._handle_stripe_checkout(handler)
        self.assertEqual(handler._status, 503)
        # The customer-facing message must not leak our secret key
        body_bytes = self._read_response(handler)
        self.assertNotIn(b"sk_test", body_bytes)


# ──────────────────────────────────────────────────────────────────────────
# Tests for stripe_api.charges_enabled() — the card-CTA gate.
# ──────────────────────────────────────────────────────────────────────────

class TestChargesEnabled(unittest.TestCase):
    """A configured account is not necessarily a chargeable one (live
    incident 2026-07-09: charges_enabled=false while every price/link/env
    was valid). The gate must reflect Stripe's answer, cache it, and serve
    the stale answer over flapping on transient API failures."""

    def setUp(self):
        sys.modules.pop("stripe_api", None)
        os.environ["STRIPE_SECRET_KEY"] = "sk_test_unit_test_key"
        import stripe_api
        self.stripe_api = stripe_api

    def tearDown(self):
        os.environ.pop("STRIPE_SECRET_KEY", None)
        sys.modules.pop("stripe_api", None)

    def test_unconfigured_key_returns_none(self):
        self.stripe_api.STRIPE_SECRET_KEY = ""
        self.assertIsNone(self.stripe_api.charges_enabled())

    def test_reads_account_and_caches(self):
        calls = []

        def fake_request(method, path, form=None):
            calls.append(path)
            return {"ok": True, "data": {"charges_enabled": True}}

        with patch.object(self.stripe_api, "_request", side_effect=fake_request):
            self.assertIs(self.stripe_api.charges_enabled(), True)
            self.assertIs(self.stripe_api.charges_enabled(), True)
        # Second call must come from cache — one API hit only.
        self.assertEqual(calls, ["/account"])

    def test_restricted_account_returns_false(self):
        with patch.object(self.stripe_api, "_request",
                          return_value={"ok": True, "data": {"charges_enabled": False}}):
            self.assertIs(self.stripe_api.charges_enabled(), False)

    def test_stale_if_error_keeps_last_known_answer(self):
        with patch.object(self.stripe_api, "_request",
                          return_value={"ok": True, "data": {"charges_enabled": True}}):
            self.assertIs(self.stripe_api.charges_enabled(), True)
        # Expire the cache, then fail the lookup: last known answer survives.
        self.stripe_api._ACCOUNT_CACHE["ts"] = 0.0
        with patch.object(self.stripe_api, "_request",
                          return_value={"ok": False, "error": "boom"}):
            self.assertIs(self.stripe_api.charges_enabled(), True)


if __name__ == "__main__":
    unittest.main()
