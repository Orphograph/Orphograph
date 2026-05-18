"""test_stripe_refund.py — verify charge.refunded / charge.dispute.created
webhook events revoke unused Pack credits without touching consumed ones.

Money-leak premortem item A-1: refunded customers must NOT keep spendable
Pack credits. These tests pin the contract.
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
    yield tmp_path


def _read_ledger(tmp_path):
    p = tmp_path / "credit_ledger.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _refund_event(*, event_id: str, session_id: str | None,
                  via: str = "metadata") -> bytes:
    """Build a synthetic charge.refunded event.

    via="metadata"     → session_id on the charge object's metadata
    via="payment_intent" → session_id on the expanded payment_intent.metadata
    via="missing"      → no session_id anywhere
    """
    charge_obj: dict = {"id": "ch_x", "amount": 5000, "refunded": True}
    if via == "metadata" and session_id:
        charge_obj["metadata"] = {"checkout_session_id": session_id}
    elif via == "payment_intent" and session_id:
        charge_obj["payment_intent"] = {
            "id": "pi_x",
            "metadata": {"checkout_session_id": session_id},
        }
    return json.dumps({
        "id": event_id,
        "type": "charge.refunded",
        "data": {"object": charge_obj},
    }).encode()


def _dispute_event(*, event_id: str, session_id: str) -> bytes:
    return json.dumps({
        "id": event_id,
        "type": "charge.dispute.created",
        "data": {"object": {
            "id": "dp_x",
            "metadata": {"checkout_session_id": session_id},
        }},
    }).encode()


def test_refund_revokes_unused_credits_not_consumed(_isolate):
    session_id = "cs_buyer_1"
    credits.add_credits(
        claim_code="pk_buyer",
        email="buyer@example.com",
        amount=10,
        source=f"stripe:{session_id}",
    )
    # Buyer already burned 3 credits → 3 anchors already happened.
    for _ in range(3):
        credits.consume_credit("pk_buyer")
    assert credits.balance("pk_buyer") == 7

    payload = _refund_event(event_id="evt_refund_1", session_id=session_id)
    result = stripe_webhook.handle_event(payload)

    assert result["ok"] is True
    assert result["event_type"] == "charge.refunded"
    assert result["session_id"] == session_id
    assert result["revoked"] == [{"claim_code": "pk_buyer", "revoked": 7}]
    # Unused credits are gone; consumed credits stayed consumed (net 0).
    assert credits.balance("pk_buyer") == 0

    # Verify ledger has the revoke row with the right source + sign.
    rows = _read_ledger(_isolate)
    revoke_rows = [r for r in rows if r.get("source") == f"stripe-refund:{session_id}"]
    assert len(revoke_rows) == 1
    assert revoke_rows[0]["credits_delta"] == -7
    assert revoke_rows[0]["claim_code"] == "pk_buyer"


def test_refund_without_session_id_returns_ok_skipped(_isolate):
    payload = _refund_event(event_id="evt_no_sid", session_id=None, via="missing")
    result = stripe_webhook.handle_event(payload)
    # Must NOT crash. Must return ok so Stripe stops retrying.
    assert result["ok"] is True
    assert result["no_session_id"] is True
    assert result["event_type"] == "charge.refunded"
    # No revoke rows written.
    assert _read_ledger(_isolate) == []


def test_dispute_event_writes_dispute_source_entry(_isolate):
    session_id = "cs_disputed"
    credits.add_credits(
        claim_code="pk_disputed",
        email="bad@example.com",
        amount=10,
        source=f"stripe:{session_id}",
    )
    payload = _dispute_event(event_id="evt_dispute_1", session_id=session_id)
    result = stripe_webhook.handle_event(payload)

    assert result["ok"] is True
    assert result["event_type"] == "charge.dispute.created"
    assert result["revoked"] == [{"claim_code": "pk_disputed", "revoked": 10}]

    rows = _read_ledger(_isolate)
    dispute_rows = [r for r in rows if r.get("source") == f"stripe-dispute:{session_id}"]
    assert len(dispute_rows) == 1
    assert dispute_rows[0]["credits_delta"] == -10
    # Important: dispute source string is distinct from refund source string.
    assert not any(r.get("source", "").startswith("stripe-refund:") for r in rows)


def test_refund_is_idempotent_on_double_fire(_isolate):
    """Stripe replays webhooks. Two charge.refunded deliveries for the same
    session must revoke once, not twice (otherwise we'd double-negative the
    ledger and force balance underwater).
    """
    session_id = "cs_replay"
    credits.add_credits(
        claim_code="pk_replay",
        email="x@y.com",
        amount=10,
        source=f"stripe:{session_id}",
    )

    # First delivery — distinct event_id so dedupe doesn't short-circuit.
    payload_a = _refund_event(event_id="evt_refund_a", session_id=session_id)
    r1 = stripe_webhook.handle_event(payload_a)
    assert r1["revoked"] == [{"claim_code": "pk_replay", "revoked": 10}]
    assert credits.balance("pk_replay") == 0

    # Second delivery — different event_id (e.g. Stripe re-sent under a new
    # delivery ID). The event-id dedupe won't catch it; the SOURCE-level
    # idempotency in revoke_credits_by_source must.
    payload_b = _refund_event(event_id="evt_refund_b", session_id=session_id)
    r2 = stripe_webhook.handle_event(payload_b)
    assert r2["ok"] is True
    assert r2["revoked"] == [{"claim_code": "pk_replay", "revoked": 0, "skipped": "already_revoked"}]

    # Balance still 0 — not underwater.
    assert credits.balance("pk_replay") == 0
    # Only ONE negative revoke row exists.
    rows = _read_ledger(_isolate)
    revoke_rows = [r for r in rows if r.get("source") == f"stripe-refund:{session_id}"]
    assert len(revoke_rows) == 1


def test_gift_pack_is_revocable(_isolate):
    """Buyer disputed → gift recipient's pack must also be revoked.
    Gifted packs have source `stripe-gift:<sid>` so the substring match must
    catch them too.
    """
    session_id = "cs_gift_dispute"
    credits.add_credits(
        claim_code="pk_gifted",
        email="recipient@example.com",
        amount=10,
        source=f"stripe-gift:{session_id}",
    )
    # Recipient already used 2 credits.
    credits.consume_credit("pk_gifted")
    credits.consume_credit("pk_gifted")
    assert credits.balance("pk_gifted") == 8

    payload = _refund_event(event_id="evt_gift_refund", session_id=session_id)
    result = stripe_webhook.handle_event(payload)

    assert result["ok"] is True
    assert result["revoked"] == [{"claim_code": "pk_gifted", "revoked": 8}]
    assert credits.balance("pk_gifted") == 0


def test_refund_recovers_session_id_from_payment_intent_metadata(_isolate):
    """If the charge object doesn't carry session_id directly but the
    expanded payment_intent does, we still find it.
    """
    session_id = "cs_via_pi"
    credits.add_credits(
        claim_code="pk_pi",
        email="pi@example.com",
        amount=10,
        source=f"stripe:{session_id}",
    )
    payload = _refund_event(
        event_id="evt_via_pi", session_id=session_id, via="payment_intent",
    )
    result = stripe_webhook.handle_event(payload)
    assert result["session_id"] == session_id
    assert result["revoked"] == [{"claim_code": "pk_pi", "revoked": 10}]
    assert credits.balance("pk_pi") == 0
