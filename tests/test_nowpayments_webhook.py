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


# ------------------------------------------- per-order mint dedup (double-grant)

def _ledger_rows():
    if not credits.LEDGER_PATH.exists():
        return []
    return [json.loads(l) for l in credits.LEDGER_PATH.read_text().splitlines() if l.strip()]


def test_confirmed_then_finished_same_order_credits_only_once():
    """NOWPayments fires an IPN per lifecycle transition, so one paid order
    delivers BOTH a `confirmed` and a `finished` IPN with DIFFERENT per-status
    event_ids. The mint must be deduped per ORDER, not per status, or every
    crypto buyer is credited (and emailed a claim code) twice."""
    order_id = "np_lifecycle_001"
    base = {
        "order_id": order_id,
        "invoice_id": "inv_lifecycle_001",
        "customer_email": "lifecycle@example.com",
        "plan": "writer_pack",
    }
    confirmed_payload, _ = _sign({**base, "payment_status": "confirmed"})
    finished_payload, _ = _sign({**base, "payment_status": "finished"})

    first = nowpayments_webhook.handle_event(confirmed_payload)
    assert first.get("claim_code_minted") is True
    rows_after_first = _ledger_rows()
    assert len(rows_after_first) == 1
    assert rows_after_first[0]["credits_delta"] == 10

    second = nowpayments_webhook.handle_event(finished_payload)
    assert second.get("duplicate_mint") == order_id
    assert second.get("claim_code_minted") is None
    # Ledger unchanged: exactly one positive grant for the order.
    assert _ledger_rows() == rows_after_first, "confirmed+finished must mint once"


# ------------------------------------------- plan round-trip (pack50 undergrant)

def test_plan_resolved_from_order_id_token_when_no_plan_key():
    """Real IPNs do not echo a `plan` key. The pack must be recovered from the
    plan token we embed in order_id (np_<plan>_<rand>), else a $29 Pack-of-50
    silently grants 10 credits."""
    body = {
        "order_id": "np_pack_50_Ab12Cd34",   # plan token embedded by the caller
        "payment_status": "finished",
        "invoice_id": "inv_rt_id",
        "customer_email": "rt-id@example.com",
        # NO plan key, NO order_description
    }
    payload, _sig = _sign(body)
    result = nowpayments_webhook.handle_event(payload)
    assert result.get("claim_code_minted") is True
    assert result["plan"] == "pack_50"
    assert result["credits"] == 50
    assert result.get("plan_resolved") is True


def test_plan_resolved_from_order_description_when_no_plan_key():
    body = {
        "order_id": "np_opaque_xyz",          # no plan token here
        "payment_status": "finished",
        "invoice_id": "inv_rt_desc",
        "customer_email": "rt-desc@example.com",
        "order_description": "Orphograph pack_50 credit pack",
    }
    payload, _sig = _sign(body)
    result = nowpayments_webhook.handle_event(payload)
    assert result["plan"] == "pack_50"
    assert result["credits"] == 50


def test_unresolved_plan_defaults_to_smaller_pack_and_flags():
    """When the pack cannot be determined from any echoed field, default to the
    SMALLER pack (never over-grant) and surface plan_resolved=False so the
    founder can reconcile rather than silently over/under-shipping."""
    body = {
        "order_id": "np_opaque_only",         # no plan token
        "payment_status": "finished",
        "invoice_id": "inv_unresolved",
        "customer_email": "unresolved@example.com",
        # no plan key, no order_description
    }
    payload, _sig = _sign(body)
    result = nowpayments_webhook.handle_event(payload)
    assert result.get("claim_code_minted") is True
    assert result["plan"] == "writer_pack"
    assert result["credits"] == 10
    assert result.get("plan_resolved") is False


# ------------------------------------------- underpayment guard

def test_underpayment_warns_but_still_credits_by_default():
    """Default posture is WARN-ONLY: we never reject a payment NOWPayments has
    already accepted as finished (paid-but-no-credit is the worse error)."""
    body = {
        "order_id": "np_underpay_warn",
        "payment_status": "finished",
        "invoice_id": "inv_underpay_warn",
        "customer_email": "underpay-warn@example.com",
        "plan": "writer_pack",
        "pay_amount": "0.0010",
        "actually_paid": "0.0005",            # 50% short
    }
    payload, _sig = _sign(body)
    result = nowpayments_webhook.handle_event(payload)
    assert result.get("claim_code_minted") is True
    assert result["credits"] == 10


def test_underpayment_blocks_when_enforcement_enabled(monkeypatch):
    monkeypatch.setenv("ORPHO_NOWPAY_ENFORCE_AMOUNT", "1")
    body = {
        "order_id": "np_underpay_block",
        "payment_status": "finished",
        "invoice_id": "inv_underpay_block",
        "customer_email": "underpay-block@example.com",
        "plan": "writer_pack",
        "pay_amount": "0.0010",
        "actually_paid": "0.0005",
    }
    payload, _sig = _sign(body)
    result = nowpayments_webhook.handle_event(payload)
    assert result.get("ignored") is True
    assert result.get("reason") == "underpaid"
    assert _ledger_rows() == [], "enforced underpayment must not mint"


def test_exact_payment_credits_under_enforcement(monkeypatch):
    monkeypatch.setenv("ORPHO_NOWPAY_ENFORCE_AMOUNT", "1")
    body = {
        "order_id": "np_exact_pay",
        "payment_status": "finished",
        "invoice_id": "inv_exact_pay",
        "customer_email": "exact@example.com",
        "plan": "writer_pack",
        "pay_amount": "0.0010",
        "actually_paid": "0.0010",            # paid in full
    }
    payload, _sig = _sign(body)
    result = nowpayments_webhook.handle_event(payload)
    assert result.get("claim_code_minted") is True
    assert result["credits"] == 10


# ------------------------------------------- golden full-payload end-to-end

# A realistic NOWPayments IPN body with the full field set NOWPayments actually
# sends (existing tests use minimal stubs — this guards against format drift if
# NOWPayments adds/reorders fields, since the signature is over canonical JSON).
GOLDEN_IPN_FIELDS = {
    "payment_id": 5077125051,
    "invoice_id": 4944856743,
    "payment_status": "finished",
    "pay_address": "bc1qexampleexampleexampleexampleexampleex",
    "price_amount": 19,
    "price_currency": "usd",
    "pay_amount": 0.000196,
    "actually_paid": 0.000196,
    "pay_currency": "btc",
    "order_id": "np_writer_pack_GoLdEn01",
    "order_description": "Orphograph Writer Pack — 10 anchors",
    "purchase_id": "6097863291",
    "outcome_amount": 0.000194,
    "outcome_currency": "btc",
    "customer_email": "golden@example.com",
}


def test_golden_full_ipn_signature_and_exactly_once_mint():
    """End-to-end with a realistic full IPN payload: the signature verifies over
    the canonical body, the `confirmed` IPN mints exactly one Writer Pack (10),
    and the `finished` IPN for the same order does NOT double-mint."""
    confirmed = {**GOLDEN_IPN_FIELDS, "payment_status": "confirmed"}
    finished = {**GOLDEN_IPN_FIELDS, "payment_status": "finished"}

    c_payload, c_sig = _sign(confirmed)
    f_payload, f_sig = _sign(finished)
    # signature must verify over the exact canonical bytes, and reject a mismatch
    assert nowpayments_webhook.verify_signature(c_payload, c_sig, SECRET) is True
    assert nowpayments_webhook.verify_signature(f_payload, f_sig, SECRET) is True
    assert nowpayments_webhook.verify_signature(c_payload, f_sig, SECRET) is False

    first = nowpayments_webhook.handle_event(c_payload)
    assert first.get("claim_code_minted") is True
    assert first["credits"] == 10
    rows = _ledger_rows()
    assert len(rows) == 1
    assert rows[0]["credits_delta"] == 10
    # order_id (not just invoice_id) is recorded in the source for refund linkage
    assert GOLDEN_IPN_FIELDS["order_id"] in rows[0]["source"]

    second = nowpayments_webhook.handle_event(f_payload)
    assert second.get("duplicate_mint") == GOLDEN_IPN_FIELDS["order_id"]
    assert _ledger_rows() == rows, "full-payload confirmed+finished must mint once"


def test_golden_ipn_rejects_tampered_signature():
    """A body altered after signing must fail signature verification — a
    paid-amount tamper without the secret can't slip through."""
    payload, sig = _sign(GOLDEN_IPN_FIELDS)
    tampered = {**GOLDEN_IPN_FIELDS, "actually_paid": 9.999}
    t_payload, _ = _sign(tampered, secret="attacker_does_not_know_the_secret")
    # the original signature does not validate the tampered body
    assert nowpayments_webhook.verify_signature(t_payload, sig, SECRET) is False


# ------------------------------------------- exactly-once mint (TOCTOU lock)

def test_confirmed_then_finished_mints_once():
    """NOWPayments fires BOTH `confirmed` and `finished` for one paid order
    (different event_ids). The per-order mint marker must mint exactly once."""
    base = {
        "order_id": "np_seq_001",
        "invoice_id": "inv_seq_001",
        "customer_email": "seq@example.com",
        "plan": "writer_pack",
    }
    p1, _ = _sign({**base, "payment_status": "confirmed"})
    p2, _ = _sign({**base, "payment_status": "finished"})

    r1 = nowpayments_webhook.handle_event(p1)
    assert r1.get("claim_code_minted") is True
    r2 = nowpayments_webhook.handle_event(p2)
    assert r2.get("claim_code_minted") is not True
    assert r2.get("duplicate_mint") == "np_seq_001", r2

    mints = [
        json.loads(l) for l in credits.LEDGER_PATH.read_text().splitlines() if l.strip()
    ]
    mints = [r for r in mints if r.get("credits_delta", 0) > 0 and "np_seq_001" in r.get("source", "")]
    assert len(mints) == 1, f"exactly one mint expected, got {len(mints)}"


def test_crash_lost_marker_does_not_double_mint():
    """If the processed-events marker is lost (process crashed after the credit
    row fsync'd but before the marker was written), a redelivered IPN must still
    NOT double-mint — the credit-ledger cross-check (find_claim_code_by_source)
    catches it. This is the crash-safety the file lock alone cannot provide."""
    body = {
        "order_id": "np_crash_001",
        "payment_status": "finished",
        "invoice_id": "inv_crash_001",
        "customer_email": "crash@example.com",
        "plan": "writer_pack",
    }
    payload, _sig = _sign(body)

    first = nowpayments_webhook.handle_event(payload)
    assert first.get("claim_code_minted") is True
    rows_before = credits.LEDGER_PATH.read_text().splitlines()

    # Simulate a crash that wiped the processed-events log (markers gone) while
    # the fsync'd credit ledger survived.
    nowpayments_webhook.PROCESSED_EVENTS_PATH.unlink()

    second = nowpayments_webhook.handle_event(payload)
    assert second.get("duplicate_mint") == "np_crash_001", second
    assert second.get("claim_code_minted") is not True
    rows_after = credits.LEDGER_PATH.read_text().splitlines()
    assert rows_before == rows_after, "crash-lost marker must NOT cause a re-mint"


def test_dedupe_lock_path_tracks_events_path():
    """The cross-process sentinel lock must sit beside the (possibly overridden)
    processed-events ledger, resolved at call time — not a stale import-time path."""
    lp = nowpayments_webhook._dedupe_lock_path()
    expected = nowpayments_webhook.PROCESSED_EVENTS_PATH.with_suffix(
        nowpayments_webhook.PROCESSED_EVENTS_PATH.suffix + ".lock")
    assert lp == expected
    assert str(lp).endswith(".jsonl.lock")


def test_claim_email_sent_once_outside_lock(monkeypatch):
    """The claim-code email (now sent AFTER the lock releases) fires exactly once
    on a fresh mint and never on a duplicate IPN — the refactor must not drop or
    duplicate delivery."""
    calls = []
    monkeypatch.setattr(
        nowpayments_webhook.mailer, "send_pack_claim_email",
        lambda to, code, n: (calls.append((to, code, n)), True)[1],
    )
    body = {
        "order_id": "np_email_001",
        "payment_status": "finished",
        "invoice_id": "inv_email_001",
        "customer_email": "mail@example.com",
        "plan": "writer_pack",
    }
    payload, _sig = _sign(body)
    r1 = nowpayments_webhook.handle_event(payload)
    assert r1.get("claim_code_minted") is True
    assert len(calls) == 1, f"exactly one email on fresh mint, got {len(calls)}"
    assert calls[0][0] == "mail@example.com" and calls[0][2] == 10
    nowpayments_webhook.handle_event(payload)  # duplicate
    assert len(calls) == 1, "duplicate IPN must not re-send the claim-code email"
