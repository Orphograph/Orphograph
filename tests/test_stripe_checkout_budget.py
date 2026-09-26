"""Starting a card checkout has its own budget, not the anchor one.

It drew on the anchor bucket (3 a day per address in production), so a buyer
who opened checkout a fourth time in a day from one /24 was refused for hours.
Founder decision 2026-09-26: checkout gets its own bucket (10 at once, then 10
an hour per address). Stripe is stubbed in the server process and every
request here is refused before a session would be created (unknown plan), so
nothing is sent anywhere; the budget is spent before the body is read.
"""
from __future__ import annotations

import json

import _srv

HEADERS = {"Content-Type": "application/json"}


def _checkout(base: str) -> int:
    return _srv.request(base, "/api/stripe/checkout", "POST",
                        json.dumps({"plan": "no-such-plan", "email": "a@example.test"}).encode(),
                        HEADERS)[0]


def test_checkout_survives_a_spent_anchor_budget(tmp_path):
    for base in _srv.server_processes(tmp_path, stub_calendars=True, stub_stripe=True,
                                      STRIPE_SECRET_KEY="sk_test_not_a_real_key",
                                      RATE_LIMIT_PER_DAY="1"):
        assert _srv.request(base, "/api/anchor", "POST",
                            json.dumps({"hash_hex": "ab" * 32}).encode(), HEADERS)[0] == 200
        assert _srv.request(base, "/api/anchor", "POST",
                            json.dumps({"hash_hex": "cd" * 32}).encode(), HEADERS)[0] == 429  # control
        codes = [_checkout(base) for _ in range(4)]
        # Each reached the handler past the limiter and was refused on the
        # unknown plan: exactly 400, not 429 and not "not configured".
        assert codes == [400] * 4, codes


def test_checkout_still_has_a_ceiling(tmp_path):
    for base in _srv.server_processes(tmp_path, stub_calendars=True, stub_stripe=True,
                                      STRIPE_SECRET_KEY="sk_test_not_a_real_key"):
        codes = [_checkout(base) for _ in range(11)]
        assert codes[:10] == [400] * 10, codes
        assert codes[10] == 429, codes
