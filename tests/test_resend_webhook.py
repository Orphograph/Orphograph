#!/usr/bin/env python3
"""Tests for server/resend_webhook.py — Svix signature verification, bounce/
complaint suppression recording, idempotency, and the mailer skip-on-suppressed
integration. Offline; no network.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

import resend_webhook as rw

SECRET = "whsec_" + base64.b64encode(b"unit-test-signing-secret").decode("ascii")


def _svix_headers(body: bytes, secret: str = SECRET, svix_id: str = "msg_1",
                  ts: int | None = None) -> dict:
    ts = int(time.time()) if ts is None else ts
    key = secret[len("whsec_"):] if secret.startswith("whsec_") else secret
    signed = f"{svix_id}.{ts}.".encode("utf-8") + body
    sig = base64.b64encode(
        hmac.new(base64.b64decode(key), signed, hashlib.sha256).digest()).decode("ascii")
    return {"svix-id": svix_id, "svix-timestamp": str(ts), "svix-signature": "v1," + sig}


def _event(etype: str, email: str, email_id: str = "em_1") -> bytes:
    return json.dumps({"type": etype, "data": {"email_id": email_id, "to": [email]}}).encode()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(rw, "SUPPRESSION_LIST_PATH", tmp_path / "suppressed.jsonl")
    monkeypatch.setattr(rw, "PROCESSED_EVENTS_PATH", tmp_path / "processed.jsonl")
    yield


# ── signature verification ───────────────────────────────────────────────
class TestVerify:
    def test_good_signature(self):
        body = _event("email.bounced", "a@b.co")
        assert rw.verify_signature(body, _svix_headers(body), SECRET) is True

    def test_tampered_signature_rejected(self):
        body = _event("email.bounced", "a@b.co")
        h = _svix_headers(body)
        h["svix-signature"] = "v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        assert rw.verify_signature(body, h, SECRET) is False

    def test_stale_timestamp_rejected(self):
        body = _event("email.bounced", "a@b.co")
        h = _svix_headers(body, ts=int(time.time()) - 10_000)
        assert rw.verify_signature(body, h, SECRET) is False

    def test_missing_headers_rejected(self):
        body = _event("email.bounced", "a@b.co")
        assert rw.verify_signature(body, {}, SECRET) is False

    def test_wrong_secret_rejected(self):
        body = _event("email.bounced", "a@b.co")
        other = "whsec_" + base64.b64encode(b"a-different-secret").decode("ascii")
        assert rw.verify_signature(body, _svix_headers(body, secret=other), SECRET) is False

    def test_case_insensitive_headers(self):
        body = _event("email.bounced", "a@b.co")
        h = _svix_headers(body)
        upper = {k.upper(): v for k, v in h.items()}
        assert rw.verify_signature(body, upper, SECRET) is True


# ── event handling + suppression ──────────────────────────────────────────
class TestHandle:
    def test_bounce_records_suppression(self):
        res = rw.handle_event(_event("email.bounced", "bounce@x.co"))
        assert res["ok"] and res["suppressed"] == ["bounce@x.co"]
        assert rw.is_suppressed("bounce@x.co") is True
        assert rw.is_suppressed("BOUNCE@X.CO") is True  # case-insensitive

    def test_complaint_records_suppression(self):
        rw.handle_event(_event("email.complained", "spam@x.co", email_id="em_c"))
        assert rw.is_suppressed("spam@x.co") is True

    def test_other_event_ignored_not_suppressed(self):
        res = rw.handle_event(_event("email.delivered", "ok@x.co", email_id="em_d"))
        assert res.get("ignored") is True
        assert rw.is_suppressed("ok@x.co") is False

    def test_dedup_does_not_double_record(self):
        body = _event("email.bounced", "dup@x.co", email_id="em_dup")
        first = rw.handle_event(body)
        assert first.get("suppressed") == ["dup@x.co"]
        second = rw.handle_event(body)
        assert "duplicate" in second
        rows = [l for l in rw.SUPPRESSION_LIST_PATH.read_text().splitlines() if l.strip()]
        assert len(rows) == 1, "duplicate event re-recorded the suppression"

    def test_not_suppressed_unknown_address(self):
        assert rw.is_suppressed("never-seen@x.co") is False


# ── mailer integration ────────────────────────────────────────────────────
def test_mailer_skips_suppressed_address_before_network(monkeypatch):
    import mailer
    # record a suppression in the same isolated ledger the mailer will consult
    rw.record_suppression("blocked@x.co", "email.bounced")
    # make it look configured so a NON-suppressed send would proceed to network…
    monkeypatch.setattr(mailer, "RESEND_API_KEY", "re_live_fake")
    # …and blow up if any network call is attempted, proving the gate short-circuits
    def _boom(*a, **k):
        raise AssertionError("network attempted for a suppressed address")
    monkeypatch.setattr(mailer.urllib.request, "urlopen", _boom)
    out = mailer._send("blocked@x.co", "Subj", "text", "<p>html</p>")
    assert out is False


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
