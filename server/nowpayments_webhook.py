#!/usr/bin/env python3
"""nowpayments_webhook.py — IPN handler for NOWPayments callbacks.

Stdlib only. Mirrors the idempotency + signature-verify discipline of
stripe_webhook.py.

Signature: NOWPayments POSTs JSON, signs it with HMAC-SHA512 of the
JSON body (with keys sorted) using NOWPAYMENTS_IPN_SECRET, and puts
the hex digest in the `x-nowpayments-sig` header.

States we credit:
    finished, confirmed       -> mint a Pack claim code

States we DO NOT credit:
    partially_paid            -> log and 200 ignored
    failed, expired           -> log and 200 ignored
    refunded                  -> revoke any prior credits for this order

Idempotency: event_id = "<order_id>:<payment_status>" — same delivery
twice short-circuits without minting a second pack.

Public API:
    verify_signature(payload_bytes, sig_header_hex, secret) -> bool
    handle_event(payload_bytes) -> dict
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import auth  # noqa: F401  (mask_email available if needed for logs)
import credits
import mailer
from file_lock import locked

# Plan -> credit-pack metadata. Kept in sync with nowpayments_api.PLANS,
# but we import only what we need to avoid a hard dep on the outbound module.
PLAN_CREDITS = {
    "writer_pack": 10,
    "pack_50": 50,
}
DEFAULT_PLAN = "writer_pack"

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
PROCESSED_EVENTS_PATH = Path(os.environ.get(
    "ORPHO_NOWPAYMENTS_PROCESSED_EVENTS",
    str(DATA_DIR / "nowpayments_processed_events.jsonl"),
))

_dedupe_lock = threading.Lock()


def verify_signature(payload: bytes, sig_header_hex: str, secret: str) -> bool:
    """HMAC-SHA512 of JSON body (sorted keys) compared in constant time.

    NOWPayments' IPN signing scheme:
      sig = HMAC_SHA512(secret, json.dumps(sorted_body)).hexdigest()
    """
    if not payload or not sig_header_hex or not secret:
        return False
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    # Canonical JSON: sort keys, no whitespace. NOWPayments docs specify
    # `json.dumps(data, separators=(',', ':'), sort_keys=True)`.
    canonical = json.dumps(parsed, separators=(",", ":"), sort_keys=True).encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, sig_header_hex.strip())


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
    """Process a verified NOWPayments IPN. Returns a status dict.

    Caller must have already verified the HMAC signature. The function is
    safe to call with arbitrary JSON — bad shapes return {"ok": False}.
    """
    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error": "invalid event JSON"}

    if not isinstance(event, dict):
        return {"ok": False, "error": "not an object"}

    payment_status = (event.get("payment_status") or "").strip().lower()
    order_id = (event.get("order_id") or "").strip()
    invoice_id = str(event.get("invoice_id") or event.get("payment_id") or "").strip()
    customer_email = (event.get("customer_email") or "").strip()
    # `order_description` lets us round-trip the plan since order_id is
    # our internal token. We accept either a `plan` key (preferred) or a
    # description containing "pack_50" / "writer_pack".
    plan = (event.get("plan") or "").strip().lower()
    if not plan:
        desc = (event.get("order_description") or "").lower()
        if "pack_50" in desc or "pack-of-50" in desc or "pack of 50" in desc:
            plan = "pack_50"
        elif "writer" in desc:
            plan = "writer_pack"
    if plan not in PLAN_CREDITS:
        plan = DEFAULT_PLAN
    pack_credits = PLAN_CREDITS[plan]

    if not order_id:
        return {"ok": False, "error": "missing order_id"}
    if not payment_status:
        return {"ok": False, "error": "missing payment_status"}

    event_id = f"{order_id}:{payment_status}"

    with _dedupe_lock:
        if _has_been_processed(event_id):
            return {"ok": True, "duplicate": event_id}

        # Crediting branch.
        if payment_status in ("finished", "confirmed"):
            if not customer_email or "@" not in customer_email:
                sys.stderr.write(
                    f"[nowpayments_webhook] order {order_id} {payment_status} "
                    f"has no customer email — cannot deliver claim code\n"
                )
                result = {"ok": False, "error": "no customer email"}
                _mark_processed(event_id, result)
                return result

            claim_code = credits.new_claim_code()
            source = f"nowpayments:{invoice_id or order_id}"
            credits.add_credits(
                claim_code=claim_code,
                email=customer_email,
                amount=pack_credits,
                source=source,
            )
            sent = mailer.send_pack_claim_email(customer_email, claim_code, pack_credits)
            sys.stderr.write(
                f"[nowpayments_webhook] minted claim_code order={order_id} "
                f"plan={plan} credits={pack_credits} email_sent={sent}\n"
            )
            result = {
                "ok": True,
                "claim_code_minted": True,
                "plan": plan,
                "credits": pack_credits,
                "order_id": order_id,
            }
            _mark_processed(event_id, result)
            return result

        # Partial: do not credit, but acknowledge so NOWPayments stops retrying.
        if payment_status == "partially_paid":
            sys.stderr.write(
                f"[nowpayments_webhook] order {order_id} partially_paid — no credit\n"
            )
            result = {"ok": True, "ignored": True, "reason": "partially_paid",
                      "order_id": order_id}
            _mark_processed(event_id, result)
            return result

        # Failed / expired: no credit, just ack.
        if payment_status in ("failed", "expired"):
            result = {"ok": True, "ignored": True, "reason": payment_status,
                      "order_id": order_id}
            _mark_processed(event_id, result)
            return result

        # Refund (after we already minted). Revoke any unused credits whose
        # source mentions this order_id. Already-spent anchors stay valid.
        if payment_status == "refunded":
            revoke_source = f"nowpayments-refund:{order_id}"
            revoked = credits.revoke_credits_by_source(
                source_substring=order_id,
                revoke_source=revoke_source,
            )
            sys.stderr.write(
                f"[nowpayments_webhook] order {order_id} refunded — revoked={revoked}\n"
            )
            result = {"ok": True, "refunded": True, "order_id": order_id,
                      "revoked": revoked}
            _mark_processed(event_id, result)
            return result

        # Anything else (sending, waiting, confirming): ack without crediting.
        result = {"ok": True, "ignored": True, "reason": payment_status,
                  "order_id": order_id}
        _mark_processed(event_id, result)
        return result
