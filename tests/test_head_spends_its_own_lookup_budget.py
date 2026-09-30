"""A HEAD must not spend the budget a person's GET needs.

`do_HEAD` answers by running the GET routing and discarding the body, so the
two read-only lookups a buyer's confirmation page makes spent a per-address
token on a HEAD as well:

  * GET /api/nowpayments/order/<id>    web/pay/success.js polls it
  * GET /api/stripe/session?id=cs_...  web/buy.js asks once per load

Link scanners and uptime probes send HEAD. One /24 shares a bucket, so a
probe on the buyer's network used the budget up and the buyer's own page was
answered 429 (found 2026-09-27).

A HEAD spends from a key of its own on the same limiter. It stays bounded:
the session lookup makes its Stripe read whatever the method, so a HEAD with
no limit would undo the limiter.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import _srv

APP = Path(__file__).resolve().parent.parent / "server" / "app.py"

# route -> (the constant that sizes its bucket, a path that reaches its limiter)
ROUTES = {
    "order-status": ("STATUS_RATE_CAPACITY", "/api/nowpayments/order/np_head_budget_{i}"),
    "stripe-session": ("SESSION_LOOKUP_CAPACITY", "/api/stripe/session?id=cs_test_headbudget{i}"),
}


def _capacity(name: str) -> int:
    """The bucket size as the server's source declares it. Read, not imported:
    importing the module here would run its start-up inside the test process."""
    for node in ast.parse(APP.read_text()).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name):
            value = ast.literal_eval(node.value)
            assert isinstance(value, int) and value > 0, (name, value)
            return value
    raise AssertionError(f"the server no longer assigns {name}; re-derive this test")


def _route(route: str) -> tuple[int, str]:
    constant, path = ROUTES[route]
    return _capacity(constant), path


# FUNCTION scope: the limiter is per-server state and every test here starts
# from a full bucket.
@pytest.fixture()
def server(tmp_path):
    # Stripe is replaced in the server process, so a well-formed session id
    # is answered 200 and nothing leaves this machine.
    yield from _srv.server_processes(
        tmp_path, stub_calendars=True, stub_stripe=True,
        STRIPE_SECRET_KEY="sk_test_not_a_real_key")


def _codes(base: str, path: str, method: str, n: int, tag: str = "") -> list[int]:
    return [_srv.request(base, path.format(i=f"{tag}{i}"), method)[0] for i in range(n)]


@pytest.mark.parametrize("route", ROUTES)
def test_heads_leave_the_whole_get_budget(server, route):
    capacity, path = _route(route)
    heads = _codes(server, path, "HEAD", capacity, "probe")
    # Control: every HEAD was answered by the handler, past its limiter.
    assert heads == [200] * capacity, heads
    status, body, _h = _srv.request(server, path.format(i="buyer"))
    assert status == 200, f"a HEAD spent the GET budget: {status} {body!r}"
    assert json.loads(body), "the GET did not get its normal answer"
    # Not one token left over: the whole budget.
    rest = _codes(server, path, "GET", capacity - 1, "poll")
    assert rest == [200] * (capacity - 1), rest


@pytest.mark.parametrize("route", ROUTES)
def test_a_head_is_still_bounded(server, route):
    capacity, path = _route(route)
    answers = [_srv.request(server, path.format(i=i), "HEAD") for i in range(capacity + 1)]
    assert [a[0] for a in answers] == [200] * capacity + [429], [a[0] for a in answers]
    _status, body, headers = answers[-1]
    assert int(headers.get("Retry-After") or 0) >= 1, headers
    assert body == b"", "a HEAD answer carries no body"


@pytest.mark.parametrize("route", ROUTES)
def test_control_a_get_is_still_bounded(server, route):
    capacity, path = _route(route)
    answers = [_srv.request(server, path.format(i=i)) for i in range(capacity + 1)]
    assert [a[0] for a in answers] == [200] * capacity + [429], [a[0] for a in answers]
    _status, body, headers = answers[-1]
    assert headers.get("Retry-After") == str(json.loads(body)["retry_after_seconds"]), headers


def test_a_refused_head_makes_no_stripe_read(server, tmp_path):
    """What the bound is for: each session lookup that passes the limiter is
    one read against the Stripe account, HEAD or GET."""
    capacity, path = _route("stripe-session")
    codes = _codes(server, path, "HEAD", capacity + 3)
    assert codes == [200] * capacity + [429] * 3, codes
    calls = [json.loads(line) for line in
             (tmp_path / "stub_stripe_calls.jsonl").read_text().splitlines()]
    reads = [c["path"] for c in calls if c["path"].startswith("/checkout/sessions/")]
    assert len(reads) == capacity, reads
