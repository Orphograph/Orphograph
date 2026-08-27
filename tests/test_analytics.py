from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

import analytics


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(analytics, "EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(analytics, "DEMAND_EVENTS_PATH", tmp_path / "demand_events.jsonl")
    monkeypatch.setenv("ORPHO_ANALYTICS_HMAC_SECRET", "test-only-secret")
    monkeypatch.delenv("ORPHO_INTERNAL_API_KEY_HASHES", raising=False)
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


def test_metrics_does_not_invent_mrr_from_subscriber_count(tmp_path, monkeypatch):
    monkeypatch.setattr(analytics, "SUBSCRIPTIONS_PATH", tmp_path / "subscriptions.jsonl")
    monkeypatch.setattr(analytics, "STRIPE_EVENTS_PATH", tmp_path / "stripe_processed_events.jsonl")
    result = analytics.metrics()
    assert result["mrr"] is None
    assert result["arr"] is None
    assert result["ltv"] is None
    assert result["revenue_data_quality"] == "unavailable"


def test_internal_api_key_is_classified_server_side(monkeypatch):
    raw_key = "orpho_office_secret"
    digest = hashlib.sha256(raw_key.encode()).hexdigest()
    monkeypatch.setenv("ORPHO_INTERNAL_API_KEY_HASHES", digest)
    assert analytics.classify_origin(
        api_key=raw_key, authenticated=True, paid=True
    ) == "office_automation"
    assert analytics.classify_origin(
        api_key="orpho_customer", authenticated=True, paid=True
    ) == "external_authenticated"
    assert analytics.classify_origin() == "external_anonymous"


def test_demand_event_schema_cannot_persist_raw_client_or_api_key(monkeypatch):
    raw_key = "orpho_office_secret"
    digest = hashlib.sha256(raw_key.encode()).hexdigest()
    monkeypatch.setenv("ORPHO_INTERNAL_API_KEY_HASHES", digest)
    origin = analytics.classify_origin(
        api_key=raw_key, authenticated=True, paid=True
    )
    assert analytics.record_demand(
        "anchor_succeeded",
        origin_class=origin,
        auth_path="api_key",
        surface="folder",
        outcome="success",
        client_key="192.0.2.0/24",
    ) is True
    raw = analytics.DEMAND_EVENTS_PATH.read_text()
    row = json.loads(raw)
    assert raw_key not in raw
    assert "192.0.2.0/24" not in raw
    assert set(row) == {
        "ts", "event_version", "event", "origin_class", "auth_path",
        "surface", "offer_version", "outcome", "privacy_safe_cohort",
        "data_quality",
    }
    assert row["origin_class"] == "office_automation"
    assert len(row["privacy_safe_cohort"]) == 20


def test_summary_excludes_four_office_events_from_one_external_signal(monkeypatch):
    raw_key = "orpho_office_secret"
    monkeypatch.setenv(
        "ORPHO_INTERNAL_API_KEY_HASHES",
        hashlib.sha256(raw_key.encode()).hexdigest(),
    )
    office = analytics.classify_origin(
        api_key=raw_key, authenticated=True, paid=True
    )
    for _ in range(4):
        assert analytics.record_demand(
            "anchor_succeeded", origin_class=office, auth_path="api_key",
            surface="folder", outcome="success", client_key="office-bucket",
        )
    assert analytics.record_demand(
        "anchor_succeeded", origin_class="external_anonymous", auth_path="free",
        surface="single", outcome="success", client_key="visitor-bucket",
    )
    summary = analytics.demand_summary()
    assert summary["origins"] == {
        "external": 1,
        "office_automation": 4,
        "unknown": 0,
    }
    assert summary["office_excluded_from_external"] is True
    assert summary["data_quality"] == "complete"


def test_missing_or_unreadable_demand_is_unavailable_not_zero(monkeypatch):
    assert analytics.demand_summary()["data_quality"] == "unavailable"
    monkeypatch.setattr(analytics, "DEMAND_EVENTS_PATH", analytics.DEMAND_EVENTS_PATH.parent)
    summary = analytics.demand_summary()
    assert summary["data_quality"] == "unavailable"
    assert "error" in summary


def test_invalid_demand_vocabulary_is_rejected_without_a_ledger():
    assert analytics.record_demand(
        "invented_event", origin_class="human", auth_path="free",
        surface="single", outcome="success",
    ) is False
    assert not analytics.DEMAND_EVENTS_PATH.exists()


def test_missing_privacy_secret_refuses_to_create_attribution_ledger(monkeypatch):
    monkeypatch.delenv("ORPHO_ANALYTICS_HMAC_SECRET")
    assert analytics.record_demand(
        "anchor_succeeded", origin_class="external_anonymous", auth_path="free",
        surface="single", outcome="success", client_key="192.0.2.0/24",
    ) is False
    assert not analytics.DEMAND_EVENTS_PATH.exists()


def test_demand_write_error_is_best_effort(tmp_path, monkeypatch):
    monkeypatch.setattr(analytics, "DEMAND_EVENTS_PATH", tmp_path)
    assert analytics.record_demand(
        "anchor_succeeded", origin_class="external_anonymous", auth_path="free",
        surface="single", outcome="success", client_key="visitor",
    ) is False


def test_summary_marks_malformed_stale_and_incomplete_rows(tmp_path):
    now = datetime.now(timezone.utc)
    rows = [
        "not-json",
        json.dumps({"ts": "no timezone", "event": "anchor_succeeded"}),
        json.dumps({
            "ts": (now - timedelta(days=365)).isoformat(),
            "event": "anchor_succeeded",
            "origin_class": "external_anonymous",
        }),
        json.dumps({
            "ts": now.isoformat(),
            "event": "anchor_succeeded",
            "origin_class": "invented",
            "surface": "invented",
            "auth_path": "invented",
            "data_quality": "cohort_unavailable",
        }),
    ]
    analytics.DEMAND_EVENTS_PATH.write_text("\n".join(rows) + "\n")
    summary = analytics.demand_summary(days_back=30)
    assert summary["data_quality"] == "degraded"
    assert summary["malformed_rows"] == 2
    assert summary["incomplete_cohorts"] == 1
    assert summary["origins"]["unknown"] == 1
    assert summary["total_events"] == 1


def test_offer_version_is_closed_vocabulary(monkeypatch):
    monkeypatch.setenv("ORPHO_OFFER_VERSION", "../../bad value")
    assert analytics.record_demand(
        "checkout_created", origin_class="external_anonymous", auth_path="none",
        surface="stripe", outcome="success", client_key="visitor",
    )
    row = json.loads(analytics.DEMAND_EVENTS_PATH.read_text())
    assert row["offer_version"] == "invalid"


def test_subscription_ledger_reader_handles_jsonl_and_corruption(tmp_path, monkeypatch):
    path = tmp_path / "subscriptions.jsonl"
    monkeypatch.setattr(analytics, "SUBSCRIPTIONS_PATH", path)
    assert analytics._current_subscriptions() == []
    path.write_text('{"status":"active"}\n')
    assert analytics._current_subscriptions() == [{"status": "active"}]
    path.write_text("not-json\n")
    assert analytics._current_subscriptions() == []


def test_stripe_subscription_events_count_active_and_recent_churn(tmp_path, monkeypatch):
    path = tmp_path / "stripe.jsonl"
    monkeypatch.setattr(analytics, "STRIPE_EVENTS_PATH", path)
    now = datetime.now(timezone.utc)

    def event(kind: str, email: str, when: datetime) -> dict:
        field = "created" if kind.endswith("created") else "canceled_at"
        return {
            "type": kind,
            "data": {"object": {
                "metadata": {"email": email},
                field: when.isoformat(),
            }},
        }

    path.write_text("\n".join([
        json.dumps(event("customer.subscription.created", "a@example.com", now - timedelta(days=5))),
        "not-json",
        json.dumps(event("customer.subscription.deleted", "a@example.com", now - timedelta(days=1))),
        json.dumps(event("customer.subscription.created", "b@example.com", now)),
        json.dumps({"type": "ignored"}),
    ]) + "\n")
    result = analytics.metrics(days_back=30)
    assert result["customers"] == {
        "active": 1,
        "churned_this_month": 1,
        "total": 2,
    }
    assert result["churn_rate"] == 0.5
    assert analytics._parse_iso_date("bad") == datetime.min.replace(tzinfo=timezone.utc)
