#!/usr/bin/env python3
"""affiliate.py — real affiliate program on top of referrals.

Tiers (from deploy/INFLUENCER_TARGETS.md):
    - Pack signup (one-shot, $7):  referrer earns $5  + referee gets +10 bonus credits
    - Personal signup ($5/mo):     referrer earns $25
    - Creator signup ($19/mo):     referrer earns $99

Payout:
    - Cash threshold: $50 balance
    - Channels:    PayPal email | BTC address | Pack credits (1.2x boost — $50 → 60 anchor credits)

Privacy invariants (per feedback_orphograph_privacy_doctrine.md):
    - The affiliate ledger NEVER stores referee plaintext emails — only
      auth.email_id() HMAC hashes.
    - The /api/me/affiliate endpoint returns aggregate counters and the
      affiliate's OWN balance/payout history. It does not surface any
      identifier of who referred them.
    - Referee email is hashed before write; nobody can de-anonymise
      referees by reading the ledger.

Compliance:
    - FTC endorsement guides: surfaced on /affiliate page (we don't enforce
      affiliate disclosure in code, but the program page links to FTC docs
      and the affiliate Terms make disclosure a participation requirement).
    - Anti-fraud: a user's ref code is derived from their email_id and is
      ignored if a buyer tries to refer themselves.

Append-only ledger event shapes (file: data/affiliate_ledger.jsonl):
    {"ts": ..., "event": "signup_pack",     "ref_code": ..., "referee_email_id": "<hash>", "amount_usd": 5.0,  "stripe_session": ...}
    {"ts": ..., "event": "signup_personal", "ref_code": ..., "referee_email_id": "<hash>", "amount_usd": 25.0, "stripe_customer": ...}
    {"ts": ..., "event": "signup_creator",  "ref_code": ..., "referee_email_id": "<hash>", "amount_usd": 99.0, "stripe_customer": ...}
    {"ts": ..., "event": "active_marker",   "ref_code": ..., "referee_email_id": "<hash>"}  # subscription still active at poll
    {"ts": ..., "event": "payout_requested","ref_code": ..., "method": "paypal|btc|credits", "amount_usd": ..., "destination": "<masked>"}
    {"ts": ..., "event": "payout_paid",     "ref_code": ..., "method": ..., "amount_usd": ..., "tx_ref": "..."}

Public API:
    code_for_email(email) -> str
    email_id_for_ref_code(ref_code) -> str | None
    register_signup(ref_code, tier, referee_email, stripe_id="") -> dict
    mark_active(ref_code, referee_email) -> None
    stats(email) -> dict
    request_payout(email, method, destination) -> dict
    record_paid(ref_code, method, amount_usd, tx_ref="") -> None
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth  # noqa: E402
import credits  # noqa: E402
from file_lock import locked  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get(
    "ORPHO_DATA_DIR",
    str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT),
))
LEDGER_PATH = Path(os.environ.get(
    "ORPHO_AFFILIATE_LEDGER",
    str(DATA_DIR / "affiliate_ledger.jsonl"),
))
CODE_REGISTRY = Path(os.environ.get(
    "ORPHO_AFFILIATE_CODES",
    str(DATA_DIR / "affiliate_codes.jsonl"),
))

# Tier bounty table, in USD. Keep stable in code — these are contractual.
TIER_BOUNTY_USD = {
    "pack": 5.0,
    "personal": 25.0,
    "creator": 99.0,
}
PACK_REFEREE_BONUS_CREDITS = int(os.environ.get("ORPHO_PACK_REFEREE_BONUS", "10"))
PAYOUT_MIN_USD = float(os.environ.get("ORPHO_PAYOUT_MIN_USD", "50"))
# $1 of affiliate balance buys 1 anchor credit normally; when paid out as
# credits we apply a 1.2x boost ($50 affiliate → 60 anchor credits).
CREDIT_PAYOUT_BOOST = float(os.environ.get("ORPHO_CREDIT_PAYOUT_BOOST", "1.2"))


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ref_code_from_email_id(email_id_hex: str) -> str:
    """Per-customer stable ref code: ref_<first 8 hex chars of email_id>."""
    if not email_id_hex:
        return ""
    return "ref_" + email_id_hex[:8]


def code_for_email(email: str) -> str:
    """Return the stable ref code for this email. Persists across sessions.

    Side effect: the (ref_code, email_id) mapping is recorded the first
    time it's looked up so the webhook can reverse it later. We do NOT
    record plaintext email anywhere — only the email_id hash.
    """
    if not email:
        return ""
    eid = auth.email_id(email)
    if not eid:
        return ""
    code = _ref_code_from_email_id(eid)
    _ensure_registered(code, eid)
    return code


def _ensure_registered(ref_code: str, email_id_hex: str) -> None:
    if not ref_code or not email_id_hex:
        return
    # Idempotent: scan once for an existing row, write only if missing.
    if CODE_REGISTRY.exists():
        with CODE_REGISTRY.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if row.get("ref_code") == ref_code:
                    return
    with locked(CODE_REGISTRY, mode="a", exclusive=True) as f:
        f.write(json.dumps({
            "ts": _iso(),
            "ref_code": ref_code,
            "email_id": email_id_hex,
        }, separators=(",", ":")) + "\n")


def email_id_for_ref_code(ref_code: str) -> str | None:
    """Return the referrer's email_id hash for a ref code, or None."""
    if not ref_code or not CODE_REGISTRY.exists():
        return None
    with CODE_REGISTRY.open() as f:
        for line in f:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if row.get("ref_code") == ref_code:
                return row.get("email_id")
    return None


def is_self_referral(ref_code: str, buyer_email: str) -> bool:
    """True iff this ref code belongs to the buyer themselves."""
    if not ref_code or not buyer_email:
        return False
    referrer_eid = email_id_for_ref_code(ref_code)
    if not referrer_eid:
        return False
    return referrer_eid == auth.email_id(buyer_email)


def _append(row: dict) -> None:
    with locked(LEDGER_PATH, mode="a", exclusive=True) as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _scan() -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    rows: list[dict] = []
    with LEDGER_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
    return rows


def _already_logged(ref_code: str, referee_eid: str, event: str) -> bool:
    """Has this (ref_code, referee, event) combination been recorded?

    Used to make register_signup idempotent across Stripe webhook replays.
    """
    for row in _scan():
        if (row.get("ref_code") == ref_code
                and row.get("referee_email_id") == referee_eid
                and row.get("event") == event):
            return True
    return False


def register_signup(
    ref_code: str,
    tier: str,
    referee_email: str,
    stripe_id: str = "",
) -> dict:
    """Log a signup bounty event. tier ∈ {"pack","personal","creator"}.

    Idempotent: same (ref_code, referee, tier) replays no-op.
    Privacy: stores ONLY auth.email_id(referee_email), never the plaintext.

    Returns {"ok": True, "bounty_usd": N} or {"ok": False, "reason": ...}.
    """
    if not ref_code or not tier or not referee_email:
        return {"ok": False, "reason": "missing input"}
    if tier not in TIER_BOUNTY_USD:
        return {"ok": False, "reason": f"unknown tier {tier!r}"}

    referrer_eid = email_id_for_ref_code(ref_code)
    if not referrer_eid:
        return {"ok": False, "reason": "unknown ref code"}
    referee_eid = auth.email_id(referee_email)
    if referrer_eid == referee_eid:
        return {"ok": False, "reason": "cannot self-refer"}

    event = f"signup_{tier}"
    lockfile = LEDGER_PATH.with_suffix(LEDGER_PATH.suffix + ".lock")
    with locked(lockfile, mode="a", exclusive=True):
        if _already_logged(ref_code, referee_eid, event):
            return {"ok": False, "reason": "already credited"}
        bounty = TIER_BOUNTY_USD[tier]
        _append({
            "ts": _iso(),
            "event": event,
            "ref_code": ref_code,
            "referee_email_id": referee_eid,
            "amount_usd": bounty,
            "stripe_id": stripe_id,
        })
    return {"ok": True, "bounty_usd": bounty, "tier": tier}


def stats(email: str) -> dict:
    """Aggregate stats for an affiliate (the signed-in user).

    Returns dict with:
        ref_code:           the user's own code
        balance_usd:        unpaid balance (signups − payouts)
        lifetime_usd:       total signup bounties ever earned
        referrals_count:    distinct referee_email_id values across all signup events
        referrals_active:   distinct referee_email_id values with an active_marker AFTER their signup_personal/creator event
        payout_history:     list[{ts, method, amount_usd, status}]
        payout_min_usd:     threshold to request payout
        payout_eligible:    balance >= threshold
    """
    if not email:
        return {
            "ref_code": "",
            "balance_usd": 0.0,
            "lifetime_usd": 0.0,
            "referrals_count": 0,
            "referrals_active": 0,
            "payout_history": [],
            "payout_min_usd": PAYOUT_MIN_USD,
            "payout_eligible": False,
        }
    ref_code = code_for_email(email)
    rows = _scan()

    signup_events = []
    payout_requests = []
    payout_paid = []
    active_markers: set[str] = set()
    for row in rows:
        if row.get("ref_code") != ref_code:
            continue
        ev = row.get("event", "")
        if ev.startswith("signup_"):
            signup_events.append(row)
        elif ev == "payout_requested":
            payout_requests.append(row)
        elif ev == "payout_paid":
            payout_paid.append(row)
        elif ev == "active_marker":
            eid = row.get("referee_email_id")
            if eid:
                active_markers.add(eid)

    lifetime_usd = sum(float(r.get("amount_usd", 0) or 0) for r in signup_events)
    paid_or_pending = sum(
        float(r.get("amount_usd", 0) or 0)
        for r in payout_requests
    )
    balance_usd = round(lifetime_usd - paid_or_pending, 2)

    distinct_referees = {
        r.get("referee_email_id")
        for r in signup_events
        if r.get("referee_email_id")
    }
    # "active" = subscription-tier referees who still have an active_marker.
    subscription_referees = {
        r.get("referee_email_id")
        for r in signup_events
        if r.get("event") in ("signup_personal", "signup_creator")
        and r.get("referee_email_id")
    }
    referrals_active = len(subscription_referees & active_markers)

    history = []
    for r in payout_requests + payout_paid:
        history.append({
            "ts": r.get("ts"),
            "event": r.get("event"),
            "method": r.get("method"),
            "amount_usd": float(r.get("amount_usd", 0) or 0),
            # destination is intentionally masked in the API surface.
        })
    history.sort(key=lambda x: x.get("ts") or "", reverse=True)

    return {
        "ref_code": ref_code,
        "balance_usd": balance_usd,
        "lifetime_usd": round(lifetime_usd, 2),
        "referrals_count": len(distinct_referees),
        "referrals_active": referrals_active,
        "payout_history": history,
        "payout_min_usd": PAYOUT_MIN_USD,
        "payout_eligible": balance_usd >= PAYOUT_MIN_USD,
    }


def _mask_destination(method: str, destination: str) -> str:
    """Mask a payout destination for log/display. We store the masked form
    in the ledger so disk leakage doesn't expose full PayPal/BTC addresses.
    """
    if not destination:
        return ""
    if method == "paypal" and "@" in destination:
        local, _, dom = destination.partition("@")
        return (local[:1] + "***@" + dom) if local else ("***@" + dom)
    if method == "btc":
        if len(destination) <= 8:
            return destination[:2] + "***"
        return destination[:4] + "***" + destination[-4:]
    return destination[:2] + "***"


def request_payout(email: str, method: str, destination: str) -> dict:
    """Affiliate-initiated payout request. Requires balance >= $50.

    method ∈ {"paypal","btc","credits"}.
    For "credits", `destination` is ignored; we credit the user's own
    Pack ledger using the boosted conversion.

    Returns {"ok": True, "amount_usd": ...} or {"ok": False, "reason": ...}.
    """
    if not email:
        return {"ok": False, "reason": "not authenticated"}
    if method not in ("paypal", "btc", "credits"):
        return {"ok": False, "reason": "invalid method"}
    ref_code = code_for_email(email)
    s = stats(email)
    balance = s["balance_usd"]
    if balance < PAYOUT_MIN_USD:
        return {
            "ok": False,
            "reason": "below threshold",
            "balance_usd": balance,
            "min_usd": PAYOUT_MIN_USD,
        }
    if method in ("paypal", "btc") and not destination:
        return {"ok": False, "reason": "destination required"}

    lockfile = LEDGER_PATH.with_suffix(LEDGER_PATH.suffix + ".lock")
    with locked(lockfile, mode="a", exclusive=True):
        # Re-check balance under lock (race-safety).
        s2 = stats(email)
        if s2["balance_usd"] < PAYOUT_MIN_USD:
            return {
                "ok": False,
                "reason": "below threshold (race)",
                "balance_usd": s2["balance_usd"],
            }
        masked = _mask_destination(method, destination) if destination else ""
        _append({
            "ts": _iso(),
            "event": "payout_requested",
            "ref_code": ref_code,
            "method": method,
            "amount_usd": s2["balance_usd"],
            "destination_masked": masked,
        })

        # For Pack-credit payouts we settle immediately into the credit
        # ledger with the boost applied. PayPal/BTC payouts settle out-of-band.
        if method == "credits":
            anchor_credits = int(round(s2["balance_usd"] * CREDIT_PAYOUT_BOOST))
            # Mint a fresh claim code so the new credits are bearer-spendable
            # without merging into a previous Pack purchase.
            claim_code = credits.new_claim_code()
            credits.add_credits(
                claim_code=claim_code,
                email=email,
                amount=anchor_credits,
                source=f"affiliate_payout:{ref_code}",
            )
            _append({
                "ts": _iso(),
                "event": "payout_paid",
                "ref_code": ref_code,
                "method": "credits",
                "amount_usd": s2["balance_usd"],
                "tx_ref": f"claim:{claim_code}",
                "anchor_credits": anchor_credits,
            })
            return {
                "ok": True,
                "method": "credits",
                "amount_usd": s2["balance_usd"],
                "anchor_credits": anchor_credits,
                "claim_code": claim_code,
            }
    return {"ok": True, "method": method, "amount_usd": s2["balance_usd"]}


def record_paid(ref_code: str, method: str, amount_usd: float, tx_ref: str = "") -> None:
    """Operator-side: mark a manual PayPal/BTC payout as paid."""
    if not ref_code:
        return
    _append({
        "ts": _iso(),
        "event": "payout_paid",
        "ref_code": ref_code,
        "method": method,
        "amount_usd": float(amount_usd),
        "tx_ref": tx_ref,
    })


def mark_active(ref_code: str, referee_email: str) -> None:
    """Mark that a referee's subscription is still active. Idempotent
    per-day-ish: we just keep one marker per (ref_code, referee_eid).
    """
    if not ref_code or not referee_email:
        return
    referee_eid = auth.email_id(referee_email)
    if not referee_eid:
        return
    # Cheap dedupe: if a marker exists, skip.
    for row in _scan():
        if (row.get("ref_code") == ref_code
                and row.get("referee_email_id") == referee_eid
                and row.get("event") == "active_marker"):
            return
    _append({
        "ts": _iso(),
        "event": "active_marker",
        "ref_code": ref_code,
        "referee_email_id": referee_eid,
    })
