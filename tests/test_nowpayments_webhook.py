"""test_nowpayments_webhook.py — NOWPayments IPN handler.

Verifies HMAC-SHA512 signing, idempotent crediting, ignored-status branches,
and the refund revoke path. No real network — credits/mailer are isolated
to a tmp ledger and the mailer is inert without RESEND_API_KEY.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

import credits
import nowpayments_webhook


SECRET = "ipn_secret_for_tests"


def _sign(body: dict, secret: str = SECRET) -> tuple[bytes, str]:
    """Build (payload_bytes, signature_hex) the way NOWPayments would."""
    canonical = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha512).hexdigest()
    return canonical, sig


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Every test gets a fresh credit ledger + processed-events log."""
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    monkeypatch.setattr(
        nowpayments_webhook,
        "PROCESSED_EVENTS_PATH",
        tmp_path / "nowpayments_processed_events.jsonl",
    )
    yield


# ----------------------------------------------------------- signature tests

def test_verify_signature_accepts_good_signature():
    body = {"order_id": "np_x", "payment_status": "finished"}
    payload, sig = _sign(body)
    assert nowpayments_webhook.verify_signature(payload, sig, SECRET) is True


def test_verify_signature_rejects_bad_signature_returns_400_path():
    """A tampered signature must not pass — the route must 400 on this."""
    body = {"order_id": "np_x", "payment_status": "finished"}
    payload, _good = _sign(body)
    assert nowpayments_webhook.verify_signature(payload, "deadbeef" * 16, SECRET) is False
    # And confirm we know how to detect the bad-sig precondition. The route
    # itself short-circuits on a bad sig; here we lock in the contract.


def test_verify_signature_rejects_wrong_secret():
    body = {"order_id": "np_x"}
    payload, sig = _sign(body, "real_secret")
    assert nowpayments_webhook.verify_signature(payload, sig, "wrong_secret") is False


# ----------------------------------------------------------- crediting tests

def test_finished_status_grants_credits(tmp_path):
    body = {
        "order_id": "np_order_001",
        "payment_status": "finished",
        "invoice_id": "inv_001",
        "customer_email": "buyer@example.com",
        "plan": "writer_pack",
    }
    payload, sig = _sign(body)
    assert nowpayments_webhook.verify_signature(payload, sig, SECRET) is True
    result = nowpayments_webhook.handle_event(payload)
    assert result["ok"] is True
    assert result.get("claim_code_minted") is True
    assert result["plan"] == "writer_pack"
    assert result["credits"] == 10

    # Ledger should have exactly one positive row, 10 credits for one code.
    rows = [
        json.loads(l)
        for l in credits.LEDGER_PATH.read_text().splitlines()
        if l.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["credits_delta"] == 10
    assert rows[0]["source"].startswith("nowpayments:")


def test_finished_status_pack_50_grants_50_credits():
    body = {
        "order_id": "np_order_050",
        "payment_status": "finished",
        "invoice_id": "inv_050",
        "customer_email": "biggie@example.com",
        "plan": "pack_50",
    }
    payload, _sig = _sign(body)
    result = nowpayments_webhook.handle_event(payload)
    assert result.get("claim_code_minted") is True
    assert result["credits"] == 50


# ----------------------------------------------------------- idempotency

def test_redelivery_of_same_event_does_not_double_credit():
    body = {
        "order_id": "np_dup_001",
        "payment_status": "finished",
        "invoice_id": "inv_dup_001",
        "customer_email": "dup@example.com",
        "plan": "writer_pack",
    }
    payload, _sig = _sign(body)

    first = nowpayments_webhook.handle_event(payload)
    assert first.get("claim_code_minted") is True

    rows_before = credits.LEDGER_PATH.read_text().splitlines()
    second = nowpayments_webhook.handle_event(payload)
    assert second == {"ok": True, "duplicate": "np_dup_001:finished"}
    rows_after = credits.LEDGER_PATH.read_text().splitlines()
    assert rows_before == rows_after, "duplicate IPN must not mutate the credit ledger"


# ----------------------------------------------------------- non-crediting statuses

def test_partially_paid_returns_200_without_credit():
    body = {
        "order_id": "np_partial_001",
        "payment_status": "partially_paid",
        "invoice_id": "inv_partial_001",
        "customer_email": "p@example.com",
    }
    payload, _sig = _sign(body)
    result = nowpayments_webhook.handle_event(payload)
    assert result["ok"] is True
    assert result.get("ignored") is True
    assert result.get("reason") == "partially_paid"
    # No credit ledger should exist (or it should be empty).
    if credits.LEDGER_PATH.exists():
        assert credits.LEDGER_PATH.read_text().strip() == ""


def test_failed_status_returns_200_without_credit():
    body = {
        "order_id": "np_fail_001",
        "payment_status": "failed",
        "invoice_id": "inv_fail_001",
    }
    payload, _sig = _sign(body)
    result = nowpayments_webhook.handle_event(payload)
    assert result["ok"] is True
    assert result.get("ignored") is True
    assert result.get("reason") == "failed"
    if credits.LEDGER_PATH.exists():
        assert credits.LEDGER_PATH.read_text().strip() == ""


# ----------------------------------------------------------- refund branch

def test_refunded_after_credit_revokes_unused_credits():
    # First: a fresh finished payment that mints a pack.
    order_id = "np_refund_target"
    finished_body = {
        "order_id": order_id,
        "payment_status": "finished",
        "invoice_id": "inv_refund_target",
        "customer_email": "buyer-refund@example.com",
        "plan": "writer_pack",
    }
    payload_f, _sig_f = _sign(finished_body)
    minted = nowpayments_webhook.handle_event(payload_f)
    assert minted.get("claim_code_minted") is True

    # Now a refund IPN for the same order. Should call
    # credits.revoke_credits_by_source under the hood and tag with the
    # `nowpayments-refund:` prefix.
    refund_body = {
        "order_id": order_id,
        "payment_status": "refunded",
        "invoice_id": "inv_refund_target",
    }
    payload_r, _sig_r = _sign(refund_body)
    result = nowpayments_webhook.handle_event(payload_r)
    assert result["ok"] is True
    assert result.get("refunded") is True
    assert result["order_id"] == order_id
    revoked = result.get("revoked") or []
    # At least one code should be revoked (the one just minted).
    assert len(revoked) >= 1

    # The ledger must contain a negative row tagged with the
    # nowpayments-refund: source prefix.
    rows = [
        json.loads(l)
        for l in credits.LEDGER_PATH.read_text().splitlines()
        if l.strip()
    ]
    neg_rows = [r for r in rows if r["credits_delta"] < 0]
    assert neg_rows, "expected at least one negative row from the refund"
    assert any(
        r["source"].startswith(f"nowpayments-refund:{order_id}")
        for r in neg_rows
    ), "refund row must be tagged with nowpayments-refund:<order_id>"


# ----------------------------------------------------------- missing-email guard

def test_finished_status_without_email_does_not_credit():
    body = {
        "order_id": "np_no_email",
        "payment_status": "finished",
        "invoice_id": "inv_no_email",
        # no customer_email
        "plan": "writer_pack",
    }
    payload, _sig = _sign(body)
    result = nowpayments_webhook.handle_event(payload)
    assert result["ok"] is False
    assert "no customer email" in result.get("error", "")
    if credits.LEDGER_PATH.exists():
        assert credits.LEDGER_PATH.read_text().strip() == ""
