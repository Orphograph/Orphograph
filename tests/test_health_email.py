"""test_health_email.py — the resend_configured flag on /api/health.

Surfaces whether transactional email (claim codes, receipts, sign-in links)
can be delivered, as a BOOLEAN ONLY — never the API key value — so the founder
and an external monitor can tell from the public, ungated health endpoint
whether a paid buyer's claim code will actually email.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import health  # noqa: E402
import mailer  # noqa: E402


def test_email_snapshot_true_when_key_set(monkeypatch):
    monkeypatch.setattr(mailer, "RESEND_API_KEY", "re_live_examplekey")
    assert health._email_snapshot() == {"resend_configured": True}


def test_email_snapshot_false_when_key_unset(monkeypatch):
    monkeypatch.setattr(mailer, "RESEND_API_KEY", "")
    assert health._email_snapshot() == {"resend_configured": False}


def test_snapshot_includes_email_key():
    # The public snapshot must carry the new section as a boolean.
    snap = health.snapshot()
    assert "email" in snap, "/api/health missing the 'email' section"
    assert "resend_configured" in snap["email"]
    assert isinstance(snap["email"]["resend_configured"], bool)


def test_secret_value_never_leaks(monkeypatch):
    # The flag must reflect that the key is set WITHOUT exposing its value —
    # /api/health is public and ungated.
    secret = "re_live_SUPER_SECRET_VALUE_do_not_leak_123"
    monkeypatch.setattr(mailer, "RESEND_API_KEY", secret)
    snap = health._email_snapshot()
    assert snap["resend_configured"] is True
    assert secret not in json.dumps(snap), "RESEND_API_KEY value leaked into the snapshot"
