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


def _deleted(email):
    # The row gdpr.delete_for_email appends to both ledgers.
    for path in (subscriptions.SUB_LEDGER, subscriptions.CUSTOMER_MAP):
        with path.open("a") as f:
            f.write(json.dumps({"ts": "2026-09-26T00:00:00+00:00",
                                "event": "email_deleted", "email": email}) + "\n")


def test_deletion_unlinks_the_customer_for_good_and_a_new_customer_links():
    """A deleted email's customer stops resolving to it, so that customer's
    later events cannot stamp the email back on. The SAME customer mapped to
    the address again stays unlinked: Stripe makes a new customer for every
    checkout (create_checkout_session never sends one), so that mapping can
    only be a late event about the deleted account's own old checkout. A new
    customer id after the delete is a new subscription, and links."""
    end = time.time() + 86400
    subscriptions.record_customer_email("cus_x", "a@b.com")
    subscriptions.record_subscription_event("cus_x", "active", end, "sub_x")
    assert subscriptions.is_active("a@b.com") is True

    _deleted("a@b.com")
    assert subscriptions._email_for_customer("cus_x") is None
    subscriptions.record_subscription_event("cus_x", "active", end, "sub_x")
    assert subscriptions.is_active("a@b.com") is False
    assert subscriptions.stripe_subscription_id_for("a@b.com") == ""

    subscriptions.record_customer_email("cus_x", "a@b.com")
    assert subscriptions._email_for_customer("cus_x") is None
    subscriptions.record_subscription_event("cus_x", "active", end, "sub_x")
    assert subscriptions.is_active("a@b.com") is False
    assert subscriptions.subscriptions_for("a@b.com") == []

    subscriptions.record_customer_email("cus_y", "a@b.com")
    assert subscriptions._email_for_customer("cus_y") == "a@b.com"
    subscriptions.record_subscription_event("cus_y", "active", end, "sub_y")
    assert subscriptions.is_active("a@b.com") is True
    assert subscriptions.stripe_subscription_id_for("a@b.com") == "sub_y"
    assert [r["stripe_sub"] for r in subscriptions.subscriptions_for("a@b.com")] == ["sub_y"]


def test_deleting_one_email_leaves_other_emails_customers_linked():
    end = time.time() + 86400
    subscriptions.record_customer_email("cus_a", "a@b.com")
    subscriptions.record_customer_email("cus_b", "b@b.com")
    _deleted("a@b.com")
    assert subscriptions._email_for_customer("cus_a") is None
    assert subscriptions._email_for_customer("cus_b") == "b@b.com"
    subscriptions.record_subscription_event("cus_b", "active", end, "sub_b")
    assert subscriptions._read_all(subscriptions.SUB_LEDGER)[-1]["email"] == "b@b.com"


def test_subscriptions_for_gives_the_latest_row_of_every_subscription():
    """Delete has to see every subscription an address pays for, each by its
    own newest row, not the newest row of all of them."""
    end = time.time() + 86400
    subscriptions.record_customer_email("cus_1", "a@b.com")
    subscriptions.record_customer_email("cus_2", "a@b.com")
    subscriptions.record_subscription_event("cus_1", "active", end, "sub_1")
    subscriptions.record_subscription_event("cus_2", "active", end, "sub_2")
    subscriptions.record_subscription_event("cus_1", "past_due", end, "sub_1")
    subscriptions.record_subscription_event("cus_1", "canceled", end, "sub_1")
    subscriptions.record_subscription_event("cus_other", "active", end, "sub_other")
    assert [(r["stripe_sub"], r["status"]) for r in subscriptions.subscriptions_for("a@b.com")] == [
        ("sub_1", "canceled"), ("sub_2", "active")]
    # The newest row of all is sub_1's cancellation, but sub_2 is still paid
    # for: status (and so the page, cancel and reactivate) reads sub_2. It
    # read sub_1 until 2026-09-27, which left Cancel pointing at the
    # subscription that had already ended.
    assert subscriptions.status_for("a@b.com")["stripe_sub"] == "sub_2"
