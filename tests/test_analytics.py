from __future__ import annotations

import json

import pytest

import analytics


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(analytics, "EVENTS_PATH", tmp_path / "events.jsonl")
    yield


def test_record_accepts_allowed_event():
    assert analytics.record("page_view", "landing", "192.0.2.0/24") is True
    rows = analytics.EVENTS_PATH.read_text().strip().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["event"] == "page_view"
    assert row["page"] == "landing"
    assert row["ip_prefix"] == "192.0.2.0/24"


def test_record_rejects_unknown_event():
    assert analytics.record("steal_data", "landing", "x") is False
    assert not analytics.EVENTS_PATH.exists() or analytics.EVENTS_PATH.read_text() == ""


def test_record_coerces_unknown_page():
    analytics.record("page_view", "../../etc/passwd", "x")
    rows = analytics.EVENTS_PATH.read_text().strip().splitlines()
    assert json.loads(rows[0])["page"] == "other"


def test_record_does_not_capture_email_or_path():
    """Make sure the schema can't accidentally smuggle PII / referer paths."""
    analytics.record("page_view", "landing", "192.0.2.0/24", referer_host="news.ycombinator.com")
    row = json.loads(analytics.EVENTS_PATH.read_text().strip())
    assert row["ref_host"] == "news.ycombinator.com"
    # No email, no full IP, no full URL fields permitted.
    assert set(row.keys()) == {"ts", "event", "page", "ip_prefix", "ref_host"}


def test_ip_prefix_field_is_truncated_input():
    """Caller is responsible for truncation; the module accepts the prefix as-is
    but bounds the length to 64 chars."""
    long = "a" * 1000
    analytics.record("page_view", "landing", long)
    row = json.loads(analytics.EVENTS_PATH.read_text().strip())
    assert len(row["ip_prefix"]) == 64
