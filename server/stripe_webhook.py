#!/usr/bin/env python3
"""stripe_webhook.py — Stripe webhook signature verification + event handler.

Stdlib only. Verifies Stripe-Signature header per Stripe's documented scheme:
https://stripe.com/docs/webhooks/signatures

Multi-v1 support: during signing-key rotation Stripe sends multiple v1=
signatures; we accept if ANY matches our secret.

Idempotency: every successfully-processed event ID is appended to a
processed-events ledger. Duplicate deliveries (Stripe retries, replays,
or rare double-fires) are detected and no-op'd. Without this, the same
checkout.session.completed could mint multiple claim codes.

On checkout.session.completed for our Pack product, mints a claim_code,
adds N credits, sends the claim email.

Inert until STRIPE_WEBHOOK_SECRET is set in env.

Public API:
    verify_signature(payload_bytes, sig_header, secret, tolerance_sec=300) -> bool
    handle_event(payload_bytes) -> dict
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from stripe_api import mask_session_ids


def _stderr(text: str) -> None:
    """Every line this module writes goes out with Checkout Session ids masked:
    a full id answers /api/stripe/session with the buyer's email."""
    sys.stderr.write(mask_session_ids(text))

import auth
import credits
import mailer
import referrals
import subscriptions
from file_lock import locked

ENABLE_AUTO_SIGNIN_TOKEN = os.environ.get("ORPHO_AUTO_SIGNIN_ON_CHECKOUT", "1") == "1"


PACK_CREDITS = int(os.environ.get("PACK_CREDIT_COUNT", "10"))
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
PROCESSED_EVENTS_PATH = Path(os.environ.get(
    "ORPHO_PROCESSED_EVENTS", str(DATA_DIR / "stripe_processed_events.jsonl")
))
_dedupe_lock = threading.Lock()

# payment_intent -> checkout session_id map. Real Stripe charge.refunded /
# charge.dispute.created webhooks carry only the bare `payment_intent` id (and
# the charge's metadata is inherited from the PaymentIntent, NOT the Checkout
# Session — which we never write to). Without this map the refund handler can
# never recover the session_id and credits are never revoked = money leak.
PI_SESSION_MAP_PATH = Path(os.environ.get(
    "ORPHO_STRIPE_PI_SESSION_MAP", str(DATA_DIR / "stripe_pi_session_map.jsonl")
))


def _record_pi_session(payment_intent_id: str, session_id: str) -> None:
    """Persist payment_intent_id -> checkout session_id (append-only)."""
    if not payment_intent_id or not session_id:
        return
    with locked(PI_SESSION_MAP_PATH, mode="a", exclusive=True) as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "payment_intent": payment_intent_id,
            "session_id": session_id,
        }, separators=(",", ":")) + "\n")


def _lookup_session_by_pi(payment_intent_id: str) -> str:
    """Resolve a checkout session_id from a bare payment_intent id. Last
    write wins (re-minted sessions are rare; latest mapping is correct)."""
    if not payment_intent_id or not PI_SESSION_MAP_PATH.exists():
        return ""
    found = ""
    with PI_SESSION_MAP_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("payment_intent") == payment_intent_id:
                found = row.get("session_id") or found
    return found


def verify_signature(payload: bytes, sig_header: str, secret: str, tolerance_sec: int = 300) -> bool:
    if not secret or not sig_header:
        return False
    timestamp: str | None = None
    received_sigs: list[str] = []
    for part in sig_header.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if k == "t":
            timestamp = v
        elif k == "v1":
            received_sigs.append(v)
    if not timestamp or not received_sigs:
        return False
    try:
        ts_int = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts_int) > tolerance_sec:
        return False
    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in received_sigs)


def _has_been_processed(event_id: str) -> bool:
    if not event_id or not PROCESSED_EVENTS_PATH.exists():
        return False
    with PROCESSED_EVENTS_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event_id") == event_id:
                return True
    return False


def _session_delivered(session_id: str, kind: str) -> bool:
    """Has a delivery of `kind` ("claim_code_minted" / "subscription_checkout")
    for this session been marked FINISHED? Read from the processed-event
    markers, which are written last: credits on the ledger with no such marker
    mean an attempt died part-way, and that one must be resumed, not skipped."""
    if not session_id or not PROCESSED_EVENTS_PATH.exists():
        return False
    with PROCESSED_EVENTS_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                result = json.loads(line).get("result") or {}
            except (json.JSONDecodeError, AttributeError):
                continue
            if result.get("session_id") == session_id and result.get(kind):
                return True
    return False


def _settlement_failed(session_id: str) -> bool:
    """Has a failed settlement already been recorded for exactly this session?"""
    return _session_delivered(session_id, "settlement_failed")


def _mark_processed(event_id: str, result: dict) -> None:
    if not event_id:
        return
    with locked(PROCESSED_EVENTS_PATH, mode="a", exclusive=True) as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event_id": event_id,
            "result": result,
        }, separators=(",", ":")) + "\n")


def demand_events(result: dict) -> list[tuple[str, str, bool]]:
    """(event, auth_path, paid) for each server-side demand event a handled
    result earns.

    `payment_confirmed` is a claim that money settled. Delivery happens at
    `completed`, which for a delayed payment method arrives `unpaid`; recording
    a confirmed payment then counted sessions whose bank debit later failed
    into the paid-demand instrument. So: an unpaid delivery earns only the
    entitlement, and the payment is confirmed when the settlement event lands.
    A result from before the status was recorded reads as settled, which is
    what every card payment is.
    """
    if not result.get("ok") or result.get("duplicate"):
        return []
    subscription = bool(result.get("subscription_checkout")
                        or result.get("mode") == "subscription")
    auth_path = "subscription" if subscription else "pack"
    delivered_now = bool(result.get("claim_code_minted")
                         or result.get("subscription_checkout"))
    settled = result.get("payment_status") != "unpaid"
    events: list[tuple[str, str, bool]] = []
    if delivered_now:
        if settled:
            events.append(("payment_confirmed", auth_path, True))
        events.append(("entitlement_activated", auth_path, settled))
    elif result.get("payment_settled"):
        events.append(("payment_confirmed", auth_path, True))
    return events


def handle_event(payload: bytes) -> dict:
    """Process a verified Stripe event. Returns a small status dict.

    Idempotent: same event ID processed twice → second call returns
    {"ok": True, "duplicate": event_id} and does nothing.
    """
    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error": "invalid event JSON"}

    event_id = event.get("id", "")
    event_type = event.get("type", "")

    # The economic effect and its processed marker are one cross-process
    # critical section. Locking only _mark_processed is too late: two server
    # processes can both observe "not processed", both mint credits, and then
    # each append a marker. The threading lock handles sibling request threads;
    # the separate sentinel handles independent processes sharing the ledger.
    processing_lock = PROCESSED_EVENTS_PATH.with_suffix(
        PROCESSED_EVENTS_PATH.suffix + ".processing.lock"
    )
    with _dedupe_lock, locked(processing_lock, mode="a", exclusive=True):
        if event_id and _has_been_processed(event_id):
            return {"ok": True, "duplicate": event_id}

        # Subscription lifecycle — Personal tier.
        if event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
            obj = event.get("data", {}).get("object", {}) or {}
            sub_id = obj.get("id", "")
            customer = obj.get("customer", "")
            status = "canceled" if event_type == "customer.subscription.deleted" else obj.get("status", "")
            # Stripe's 2024+ billing API moved current_period_end from the
            # top-level subscription object onto the subscription_item.
            # Fall back to the top-level value for older API versions.
            current_period_end = obj.get("current_period_end")
            if current_period_end is None:
                items = ((obj.get("items") or {}).get("data") or [])
                if items:
                    current_period_end = items[0].get("current_period_end")
            cancel_at_period_end = bool(obj.get("cancel_at_period_end", False))
            subscriptions.record_subscription_event(
                stripe_customer=customer,
                status=status,
                current_period_end=current_period_end,
                sub_id=sub_id,
                event_type=event_type,
                cancel_at_period_end=cancel_at_period_end,
            )
            result = {"ok": True, "subscription_event": event_type, "status": status}
            _mark_processed(event_id, result)
            return result

        # Refund or dispute → revoke any unused Pack credits minted for the
        # originating checkout session. Already-consumed credits stay
        # consumed (anchors already happened). Without this, a refunded
        # buyer keeps spendable credits = money leak.
        if event_type in {"charge.refunded", "charge.dispute.created"}:
            obj = event.get("data", {}).get("object", {}) or {}
            obj_meta = obj.get("metadata", {}) or {}
            # Stripe's `charge` and `dispute` objects carry their own
            # metadata, but the link to our checkout session is usually
            # on the payment_intent. Try the cheap places first.
            session_id = (
                obj_meta.get("checkout_session_id")
                or obj_meta.get("session_id")
                or ""
            )
            if not session_id:
                # payment_intent may be expanded into an object or — in real
                # webhooks — a bare id string. Try expanded metadata first, then
                # resolve the bare id via the pi->session map we persisted at
                # checkout.session.completed time.
                pi = obj.get("payment_intent")
                if isinstance(pi, dict):
                    pi_meta = pi.get("metadata", {}) or {}
                    session_id = (
                        pi_meta.get("checkout_session_id")
                        or pi_meta.get("session_id")
                        or ""
                    )
                    if not session_id:
                        pi = pi.get("id") or ""
                if not session_id and isinstance(pi, str) and pi:
                    session_id = _lookup_session_by_pi(pi)
            if not session_id:
                _stderr(
                    f"[stripe_webhook] {event_type} event {event_id} had no "
                    f"recoverable session_id; cannot revoke credits\n"
                )
                result = {"ok": True, "no_session_id": True, "event_type": event_type}
                _mark_processed(event_id, result)
                return result

            prefix = (
                "stripe-refund" if event_type == "charge.refunded" else "stripe-dispute"
            )
            revoke_source = f"{prefix}:{session_id}"
            revoked = credits.revoke_credits_by_source(
                source_token=session_id, revoke_source=revoke_source,
            )
            _stderr(
                f"[stripe_webhook] {event_type} session={session_id}: "
                f"revoked={revoked}\n"
            )
            result = {
                "ok": True,
                "event_type": event_type,
                "session_id": session_id,
                "revoked": revoked,
            }
            _mark_processed(event_id, result)
            return result

        # A delayed payment method (bank debit and the like) completes the
        # form first and settles days later. `completed` then arrives with
        # payment_status "unpaid", and the outcome arrives as one of the two
        # async events. Delivery stays at `completed`, as it always has: making
        # it wait for `async_payment_succeeded` would hand a paying buyer
        # nothing whenever this endpoint is not subscribed to that event, which
        # is worse than the risk it removes. What was missing is the other half:
        # when the settlement FAILS, take back what is still unused.
        if event_type == "checkout.session.async_payment_failed":
            failed = event.get("data", {}).get("object", {}) or {}
            failed_sid = failed.get("id", "")
            revoked = credits.revoke_credits_by_source(
                source_token=failed_sid,
                revoke_source=f"stripe-async-failed:{failed_sid}",
            ) if failed_sid else []
            _stderr(
                f"[stripe_webhook] async payment FAILED session={failed_sid}: "
                f"revoked={revoked}\n"
            )
            # `settlement_failed` rides in the marker: Stripe does not order
            # events, so this can arrive BEFORE the `completed` it belongs to,
            # and that one must then refuse to deliver (see below).
            result = {"ok": True, "event_type": event_type,
                      "session_id": failed_sid, "revoked": revoked,
                      "settlement_failed": True}
            _mark_processed(event_id, result)
            return result

        # `async_payment_succeeded` is accepted as a delivery trigger so a
        # missed `completed` still gets delivered; the once-only guard below
        # makes it a no-op for a session that already was.
        if event_type not in {"checkout.session.completed",
                              "checkout.session.async_payment_succeeded"}:
            result = {"ok": True, "ignored": event_type}
            _mark_processed(event_id, result)
            return result

        # ORPHO_DISABLE_CHECKOUT is deliberately NOT consulted here. The freeze
        # stops new checkouts being created; this is a payment already taken.
        # Hosted payment links stay payable whatever our environment says, so
        # discarding here charged the buyer for nothing, and the processed
        # marker closed the replay path.

        session = event.get("data", {}).get("object", {}) or {}
        if session.get("payment_status") == "unpaid":
            _stderr(
                f"[stripe_webhook] WARNING session {session.get('id', '')} completed "
                f"UNPAID (delayed payment method): delivering now; a failed "
                f"settlement revokes what is unused\n"
            )
        customer_email = (
            session.get("customer_email")
            or session.get("customer_details", {}).get("email")
            or ""
        )
        session_id = session.get("id", "")

        if session_id and _settlement_failed(session_id):
            # The failure arrived first (events are not ordered). Delivering
            # now would mint a pack for a payment already known not to have
            # settled, and nothing would ever take it back.
            _stderr(
                f"[stripe_webhook] session {session_id} was already reported "
                f"FAILED; not delivering\n"
            )
            result = {"ok": True, "settlement_failed": True,
                      "session_id": session_id, "delivered": False}
            _mark_processed(event_id, result)
            return result

        if not customer_email:
            # A paid session with no resolvable email. Do NOT mark the event
            # processed: marking it would dedupe every Stripe retry into a
            # permanent "paid-but-nothing" dead end. Leaving it unmarked lets a
            # corrected redelivery (or manual replay) still deliver the pack.
            # Alert loudly so the founder can reconcile from the Stripe session.
            _stderr(
                f"[stripe_webhook] WARNING session {session_id} completed with NO "
                f"customer email — credits NOT minted; event left unprocessed for "
                f"retry/manual recovery. RECONCILE from Stripe dashboard\n"
            )
            return {"ok": False, "error": "no customer email", "recoverable": True}
        masked = auth.mask_email(customer_email)

        # Capture customer_id → email mapping for later subscription events,
        # which carry only the customer ID.
        stripe_customer = session.get("customer", "")
        if stripe_customer:
            subscriptions.record_customer_email(stripe_customer, customer_email)

        # If this checkout was for a subscription rather than a one-time Pack,
        # don't mint Pack credits — the subscription delivers value via the
        # signed-in account instead. Without a welcome email, the customer
        # has no obvious path to reach their account (they didn't create one
        # — they just paid Stripe), so this send is load-bearing for UX.
        if session.get("mode") == "subscription":
            if _session_delivered(session_id, "subscription_checkout"):
                # A second event about a session already welcomed.
                result = {"ok": True, "already_delivered": True, "session_id": session_id,
                          "mode": "subscription"}
                if event_type == "checkout.session.async_payment_succeeded":
                    result["payment_settled"] = True
                _mark_processed(event_id, result)
                return result
            # Plan label: best-effort read of the line item's price metadata.
            plan_label = "Standing Order"
            try:
                amount_total = int(session.get("amount_total") or 0)
                if amount_total >= 5000:
                    plan_label = "Personal annual"
                elif amount_total >= 500:
                    plan_label = "Standing Order"
            except (TypeError, ValueError):
                pass
            sent = mailer.send_subscription_welcome_email(customer_email, plan_label=plan_label)
            _stderr(
                f"[stripe_webhook] subscription welcome sent for session {session_id} "
                f"({masked}) plan={plan_label} (email_sent={sent})\n"
            )
            result = {"ok": True, "subscription_checkout": True,
                      "welcome_email_sent": sent, "session_id": session_id,
                      "payment_status": session.get("payment_status") or ""}
            _mark_processed(event_id, result)
            return result

        # Gifting: if the buyer set `metadata.gift_to_email` in the Stripe
        # checkout, deliver the claim code to that recipient instead of the
        # buyer. The credits ledger records the recipient as the account
        # email so they can use the credits when they sign in.
        meta = session.get("metadata", {}) or {}
        gift_to_raw = (meta.get("gift_to_email") or "").strip()
        gift_message = (meta.get("gift_message") or "").strip()[:500]
        is_gift = False
        recipient_email = customer_email
        # Minimal email shape validation; mailer will skip cleanly if invalid.
        if gift_to_raw and "@" in gift_to_raw and len(gift_to_raw) <= 254:
            recipient_email = gift_to_raw
            is_gift = True
        elif gift_to_raw:
            # Buyer intended to gift but the address failed shape check.
            # Falling back to buyer-as-recipient is the right behavior (don't
            # eat the money), but the founder must see this in logs so they
            # can manually contact the buyer to fix the typo.
            _stderr(
                f"[stripe_webhook] session {session_id} had malformed gift_to_email "
                f"({auth.mask_email(gift_to_raw)!r}); delivering pack to buyer instead\n"
            )

        # Credit count: multi-size packs (e.g. pack50) carry `credit_count` in
        # the checkout-session metadata; the entry Writer Pack sends none and
        # falls back to PACK_CREDITS (=10) — so existing purchases are unchanged.
        try:
            credit_amount = int(meta.get("credit_count") or PACK_CREDITS)
        except (TypeError, ValueError):
            credit_amount = PACK_CREDITS
        if credit_amount <= 0:
            credit_amount = PACK_CREDITS
        # One paid session is delivered once, whichever events describe it.
        # Event-id dedupe cannot see two different events about one session.
        # Exact source match: a lookalike id must neither cost a buyer their
        # pack nor hide an earlier delivery.
        already = credits.find_mint_by_exact_source(
            {f"stripe:{session_id}", f"stripe-gift:{session_id}"}) if session_id else None
        if already and _session_delivered(session_id, "claim_code_minted"):
            result = {"ok": True, "already_delivered": True, "session_id": session_id}
            if event_type == "checkout.session.async_payment_succeeded":
                result["payment_settled"] = True
            _mark_processed(event_id, result)
            return result
        resumed = bool(already)
        if resumed:
            # Credits are on the ledger and no delivery was ever marked
            # finished: an earlier attempt died between the mint and the
            # marker. Mint nothing, and FINISH it with the code already issued.
            # Everything below is safe to repeat (the map is last-write-wins,
            # a referral is credited once), and a second claim email is a far
            # better outcome than none.
            claim_code = already["claim_code"]
            credit_amount = int(already.get("credits_delta") or credit_amount)
            _stderr(
                f"[stripe_webhook] resuming an unfinished delivery for session {session_id}\n"
            )
        else:
            claim_code = credits.new_claim_code()
            credits.add_credits(
                claim_code=claim_code,
                email=recipient_email,
                amount=credit_amount,
                source=(
                    f"stripe-gift:{session_id}" if is_gift else f"stripe:{session_id}"
                ),
            )
        # Map payment_intent -> session_id so a later refund/dispute (whose
        # charge carries only the bare payment_intent id) can find and revoke
        # exactly these credits. mode==payment sessions carry a string PI id.
        payment_intent_id = session.get("payment_intent")
        if isinstance(payment_intent_id, str) and payment_intent_id:
            _record_pi_session(payment_intent_id, session_id)

        # Referral: Stripe Payment Links accept arbitrary key=value pairs
        # in metadata (set client-side by adding ?prefilled_metadata or via
        # the Stripe API). We look for `ref_code` first, fall back to any
        # field that smells like a ref code.
        ref_credit_result = None
        ref_code = (meta.get("ref_code") or meta.get("ref") or "").strip()
        if ref_code:
            # Referral attribution stays with the BUYER (who clicked the
            # affiliate link), not the gift recipient.
            ref_credit_result = referrals.apply(ref_code, customer_email, claim_code)

        if is_gift:
            sent = mailer.send_pack_gift_email(
                to=recipient_email,
                from_email=customer_email,
                claim_code=claim_code,
                credit_count=credit_amount,
                message=gift_message,
            )
            _stderr(
                f"[stripe_webhook] gifted Pack for session {session_id}: "
                f"buyer={masked} → recipient={auth.mask_email(recipient_email)}, "
                f"{credit_amount} credits (email_sent={sent})\n"
            )
            if not sent:
                # Credits are minted; recipient won't get the code by email.
                # Founder can recover by querying the credits ledger for the
                # recipient's email and re-sending manually.
                _stderr(
                    f"[stripe_webhook] WARNING gift email failed for session {session_id}; "
                    f"claim code minted but recipient not notified\n"
                )
        else:
            sent = mailer.send_pack_claim_email(customer_email, claim_code, credit_amount)
            _stderr(
                f"[stripe_webhook] minted claim_code for session {session_id} ({masked}): "
                f"{credit_amount} credits (email_sent={sent})\n"
            )
        # session_id rides in the marker so the once-only guard can tell a
        # FINISHED delivery from credits left behind by one that died.
        result = {"ok": True, "claim_code_minted": True, "gift": is_gift,
                  "session_id": session_id,
                  "payment_status": session.get("payment_status") or ""}
        if resumed:
            result["resumed"] = True
        if ref_credit_result is not None:
            result["referral"] = ref_credit_result
        _mark_processed(event_id, result)
        return result
