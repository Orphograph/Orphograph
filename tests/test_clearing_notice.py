"""A pack paid by bank debit tells the buyer it is clearing (1A).

Founder decision 2026-09-27. Since #274 a pack paid by a delayed method is
held until the payment settles, which takes days, and until then the buyer
heard nothing from us: only the /buy page text, which a buyer who closed the
tab or paid through a hosted link never saw. At the unpaid `completed` the
buyer now gets one short notice: the payment is received and clearing, and
the Pack code follows when Stripe confirms it. A redelivered `completed`
does not send it again.
"""
from __future__ import annotations

import json

import pytest

import credits
import mailer
import stripe_webhook


@pytest.fixture()
def sent(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    monkeypatch.setattr(stripe_webhook, "PROCESSED_EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(stripe_webhook, "PI_SESSION_MAP_PATH", tmp_path / "pi_map.jsonl")
    out: list[tuple[str, str]] = []
    monkeypatch.setattr(stripe_webhook.mailer, "send_pack_claim_email",
                        lambda to, code, n: out.append(("claim", to)) or True)
    monkeypatch.setattr(stripe_webhook.mailer, "send_pack_clearing_email",
                        lambda to: out.append(("clearing", to)) or True, raising=False)
    monkeypatch.setattr(stripe_webhook.mailer, "send_subscription_welcome_email",
                        lambda *a, **k: out.append(("welcome", a[0])) or True)
    return out


def _event(event_id: str, session_id: str, *, type_: str = "checkout.session.completed",
           **session) -> bytes:
    obj = {"id": session_id, "mode": "payment", "customer_email": "buyer@example.test", **session}
    return json.dumps({"id": event_id, "type": type_, "data": {"object": obj}}).encode()


def test_a_held_pack_tells_the_buyer_once_then_delivers(sent):
    first = stripe_webhook.handle_event(_event("evt_c1", "cs_bank", payment_status="unpaid"))
    assert first.get("awaiting_settlement") is True, first
    assert sent == [("clearing", "buyer@example.test")], sent
    # Stripe redelivers `completed` under a new event id: no second notice.
    stripe_webhook.handle_event(_event("evt_c1_again", "cs_bank", payment_status="unpaid"))
    assert sent == [("clearing", "buyer@example.test")], sent
    # The settlement delivers the Pack code, once, as before.
    stripe_webhook.handle_event(_event("evt_c1_paid", "cs_bank",
                                       type_="checkout.session.async_payment_succeeded",
                                       payment_status="paid"))
    assert sent == [("clearing", "buyer@example.test"), ("claim", "buyer@example.test")], sent


def test_a_failed_notice_is_tried_again_on_redelivery(sent, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(stripe_webhook.mailer, "send_pack_clearing_email",
                        lambda to: calls.append(to) and False, raising=False)
    stripe_webhook.handle_event(_event("evt_f1", "cs_fail", payment_status="unpaid"))
    stripe_webhook.handle_event(_event("evt_f2", "cs_fail", payment_status="unpaid"))
    assert calls == ["buyer@example.test", "buyer@example.test"], calls


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
    body = msg["text"]
    assert "few business days" in body and "claim code" in body.lower()
    assert "pk_" not in body, "no code exists yet; none may be shown"
