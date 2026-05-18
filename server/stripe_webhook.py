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

import auth
import credits
import mailer
import referrals
import subscriptions
from file_lock import locked


PACK_CREDITS = int(os.environ.get("PACK_CREDIT_COUNT", "10"))
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
PROCESSED_EVENTS_PATH = Path(os.environ.get(
    "ORPHO_PROCESSED_EVENTS", str(DATA_DIR / "stripe_processed_events.jsonl")
))
_dedupe_lock = threading.Lock()


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


def _mark_processed(event_id: str, result: dict) -> None:
    if not event_id:
        return
    with locked(PROCESSED_EVENTS_PATH, mode="a", exclusive=True) as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event_id": event_id,
            "result": result,
        }, separators=(",", ":")) + "\n")


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

    # In-process lock keeps two webhook threads from racing.
    # Cross-process race for the rare two-fly-machines case is bounded by
    # _mark_processed taking an fcntl lock; an interleave could still mint
    # twice in a tiny window, but each subsequent call sees the marker and
    # short-circuits. Acceptable for a single-product MVP.
    with _dedupe_lock:
        if event_id and _has_been_processed(event_id):
            return {"ok": True, "duplicate": event_id}

        # Subscription lifecycle — Personal tier.
        if event_type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
            obj = event.get("data", {}).get("object", {}) or {}
            sub_id = obj.get("id", "")
            customer = obj.get("customer", "")
            status = "canceled" if event_type == "customer.subscription.deleted" else obj.get("status", "")
            current_period_end = obj.get("current_period_end")
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
                # payment_intent may be expanded into an object or a bare id.
                pi = obj.get("payment_intent")
                if isinstance(pi, dict):
                    pi_meta = pi.get("metadata", {}) or {}
                    session_id = (
                        pi_meta.get("checkout_session_id")
                        or pi_meta.get("session_id")
                        or ""
                    )
            if not session_id:
                sys.stderr.write(
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
                source_substring=session_id, revoke_source=revoke_source,
            )
            sys.stderr.write(
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

        if event_type != "checkout.session.completed":
            result = {"ok": True, "ignored": event_type}
            _mark_processed(event_id, result)
            return result

        # Admin toggle: disable checkout if payment system is down
        ORPHO_DISABLE_CHECKOUT = os.environ.get("ORPHO_DISABLE_CHECKOUT", "0") == "1"
        if ORPHO_DISABLE_CHECKOUT:
            sys.stderr.write(f"[stripe_webhook] checkout disabled; discarding session event {event.get('id')}\n")
            result = {"ok": True, "disabled": "checkout temporarily disabled"}
            _mark_processed(event_id, result)
            return result

        session = event.get("data", {}).get("object", {}) or {}
        customer_email = (
            session.get("customer_email")
            or session.get("customer_details", {}).get("email")
            or ""
        )
        session_id = session.get("id", "")

        if not customer_email:
            sys.stderr.write(f"[stripe_webhook] session {session_id} had no customer email; skipping\n")
            result = {"ok": False, "error": "no customer email"}
            _mark_processed(event_id, result)
            return result
        masked = auth.mask_email(customer_email)

        # Capture customer_id → email mapping for later subscription events,
        # which carry only the customer ID.
        stripe_customer = session.get("customer", "")
        if stripe_customer:
            subscriptions.record_customer_email(stripe_customer, customer_email)

        # If this checkout was for a subscription rather than a one-time Pack,
        # don't mint Pack credits — the subscription will deliver value instead.
        if session.get("mode") == "subscription":
            # Email omitted from the response body; only Stripe sees these,
            # but we avoid having it round-trip through any cache or replay UI.
            result = {"ok": True, "subscription_checkout": True}
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
            sys.stderr.write(
                f"[stripe_webhook] session {session_id} had malformed gift_to_email "
                f"({auth.mask_email(gift_to_raw)!r}); delivering pack to buyer instead\n"
            )

        claim_code = credits.new_claim_code()
        credits.add_credits(
            claim_code=claim_code,
            email=recipient_email,
            amount=PACK_CREDITS,
            source=(
                f"stripe-gift:{session_id}" if is_gift else f"stripe:{session_id}"
            ),
        )

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
                credit_count=PACK_CREDITS,
                message=gift_message,
            )
            sys.stderr.write(
                f"[stripe_webhook] gifted Pack for session {session_id}: "
                f"buyer={masked} → recipient={auth.mask_email(recipient_email)}, "
                f"{PACK_CREDITS} credits (email_sent={sent})\n"
            )
            if not sent:
                # Credits are minted; recipient won't get the code by email.
                # Founder can recover by querying the credits ledger for the
                # recipient's email and re-sending manually.
                sys.stderr.write(
                    f"[stripe_webhook] WARNING gift email failed for session {session_id}; "
                    f"claim code minted but recipient not notified\n"
                )
        else:
            sent = mailer.send_pack_claim_email(customer_email, claim_code, PACK_CREDITS)
            sys.stderr.write(
                f"[stripe_webhook] minted claim_code for session {session_id} ({masked}): "
                f"{PACK_CREDITS} credits (email_sent={sent})\n"
            )
        result = {"ok": True, "claim_code_minted": True, "gift": is_gift}
        if ref_credit_result is not None:
            result["referral"] = ref_credit_result
        _mark_processed(event_id, result)
        return result
