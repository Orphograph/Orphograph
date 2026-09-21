"""Settlement events must not touch another buyer, and must not depend on order.

Found by /code-review high 259 (2026-09-20), each one reproduced by calling the
handler directly before any change:

1. THE REVOKE MATCHED BY SUBSTRING. `revoke_credits_by_source` asked "does the
   session id appear anywhere in the mint source", so a failed settlement for
   `cs_nest_1` revoked the pack that `cs_nest_12` paid for. The delivery side
   already refused lookalike ids; the take-back side did not, and a refund or a
   dispute goes through the same function.

2. A FAILURE THAT ARRIVED FIRST WAS FORGOTTEN. Stripe does not order events.
   `async_payment_failed` for a session with nothing minted yet revoked nothing
   and recorded nothing, and the `completed` that followed minted a pack for a
   payment already known to have failed. Nothing revoked it later.

In-process on purpose: the handler and the credit ledger are the units under
test, and a real server cannot reorder two events on demand.
"""
from __future__ import annotations

import json

import pytest

import credits
import stripe_webhook


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    monkeypatch.setattr(stripe_webhook, "PROCESSED_EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(stripe_webhook, "PI_SESSION_MAP_PATH", tmp_path / "pi_map.jsonl")
    sent: list[str] = []
    monkeypatch.setattr(stripe_webhook.mailer, "send_pack_claim_email",
                        lambda to, code, n: sent.append(code) or True)
    monkeypatch.setattr(stripe_webhook.mailer, "send_pack_gift_email",
                        lambda **kw: sent.append(kw.get("claim_code", "")) or True)
    yield sent


def _event(event_id: str, session_id: str, *,
           type_: str = "checkout.session.completed", **session) -> bytes:
    obj = {"id": session_id, "mode": "payment",
           "customer_email": f"{session_id}@example.test", **session}
    return json.dumps({"id": event_id, "type": type_, "data": {"object": obj}}).encode()


def _rows() -> list[dict]:
    p = credits.LEDGER_PATH
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _held(session_id: str) -> int:
    """What the buyer of exactly this session can still spend."""
    codes = {r["claim_code"] for r in _rows()
             if r.get("source") in {f"stripe:{session_id}", f"stripe-gift:{session_id}"}
             and int(r.get("credits_delta") or 0) > 0}
    return sum(int(r.get("credits_delta") or 0) for r in _rows()
               if r.get("claim_code") in codes)


# --- 1. the take-back matches a whole id --------------------------------------

def test_the_revoke_matches_a_whole_id_never_a_substring():
    credits.add_credits(claim_code="pk_long", email="a@example.test", amount=10,
                        source="stripe:cs_nest_12")
    # Control: the exact id DOES revoke, so a "no match" below is not just a
    # helper that cannot revoke anything.
    assert credits.revoke_credits_by_source("cs_nest_1", "stripe-refund:cs_nest_1") == []
    assert _held("cs_nest_12") == 10, "a lookalike id revoked another buyer's pack"

    revoked = credits.revoke_credits_by_source("cs_nest_12", "stripe-refund:cs_nest_12")
    assert [r["revoked"] for r in revoked] == [10]
    assert _held("cs_nest_12") == 0


def test_the_revoke_still_finds_every_source_shape_it_is_called_with():
    """Refunds pass a bare session id, NOWPayments passes a bare order id that
    sits inside `nowpayments:<invoice>:<order>`. None of those may stop working."""
    credits.add_credits(claim_code="pk_gift", email="g@example.test", amount=10,
                        source="stripe-gift:cs_gift_1")
    credits.add_credits(claim_code="pk_now", email="n@example.test", amount=10,
                        source="nowpayments:inv_7:ord_7")
    assert [r["revoked"] for r in credits.revoke_credits_by_source(
        "cs_gift_1", "stripe-refund:cs_gift_1")] == [10]
    assert [r["revoked"] for r in credits.revoke_credits_by_source(
        "ord_7", "nowpayments-refund:ord_7")] == [10]
    # A fragment of an order id is not the order id.
    credits.add_credits(claim_code="pk_now2", email="n2@example.test", amount=10,
                        source="nowpayments:inv_88:ord_88")
    assert credits.revoke_credits_by_source("ord_8", "nowpayments-refund:ord_8") == []


def test_a_failed_settlement_for_a_lookalike_id_leaves_the_other_buyer_alone():
    stripe_webhook.handle_event(_event("evt_a", "cs_nest_12", payment_status="paid"))
    assert _held("cs_nest_12") == 10, "control: the long id was delivered"

    result = stripe_webhook.handle_event(_event(
        "evt_b", "cs_nest_1", type_="checkout.session.async_payment_failed",
        payment_status="unpaid"))
    assert result["revoked"] == []
    assert _held("cs_nest_12") == 10


# --- 2. a failure that arrives first is remembered ----------------------------

def test_a_settlement_failure_that_arrives_first_stops_the_late_completed(_isolate):
    sent = _isolate
    stripe_webhook.handle_event(_event(
        "evt_f1", "cs_ooo", type_="checkout.session.async_payment_failed",
        payment_status="unpaid"))
    late = stripe_webhook.handle_event(_event(
        "evt_f2", "cs_ooo", payment_status="unpaid"))
    assert not late.get("claim_code_minted"), late
    assert late.get("settlement_failed") is True, late
    assert _held("cs_ooo") == 0, "credits were minted for a payment known to have failed"
    assert sent == [], "a claim email went out for a failed payment"


def test_control_without_a_prior_failure_the_same_completed_still_delivers(_isolate):
    """Delivery at `completed` is the buyer-protective policy and stays."""
    result = stripe_webhook.handle_event(_event("evt_ok", "cs_ok", payment_status="unpaid"))
    assert result.get("claim_code_minted") is True, result
    assert _held("cs_ok") == 10


def test_a_failure_for_one_session_does_not_block_another(_isolate):
    stripe_webhook.handle_event(_event(
        "evt_g1", "cs_ooo_1", type_="checkout.session.async_payment_failed",
        payment_status="unpaid"))
    other = stripe_webhook.handle_event(_event("evt_g2", "cs_ooo_12", payment_status="paid"))
    assert other.get("claim_code_minted") is True, "an id that merely contains a failed id was blocked"
    assert _held("cs_ooo_12") == 10
