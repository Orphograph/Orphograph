"""The buyer's post-payment status lookups must survive their own page.

web/pay/success.js polls GET /api/nowpayments/order/<id> up to 6 times and
web/buy.js calls GET /api/stripe/session?id=… once per load. Both drew on the
anchor limiter (3 per day per IP prefix), so polls 4-6 answered 429 and a
crypto payment credited after the third poll never showed as confirmed.
Measured in production on 2026-09-22: 200, 200, 200, 429, 429.

Every other test of these routes boots with RATE_LIMIT_PER_DAY=100000, which
is why none of them could see it. This one boots with the production default.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import _srv

REPO_ROOT = Path(__file__).resolve().parent.parent
SUCCESS_JS = REPO_ROOT / "web" / "pay" / "success.js"


@pytest.fixture()
def server(tmp_path):
    # "3" is the production default (app.py, _per_day_default). _srv's own
    # default of 100000 is exactly what hid this defect from every other test.
    yield from _srv.server_processes(
        tmp_path,
        RATE_LIMIT_PER_DAY="3",
        # Configured, so /api/stripe/session reaches its limiter; the ids
        # below are malformed, so nothing is ever sent to Stripe.
        STRIPE_SECRET_KEY="sk_test_not_a_real_key",
    )


def _status(base: str, path: str) -> int:
    return _srv.request(base, path, headers={"Accept": "application/json"})[0]


def _page_poll_count() -> int:
    m = re.search(r"MAX_TRIES\s*=\s*(\d+)", SUCCESS_JS.read_text())
    assert m, "success.js no longer declares MAX_TRIES; re-derive this test"
    return int(m.group(1))


def test_every_poll_the_success_page_makes_is_answered(server):
    polls = _page_poll_count()
    codes = [_status(server, f"/api/nowpayments/order/np_unknown_{i}")
             for i in range(polls)]
    assert 429 not in codes, codes
    assert set(codes) == {200}, codes


def test_session_lookup_survives_reloads(server):
    # Malformed id: 400 comes from validation AFTER the limiter, so a 429 here
    # can only be the budget.
    codes = [_status(server, "/api/stripe/session?id=bad") for _ in range(6)]
    assert codes == [400] * 6, codes


def test_the_lookups_are_still_bounded(server):
    codes = [_status(server, f"/api/nowpayments/order/np_unknown_{i}")
             for i in range(40)]
    assert 429 in codes, "status lookups lost their rate limit entirely"
