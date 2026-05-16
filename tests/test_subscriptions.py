from __future__ import annotations

import json
import time

import pytest

import stripe_webhook
import subscriptions


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(subscriptions, "SUB_LEDGER", tmp_path / "subs.jsonl")
    monkeypatch.setattr(subscriptions, "CUSTOMER_MAP", tmp_path / "cust.jsonl")
    monkeypatch.setattr(stripe_webhook, "PROCESSED_EVENTS_PATH", tmp_path / "events.jsonl")
    yield


def _event(event_type, obj, event_id):
    return json.dumps({"id": event_id, "type": event_type, "data": {"object": obj}}).encode()


def test_no_subscription_is_inactive():
    assert subscriptions.is_active("a@b.com") is False


def test_subscription_created_then_active():
    end = time.time() + 86400
    payload = _event("customer.subscription.created", {
        "id": "sub_x",
        "customer": "cus_x",
        "status": "active",
        "current_period_end": end,
    }, "evt_sub_1")
    # also seed the customer_id → email mapping
    subscriptions.record_customer_email("cus_x", "a@b.com")
    result = stripe_webhook.handle_event(payload)
    assert result["subscription_event"] == "customer.subscription.created"
    assert subscriptions.is_active("a@b.com") is True


def test_subscription_canceled_becomes_inactive():
    subscriptions.record_customer_email("cus_x", "a@b.com")
    stripe_webhook.handle_event(_event(
        "customer.subscription.created",
        {"id": "sub_x", "customer": "cus_x", "status": "active", "current_period_end": time.time() + 86400},
        "evt_create",
    ))
    assert subscriptions.is_active("a@b.com") is True
    stripe_webhook.handle_event(_event(
        "customer.subscription.deleted",
        {"id": "sub_x", "customer": "cus_x"},
        "evt_delete",
    ))
    assert subscriptions.is_active("a@b.com") is False


def test_expired_period_end_means_inactive():
    subscriptions.record_customer_email("cus_x", "a@b.com")
    stripe_webhook.handle_event(_event(
        "customer.subscription.updated",
        {"id": "sub_x", "customer": "cus_x", "status": "active", "current_period_end": time.time() - 10},
        "evt_expired",
    ))
    assert subscriptions.is_active("a@b.com") is False


def test_past_due_means_inactive():
    subscriptions.record_customer_email("cus_x", "a@b.com")
    stripe_webhook.handle_event(_event(
        "customer.subscription.updated",
        {"id": "sub_x", "customer": "cus_x", "status": "past_due", "current_period_end": time.time() + 86400},
        "evt_pd",
    ))
    assert subscriptions.is_active("a@b.com") is False


def test_subscription_checkout_does_not_mint_pack(tmp_path, monkeypatch):
    """A subscription-mode checkout completion must NOT mint Pack credits."""
    import credits
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    result = stripe_webhook.handle_event(_event(
        "checkout.session.completed",
        {"id": "cs_sub", "customer": "cus_y", "customer_email": "sub@b.com", "mode": "subscription"},
        "evt_sub_checkout",
    ))
    assert result.get("subscription_checkout") is True
    assert result.get("claim_code_minted") is None
    # Email must NOT round-trip through the response body.
    assert "customer_email" not in result
    assert "sub@b.com" not in str(result)
    # No credits were minted
    assert not (tmp_path / "credit_ledger.jsonl").exists() or \
        (tmp_path / "credit_ledger.jsonl").read_text().strip() == ""


def test_stripe_webhook_logs_mask_email(tmp_path, monkeypatch, capsys):
    """Webhook stderr must NEVER contain the plaintext customer email."""
    import credits
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    stripe_webhook.handle_event(_event(
        "checkout.session.completed",
        {"id": "cs_pack", "customer": "cus_pack", "customer_email": "leaktest@example.com"},
        "evt_log_mask",
    ))
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "leaktest@example.com" not in combined, (
        "plaintext email must not appear in webhook logs"
    )
    # The masked form should appear instead.
    assert "l***@example.com" in combined


def test_pack_checkout_still_mints_credits(tmp_path, monkeypatch):
    """A non-subscription checkout (Pack purchase) still mints credits."""
    import credits
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    result = stripe_webhook.handle_event(_event(
        "checkout.session.completed",
        {"id": "cs_pack", "customer": "cus_pack", "customer_email": "pack@b.com"},
        "evt_pack_checkout",
    ))
    assert result.get("claim_code_minted") is True


def test_isolation_between_emails():
    subscriptions.record_customer_email("cus_a", "a@b.com")
    subscriptions.record_customer_email("cus_b", "b@b.com")
    stripe_webhook.handle_event(_event(
        "customer.subscription.created",
        {"id": "sub_a", "customer": "cus_a", "status": "active", "current_period_end": time.time() + 86400},
        "evt_a",
    ))
    assert subscriptions.is_active("a@b.com") is True
    assert subscriptions.is_active("b@b.com") is False
