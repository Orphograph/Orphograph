"""Founder decisions 2026-09-27: subscription lookup ignores the case of the
address (3A), and a buyer who already subscribes is not sold a second
subscription (2A).

3A. Stripe keeps the case the buyer typed at checkout and sign-in keeps the
case typed there, so "Bob@Example.com" paid and "bob@example.com" signed in
as someone with no subscription. Every comparison now goes through
email_fold.fold_email on both sides (ASCII A-Z only: a Kelvin-sign address
still never merges with a plain "k" one). The GDPR export and deletion
matched the same way, so a person's export left out, and their deletion
missed, rows Stripe had stored in another case.

2A. Checkout never asked. A signed-in buyer whose account already has an
active subscription is now refused before Stripe is called. A signed-out
buyer cannot be checked without telling anyone who asks whether an address
subscribes, and hosted Payment Links never reach this server before payment,
so a second subscription arriving that way is caught at the webhook: logged
loudly and marked, never refunded automatically (a founder call).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

import _srv
import gdpr
import stripe_webhook
import subscriptions

FUTURE = time.time() + 30 * 86400


@pytest.fixture()
def ledgers(tmp_path, monkeypatch):
    monkeypatch.setattr(subscriptions, "SUB_LEDGER", tmp_path / "subscriptions.jsonl")
    monkeypatch.setattr(subscriptions, "CUSTOMER_MAP", tmp_path / "stripe_customer_emails.jsonl")
    return tmp_path


# --- 3A. case does not decide who subscribes ---------------------------------------

@pytest.mark.parametrize("signed_in_as", ["bob@example.com", "BOB@EXAMPLE.COM", " Bob@Example.com "])
def test_a_subscription_paid_in_one_case_is_found_in_another(ledgers, signed_in_as):
    subscriptions.record_customer_email("cus_bob", "Bob@Example.com")
    subscriptions.record_subscription_event("cus_bob", "active", FUTURE, sub_id="sub_bob")
    assert subscriptions.is_active(signed_in_as), signed_in_as
    assert subscriptions.stripe_subscription_id_for(signed_in_as) == "sub_bob"
    assert [r["stripe_sub"] for r in subscriptions.subscriptions_for(signed_in_as)] == ["sub_bob"]


def test_a_deletion_in_one_case_cuts_off_the_other(ledgers):
    subscriptions.record_customer_email("cus_bob", "Bob@Example.com")
    subscriptions.record_subscription_event("cus_bob", "active", FUTURE, sub_id="sub_bob")
    with (ledgers / "stripe_customer_emails.jsonl").open("a") as f:
        f.write(json.dumps({"event": subscriptions.DELETED_EVENT, "email": "bob@example.com"}) + "\n")
    with (ledgers / "subscriptions.jsonl").open("a") as f:
        f.write(json.dumps({"event": subscriptions.DELETED_EVENT, "email": "bob@example.com"}) + "\n")
    assert not subscriptions.is_active("Bob@Example.com"), "a deletion must not depend on case"
    # A late event about the deleted account's own customer stays cut off.
    subscriptions.record_subscription_event("cus_bob", "active", FUTURE, sub_id="sub_bob")
    assert not subscriptions.is_active("bob@example.com")


def test_only_ascii_letters_fold(ledgers):
    """Control: a Kelvin-sign spelling is a different mailbox."""
    subscriptions.record_customer_email("cus_k", "Ken@example.com")
    subscriptions.record_subscription_event("cus_k", "active", FUTURE, sub_id="sub_k")
    assert subscriptions.is_active("Ken@example.com")
    assert not subscriptions.is_active("ken@example.com")


def test_the_gdpr_export_finds_rows_stored_in_another_case(ledgers, monkeypatch):
    monkeypatch.setattr(gdpr.subscriptions, "SUB_LEDGER", subscriptions.SUB_LEDGER)
    monkeypatch.setattr(gdpr.subscriptions, "CUSTOMER_MAP", subscriptions.CUSTOMER_MAP)
    subscriptions.record_customer_email("cus_bob", "Bob@Example.com")
    subscriptions.record_subscription_event("cus_bob", "active", FUTURE, sub_id="sub_bob")
    items = gdpr.export_for_email("bob@example.com")["items"]
    assert len(items["customer_email_map"]) == 1, items
    assert len(items["subscription_ledger"]) == 1, items


# --- 2A. no second subscription for a signed-in subscriber -------------------------

SUBSCRIBER, PLAIN = "Sub@Example.test", "plain@example.test"


def _seed(data: Path) -> None:
    (data / "auth_sessions.jsonl").write_text("".join(json.dumps(dict(
        event="created", session_hash=hashlib.sha256(f"session-{i}".encode()).hexdigest(),
        email=e, expires_unix=time.time() + 3600)) + "\n" for i, e in enumerate(
            ["sub@example.test", PLAIN])))                 # signed in in another case
    (data / "subscriptions.jsonl").write_text(json.dumps(dict(
        email=SUBSCRIBER, status="active", stripe_sub="sub_existing")) + "\n")


def _stripe_calls(data: Path) -> int:
    p = data / "stub_stripe_calls.jsonl"
    return len(p.read_text().splitlines()) if p.exists() else 0


def test_a_signed_in_subscriber_is_not_sold_a_second_subscription(tmp_path):
    _seed(tmp_path)
    for base in _srv.server_processes(tmp_path, stub_calendars=True, stub_stripe=True,
                                      STRIPE_SECRET_KEY="sk_test_not_a_real_key",
                                      STRIPE_PRICE_SUB="price_test_sub"):
        body = json.dumps({"plan": "pro"}).encode()
        status, raw, _h = _srv.request(base, "/api/stripe/checkout", "POST", body, {
            "Content-Type": "application/json", "Cookie": "orpho_sid=session-0"})
        assert status == 409, (status, raw)
        assert "already" in json.loads(raw)["error"].lower()
        assert _stripe_calls(tmp_path) == 0, "Stripe was asked for a session anyway"

        # Controls: a signed-in non-subscriber, and a signed-out buyer (who
        # cannot be checked without an oracle), both reach Stripe.
        for headers in ({"Cookie": "orpho_sid=session-1"}, {}):
            before = _stripe_calls(tmp_path)
            status, raw, _h = _srv.request(base, "/api/stripe/checkout", "POST", body, {
                "Content-Type": "application/json", **headers})
            assert status != 409, (headers, status, raw)
            assert _stripe_calls(tmp_path) == before + 1, (headers, status, raw)


def test_a_pack_is_still_sold_to_a_subscriber(tmp_path):
    """Control: only a second SUBSCRIPTION is refused."""
    _seed(tmp_path)
    for base in _srv.server_processes(tmp_path, stub_calendars=True, stub_stripe=True,
                                      STRIPE_SECRET_KEY="sk_test_not_a_real_key",
                                      STRIPE_PRICE_PACK="price_test_pack"):
        status, raw, _h = _srv.request(base, "/api/stripe/checkout", "POST",
                                       json.dumps({"plan": "pack"}).encode(), {
                                           "Content-Type": "application/json",
                                           "Cookie": "orpho_sid=session-0"})
        assert status != 409, (status, raw)
        assert _stripe_calls(tmp_path) == 1


# --- 2A. a second subscription that arrives anyway is caught ------------------------

def _completed_sub(event_id: str, session_id: str, email: str, sub_id: str) -> bytes:
    return json.dumps({"id": event_id, "type": "checkout.session.completed", "data": {"object": {
        "id": session_id, "mode": "subscription", "payment_status": "paid",
        "customer": f"cus_{session_id}", "subscription": sub_id,
        "customer_email": email, "amount_total": 900}}}).encode()


@pytest.fixture()
def webhook(ledgers, monkeypatch):
    monkeypatch.setattr(stripe_webhook, "PROCESSED_EVENTS_PATH", ledgers / "events.jsonl")
    monkeypatch.setattr(stripe_webhook, "PI_SESSION_MAP_PATH", ledgers / "pi_map.jsonl")
    monkeypatch.setattr(stripe_webhook.mailer, "send_subscription_welcome_email",
                        lambda *a, **k: True)
    return ledgers


def test_a_second_subscription_from_a_payment_link_is_flagged(webhook, capsys):
    subscriptions.record_customer_email("cus_old", "bob@example.com")
    subscriptions.record_subscription_event("cus_old", "active", FUTURE, sub_id="sub_old")
    capsys.readouterr()
    result = stripe_webhook.handle_event(_completed_sub("evt_dup", "cs_dup", "Bob@Example.com", "sub_new"))
    assert result.get("duplicate_subscription") is True, result
    err = capsys.readouterr().err
    assert "DUPLICATE subscription" in err and "sub_new" in err and "sub_old" in err, err


def test_a_first_subscription_is_not_flagged(webhook):
    """Control, including the new subscription's own events arriving first."""
    subscriptions.record_customer_email("cus_cs_first", "carol@example.com")
    subscriptions.record_subscription_event("cus_cs_first", "active", FUTURE, sub_id="sub_first")
    result = stripe_webhook.handle_event(_completed_sub("evt_first", "cs_first", "carol@example.com", "sub_first"))
    assert result.get("subscription_checkout") is True, result
    assert not result.get("duplicate_subscription"), result


# --- review of 8a00a97: "active" means ANY subscription is active ------------------

def test_a_subscriber_whose_other_subscription_was_canceled_is_still_active(ledgers):
    """is_active read only the newest row across ALL of an address's
    subscriptions, so cancelling one duplicate (what the DUPLICATE log line
    tells the founder to do) cut off the one still being paid for."""
    subscriptions.record_customer_email("cus_old", "bob@example.com")
    subscriptions.record_customer_email("cus_new", "bob@example.com")
    subscriptions.record_subscription_event("cus_old", "active", FUTURE, sub_id="sub_old")
    subscriptions.record_subscription_event("cus_new", "active", FUTURE, sub_id="sub_new")
    subscriptions.record_subscription_event("cus_old", "canceled", FUTURE, sub_id="sub_old")
    assert subscriptions.active_subscription_ids("bob@example.com") == ["sub_new"]
    assert subscriptions.is_active("bob@example.com"), "a paying subscriber read as inactive"


def test_a_row_without_a_subscription_id_still_counts(ledgers):
    """Control: rows written without a Stripe subscription id keep the old
    newest-row meaning."""
    with (ledgers / "subscriptions.jsonl").open("a") as f:
        f.write(json.dumps({"email": "legacy@example.com", "status": "active"}) + "\n")
    assert subscriptions.is_active("legacy@example.com")


def test_the_guard_refuses_while_any_subscription_is_active(tmp_path):
    (tmp_path / "auth_sessions.jsonl").write_text(json.dumps(dict(
        event="created", session_hash=hashlib.sha256(b"session-0").hexdigest(),
        email="bob@example.com", expires_unix=time.time() + 3600)) + "\n")
    (tmp_path / "subscriptions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        dict(email="bob@example.com", status="active", stripe_sub="sub_old", current_period_end=FUTURE),
        dict(email="bob@example.com", status="active", stripe_sub="sub_new", current_period_end=FUTURE),
        dict(email="bob@example.com", status="canceled", stripe_sub="sub_old", current_period_end=FUTURE),
    ]))
    for base in _srv.server_processes(tmp_path, stub_calendars=True, stub_stripe=True,
                                      STRIPE_SECRET_KEY="sk_test_not_a_real_key",
                                      STRIPE_PRICE_SUB="price_test_sub"):
        status, raw, _h = _srv.request(base, "/api/stripe/checkout", "POST",
                                       json.dumps({"plan": "pro"}).encode(), {
                                           "Content-Type": "application/json",
                                           "Cookie": "orpho_sid=session-0"})
        assert status == 409, (status, raw)
        assert _stripe_calls(tmp_path) == 0


def _sub_event(event_id: str, type_: str, customer: str, sub_id: str, status: str = "active") -> bytes:
    return json.dumps({"id": event_id, "type": type_, "data": {"object": {
        "id": sub_id, "customer": customer, "status": status,
        "current_period_end": FUTURE}}}).encode()


def test_a_duplicate_is_flagged_even_when_both_checkouts_complete_first(webhook, capsys):
    """Stripe does not order events: both `completed` can land before either
    subscription row exists, so neither checkout sees the other. The second
    subscription is caught when its own created event lands."""
    for n in ("A", "B"):
        stripe_webhook.handle_event(json.dumps({"id": f"evt_c{n}", "type": "checkout.session.completed",
            "data": {"object": {"id": f"cs_{n}", "mode": "subscription", "payment_status": "paid",
                                "customer": f"cus_{n}", "subscription": f"sub_{n}",
                                "customer_email": "bob@example.com", "amount_total": 900}}}).encode())
    capsys.readouterr()
    first = stripe_webhook.handle_event(_sub_event("evt_sA", "customer.subscription.created", "cus_A", "sub_A"))
    second = stripe_webhook.handle_event(_sub_event("evt_sB", "customer.subscription.created", "cus_B", "sub_B"))
    assert not first.get("duplicate_subscription"), first
    assert second.get("duplicate_subscription") is True, second
    err = capsys.readouterr().err
    assert "DUPLICATE subscription" in err and "sub_A" in err and "sub_B" in err, err


# --- review of 4b0ae7e: what the account acts on is the ACTIVE subscription -------

def _old_canceled_new_active(ledgers):
    subscriptions.record_customer_email("cus_old", "bob@example.com")
    subscriptions.record_customer_email("cus_new", "bob@example.com")
    subscriptions.record_subscription_event("cus_old", "active", FUTURE, sub_id="sub_old")
    subscriptions.record_subscription_event("cus_new", "active", FUTURE, sub_id="sub_new")
    subscriptions.record_subscription_event("cus_old", "canceled", FUTURE, sub_id="sub_old")


def test_the_account_acts_on_the_subscription_still_being_paid(ledgers):
    """The page said Active (is_active) while status_for and
    stripe_subscription_id_for still named the newest row, the canceled
    duplicate, so Cancel could only hit the subscription that had ended."""
    _old_canceled_new_active(ledgers)
    assert subscriptions.stripe_subscription_id_for("bob@example.com") == "sub_new"
    assert (subscriptions.status_for("bob@example.com") or {}).get("status") == "active"


def test_with_nothing_active_the_newest_row_still_speaks(ledgers):
    """Control: an ended subscription is still reported as ended."""
    subscriptions.record_customer_email("cus_a", "ann@example.com")
    subscriptions.record_subscription_event("cus_a", "active", FUTURE, sub_id="sub_a")
    subscriptions.record_subscription_event("cus_a", "canceled", FUTURE, sub_id="sub_a")
    assert (subscriptions.status_for("ann@example.com") or {}).get("status") == "canceled"
    assert subscriptions.stripe_subscription_id_for("ann@example.com") == "sub_a"


def test_a_duplicate_that_becomes_active_later_is_flagged_once(webhook, capsys):
    """Created `incomplete` (a delayed payment), active on a later update: the
    created-time check never saw it active. A renewal of an already active
    subscription is not flagged again."""
    for n in ("A", "B"):
        subscriptions.record_customer_email(f"cus_{n}", "bob@example.com")
    stripe_webhook.handle_event(_sub_event("evt_a1", "customer.subscription.created", "cus_A", "sub_A"))
    stripe_webhook.handle_event(_sub_event("evt_b1", "customer.subscription.created", "cus_B", "sub_B",
                                           status="incomplete"))
    capsys.readouterr()
    became = stripe_webhook.handle_event(_sub_event("evt_b2", "customer.subscription.updated",
                                                    "cus_B", "sub_B"))
    assert became.get("duplicate_subscription") is True, became
    renewal = stripe_webhook.handle_event(_sub_event("evt_b3", "customer.subscription.updated",
                                                     "cus_B", "sub_B"))
    assert not renewal.get("duplicate_subscription"), renewal
    assert capsys.readouterr().err.count("DUPLICATE subscription") == 1


def test_a_row_with_no_subscription_id_is_still_overridden_by_any_later_row(ledgers):
    """A hand-written row with no subscription id and no period end used to
    be ended by whatever row came after it. Grouped on its own it would stay
    active forever (found in review of 4b0ae7e)."""
    with (ledgers / "subscriptions.jsonl").open("a") as f:
        f.write(json.dumps({"email": "legacy@example.com", "status": "active",
                            "current_period_end": None}) + "\n")
    assert subscriptions.is_active("legacy@example.com")  # control: newest row of all
    subscriptions.record_customer_email("cus_L", "legacy@example.com")
    subscriptions.record_subscription_event("cus_L", "canceled", FUTURE, sub_id="sub_L")
    assert not subscriptions.is_active("legacy@example.com")
    assert (subscriptions.status_for("legacy@example.com") or {}).get("status") == "canceled"


def test_one_duplicate_is_flagged_once_in_the_usual_event_order(webhook, capsys):
    """completed and created both look for a duplicate; in the usual order
    both saw it and the founder log said it twice."""
    subscriptions.record_customer_email("cus_A", "bob@example.com")
    stripe_webhook.handle_event(_sub_event("evt_a", "customer.subscription.created", "cus_A", "sub_A"))
    capsys.readouterr()
    done = stripe_webhook.handle_event(json.dumps({"id": "evt_cB", "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_B", "mode": "subscription", "payment_status": "paid",
                            "customer": "cus_B", "subscription": "sub_B",
                            "customer_email": "bob@example.com", "amount_total": 900}}}).encode())
    assert done.get("duplicate_subscription") is True, done
    made = stripe_webhook.handle_event(_sub_event("evt_b", "customer.subscription.created", "cus_B", "sub_B"))
    assert not made.get("duplicate_subscription"), made
    assert capsys.readouterr().err.count("DUPLICATE subscription") == 1
