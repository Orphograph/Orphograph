from __future__ import annotations

import pytest

import auth
import credits
import gdpr
import subscriptions


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credits.jsonl")
    monkeypatch.setattr(subscriptions, "SUB_LEDGER", tmp_path / "subs.jsonl")
    monkeypatch.setattr(subscriptions, "CUSTOMER_MAP", tmp_path / "cust.jsonl")
    monkeypatch.setattr(auth, "TOKEN_LEDGER", tmp_path / "tokens.jsonl")
    monkeypatch.setattr(gdpr, "DELETIONS_LEDGER", tmp_path / "deletions.jsonl")
    yield


def test_export_returns_only_target_email_rows():
    credits.add_credits("pk_a", "alice@b.com", 10, "test")
    credits.add_credits("pk_b", "bob@b.com", 10, "test")
    out = gdpr.export_for_email("alice@b.com")
    assert out["email"] == "alice@b.com"
    rows = out["items"]["credit_ledger"]
    assert all(r["email"] == "alice@b.com" for r in rows)
    assert any(r["claim_code"] == "pk_a" for r in rows)
    assert not any(r.get("claim_code") == "pk_b" for r in rows)


def test_export_empty_for_unknown_email():
    out = gdpr.export_for_email("nobody@example.com")
    for v in out["items"].values():
        assert v == []


def test_export_empty_email_returns_empty():
    out = gdpr.export_for_email("")
    assert out == {"email": "", "items": {}}


def test_delete_appends_tombstone_to_every_ledger():
    credits.add_credits("pk_a", "alice@b.com", 10, "test")
    subscriptions.record_customer_email("cus_x", "alice@b.com")
    auth.issue_link_token("alice@b.com")

    result = gdpr.delete_for_email("alice@b.com")
    assert result["events_appended"] == 4
    assert gdpr.is_email_deleted("alice@b.com") is True

    for ledger in (credits.LEDGER_PATH, subscriptions.CUSTOMER_MAP, auth.TOKEN_LEDGER):
        contents = ledger.read_text()
        assert '"event": "email_deleted"' in contents or '"event":"email_deleted"' in contents


def test_delete_does_not_purge_historic_rows():
    """Append-only by design: we tombstone, we do not rewrite history."""
    credits.add_credits("pk_a", "alice@b.com", 10, "test")
    pre = credits.LEDGER_PATH.read_text()
    gdpr.delete_for_email("alice@b.com")
    post = credits.LEDGER_PATH.read_text()
    assert pre in post, "delete must be append-only, not rewrite"


def test_deletion_audit_log_records_who_and_when():
    gdpr.delete_for_email("audit@b.com")
    rows = (gdpr.DELETIONS_LEDGER).read_text().strip().splitlines()
    assert len(rows) == 1
    import json
    row = json.loads(rows[0])
    assert row["email"] == "audit@b.com"
    assert "credit_ledger" in row["ledgers_touched"]
    assert "ts" in row


def test_is_email_deleted_returns_false_for_untouched():
    assert gdpr.is_email_deleted("never@b.com") is False
