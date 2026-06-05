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
# Intra-process guard (threads within one machine). The cross-process file lock
# (see _dedupe_lock_path) adds multi-machine atomicity; we hold BOTH around the
# check-then-mint-then-mark section.
_dedupe_lock = threading.Lock()


def _dedupe_lock_path() -> Path:
    """Sibling .lock of the processed-events ledger — the cross-process sentinel
    held around the whole mint critical section so two machines sharing the data
    volume cannot both pass the dedup check and double-mint one order. Mirrors
    credits.consume_credit's lockfile pattern. Resolved at CALL time so a runtime
    (or test) override of PROCESSED_EVENTS_PATH is honored. It is a DISTINCT file
    from the ledger it guards, so it never self-deadlocks with _mark_processed's
    append lock on PROCESSED_EVENTS_PATH itself."""
    return PROCESSED_EVENTS_PATH.with_suffix(PROCESSED_EVENTS_PATH.suffix + ".lock")


def _to_float(v) -> float | None:
    """Best-effort float parse; None on missing/garbage (used for amount guards)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
    # Resolve the credit pack. NOWPayments echoes `order_id` and
    # `order_description` verbatim, so we round-trip the plan through BOTH
    # (set in nowpayments_api.create_invoice + _handle_nowpayments_create):
    #   order_id          = "np_<plan>_<rand>"
    #   order_description = "Orphograph <plan> credit pack"
    # Accept an explicit `plan` key first, then an exact plan token in either
    # echoed field, then the legacy human-readable description variants.
    plan = (event.get("plan") or "").strip().lower()
    if plan not in PLAN_CREDITS:
        haystack = f"{event.get('order_description') or ''} {order_id}".lower()
        for known in PLAN_CREDITS:          # exact tokens: writer_pack, pack_50
            if known in haystack:
                plan = known
                break
    if plan not in PLAN_CREDITS:
        desc = (event.get("order_description") or "").lower()
        if "pack-of-50" in desc or "pack of 50" in desc:
            plan = "pack_50"
        elif "writer" in desc:
            plan = "writer_pack"
    plan_resolved = plan in PLAN_CREDITS
    if not plan_resolved:
        # Could not determine the pack from any echoed field. Default to the
        # SMALLER pack so we never OVER-grant, and alert loudly so the founder
        # can reconcile (top up to the larger pack) rather than silently
        # shipping 10 credits for a possible $29 purchase.
        sys.stderr.write(
            f"[nowpayments_webhook] order {order_id}: UNRESOLVED plan "
            f"(desc={event.get('order_description')!r}) — defaulting to "
            f"{DEFAULT_PLAN}; RECONCILE MANUALLY\n"
        )
        plan = DEFAULT_PLAN
    pack_credits = PLAN_CREDITS[plan]

    if not order_id:
        return {"ok": False, "error": "missing order_id"}
    if not payment_status:
        return {"ok": False, "error": "missing payment_status"}

    event_id = f"{order_id}:{payment_status}"

    # Decide + mint under the lock; the (slow, network) claim-code email is sent
    # AFTER the lock releases, so a multi-second Resend retry can't hold the
    # cross-process sentinel and head-of-line-block every other machine's IPN.
    result, pending_email = _decide_and_mint_locked(
        event_id, order_id, payment_status, customer_email,
        pack_credits, plan, plan_resolved, invoice_id, event,
    )
    if pending_email is not None:
        to_email, claim_code, credit_count = pending_email
        sent = mailer.send_pack_claim_email(to_email, claim_code, credit_count)
        sys.stderr.write(
            f"[nowpayments_webhook] minted claim_code order={order_id} "
            f"plan={plan} credits={pack_credits} email_sent={sent}\n"
        )
    return result


def _decide_and_mint_locked(
    event_id: str,
    order_id: str,
    payment_status: str,
    customer_email: str,
    pack_credits: int,
    plan: str,
    plan_resolved: bool,
    invoice_id: str,
    event: dict,
) -> "tuple[dict, tuple | None]":
    """Dedup + mint critical section. Returns (result, pending_email): pending_email
    is (to, claim_code, credit_count) IFF a fresh mint happened this call, else None.
    The caller sends that email AFTER this returns — i.e. after the cross-process
    lock is released — so a slow Resend call never head-of-line-blocks the fleet.
    All money mutations (credit add + dedup marks) happen INSIDE the lock."""
    # Hold BOTH the intra-process threading lock and the cross-process file lock
    # for the entire check-then-mint-then-mark section, so neither two threads
    # nor two machines can interleave and double-mint a single order.
    with _dedupe_lock, locked(_dedupe_lock_path(), exclusive=True):
        if _has_been_processed(event_id):
            return {"ok": True, "duplicate": event_id}, None

        # Crediting branch. NOWPayments' lifecycle (waiting -> confirming ->
        # confirmed -> sending -> finished) fires an IPN per transition, so a
        # single paid order delivers BOTH a `confirmed` and a `finished` IPN.
        # Those carry different per-status event_ids, so we dedup the MINT on a
        # per-ORDER marker: whichever status arrives first credits; the sibling
        # no-ops. (Refund/partial/failed keep their own per-status keys below.)
        if payment_status in ("finished", "confirmed"):
            mint_marker = f"mint:{order_id}"
            # Exactly-once mint. The per-order marker dedups the normal case; we
            # ALSO cross-check the credit ledger (the money source of truth) by
            # order_id, so even if the marker write was lost to a crash AFTER a
            # prior successful mint, a retried/duplicate IPN will not double-mint.
            # find_claim_code_by_source returns the positive mint row; refund
            # rows are negative and ignored.
            already_minted = credits.find_claim_code_by_source(order_id) is not None
            if _has_been_processed(mint_marker) or already_minted:
                result = {"ok": True, "duplicate_mint": order_id,
                          "status": payment_status}
                # Re-assert the marker so a crash-lost marker self-heals.
                _mark_processed(mint_marker, result)
                _mark_processed(event_id, result)
                return result, None

            if not customer_email or "@" not in customer_email:
                sys.stderr.write(
                    f"[nowpayments_webhook] order {order_id} {payment_status} "
                    f"has no customer email — cannot deliver claim code\n"
                )
                result = {"ok": False, "error": "no customer email"}
                # NOTE: do not set mint_marker — nothing was minted, so a later
                # IPN that DOES carry an email can still deliver the pack.
                _mark_processed(event_id, result)
                return result, None

            # Underpayment guard. NOWPayments only marks `finished` once its own
            # merchant-configured tolerance is met, but FX/fee slippage across
            # the supported networks can let a finished/confirmed IPN settle
            # below the quoted crypto amount. Measure actually_paid vs pay_amount
            # (both in the pay currency). Default is WARN-ONLY so we never reject
            # a payment NOWPayments already accepted (paid-but-no-credit is the
            # worse error); set ORPHO_NOWPAY_ENFORCE_AMOUNT=1 to refuse on gross
            # underpayment. Fails OPEN when the amount fields are absent.
            actually_paid = _to_float(event.get("actually_paid"))
            pay_amount = _to_float(event.get("pay_amount"))
            if actually_paid is not None and pay_amount and pay_amount > 0:
                tol = _to_float(os.environ.get("ORPHO_NOWPAY_UNDERPAY_TOLERANCE"))
                if tol is None:
                    tol = 0.02
                if actually_paid < pay_amount * (1.0 - tol):
                    enforce = os.environ.get("ORPHO_NOWPAY_ENFORCE_AMOUNT", "0") == "1"
                    sys.stderr.write(
                        f"[nowpayments_webhook] order {order_id} UNDERPAID: "
                        f"actually_paid={actually_paid} < pay_amount={pay_amount} "
                        f"(tol={tol}) enforce={enforce}\n"
                    )
                    if enforce:
                        result = {"ok": True, "ignored": True, "reason": "underpaid",
                                  "order_id": order_id, "actually_paid": actually_paid,
                                  "pay_amount": pay_amount}
                        _mark_processed(event_id, result)
                        return result, None

            claim_code = credits.new_claim_code()
            # Include BOTH invoice_id and order_id in the source so that
            # later refund IPNs (which arrive with the order_id we issued)
            # can locate this row via substring match.
            source = f"nowpayments:{invoice_id or order_id}:{order_id}"
            credits.add_credits(
                claim_code=claim_code,
                email=customer_email,
                amount=pack_credits,
                source=source,
            )
            result = {
                "ok": True,
                "claim_code_minted": True,
                "plan": plan,
                "plan_resolved": plan_resolved,
                "credits": pack_credits,
                "order_id": order_id,
            }
            # Per-ORDER mint guard FIRST, so a racing sibling IPN (confirmed vs
            # finished) sees it and no-ops; then the per-status event marker.
            _mark_processed(mint_marker, result)
            _mark_processed(event_id, result)
            # Claim-code email is sent by the caller AFTER the lock releases.
            return result, (customer_email, claim_code, pack_credits)

        # Partial: do not credit, but acknowledge so NOWPayments stops retrying.
        if payment_status == "partially_paid":
            sys.stderr.write(
                f"[nowpayments_webhook] order {order_id} partially_paid — no credit\n"
            )
            result = {"ok": True, "ignored": True, "reason": "partially_paid",
                      "order_id": order_id}
            _mark_processed(event_id, result)
            return result, None

        # Failed / expired: no credit, just ack.
        if payment_status in ("failed", "expired"):
            result = {"ok": True, "ignored": True, "reason": payment_status,
                      "order_id": order_id}
            _mark_processed(event_id, result)
            return result, None

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
            return result, None

        # Anything else (sending, waiting, confirming): ack without crediting.
        result = {"ok": True, "ignored": True, "reason": payment_status,
                  "order_id": order_id}
        _mark_processed(event_id, result)
        return result, None
