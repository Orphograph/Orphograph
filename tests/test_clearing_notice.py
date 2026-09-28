"""A pack paid by a delayed method tells the buyer it is clearing (1A).

Founder decision 2026-09-27. Since #274 a pack paid by a delayed method is
held until the payment settles, which takes days, and until then the buyer
heard nothing from us: only the /buy page text, which a buyer who closed the
tab or paid through a hosted link never saw. At the unpaid `completed` the
buyer now gets one short notice: the order is received, the payment has not
settled, and the Pack code follows when Stripe confirms it. Best effort and
once per session: a redelivery keeps its event id and is a duplicate, and a
second event about a session already told sends nothing. A notice that
fails, or raises, never aborts the hold; nothing depends on it.
"""
from __future__ import annotations

import json

import pytest

import credits
import mailer
import stripe_webhook
import subscriptions


@pytest.fixture()
def sent(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    monkeypatch.setattr(stripe_webhook, "PROCESSED_EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(stripe_webhook, "PI_SESSION_MAP_PATH", tmp_path / "pi_map.jsonl")
    # The subscription control writes the customer map: keep it out of the
    # checkout (it wrote a real row into the worktree before this).
    monkeypatch.setattr(subscriptions, "CUSTOMER_MAP", tmp_path / "stripe_customer_emails.jsonl")
    monkeypatch.setattr(subscriptions, "SUB_LEDGER", tmp_path / "subscriptions.jsonl")
    out: list[tuple[str, str]] = []
    monkeypatch.setattr(stripe_webhook.mailer, "send_pack_claim_email",
                        lambda to, code, n: out.append(("claim", to)) or True)
    monkeypatch.setattr(stripe_webhook.mailer, "send_pack_clearing_email",
                        lambda to, gift=False: out.append(("clearing-gift" if gift else "clearing", to)) or True,
                        raising=False)
    monkeypatch.setattr(stripe_webhook.mailer, "send_subscription_welcome_email",
                        lambda *a, **k: out.append(("welcome", a[0])) or True)
    return out


def _event(event_id: str, session_id: str, *, type_: str = "checkout.session.completed",
           **session) -> bytes:
    obj = {"id": session_id, "mode": "payment", "customer_email": "buyer@example.test", **session}
    return json.dumps({"id": event_id, "type": type_, "data": {"object": obj}}).encode()


def test_a_held_pack_tells_the_buyer_then_delivers(sent):
    first = stripe_webhook.handle_event(_event("evt_c1", "cs_bank", payment_status="unpaid"))
    assert first.get("awaiting_settlement") is True, first
    assert first.get("clearing_notice_sent") is True, first
    assert sent == [("clearing", "buyer@example.test")], sent
    # Stripe's redelivery keeps the event id: it is a duplicate, no second notice.
    again = stripe_webhook.handle_event(_event("evt_c1", "cs_bank", payment_status="unpaid"))
    assert again.get("duplicate") == "evt_c1" and sent == [("clearing", "buyer@example.test")]
    # The settlement delivers the Pack code, once, as before.
    stripe_webhook.handle_event(_event("evt_c1_paid", "cs_bank",
                                       type_="checkout.session.async_payment_succeeded",
                                       payment_status="paid"))
    assert sent == [("clearing", "buyer@example.test"), ("claim", "buyer@example.test")], sent


def test_a_failed_notice_breaks_nothing_and_the_code_still_arrives(sent, monkeypatch):
    monkeypatch.setattr(stripe_webhook.mailer, "send_pack_clearing_email",
                        lambda to, gift=False: False, raising=False)
    held = stripe_webhook.handle_event(_event("evt_f1", "cs_fail", payment_status="unpaid"))
    assert held.get("ok") is True and held.get("clearing_notice_sent") is False, held
    stripe_webhook.handle_event(_event("evt_f1_paid", "cs_fail",
                                       type_="checkout.session.async_payment_succeeded",
                                       payment_status="paid"))
    assert ("claim", "buyer@example.test") in sent, sent


def test_a_gift_notice_goes_to_the_buyer_marked_as_a_gift(sent):
    stripe_webhook.handle_event(_event("evt_g1", "cs_gift", payment_status="unpaid",
                                       metadata={"gift_to_email": "friend@example.test"}))
    assert sent == [("clearing-gift", "buyer@example.test")], sent


def test_the_gift_notice_says_the_code_goes_to_the_recipient(monkeypatch):
    """No `sent` fixture here: it replaces the sender itself, and this checks
    the real email text."""
    out: list[tuple[str, str]] = []
    monkeypatch.setattr(mailer, "_send", lambda to, subject, text, html, **kw:
                        out.append((text, html)) or True)
    assert mailer.send_pack_clearing_email("buyer@example.test", gift=True) is True
    ((text, html),) = out
    # Both parts: a mail client shows one of them, and which one is its choice.
    for part in (text, html):
        assert "sent to the recipient" in part, part
        assert "sent to this address" not in part, part


def test_an_overlong_gift_address_is_not_treated_as_a_gift(sent):
    """The delivery refuses a gift address over 254 characters and sends the
    Pack to the buyer. The notice has to say what the delivery will do."""
    stripe_webhook.handle_event(_event("evt_g2", "cs_gift_long", payment_status="unpaid",
                                       metadata={"gift_to_email": "a" * 300 + "@example.test"}))
    assert sent == [("clearing", "buyer@example.test")], sent


def test_a_notice_that_raises_does_not_abort_the_hold(sent, monkeypatch, capsys):
    def _raises(to, gift=False):
        raise RuntimeError(f"mail transport down for {to}")
    monkeypatch.setattr(stripe_webhook.mailer, "send_pack_clearing_email", _raises,
                        raising=False)
    held = stripe_webhook.handle_event(_event("evt_r1", "cs_raise", payment_status="unpaid"))
    assert held.get("ok") is True and held.get("awaiting_settlement") is True, held
    assert held.get("clearing_notice_sent") is False, held
    err = capsys.readouterr().err
    assert "clearing notice failed" in err and "RuntimeError" in err, err
    # Only the exception's type is logged: its text can carry the address.
    assert "buyer@example.test" not in err, err
    # It WAS marked processed: the same event id is now a duplicate.
    again = stripe_webhook.handle_event(_event("evt_r1", "cs_raise", payment_status="unpaid"))
    assert again.get("duplicate") == "evt_r1", again
    stripe_webhook.handle_event(_event("evt_r1_paid", "cs_raise",
                                       type_="checkout.session.async_payment_succeeded",
                                       payment_status="paid"))
    assert sent == [("claim", "buyer@example.test")], sent


def test_two_events_about_one_held_session_send_one_notice(sent):
    """Event-id dedupe cannot see two different events about one session."""
    first = stripe_webhook.handle_event(_event("evt_t1", "cs_twice", payment_status="unpaid"))
    second = stripe_webhook.handle_event(_event("evt_t2", "cs_twice", payment_status="unpaid"))
    assert first.get("clearing_notice_sent") is True, first
    assert second.get("awaiting_settlement") is True, second
    assert second.get("clearing_notice_sent") is False, second
    assert sent == [("clearing", "buyer@example.test")], sent


def test_a_notice_that_was_not_sent_is_tried_by_the_next_event(sent, monkeypatch):
    """Control for the once-per-session guard: it skips a session that WAS
    told, not one whose notice failed."""
    working = stripe_webhook.mailer.send_pack_clearing_email
    monkeypatch.setattr(stripe_webhook.mailer, "send_pack_clearing_email",
                        lambda to, gift=False: False, raising=False)
    first = stripe_webhook.handle_event(_event("evt_u1", "cs_retry", payment_status="unpaid"))
    assert first.get("clearing_notice_sent") is False and sent == [], (first, sent)
    monkeypatch.setattr(stripe_webhook.mailer, "send_pack_clearing_email", working,
                        raising=False)
    second = stripe_webhook.handle_event(_event("evt_u2", "cs_retry", payment_status="unpaid"))
    assert second.get("clearing_notice_sent") is True, second
    assert sent == [("clearing", "buyer@example.test")], sent


@pytest.mark.parametrize("session", [
    {"payment_status": "paid"},                                   # a card: delivered at once
    {"payment_status": "unpaid", "mode": "subscription", "customer": "cus_x", "amount_total": 900},
])
def test_no_notice_when_nothing_is_held(sent, session):
    """Controls: a card payment gets its code at once; a subscription gets
    its welcome email."""
    stripe_webhook.handle_event(_event("evt_n", "cs_n", **session))
    assert not [k for k, _to in sent if k == "clearing"], sent


def test_the_notice_says_what_happens_next(monkeypatch):
    out: list[dict] = []
    monkeypatch.setattr(mailer, "_send", lambda to, subject, text, html, **kw:
                        out.append(dict(to=to, subject=subject, text=text, html=html, **kw)) or True)
    assert mailer.send_pack_clearing_email("buyer@example.test") is True
    (msg,) = out
    assert msg["to"] == "buyer@example.test"
    assert msg["transactional"] is True
    for part in (msg["text"], msg["html"]):
        assert "few business days" in part and "claim code" in part.lower(), part
        assert "does not go through, no Pack is issued" in part, part
        # The hold fires for every delayed method, and a failed debit can be
        # taken and returned by the bank: neither claim is ours to make.
        assert "paid by a bank" not in part, part
        assert "nothing is charged" not in part, part
        # Some methods need the buyer to finish a step with their bank.
        assert "asked you to complete a step" in part, part
        assert "pk_" not in part, "no code exists yet; none may be shown"
