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
