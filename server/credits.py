#!/usr/bin/env python3
"""credits.py — append-only credit ledger for Pack purchases.

Identity model: no accounts. The claim_code returned by the Stripe
webhook is the bearer token. Anyone with it can spend the credits.
Email is metadata only (for receipt delivery + customer support).

Append-only design: every event (add, consume) is a row. Balance is
the sum of credits_delta for a given claim_code. This makes the
ledger auditable, easy to back up, and robust against partial writes.

Public API:
    add_credits(claim_code, email, amount, source) -> None
    consume_credit(claim_code) -> tuple[bool, int]  # (allowed, remaining)
    balance(claim_code) -> int
    new_claim_code() -> str
    find_claim_codes_by_email(email) -> list[str]  # read-only recovery lookup
    revoke_credits_by_source(source_token, revoke_source) -> list[dict]
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from file_lock import locked

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
LEDGER_PATH = Path(os.environ.get("ORPHO_CREDIT_LEDGER", str(DATA_DIR / "credit_ledger.jsonl")))

# Threading.RLock guards same-process callers from re-entering _append while
# holding _lock for consume_credit. fcntl.flock guards multi-process callers
# (e.g. two fly machines sharing a mounted volume) from interleaving writes.
_lock = threading.RLock()


def new_claim_code() -> str:
    return "pk_" + secrets.token_urlsafe(12)


def _append(row: dict) -> None:
    data = (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")
    with _lock:
        # Binary read+append so we can inspect the tail. O_APPEND makes every
        # write land at EOF regardless of the read seek.
        with locked(LEDGER_PATH, mode="ab+", exclusive=True) as f:
            # Torn-write guard: if a prior write was interrupted (process
            # killed mid-line) the file won't end in a newline. Start a fresh
            # line first so this VALID record can't be concatenated onto — and
            # lost together with — the broken tail when _scan() json-parses it.
            f.seek(0, os.SEEK_END)
            if f.tell() > 0:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    f.write(b"\n")
            f.write(data)
            # fsync so an acknowledged grant/consume survives a crash: the
            # credit ledger is money, and a buffered-but-lost append would
            # either drop a purchase or resurrect a spent credit.
            f.flush()
            os.fsync(f.fileno())


def add_credits(claim_code: str, email: str, amount: int, source: str) -> None:
    if amount <= 0:
        raise ValueError("amount must be positive")
    _append({
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "claim_code": claim_code,
        "email": email,
        "credits_delta": int(amount),
        "source": source,
    })


def refund_credit(claim_code: str, reason: str = "anchor-refund") -> None:
    """Return ONE previously-consumed credit (e.g. anchoring failed after the
    credit was already consumed). Append-only +1 row; no balance check because
    a refund only ever adds back. Idempotency is the caller's responsibility —
    call exactly once per failed consume."""
    if not claim_code:
        return
    _append({
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "claim_code": claim_code,
        "email": "",
        "credits_delta": 1,
        "source": reason,
    })


def _scan() -> dict[str, int]:
    if not LEDGER_PATH.exists():
        return {}
    balances: dict[str, int] = {}
    with LEDGER_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            code = row.get("claim_code")
            delta = int(row.get("credits_delta", 0))
            if code:
                balances[code] = balances.get(code, 0) + delta
    return balances


def balance(claim_code: str) -> int:
    if not claim_code:
        return 0
    with _lock:
        return _scan().get(claim_code, 0)


def find_claim_code_by_source(source_token: str) -> dict | None:
    """Return the most recent {claim_code, email, source, ts} row whose
    `source` carries `source_token` as a whole colon-delimited part, or None.

    Used by the /api/recover endpoint to look up an already-minted claim
    code for a paid Stripe session — idempotent recovery without
    minting a second code. A bare session id finds both `stripe:cs_abc`
    and `stripe-gift:cs_abc`; a fragment of one finds nothing (a substring
    match let a partial order id confirm that a real order existed).
    """
    if not source_token or not LEDGER_PATH.exists():
        return None
    latest = None
    with LEDGER_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            src = row.get("source") or ""
            if _source_has_token(src, source_token) and int(row.get("credits_delta") or 0) > 0:
                # First positive (mint) row wins per claim_code; keep most recent
                # overall in case of unusual ledger interleavings.
                latest = row
    if latest is None:
        return None
    return {
        "claim_code": latest.get("claim_code"),
        "email": latest.get("email"),
        "source": latest.get("source"),
        "ts": latest.get("ts"),
        "credits_delta": int(latest.get("credits_delta") or 0),
    }


def find_nowpayments_mint(order_id: str) -> dict | None:
    """The most recent crypto mint for exactly this order, or None.

    The NOWPayments webhook mints with source "nowpayments:<invoice>:<order_id>"
    (nowpayments_webhook.py), so the kind is the first part and the order id
    the LAST. `find_claim_code_by_source` matches any whole part of any source
    and keeps the latest match, so a kind word, a referral code or an invoice id
    answered for somebody else's sale, and a later unrelated row sharing a part
    could hide the order's own mint. The predicate is applied inside the scan.

    Not used by the webhook's exactly-once check on purpose: legacy two-part
    sources ("nowpayments:<invoice>") end in the invoice, so a strict match
    would miss them and a replayed IPN could mint twice. There the loose
    lookup errs toward "already minted", which is the safe direction.
    """
    if not order_id or not LEDGER_PATH.exists():
        return None
    latest = None
    with LEDGER_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            parts = (row.get("source") or "").split(":")
            if (len(parts) >= 3 and parts[0] == "nowpayments" and parts[-1] == order_id
                    and int(row.get("credits_delta") or 0) > 0):
                latest = row
    if latest is None:
        return None
    return {
        "claim_code": latest.get("claim_code"),
        "email": latest.get("email"),
        "source": latest.get("source"),
        "ts": latest.get("ts"),
        "credits_delta": int(latest.get("credits_delta") or 0),
    }


def find_mint_by_exact_source(sources: set[str]) -> dict | None:
    """The first mint row whose `source` IS one of `sources`, or None.

    For "has this exact purchase been delivered?": the WHOLE source string,
    prefix included, so a gift delivery and a card delivery of one session are
    named separately. `find_claim_code_by_source` above answers the looser
    "is there a mint carrying this id" and returns the most recent one.
    """
    if not sources or not LEDGER_PATH.exists():
        return None
    with LEDGER_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("source") in sources and int(row.get("credits_delta") or 0) > 0:
                return {
                    "claim_code": row.get("claim_code"),
                    "email": row.get("email"),
                    "source": row.get("source"),
                    "ts": row.get("ts"),
                    "credits_delta": int(row.get("credits_delta") or 0),
                }
    return None


def find_claim_codes_by_email(email: str) -> list[str]:
    """Return the distinct claim_codes ever minted against `email`, in
    first-seen order.

    Used by the /api/pack/recover endpoint so a customer who lost their
    claim instrument can have it re-sent to the address they bought with.
    Read-only: it never mutates the ledger and never mints a code.

    Only mint rows carry a non-empty `email` (consume/refund/revoke rows
    write email=""), so a case-insensitive exact match on the address
    naturally selects the original purchase rows. Append-only means the
    same code can appear on several rows; we dedupe, preserving order.
    """
    if not email:
        return []
    needle = email.strip().lower()
    if not needle or not LEDGER_PATH.exists():
        return []
    seen: dict[str, None] = {}
    with _lock:
        with LEDGER_PATH.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                row_email = (row.get("email") or "").strip().lower()
                if row_email and row_email == needle:
                    code = row.get("claim_code")
                    if code and code not in seen:
                        seen[code] = None
    return list(seen.keys())


def _source_has_token(source: str, token: str) -> bool:
    """Is `token` one whole colon-delimited part of `source`?

    Sources are `<kind>:<id>` (`stripe:cs_abc`, `stripe-gift:cs_abc`) or
    `<kind>:<invoice>:<order>` (`nowpayments:inv_7:ord_7`). A substring test
    answers "does this id appear inside that one", so `cs_1` matched the pack
    `cs_12` paid for and a refund or a failed settlement for the short id took
    the long id's credits. A token can only match itself; a token that itself
    contains colons (`stripe:cs_abc`) matches the same run of whole parts.
    """
    return bool(token) and f":{token}:" in f":{source}:"


def revoke_credits_by_source(source_token: str, revoke_source: str) -> list[dict]:
    """Revoke unused credits for every claim_code minted with a matching source.

    `source_token` is matched as a WHOLE colon-delimited part of the `source`
    field of original add_credits rows: "cs_abc" matches both `stripe:cs_abc`
    and `stripe-gift:cs_abc`, and "ord_7" matches `nowpayments:inv_7:ord_7`,
    but "cs_ab" matches none of them. For each claim_code touched
    we compute (issued_for_source - already_consumed) and append a single
    negative ledger entry tagged with `revoke_source`.

    Idempotent: if a revoke row with the same `revoke_source` already exists
    for a claim_code, that code is skipped. Already-consumed credits stay
    consumed — we only zero what's still unused, capped at unused balance.

    Returns a list of {claim_code, revoked} dicts describing what changed.
    """
    if not source_token or not revoke_source:
        return []
    if not LEDGER_PATH.exists():
        return []

    with _lock:
        # Use the same sentinel lockfile as consume_credit so the read+write
        # critical section is atomic vs. concurrent spends and other revokes.
        lockfile = LEDGER_PATH.with_suffix(LEDGER_PATH.suffix + ".lock")
        with locked(lockfile, mode="a", exclusive=True):
            # First pass: find claim_codes whose ORIGINAL minting source
            # carries source_token as a whole part, and collect per-code totals.
            matching_codes: set[str] = set()
            balances: dict[str, int] = {}
            already_revoked: set[str] = set()
            with LEDGER_PATH.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    code = row.get("claim_code")
                    if not code:
                        continue
                    src = row.get("source", "") or ""
                    delta = int(row.get("credits_delta") or 0)
                    # A positive delta whose source contains the substring
                    # marks this code as originating from the refunded session.
                    if delta > 0 and _source_has_token(src, source_token):
                        matching_codes.add(code)
                    # If the same revoke_source has already been written for
                    # this code, mark it so we skip (idempotency).
                    if src == revoke_source:
                        already_revoked.add(code)
                    balances[code] = balances.get(code, 0) + delta

            results: list[dict] = []
            for code in sorted(matching_codes):
                if code in already_revoked:
                    results.append({"claim_code": code, "revoked": 0, "skipped": "already_revoked"})
                    continue
                unused = balances.get(code, 0)
                if unused <= 0:
                    results.append({"claim_code": code, "revoked": 0, "skipped": "no_unused"})
                    continue
                _append({
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "claim_code": code,
                    "email": "",
                    "credits_delta": -unused,
                    "source": revoke_source,
                })
                results.append({"claim_code": code, "revoked": unused})
            return results


def consume_credit(claim_code: str) -> tuple[bool, int]:
    """Atomically check + decrement. Returns (consumed, balance_after).

    Cross-process atomicity: holds an exclusive fcntl lock on the ledger
    across the read+write critical section so two machines can't both
    observe balance>0 and then each consume.
    """
    if not claim_code:
        return False, 0
    with _lock:
        # Use a sentinel lockfile sibling so we can hold the lock across
        # both the scan (read) and the append (write) without trying to
        # nest fcntl on the same file descriptor.
        lockfile = LEDGER_PATH.with_suffix(LEDGER_PATH.suffix + ".lock")
        with locked(lockfile, mode="a", exclusive=True):
            current = _scan().get(claim_code, 0)
            if current <= 0:
                return False, current
            _append({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "claim_code": claim_code,
                "email": "",
                "credits_delta": -1,
                "source": "anchor",
            })
            return True, current - 1
