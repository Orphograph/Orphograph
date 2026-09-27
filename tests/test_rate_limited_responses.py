"""Every 429 the server sends is built in one place.

Nine handlers built their 429 by hand (status, Retry-After, JSON body, length,
security headers) and had already drifted: one sent `Cache-Control: no-store`,
eight did not, while every other JSON answer goes through _json_response,
which always does. Found 2026-09-27 (@checkout-429-helper, widened from the
two checkout handlers to the class).
"""
from __future__ import annotations

import json
from pathlib import Path

import _srv

APP = Path(__file__).resolve().parent.parent / "server" / "app.py"
HEADERS = {"Content-Type": "application/json"}


def test_one_place_builds_a_429():
    assert APP.read_text().count("send_response(429)") == 1, (
        "a handler builds its own 429 again: use _send_rate_limited")


def test_a_throttled_checkout_says_when_to_retry_and_is_not_cached(tmp_path):
    for base in _srv.server_processes(tmp_path, stub_calendars=True, stub_stripe=True,
                                      STRIPE_SECRET_KEY="sk_test_not_a_real_key",
                                      CHECKOUT_RATE_PER_HOUR="1"):
        body = json.dumps({"plan": "no-such-plan", "email": "a@example.test"}).encode()
        first = _srv.request(base, "/api/stripe/checkout", "POST", body, HEADERS)
        assert first[0] == 400, first[0]  # control: the budget of 1 is spent on a real refusal
        status, raw, headers = _srv.request(base, "/api/stripe/checkout", "POST", body, HEADERS)
        assert status == 429, status
        payload = json.loads(raw)
        assert payload["error"] == "rate limit exceeded"
        assert headers.get("Retry-After") == str(payload["retry_after_seconds"]), headers
        assert headers.get("Cache-Control") == "no-store", headers
        assert headers.get("Strict-Transport-Security"), "security headers must still ride on it"
