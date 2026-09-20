"""The card webhook delivers what was paid for, and only what was paid for.

Two defects, both found by driving the real webhook route (2026-09-19):

1. SALE FREEZE ATE PAID ORDERS. `ORPHO_DISABLE_CHECKOUT=1` is the founder's
   sale freeze: it stops NEW checkouts from being created. The webhook also
   honoured it, by discarding `checkout.session.completed` and marking the
   event processed. But card checkout runs on hosted payment links, which
   stay payable whatever this server's environment says, and a session opened
   before a freeze stays payable for a day. So during a freeze a buyer could
   be charged, receive nothing, and the processed marker closed the replay
   path. The module's own no-email branch refuses to do exactly that.

2. COMPLETED IS NOT PAID. `checkout.session.completed` fires when the buyer
   finishes the form. With a delayed payment method it arrives with
   `payment_status: "unpaid"`, and the money lands (or does not) later, as
   `checkout.session.async_payment_succeeded` / `_failed`. The webhook never
   read `payment_status`, so it minted credits for money that had not arrived.

Real server, real signed POSTs, and the assertions read the server's own
credit ledger: what the buyer actually holds, not what a handler returned.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest

import _srv

SECRET = "whsec_test_fulfils_what_was_paid"


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    d = tmp_path_factory.mktemp("stripe_frozen")
    for base in _srv.server_processes(d, stub_calendars=True,
                                      STRIPE_WEBHOOK_SECRET=SECRET,
                                      ORPHO_DISABLE_CHECKOUT="1"):
        yield base, d


@pytest.fixture(scope="module")
def normal(tmp_path_factory):
    d = tmp_path_factory.mktemp("stripe_normal")
    for base in _srv.server_processes(d, stub_calendars=True,
                                      STRIPE_WEBHOOK_SECRET=SECRET):
        yield base, d


def _event(event_id: str, session_id: str, *, type_: str = "checkout.session.completed",
           **session) -> bytes:
    obj = {"id": session_id, "mode": "payment",
           "customer_email": f"{session_id}@example.test", **session}
    return json.dumps({"id": event_id, "type": type_, "data": {"object": obj}},
                      separators=(",", ":")).encode()


def _deliver(base: str, payload: bytes) -> tuple[int, dict]:
    ts = int(time.time())
    mac = hmac.new(SECRET.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    status, body, _h = _srv.request(
        base, "/api/stripe/webhook", method="POST", body=payload, timeout=20,
        headers={"Content-Type": "application/json", "Stripe-Signature": f"t={ts},v1={mac}"})
    return status, json.loads(body or b"{}")


def _credits_for(data_dir: Path, session_id: str) -> int:
    p = data_dir / "credit_ledger.jsonl"
    if not p.exists():
        return 0
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return sum(int(r.get("credits_delta", 0)) for r in rows
               if session_id in (r.get("source") or ""))


# --- 1. the sale freeze ------------------------------------------------------

def test_control_the_freeze_is_really_on(frozen):
    """Without this, every test below could pass on a server that never saw
    the toggle."""
    base, _d = frozen
    status, body, _h = _srv.request(base, "/api/config", timeout=20)
    assert status == 200
    assert json.loads(body)["toggles"]["checkout_disabled"] is True


def test_a_payment_taken_during_a_freeze_is_still_delivered(frozen):
    base, data_dir = frozen
    status, result = _deliver(base, _event("evt_freeze_paid", "cs_freeze_paid",
                                           payment_status="paid"))
    assert status == 200
    assert result.get("claim_code_minted") is True, (
        f"the buyer was charged and the webhook answered {result}")
    assert _credits_for(data_dir, "cs_freeze_paid") == 10


def test_the_forged_event_control_still_holds_during_a_freeze(frozen):
    """Delivering during a freeze must not have loosened who may deliver."""
    base, data_dir = frozen
    payload = _event("evt_freeze_forged", "cs_freeze_forged", payment_status="paid")
    status, _body, _h = _srv.request(
        base, "/api/stripe/webhook", method="POST", body=payload, timeout=20,
        headers={"Content-Type": "application/json",
                 "Stripe-Signature": f"t={int(time.time())},v1={'0' * 64}"})
    assert status == 400
    assert _credits_for(data_dir, "cs_freeze_forged") == 0


# --- 2. completed is not paid ------------------------------------------------

def test_an_unpaid_completed_session_mints_nothing(normal):
    base, data_dir = normal
    status, result = _deliver(base, _event("evt_unpaid_1", "cs_unpaid_then_paid",
                                           payment_status="unpaid"))
    assert status == 200
    assert not result.get("claim_code_minted"), "credits minted before the money arrived"
    assert _credits_for(data_dir, "cs_unpaid_then_paid") == 0


def test_the_later_payment_is_what_delivers_it_once(normal):
    base, data_dir = normal
    _deliver(base, _event("evt_unpaid_1", "cs_unpaid_then_paid", payment_status="unpaid"))
    status, result = _deliver(base, _event(
        "evt_async_ok_1", "cs_unpaid_then_paid",
        type_="checkout.session.async_payment_succeeded", payment_status="paid"))
    assert status == 200 and result.get("claim_code_minted") is True, result
    assert _credits_for(data_dir, "cs_unpaid_then_paid") == 10

    # A second, differently-numbered event about the same paid session must
    # not deliver it twice: event-id dedupe cannot see that one.
    _deliver(base, _event("evt_async_ok_2", "cs_unpaid_then_paid",
                          type_="checkout.session.async_payment_succeeded",
                          payment_status="paid"))
    assert _credits_for(data_dir, "cs_unpaid_then_paid") == 10


def test_a_failed_delayed_payment_mints_nothing(normal):
    base, data_dir = normal
    _deliver(base, _event("evt_unpaid_2", "cs_unpaid_then_failed", payment_status="unpaid"))
    _deliver(base, _event("evt_async_fail", "cs_unpaid_then_failed",
                          type_="checkout.session.async_payment_failed",
                          payment_status="unpaid"))
    assert _credits_for(data_dir, "cs_unpaid_then_failed") == 0


@pytest.mark.parametrize("status_value", ["paid", "no_payment_required", None])
def test_every_settled_shape_still_delivers(normal, status_value):
    """The card path today, a fully-discounted order, and an event from before
    this field was read. None of these may regress."""
    base, data_dir = normal
    sid = f"cs_settled_{status_value}"
    extra = {} if status_value is None else {"payment_status": status_value}
    _status, result = _deliver(base, _event(f"evt_{sid}", sid, **extra))
    assert result.get("claim_code_minted") is True, result
    assert _credits_for(data_dir, sid) == 10
