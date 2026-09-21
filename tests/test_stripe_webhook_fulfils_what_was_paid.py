"""The card webhook delivers what was paid for, once, and takes back what was not.

Found by driving the real webhook route (2026-09-19):

1. SALE FREEZE ATE PAID ORDERS. `ORPHO_DISABLE_CHECKOUT=1` is the founder's
   sale freeze: it stops NEW checkouts from being created. The webhook also
   honoured it, by discarding `checkout.session.completed` and marking the
   event processed. But card checkout runs on hosted payment links, which
   stay payable whatever this server's environment says, and a session opened
   before a freeze stays payable for a day. So during a freeze a buyer could
   be charged, receive nothing, and the processed marker closed the replay
   path. The module's own no-email branch refuses to do exactly that.

2. A FAILED DELAYED PAYMENT KEPT ITS CREDITS. With a delayed payment method,
   `completed` arrives `payment_status: "unpaid"` and the outcome follows days
   later as `async_payment_succeeded` / `_failed`. Delivery happens at
   `completed` and both outcome events were ignored, so a payment that failed
   left spendable credits behind. Delivery STAYS at `completed` on purpose:
   waiting for the success event would hand a paying buyer nothing whenever the
   endpoint is not subscribed to it, which is the worse failure. The missing
   half was the revoke.

3. ONE SESSION, ONE DELIVERY, EVEN ACROSS A CRASH. Event-id dedupe cannot see
   two different events about one session. And a delivery that dies between
   the mint and the processed marker must be FINISHED on retry with the code
   already issued: no second mint, and no skipped claim email either.

Real server, real signed POSTs, and the assertions read the server's own
ledgers and log: what the buyer actually holds, not what a handler returned.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import _srv

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRET = "whsec_test_fulfils_what_was_paid"


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    d = tmp_path_factory.mktemp("stripe_frozen")
    for base in _srv.server_processes(d, stub_calendars=True,
                                      STRIPE_WEBHOOK_SECRET=SECRET,
                                      ORPHO_DISABLE_CHECKOUT="1"):
        yield base, d


@pytest.fixture(scope="module")
def normal(tmp_path_factory):
    d = tmp_path_factory.mktemp("stripe_normal")
    for base in _srv.server_processes(d, stub_calendars=True,
                                      STRIPE_WEBHOOK_SECRET=SECRET):
        yield base, d


def _event(event_id: str, session_id: str, *, type_: str = "checkout.session.completed",
           **session) -> bytes:
    obj = {"id": session_id, "mode": "payment",
           "customer_email": f"{session_id}@example.test", **session}
    return json.dumps({"id": event_id, "type": type_, "data": {"object": obj}},
                      separators=(",", ":")).encode()


def _deliver(base: str, payload: bytes) -> tuple[int, dict]:
    ts = int(time.time())
    mac = hmac.new(SECRET.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    status, body, _h = _srv.request(
        base, "/api/stripe/webhook", method="POST", body=payload, timeout=20,
        headers={"Content-Type": "application/json", "Stripe-Signature": f"t={ts},v1={mac}"})
    return status, json.loads(body or b"{}")


def _ledger(data_dir: Path) -> list[dict]:
    p = data_dir / "credit_ledger.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _mints(data_dir: Path, session_id: str) -> list[dict]:
    """Mint rows for EXACTLY this session (never a lookalike id)."""
    wanted = {f"stripe:{session_id}", f"stripe-gift:{session_id}"}
    return [r for r in _ledger(data_dir)
            if r.get("source") in wanted and int(r.get("credits_delta", 0)) > 0]


def _spendable(data_dir: Path, session_id: str) -> int:
    """What the buyer can still spend: every row on the codes minted for it."""
    codes = {r["claim_code"] for r in _mints(data_dir, session_id)}
    return sum(int(r.get("credits_delta", 0)) for r in _ledger(data_dir)
               if r.get("claim_code") in codes)


def _log_lines(data_dir: Path, needle: str) -> list[str]:
    logs = list(data_dir.glob("server-*.log"))
    assert len(logs) == 1, logs
    return [l for l in logs[0].read_text(errors="replace").splitlines() if needle in l]


# --- 1. the sale freeze ------------------------------------------------------

def test_control_the_freeze_is_really_on(frozen):
    """Without this, every freeze test could pass on a server that never saw
    the toggle."""
    base, _d = frozen
    status, body, _h = _srv.request(base, "/api/config", timeout=20)
    assert status == 200
    assert json.loads(body)["toggles"]["checkout_disabled"] is True


def test_a_payment_taken_during_a_freeze_is_still_delivered(frozen):
    base, data_dir = frozen
    status, result = _deliver(base, _event("evt_freeze_paid", "cs_freeze_paid",
                                           payment_status="paid"))
    assert status == 200
    assert result.get("claim_code_minted") is True, (
        f"the buyer was charged and the webhook answered {result}")
    assert _spendable(data_dir, "cs_freeze_paid") == 10


def test_the_forged_event_control_still_holds_during_a_freeze(frozen):
    """Delivering during a freeze must not have loosened who may deliver."""
    base, data_dir = frozen
    payload = _event("evt_freeze_forged", "cs_freeze_forged", payment_status="paid")
    status, _body, _h = _srv.request(
        base, "/api/stripe/webhook", method="POST", body=payload, timeout=20,
        headers={"Content-Type": "application/json",
                 "Stripe-Signature": f"t={int(time.time())},v1={'0' * 64}"})
    assert status == 400
    assert _mints(data_dir, "cs_freeze_forged") == []


# --- 2. delayed payment methods ----------------------------------------------

def test_a_delayed_payment_is_delivered_at_completed_not_held_hostage(normal):
    """The buyer-protective half. If this ever flips to "wait for the success
    event", a buyer on an endpoint not subscribed to it gets nothing."""
    base, data_dir = normal
    _status, result = _deliver(base, _event("evt_d1", "cs_delayed_ok", payment_status="unpaid"))
    assert result.get("claim_code_minted") is True, result
    assert _spendable(data_dir, "cs_delayed_ok") == 10
    assert _log_lines(data_dir, "cs_delayed_ok completed UNPAID"), "the founder must see it"

    # Settlement arrives later under a different event id: nothing more to do.
    _status, again = _deliver(base, _event(
        "evt_d1_ok", "cs_delayed_ok",
        type_="checkout.session.async_payment_succeeded", payment_status="paid"))
    assert again.get("already_delivered") is True, again
    assert len(_mints(data_dir, "cs_delayed_ok")) == 1
    assert _spendable(data_dir, "cs_delayed_ok") == 10


def test_a_failed_delayed_payment_takes_back_what_is_unused(normal):
    base, data_dir = normal
    _deliver(base, _event("evt_d2", "cs_delayed_fail", payment_status="unpaid"))
    assert _spendable(data_dir, "cs_delayed_fail") == 10, "control: it was delivered first"

    status, result = _deliver(base, _event(
        "evt_d2_fail", "cs_delayed_fail",
        type_="checkout.session.async_payment_failed", payment_status="unpaid"))
    assert status == 200 and result.get("revoked"), result
    assert _spendable(data_dir, "cs_delayed_fail") == 0, "a failed payment kept its credits"

    # Another session's credits are untouched by that revoke.
    assert _spendable(data_dir, "cs_delayed_ok") == 10


def test_a_failure_for_a_session_we_never_delivered_is_a_quiet_no_op(normal):
    base, data_dir = normal
    status, result = _deliver(base, _event(
        "evt_d3_fail", "cs_never_seen",
        type_="checkout.session.async_payment_failed", payment_status="unpaid"))
    assert status == 200 and result.get("revoked") == []
    assert _mints(data_dir, "cs_never_seen") == []


def test_a_missed_completed_is_still_delivered_by_the_success_event(normal):
    base, data_dir = normal
    _status, result = _deliver(base, _event(
        "evt_d4_ok", "cs_completed_was_missed",
        type_="checkout.session.async_payment_succeeded", payment_status="paid"))
    assert result.get("claim_code_minted") is True, result
    assert _spendable(data_dir, "cs_completed_was_missed") == 10


@pytest.mark.parametrize("status_value", ["paid", "no_payment_required", None])
def test_every_settled_shape_still_delivers(normal, status_value):
    """The card path today, a fully-discounted order, and an event from before
    this field was looked at. None of these may regress."""
    base, data_dir = normal
    sid = f"cs_settled_{status_value}"
    extra = {} if status_value is None else {"payment_status": status_value}
    _status, result = _deliver(base, _event(f"evt_{sid}", sid, **extra))
    assert result.get("claim_code_minted") is True, result
    assert _spendable(data_dir, sid) == 10


# --- 3. one session, one delivery --------------------------------------------

def test_a_lookalike_session_id_neither_blocks_nor_hides_a_delivery(normal):
    """Session ids that contain one another. A substring lookup takes the
    LATEST row that contains the id, so `cs_nest_1` was answered by
    `cs_nest_12`'s row: the guard missed, and the pack was minted twice."""
    base, data_dir = normal
    _deliver(base, _event("evt_n1", "cs_nest_1", payment_status="paid"))
    _deliver(base, _event("evt_n2", "cs_nest_12", payment_status="paid"))
    _status, again = _deliver(base, _event("evt_n3", "cs_nest_1", payment_status="paid"))
    assert again.get("already_delivered") is True, again
    assert len(_mints(data_dir, "cs_nest_1")) == 1
    assert len(_mints(data_dir, "cs_nest_12")) == 1, "the longer id must still get its own pack"


def _mint_without_finishing(data_dir: Path, session_id: str, email: str) -> str:
    """The state a delivery leaves behind when the process dies after the mint
    and before the processed marker: credits on the ledger, nothing else."""
    code = (
        "import os,sys;"
        f"os.environ['ORPHO_DATA_DIR']={str(data_dir)!r};"
        f"sys.path.insert(0,{str(REPO_ROOT / 'server')!r});"
        "import credits;"
        "c=credits.new_claim_code();"
        f"credits.add_credits(claim_code=c,email={email!r},amount=10,source={'stripe:' + session_id!r});"
        "print(c)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_a_delivery_that_died_midway_is_finished_not_skipped(normal):
    base, data_dir = normal
    sid = "cs_died_midway"
    issued = _mint_without_finishing(data_dir, sid, f"{sid}@example.test")
    sent_before = len(_log_lines(data_dir, "[email:inert]"))

    # The payment processor retries the same event.
    _status, result = _deliver(base, _event("evt_died", sid, payment_status="paid",
                                            payment_intent="pi_died_midway"))
    assert result.get("claim_code_minted") is True and result.get("resumed") is True, result
    assert [r["claim_code"] for r in _mints(data_dir, sid)] == [issued], "minted a second code"
    assert _spendable(data_dir, sid) == 10
    assert len(_log_lines(data_dir, "[email:inert]")) == sent_before + 1, (
        "the claim email was never attempted: the buyer holds credits they cannot find")
    pi_map = (data_dir / "stripe_pi_session_map.jsonl").read_text()
    assert "pi_died_midway" in pi_map, "a later refund could not find these credits"

    # Once finished, it stays finished.
    sent_after = len(_log_lines(data_dir, "[email:inert]"))
    _status, again = _deliver(base, _event("evt_died_again", sid, payment_status="paid"))
    assert again.get("already_delivered") is True, again
    assert len(_log_lines(data_dir, "[email:inert]")) == sent_after, "a finished delivery was re-sent"


def test_a_subscription_is_welcomed_once(normal):
    base, data_dir = normal
    sid = "cs_sub_once"
    before = len(_log_lines(data_dir, "subscription welcome sent"))
    _status, first = _deliver(base, _event("evt_sub_1", sid, mode="subscription",
                                           payment_status="unpaid", amount_total=900))
    assert first.get("subscription_checkout") is True, first
    _status, second = _deliver(base, _event(
        "evt_sub_2", sid, type_="checkout.session.async_payment_succeeded",
        mode="subscription", payment_status="paid", amount_total=900))
    assert second.get("already_delivered") is True, second
    assert not second.get("subscription_checkout"), "would be counted as a second signup"
    assert len(_log_lines(data_dir, "subscription welcome sent")) == before + 1
    assert _mints(data_dir, sid) == [], "a subscription never mints Pack credits"


# --- the reconciler must agree with the webhook --------------------------------

def _reconciler():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "reconcile_stripe_ledger", REPO_ROOT / "scripts" / "reconcile_stripe_ledger.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ev(event_id: str, type_: str, **obj) -> dict:
    return {"id": event_id, "type": type_, "data": {"object": obj}}


def test_the_reconciler_reads_the_same_world_the_webhook_writes():
    rec = _reconciler()
    events = [
        _ev("e1", "checkout.session.completed", id="cs_paid", mode="payment"),
        _ev("e2", "checkout.session.completed", id="cs_sub", mode="subscription"),
        _ev("e3", "checkout.session.completed", id="cs_failed_revoked", mode="payment"),
        _ev("e4", "checkout.session.async_payment_failed", id="cs_failed_revoked"),
        _ev("e5", "checkout.session.completed", id="cs_failed_kept", mode="payment"),
        _ev("e6", "checkout.session.async_payment_failed", id="cs_failed_kept"),
        _ev("e7", "checkout.session.async_payment_failed", id="cs_failed_never_delivered"),
        _ev("e8", "checkout.session.completed", id="cs_really_lost", mode="payment"),
    ]
    ledger = [
        {"source": "stripe:cs_paid", "credits_delta": 10},
        {"source": "stripe:cs_failed_revoked", "credits_delta": 10},
        {"source": "stripe-async-failed:cs_failed_revoked", "credits_delta": -10},
        {"source": "stripe:cs_failed_kept", "credits_delta": 10},
    ]
    out = rec.correlate(events, ledger)
    # A subscriber is not "PAID but did NOT receive credits"; a real loss still is.
    assert out["lost"] == ["cs_really_lost"], out["lost"]
    # A failed delayed payment that kept its credits is the leak; the revoked
    # one and the never-delivered one are not.
    assert [l["session_id"] for l in out["leak"]] == ["cs_failed_kept"], out["leak"]
    assert out["leak"][0]["expected_source"] == "stripe-async-failed:cs_failed_kept"
    assert out["ghost"] == []
    assert "checkout.session.async_payment_failed" in rec.EVENT_TYPES, (
        "the reconciler never asks for the event it now reasons about")


# --- review round 2 (2026-09-20): the reconciler and the demand instrument ------

def test_the_reconciler_treats_the_settlement_event_as_a_delivery_event():
    """The webhook delivers from `async_payment_succeeded` when `completed` was
    missed. If the reconciler never asks Stripe for that event it reports the
    grant as GHOST ("granted, no payment, possible fraud")."""
    rec = _reconciler()
    events = [_ev("e1", "checkout.session.async_payment_succeeded",
                  id="cs_late", mode="payment")]
    ledger = [{"source": "stripe:cs_late", "credits_delta": 10, "claim_code": "pk_late"}]
    out = rec.correlate(events, ledger)
    assert out["ghost"] == [], out["ghost"]
    assert out["lost"] == [], out["lost"]
    assert "checkout.session.async_payment_succeeded" in rec.EVENT_TYPES


def _spent_ledger(unspent: int) -> list[dict]:
    rows = [{"source": "stripe:cs_spent", "credits_delta": 10, "claim_code": "pk_spent"}]
    if unspent < 10:
        rows.append({"source": "anchor", "credits_delta": -(10 - unspent),
                     "claim_code": "pk_spent"})
    return rows


@pytest.mark.parametrize("event_type,extra", [
    ("checkout.session.async_payment_failed", {"id": "cs_spent"}),
    ("charge.refunded", {"id": "ch_1", "metadata": {"checkout_session_id": "cs_spent"}}),
    ("charge.dispute.created", {"id": "dp_1", "metadata": {"checkout_session_id": "cs_spent"}}),
])
def test_a_pack_already_spent_is_not_a_leak(event_type, extra):
    """The webhook writes NO revoke row when nothing is left to revoke, so
    "no row" was reported as a leak on every run for as long as the event stayed
    in the window: a report that can never go green."""
    rec = _reconciler()
    ev = [_ev("e1", event_type, **extra)]
    spent = rec.correlate(ev, _spent_ledger(unspent=0))
    assert spent["leak"] == [], spent["leak"]
    assert [c["session_id"] for c in spent["consumed"]] == ["cs_spent"]

    # Controls. Credits still unspent with no revoke row IS the leak, and rows
    # that carry no claim code cannot prove anything was spent.
    kept = rec.correlate(ev, _spent_ledger(unspent=4))
    assert [l["session_id"] for l in kept["leak"]] == ["cs_spent"], kept
    assert kept["consumed"] == []
    blind = rec.correlate(ev, [{"source": "stripe:cs_spent", "credits_delta": 10}])
    assert [l["session_id"] for l in blind["leak"]] == ["cs_spent"], blind


def test_a_spent_pack_shows_in_the_report_but_is_not_drift():
    from datetime import datetime, timezone
    rec = _reconciler()
    out = rec.correlate(
        [_ev("e0", "checkout.session.completed", id="cs_spent", mode="payment"),
         _ev("e1", "checkout.session.async_payment_failed", id="cs_spent")],
        _spent_ledger(unspent=0))
    assert out["ghost"] == [] and out["lost"] == [], "control: the only finding is the spent pack"
    text = rec.render_report(out, 7, datetime(2026, 9, 20, tzinfo=timezone.utc))
    assert "OK — no drift" in text
    assert "cs_spent" in text and "nothing to revoke" in text, "the loss must stay visible"


def test_the_paid_demand_events_follow_the_money():
    import stripe_webhook
    paid = {"ok": True, "claim_code_minted": True, "payment_status": "paid"}
    assert stripe_webhook.demand_events(paid) == [
        ("payment_confirmed", "pack", True), ("entitlement_activated", "pack", True)]
    unpaid = {"ok": True, "claim_code_minted": True, "payment_status": "unpaid"}
    assert stripe_webhook.demand_events(unpaid) == [("entitlement_activated", "pack", False)]
    settled = {"ok": True, "already_delivered": True, "payment_settled": True}
    assert stripe_webhook.demand_events(settled) == [("payment_confirmed", "pack", True)]
    sub_settled = {"ok": True, "already_delivered": True, "payment_settled": True,
                   "mode": "subscription"}
    assert stripe_webhook.demand_events(sub_settled) == [
        ("payment_confirmed", "subscription", True)]
    # No status recorded (an older result) reads as settled; a repeat says nothing.
    assert [e for e, *_ in stripe_webhook.demand_events(
        {"ok": True, "subscription_checkout": True})] == [
        "payment_confirmed", "entitlement_activated"]
    assert stripe_webhook.demand_events({"ok": True, "already_delivered": True}) == []
    assert stripe_webhook.demand_events({"ok": True, "duplicate": "evt", "claim_code_minted": True}) == []
    assert stripe_webhook.demand_events({"ok": False, "claim_code_minted": True}) == []


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    d = tmp_path_factory.mktemp("stripe_measured")
    for base in _srv.server_processes(d, stub_calendars=True,
                                      STRIPE_WEBHOOK_SECRET=SECRET,
                                      ORPHO_ANALYTICS_HMAC_SECRET="x" * 32):
        yield base, d


def _demand(data_dir: Path) -> list[str]:
    p = data_dir / "demand_events.jsonl"
    if not p.exists():
        return []
    return [json.loads(l)["event"] for l in p.read_text().splitlines() if l.strip()]


def test_an_unpaid_delivery_is_not_counted_as_a_confirmed_payment(measured):
    """Through the real webhook route, reading the instrument's own file. The
    control (a card payment records both events) proves the instrument is on."""
    base, data_dir = measured
    _deliver(base, _event("evt_m_paid", "cs_m_paid", payment_status="paid"))
    assert _demand(data_dir).count("payment_confirmed") == 1, (
        "control: with the instrument on, a paid card session must record it")
    assert _demand(data_dir).count("entitlement_activated") == 1

    _deliver(base, _event("evt_m_unpaid", "cs_m_unpaid", payment_status="unpaid"))
    events = _demand(data_dir)
    assert events.count("entitlement_activated") == 2, "the pack WAS delivered"
    assert events.count("payment_confirmed") == 1, (
        "an unpaid session was counted as a confirmed payment")

    _deliver(base, _event("evt_m_settled", "cs_m_unpaid",
                          type_="checkout.session.async_payment_succeeded",
                          payment_status="paid"))
    events = _demand(data_dir)
    assert events.count("payment_confirmed") == 2, "the settlement never confirmed the payment"
    assert events.count("entitlement_activated") == 2, "settlement is not a second activation"
