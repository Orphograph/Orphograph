from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

import credits
import stripe_webhook


def _sig(payload: bytes, secret: str, ts: int | None = None) -> str:
    ts = ts if ts is not None else int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


@pytest.fixture(autouse=True)
def _isolate_processed(tmp_path, monkeypatch):
    monkeypatch.setattr(stripe_webhook, "PROCESSED_EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(stripe_webhook, "PI_SESSION_MAP_PATH", tmp_path / "pi_session_map.jsonl")
    yield


def test_signature_verify_accepts_valid():
    payload = b'{"type":"x"}'
    secret = "whsec_test"
    assert stripe_webhook.verify_signature(payload, _sig(payload, secret), secret) is True


def test_signature_verify_rejects_wrong_secret():
    payload = b'{"type":"x"}'
    assert stripe_webhook.verify_signature(payload, _sig(payload, "real"), "fake") is False


def test_signature_verify_rejects_tampered_payload():
    payload = b'{"type":"x"}'
    secret = "whsec_test"
    sig = _sig(payload, secret)
    assert stripe_webhook.verify_signature(b'{"type":"tampered"}', sig, secret) is False


def test_signature_verify_rejects_stale_timestamp():
    payload = b'{"type":"x"}'
    secret = "whsec_test"
    old = int(time.time()) - 10_000
    assert stripe_webhook.verify_signature(payload, _sig(payload, secret, ts=old), secret) is False


def test_signature_verify_rejects_missing_header():
    assert stripe_webhook.verify_signature(b"{}", "", "secret") is False
    assert stripe_webhook.verify_signature(b"{}", "garbage", "secret") is False


def test_handle_event_ignores_unrelated_types(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    payload = json.dumps({"type": "payment_intent.created"}).encode()
    result = stripe_webhook.handle_event(payload)
    assert result["ok"] is True
    assert result.get("ignored") == "payment_intent.created"


def test_handle_event_mints_pack_on_checkout_completed(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    # mailer is inert without RESEND_API_KEY so it won't actually send
    payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_test_abc", "customer_email": "buyer@example.com"}},
    }).encode()
    result = stripe_webhook.handle_event(payload)
    assert result["ok"] is True
    assert result.get("claim_code_minted") is True


def test_handle_event_skips_when_no_email(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_no_email"}},
    }).encode()
    result = stripe_webhook.handle_event(payload)
    assert result["ok"] is False


def test_handle_event_rejects_malformed_json():
    result = stripe_webhook.handle_event(b"not-json-at-all{")
    assert result["ok"] is False


def test_signature_verify_accepts_multi_v1_during_rotation():
    """Stripe sends multiple v1= during signing-secret rotation."""
    payload = b'{"type":"x"}'
    real_secret = "whsec_current"
    ts = int(time.time())
    real_mac = hmac.new(real_secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    rotated_mac = "0" * 64  # signature from a non-matching old/new key
    header = f"t={ts},v1={rotated_mac},v1={real_mac}"
    assert stripe_webhook.verify_signature(payload, header, real_secret) is True


def test_handle_event_is_idempotent_on_replay(tmp_path, monkeypatch):
    """Duplicate Stripe delivery must not mint a second Pack."""
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    payload = json.dumps({
        "id": "evt_replay_123",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_replay", "customer_email": "dup@example.com"}},
    }).encode()
    first = stripe_webhook.handle_event(payload)
    assert first.get("claim_code_minted") is True
    # ledger should have exactly 10 credits for ONE code
    bal_codes_before = sum(int(line.split('"credits_delta":')[1].split(',')[0])
                            for line in (tmp_path / "credit_ledger.jsonl").read_text().splitlines())
    second = stripe_webhook.handle_event(payload)
    assert second == {"ok": True, "duplicate": "evt_replay_123"}
    bal_codes_after = sum(int(line.split('"credits_delta":')[1].split(',')[0])
                           for line in (tmp_path / "credit_ledger.jsonl").read_text().splitlines())
    assert bal_codes_before == bal_codes_after, "duplicate webhook must not change credit ledger"


def test_handle_event_dedupes_ignored_types_too(tmp_path):
    payload = json.dumps({"id": "evt_ignored", "type": "payment_intent.created"}).encode()
    first = stripe_webhook.handle_event(payload)
    second = stripe_webhook.handle_event(payload)
    assert first.get("ignored") == "payment_intent.created"
    assert second == {"ok": True, "duplicate": "evt_ignored"}


def _ledger_total(ledger_path):
    return sum(
        int(line.split('"credits_delta":')[1].split(",")[0])
        for line in ledger_path.read_text().splitlines()
    )


def test_entry_pack_without_metadata_grants_default_credits(tmp_path, monkeypatch):
    """Backward-compat: a checkout with no credit_count metadata mints the
    default PACK_CREDITS (=10) — the existing Writer Pack path is unchanged."""
    ledger = tmp_path / "credit_ledger.jsonl"
    monkeypatch.setattr(credits, "LEDGER_PATH", ledger)
    payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_pack_x", "customer_email": "buyer@example.com"}},
    }).encode()
    result = stripe_webhook.handle_event(payload)
    assert result["ok"] is True
    assert _ledger_total(ledger) == stripe_webhook.PACK_CREDITS


def test_pack50_metadata_grants_fifty_credits(tmp_path, monkeypatch):
    """pack50 carries credit_count=50 in session metadata → mints 50, not 10."""
    ledger = tmp_path / "credit_ledger.jsonl"
    monkeypatch.setattr(credits, "LEDGER_PATH", ledger)
    payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_pack50_x",
            "customer_email": "buyer@example.com",
            "metadata": {"credit_count": "50", "plan": "pack50"},
        }},
    }).encode()
    result = stripe_webhook.handle_event(payload)
    assert result["ok"] is True
    assert _ledger_total(ledger) == 50


def test_bad_credit_count_metadata_falls_back_to_default(tmp_path, monkeypatch):
    """Garbage credit_count must not zero out or crash the grant — fall back."""
    ledger = tmp_path / "credit_ledger.jsonl"
    monkeypatch.setattr(credits, "LEDGER_PATH", ledger)
    payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_pack_bad",
            "customer_email": "buyer@example.com",
            "metadata": {"credit_count": "not-a-number"},
        }},
    }).encode()
    result = stripe_webhook.handle_event(payload)
    assert result["ok"] is True
    assert _ledger_total(ledger) == stripe_webhook.PACK_CREDITS


# --------------------------------------- email credit-count (pack50 mislabel)

def test_pack50_claim_email_reports_actual_credit_count(tmp_path, monkeypatch):
    """The ledger grants 50 for pack50, but the confirmation email previously
    hardcoded PACK_CREDITS(=10). The email must state the ACTUAL granted amount."""
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    import mailer
    captured = {}

    def fake_claim(to, claim_code, credit_count):
        captured["credit_count"] = credit_count
        return True

    monkeypatch.setattr(mailer, "send_pack_claim_email", fake_claim)
    payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_pack50_email",
            "customer_email": "buyer@example.com",
            "metadata": {"credit_count": "50", "plan": "pack50"},
        }},
    }).encode()
    result = stripe_webhook.handle_event(payload)
    assert result["ok"] is True
    assert captured.get("credit_count") == 50, "pack50 email must say 50, not 10"


def test_pack50_gift_email_reports_actual_credit_count(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    import mailer
    captured = {}

    def fake_gift(to, from_email, claim_code, credit_count, message):
        captured["credit_count"] = credit_count
        return True

    monkeypatch.setattr(mailer, "send_pack_gift_email", fake_gift)
    payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_pack50_gift",
            "customer_email": "buyer@example.com",
            "metadata": {"credit_count": "50", "gift_to_email": "friend@example.com"},
        }},
    }).encode()
    result = stripe_webhook.handle_event(payload)
    assert result.get("gift") is True
    assert captured.get("credit_count") == 50


# --------------------------------------- refund clawback via payment_intent map

def test_refund_resolves_session_via_payment_intent_map(tmp_path, monkeypatch):
    """A real charge.refunded carries only the bare payment_intent id (charge
    metadata is inherited from the PI, not the Checkout Session). The pi->session
    map persisted at mint time must let the handler find and revoke the credits."""
    ledger = tmp_path / "credit_ledger.jsonl"
    monkeypatch.setattr(credits, "LEDGER_PATH", ledger)
    completed = json.dumps({
        "id": "evt_completed_refundmap",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_refundmap",
            "customer_email": "refundmap@example.com",
            "payment_intent": "pi_refundmap_123",   # bare id, as Stripe sends
        }},
    }).encode()
    minted = stripe_webhook.handle_event(completed)
    assert minted.get("claim_code_minted") is True

    refund = json.dumps({
        "id": "evt_refund_map",
        "type": "charge.refunded",
        "data": {"object": {"payment_intent": "pi_refundmap_123", "metadata": {}}},
    }).encode()
    result = stripe_webhook.handle_event(refund)
    assert result["ok"] is True
    assert result.get("session_id") == "cs_refundmap"
    assert result.get("no_session_id") is None
    assert len(result.get("revoked") or []) >= 1
    neg = [json.loads(l) for l in ledger.read_text().splitlines()
           if l.strip() and json.loads(l)["credits_delta"] < 0]
    assert any(r["source"].startswith("stripe-refund:cs_refundmap") for r in neg)


def test_refund_with_unknown_payment_intent_cannot_revoke(tmp_path, monkeypatch):
    """Boundary: a refund whose payment_intent was never mapped still degrades
    safely to no_session_id (no crash, no spurious revoke)."""
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    refund = json.dumps({
        "id": "evt_refund_unknown_pi",
        "type": "charge.refunded",
        "data": {"object": {"payment_intent": "pi_never_seen", "metadata": {}}},
    }).encode()
    result = stripe_webhook.handle_event(refund)
    assert result["ok"] is True
    assert result.get("no_session_id") is True


# --------------------------------------- no-email checkout stays recoverable

def test_no_email_checkout_is_not_marked_processed(tmp_path, monkeypatch):
    """A paid session with no email must NOT be deduped — marking it processed
    would turn every Stripe retry into a permanent paid-but-nothing dead end."""
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    payload = json.dumps({
        "id": "evt_no_email_recover",
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_no_email_recover"}},
    }).encode()
    first = stripe_webhook.handle_event(payload)
    assert first["ok"] is False
    assert first.get("recoverable") is True
    second = stripe_webhook.handle_event(payload)
    assert second.get("recoverable") is True
    assert "duplicate" not in second, "no-email event must remain reprocessable"
