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
        # Configured, so /api/stripe/session reaches its limiter. Every
        # outbound call goes to a proxy on a closed local port, so a
        # well-formed id is refused locally and nothing reaches Stripe.
        STRIPE_SECRET_KEY="sk_test_not_a_real_key",
        HTTPS_PROXY="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9",
        NO_PROXY="", no_proxy="",
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
    # 502 is the refused proxy answering for Stripe: the handler got past its
    # limiter. A 429 here can only be the budget.
    status, body, _h = _srv.request(server, "/api/stripe/session?id=cs_test_reload0")
    # Control on the FIRST lookup (a later one could be a 429 and prove
    # nothing): the 502 is the local refusal, not Stripe, whose 401 for the
    # fake key would also map to 502.
    assert status == 502, (status, body)
    assert b"could not reach payment provider" in body.lower(), body
    codes = [_status(server, f"/api/stripe/session?id=cs_test_reload{i}") for i in range(1, 5)]
    assert codes == [502] * 4, codes


def test_session_lookups_stay_small_per_prefix(server):
    """Each well-formed id is one live Stripe read, and Stripe's read limit is
    shared with checkout and the webhook, so this burst stays small."""
    codes = [_status(server, f"/api/stripe/session?id=cs_test_burst{i}") for i in range(8)]
    assert codes[:5] == [502] * 5 and codes[5:] == [429] * 3, codes


def test_a_malformed_session_id_costs_no_budget(server):
    """The shape check runs before the limiter: a page that sent a bad value
    must not spend the buyer's budget for their real lookup."""
    assert [_status(server, "/api/stripe/session?id=bad") for _ in range(10)] == [400] * 10
    assert _status(server, "/api/stripe/session?id=cs_test_after_bad") == 502


def test_the_lookups_are_still_bounded(server):
    codes = [_status(server, f"/api/nowpayments/order/np_unknown_{i}")
             for i in range(80)]
    assert 429 in codes, "status lookups lost their rate limit entirely"


def test_no_full_session_id_reaches_the_server_output(server, tmp_path):
    """The access line hides a checkout-session id, and so must every other
    line on the same stream: stripe_api's network-error line printed the
    request path, which carries the id."""
    sid = "cs_test_FullSessionCanary0123456789"
    assert _status(server, f"/api/stripe/session?id={sid}") == 502
    logs = list(tmp_path.glob("server-*.log"))
    assert len(logs) == 1, logs
    text = logs[0].read_text(errors="replace")
    assert "URLError path=/checkout/sessions/cs_test_…456789" in text, \
        "control: the network-error line was written, masked"
    assert sid not in text, "a full checkout-session id reached the server output"
