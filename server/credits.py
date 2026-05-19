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
    revoke_credits_by_source(source_substring, revoke_source) -> list[dict]
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
    with _lock:
        with locked(LEDGER_PATH, mode="a", exclusive=True) as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


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


def find_claim_code_by_source(source_substring: str) -> dict | None:
    """Return the most recent {claim_code, email, source, ts} row whose
    `source` contains `source_substring`, or None.

    Used by the /api/recover endpoint to look up an already-minted claim
    code for a paid Stripe session — idempotent recovery without
    minting a second code. Substring match so both `stripe:cs_abc...`
    and `stripe-gift:cs_abc...` are found.
    """
    if not source_substring or not LEDGER_PATH.exists():
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
            if source_substring in src and int(row.get("credits_delta", 0)) > 0:
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
        "credits_delta": int(latest.get("credits_delta", 0)),
    }


def revoke_credits_by_source(source_substring: str, revoke_source: str) -> list[dict]:
    """Revoke unused credits for every claim_code minted with a matching source.

    `source_substring` is matched against the `source` field of original
    add_credits rows (e.g. "stripe:cs_abc" matches both
    `stripe:cs_abc` and `stripe-gift:cs_abc`). For each claim_code touched
    we compute (issued_for_source - already_consumed) and append a single
    negative ledger entry tagged with `revoke_source`.

    Idempotent: if a revoke row with the same `revoke_source` already exists
    for a claim_code, that code is skipped. Already-consumed credits stay
    consumed — we only zero what's still unused, capped at unused balance.

    Returns a list of {claim_code, revoked} dicts describing what changed.
    """
    if not source_substring or not revoke_source:
        return []
    if not LEDGER_PATH.exists():
        return []

    with _lock:
        # Use the same sentinel lockfile as consume_credit so the read+write
        # critical section is atomic vs. concurrent spends and other revokes.
        lockfile = LEDGER_PATH.with_suffix(LEDGER_PATH.suffix + ".lock")
        with locked(lockfile, mode="a", exclusive=True):
            # First pass: find claim_codes whose ORIGINAL minting source
            # contains source_substring, and collect per-code totals.
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
                    delta = int(row.get("credits_delta", 0))
                    # A positive delta whose source contains the substring
                    # marks this code as originating from the refunded session.
                    if delta > 0 and source_substring in src:
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
