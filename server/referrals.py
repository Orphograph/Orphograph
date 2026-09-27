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
- A buyer cannot self-refer: a code whose pack the buyer holds (matched
  by email id) is refused, whichever of their packs it came from.
- A referral code carries nothing of the claim code it rewards (it is an
  HMAC of it) and resolves only by exact match.

Storage: append-only JSONL of referral events.

Public API:
    code_for(claim_code) -> str                # keyed digest of the claim code, stable
    code_for_email(email) -> str               # per-customer stable code (see affiliate.py)
    email_for_ref_code(ref_code) -> str | None # → referrer email_id hash (NOT plaintext)
    apply(ref_code, new_buyer_email, new_claim_code) -> dict
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import affiliate
import auth
import credits
from file_lock import locked

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
REFERRAL_LEDGER = Path(os.environ.get("ORPHO_REFERRALS", str(DATA_DIR / "referrals.jsonl")))
REFERRAL_BONUS = int(os.environ.get("ORPHO_REFERRAL_BONUS", "10"))


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Until 2026-09-27 the code was ref_ + claim_code[3:15]: 12 of the 16 random
# characters of the bearer claim code, in a link the claim email asks the buyer
# to share. It is now a keyed digest of the claim code (the installation's HMAC
# secret, which account ids already depend on, with its own label), so the
# link says nothing about the credential. Codes already mailed in the old shape
# keep working, matched exactly; lengths tell the two apart (the account-level
# affiliate codes are 8 hex, see affiliate.py).
CODE_HEX_LEN = 10
LEGACY_NEEDLE_LEN = 12
_CODE_LABEL = b"orphograph-referral-code-v1:"


def code_for(claim_code: str) -> str:
    """The referral code for a pack: ref_<10 hex of HMAC(secret, claim code)>.

    Stable for a given claim code (it is printed in the claim email) and
    reveals nothing of it.
    """
    if not claim_code or not claim_code.startswith("pk_"):
        return ""
    digest = hmac.new(auth._hmac_secret(), _CODE_LABEL + claim_code.encode("utf-8"),
                      hashlib.sha256).hexdigest()
    return "ref_" + digest[:CODE_HEX_LEN]


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
    """The pack a referral code rewards, matched EXACTLY, or "".

    The old lookup accepted a needle of any length, so `ref_` (empty) matched
    the first pack in the ledger and any short prefix matched some pack: a
    buyer could collect the bonus with no one's code and credit a stranger.
    Only the two real shapes resolve now. No ref→claim mapping is stored; the
    credit ledger is scanned, which is cheap at this scale (revisit past
    100k packs).
    """
    if not isinstance(ref_code, str) or not ref_code.startswith("ref_"):
        return ""
    needle = ref_code[len("ref_"):]
    if len(needle) == CODE_HEX_LEN:
        def matches(claim: str) -> bool:
            return code_for(claim) == ref_code
    elif len(needle) == LEGACY_NEEDLE_LEN:
        def matches(claim: str) -> bool:
            return claim[3:3 + LEGACY_NEEDLE_LEN] == needle
    else:
        return ""
    if not credits.LEDGER_PATH.exists():
        return ""
    seen: set[str] = set()
    for row in credits.iter_ledger_rows():
        claim = row.get("claim_code", "")
        if not isinstance(claim, str) or not claim.startswith("pk_") or claim in seen:
            continue
        seen.add(claim)
        if matches(claim):
            return claim
    return ""


def _holder_email_id(claim_code: str) -> str:
    """Email id of whoever holds a pack: its mint row's address.

    Referral bonus rows are skipped; before 2026-09-26 they named the BUYER,
    not the holder (see credits.is_referral_bonus_row)."""
    for row in credits.iter_ledger_rows():
        if row.get("claim_code") != claim_code or credits.is_referral_bonus_row(row):
            continue
        email = row.get("email")
        if isinstance(email, str) and email.strip():
            return auth.email_id(email.strip())
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
    # Comparing the two claim codes alone could never fire (a new pack is not
    # the one its code came from), so a repeat buyer used an earlier pack's
    # code and collected both bonuses. The buyer is who holds the referrer.
    buyer_eid = auth.email_id(new_buyer_email.strip())
    if referrer_claim == new_claim_code or (
            buyer_eid and _holder_email_id(referrer_claim) == buyer_eid):
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
        # email="" like the referrer row below: the mint row already names the
        # code's holder. A row carrying an email is what /api/pack/recover
        # mails codes by, and on a gift the code is the RECIPIENT's while
        # new_buyer_email is the buyer's, so writing it here let the buyer
        # have the recipient's bearer code re-sent to themselves.
        credits.add_credits(
            claim_code=new_claim_code,
            email="",
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
