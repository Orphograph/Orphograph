#!/usr/bin/env python3
"""Tests for upgrade_worker._send_pin_email_if_needed — pin-notification
idempotency + crash-safety. (Flagged by the TRL re-grade: the function had no
dedicated test. It must fire EXACTLY once per receipt and must never let a
mailer failure break the upgrade worker / lose the credit-grant.)

mailer + webhooks are stubbed via sys.modules so this runs offline.
"""
from __future__ import annotations

import sys
import types

import pytest

import upgrade_worker as uw

PINNED = "2026-01-01T00:00:00+00:00"


def _stub_modules(monkeypatch, send_impl):
    """Inject fake mailer (send_pin_email=send_impl) + no-op webhooks."""
    calls = []

    def send_pin_email(email, record):
        calls.append((email, record.get("receipt_id")))
        return send_impl(email, record)

    fake_mailer = types.ModuleType("mailer")
    fake_mailer.send_pin_email = send_pin_email
    monkeypatch.setitem(sys.modules, "mailer", fake_mailer)

    fake_webhooks = types.ModuleType("webhooks")
    fake_webhooks.dispatch = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "webhooks", fake_webhooks)
    return calls


def test_no_notify_email_no_send(monkeypatch):
    calls = _stub_modules(monkeypatch, lambda e, r: True)
    rec = {"btc_pinned_at": PINNED, "receipt_id": "R1"}
    uw._send_pin_email_if_needed(rec)
    assert calls == []
    assert "pin_email_sent_at" not in rec


def test_no_pin_yet_no_send(monkeypatch):
    calls = _stub_modules(monkeypatch, lambda e, r: True)
    rec = {"notify_email": "a@b.co", "receipt_id": "R2"}  # not pinned yet
    uw._send_pin_email_if_needed(rec)
    assert calls == []
    assert "pin_email_sent_at" not in rec


def test_already_sent_no_resend(monkeypatch):
    calls = _stub_modules(monkeypatch, lambda e, r: True)
    rec = {"notify_email": "a@b.co", "btc_pinned_at": PINNED,
           "pin_email_sent_at": "2026-01-01T00:00:01+00:00", "receipt_id": "R3"}
    uw._send_pin_email_if_needed(rec)
    assert calls == []


def test_happy_sends_once_and_marks_idempotent(monkeypatch):
    calls = _stub_modules(monkeypatch, lambda e, r: True)
    rec = {"notify_email": "a@b.co", "btc_pinned_at": PINNED, "receipt_id": "R4"}
    uw._send_pin_email_if_needed(rec)
    assert len(calls) == 1
    assert rec.get("pin_email_sent_at"), "pin_email_sent_at not stamped after send"
    # second call must NOT resend (idempotency via pin_email_sent_at)
    uw._send_pin_email_if_needed(rec)
    assert len(calls) == 1, "re-sent the pin email"


def test_mailer_exception_swallowed_and_retryable(monkeypatch):
    def boom(e, r):
        raise RuntimeError("smtp down")
    calls = _stub_modules(monkeypatch, boom)
    rec = {"notify_email": "a@b.co", "btc_pinned_at": PINNED, "receipt_id": "R5"}
    uw._send_pin_email_if_needed(rec)  # must NOT raise
    assert len(calls) == 1
    # not marked sent -> next worker run retries (credit integrity > notification)
    assert "pin_email_sent_at" not in rec


def test_mailer_returns_false_not_marked(monkeypatch):
    calls = _stub_modules(monkeypatch, lambda e, r: False)
    rec = {"notify_email": "a@b.co", "btc_pinned_at": PINNED, "receipt_id": "R6"}
    uw._send_pin_email_if_needed(rec)
    assert "pin_email_sent_at" not in rec, "marked sent despite mailer returning False"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
