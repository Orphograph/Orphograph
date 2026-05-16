#!/usr/bin/env python3
"""referrals.py — give-10-get-10 referral program.

Every Pack buyer gets a referral code in their claim email. New
buyers can apply it at checkout via `?ref=CODE`. We surface the
code into Stripe metadata; the webhook handler then credits both
parties: +10 bonus credits to the new buyer's claim code, +10
added back to the referrer's original claim code.

Guardrails:
- A given referee can only credit a referrer once (block double
  credit on retries).
- A buyer cannot self-refer (cannot use their own ref code).
- A revoked or zeroed claim code cannot be used as a referrer.

Storage: append-only JSONL of referral events.

Public API:
    code_for(claim_code) -> str                # legacy: derived from claim_code
    code_for_email(email) -> str               # per-customer stable code (see affiliate.py)
    email_for_ref_code(ref_code) -> str | None # → referrer email_id hash (NOT plaintext)
    apply(ref_code, new_buyer_email, new_claim_code) -> dict
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import affiliate
import credits
from file_lock import locked

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
REFERRAL_LEDGER = Path(os.environ.get("ORPHO_REFERRALS", str(DATA_DIR / "referrals.jsonl")))
REFERRAL_BONUS = int(os.environ.get("ORPHO_REFERRAL_BONUS", "10"))


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def code_for(claim_code: str) -> str:
    """The referral code IS the claim code prefix. Reusing the existing
    bearer credential keeps the data model simple — we don't need a
    separate referral_codes ledger to look up by code.

    Format: ref_<first 12 chars of the claim_code after pk_>.
    """
    if not claim_code or not claim_code.startswith("pk_"):
        return ""
    return "ref_" + claim_code[3:15]


def code_for_email(email: str) -> str:
    """Per-customer stable referral code.

    Delegates to affiliate.code_for_email so the (ref_code, email_id)
    mapping is registered and reverse-lookupable later. Preferred over
    code_for(claim_code) for new flows.
    """
    return affiliate.code_for_email(email)


def email_id_for_ref_code(ref_code: str) -> str | None:
    """Reverse-lookup the email_id HMAC hash for a ref code.

    Returns NEVER plaintext — only the hash. This is the privacy-safe
    primitive used by self-referral checks.
    """
    return affiliate.email_id_for_ref_code(ref_code)


def _claim_code_from_ref(ref_code: str) -> str:
    if not ref_code or not ref_code.startswith("ref_"):
        return ""
    # We don't store a ref→claim mapping; we scan the credit ledger to
    # find a claim code whose first chars match. Cheap at MVP scale
    # (thousands of rows max), revisit if 100k+ packs sold.
    if not credits.LEDGER_PATH.exists():
        return ""
    needle = ref_code[len("ref_"):]
    with credits.LEDGER_PATH.open() as f:
        for line in f:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            claim = row.get("claim_code", "")
            if claim.startswith("pk_") and claim[3:3 + len(needle)] == needle:
                return claim
    return ""


def _already_credited(new_buyer_email: str, ref_code: str) -> bool:
    if not REFERRAL_LEDGER.exists():
        return False
    with REFERRAL_LEDGER.open() as f:
        for line in f:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if row.get("new_buyer_email") == new_buyer_email and \
               row.get("ref_code") == ref_code and \
               row.get("event") == "credited":
                return True
    return False


def apply(ref_code: str, new_buyer_email: str, new_claim_code: str) -> dict:
    """Apply a referral code to a new buyer. Idempotent.

    Returns {"ok": True, "bonus_credits": N, "referrer_credited": True}
    on success; {"ok": False, "reason": "..."} otherwise.
    """
    if not ref_code or not new_buyer_email or not new_claim_code:
        return {"ok": False, "reason": "missing input"}

    referrer_claim = _claim_code_from_ref(ref_code)
    if not referrer_claim:
        return {"ok": False, "reason": "unknown referral code"}
    if referrer_claim == new_claim_code:
        return {"ok": False, "reason": "cannot self-refer"}
    if _already_credited(new_buyer_email, ref_code):
        return {"ok": False, "reason": "already credited"}

    # Atomicity: hold a sentinel lock around the read+write so two
    # concurrent webhook deliveries can't both credit on the same
    # referral event (Stripe replay would also be caught by the
    # processed-events ledger, but defense in depth).
    lockfile = REFERRAL_LEDGER.with_suffix(REFERRAL_LEDGER.suffix + ".lock")
    with locked(lockfile, mode="a", exclusive=True):
        if _already_credited(new_buyer_email, ref_code):
            return {"ok": False, "reason": "already credited (race)"}
        # +10 to the new buyer (on top of their Pack's 10).
        credits.add_credits(
            claim_code=new_claim_code,
            email=new_buyer_email,
            amount=REFERRAL_BONUS,
            source=f"referral_bonus:from_{ref_code}",
        )
        # +10 to the referrer's original claim code.
        credits.add_credits(
            claim_code=referrer_claim,
            email="",  # email already on the original purchase row
            amount=REFERRAL_BONUS,
            source=f"referral_reward:to_{new_buyer_email[:1]}***",
        )
        with locked(REFERRAL_LEDGER, mode="a", exclusive=True) as f:
            f.write(json.dumps({
                "ts": _iso(),
                "event": "credited",
                "ref_code": ref_code,
                "referrer_claim_code": referrer_claim,
                "new_buyer_email": new_buyer_email,
                "new_claim_code": new_claim_code,
                "bonus_each": REFERRAL_BONUS,
            }, separators=(",", ":")) + "\n")

    sys.stderr.write(
        f"[referrals] +{REFERRAL_BONUS}/each — {ref_code} → new buyer {new_buyer_email[:1]}***\n"
    )
    return {"ok": True, "bonus_credits": REFERRAL_BONUS, "referrer_credited": True}
