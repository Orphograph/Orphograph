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
import re
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


def _ledger_rows(f):
    """Every ledger row as a dict, in order. Blank and torn lines (a crash
    mid-append) and non-object JSON are skipped: none of them can carry a
    claim code. One reader for every scan of the ledger, so they cannot
    disagree about what it says. A row's delta is parsed only when the row
    matters to the question being asked, so one bad row fails the lookups
    that touch it, not every customer's."""
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        # A claim code is a string; a row whose claim_code is a list or an
        # object is corrupt and would crash a set lookup in every scan.
        if isinstance(row, dict) and isinstance(row.get("claim_code", ""), str):
            yield row


def iter_ledger_rows(path: Path | None = None):
    """Public form of the one reader, for modules outside this one."""
    path = LEDGER_PATH if path is None else Path(path)
    if not path.exists():
        return
    with path.open() as f:
        yield from _ledger_rows(f)


_WHOLE_NUMBER = re.compile(r"-?[0-9]+(?:\.0+)?")


def parse_delta(row: dict) -> int:
    """A row's credits_delta as a whole number of credits, exactly. 10, "10"
    and "10.0" are 10. Anything else (a fraction, a bool, text, "1e2", "1_000",
    nan, inf, a list, a float too large to be exact) RAISES ValueError: a
    balance, a revocation or an exactly-once check must stop rather than read
    a real movement of credits as zero or as a rounded value."""
    raw = row.get("credits_delta")
    if raw is None or raw == "":
        return 0
    if isinstance(raw, bool):
        raise ValueError("credits_delta is a boolean")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw != raw or abs(raw) > 2 ** 53 or raw != int(raw):
            raise ValueError("credits_delta is not an exact whole number")
        return int(raw)
    if isinstance(raw, str) and _WHOLE_NUMBER.fullmatch(raw.strip()):
        return int(raw.strip().split(".", 1)[0])
    raise ValueError(f"credits_delta is not a whole number: {type(raw).__name__}")


def _scan(only: str) -> dict[str, int]:
    """`only`'s balance. Only that code's rows are parsed, so a bad row
    belonging to someone else cannot stop this code's balance."""
    if not LEDGER_PATH.exists():
        return {}
    balances: dict[str, int] = {}
    with LEDGER_PATH.open() as f:
        for row in _ledger_rows(f):
            code = row.get("claim_code")
            if code and code == only:
                balances[code] = balances.get(code, 0) + parse_delta(row)
    return balances


def balance(claim_code: str) -> int:
    if not claim_code:
        return 0
    with _lock:
        return _scan(only=claim_code).get(claim_code, 0)


def _mint_rows(matches):
    """Every well-formed positive (mint) row in ledger order, as
    (row, source, credits_delta). A torn line, a non-object row or a
    non-string source is skipped (none of them can be a mint of a string
    source). A delta that is not a number raises: an exactly-once check must
    fail closed rather than read a real mint as absent."""
    if not LEDGER_PATH.exists():
        return
    with LEDGER_PATH.open() as f:
        for row in _ledger_rows(f):
            source = row.get("source") or ""
            if not isinstance(source, str) or not matches(source):
                continue
            delta = parse_delta(row)
            if delta > 0:
                yield row, source, delta


def _mint_projection(row: dict, source: str, delta: int) -> dict:
    return {
        "claim_code": row.get("claim_code"),
        "email": row.get("email"),
        "source": source,
        "ts": row.get("ts"),
        "credits_delta": delta,
    }


def _latest_mint(matches) -> dict | None:
    """The most recent mint row whose `source` satisfies `matches(source)`."""
    latest = None
    for row, source, delta in _mint_rows(matches):
        latest = (row, source, delta)
    return _mint_projection(*latest) if latest else None


def find_claim_code_by_source(source_token: str) -> dict | None:
    """Return the most recent {claim_code, email, source, ts} row whose
    `source` carries `source_token` as a whole colon-delimited part, or None.

    Used by the /api/recover endpoint to look up an already-minted claim
    code for a paid Stripe session — idempotent recovery without
    minting a second code. A bare session id finds both `stripe:cs_abc`
    and `stripe-gift:cs_abc`; a fragment of one finds nothing (a substring
    match let a partial order id confirm that a real order existed).
    """
    if not source_token:
        return None
    return _latest_mint(lambda src: _source_has_token(src, source_token))


def find_nowpayments_mint(order_id: str) -> dict | None:
    """The most recent crypto mint for exactly this order, or None.

    The NOWPayments webhook has minted with source
    "nowpayments:<invoice or order>:<order_id>" since it shipped (8af61f2), so
    the kind is the first part and the order id the LAST. The any-part lookup
    above keeps the latest row carrying the id ANYWHERE, so a kind word, a
    referral code or an invoice id answered for somebody else's sale, and a
    later unrelated row sharing a part could hide the order's own mint. Here
    the predicate is applied inside the scan.

    Used by the order-status route, crypto recover and the NOWPayments
    webhook's exactly-once check; refund revocation uses the same predicate
    (nowpayments_mint_matcher). Not revoke_credits_by_source's default:
    that one also has to find mints by the invoice part a refund IPN may carry.
    """
    if not order_id:
        return None
    return _latest_mint(nowpayments_mint_matcher(order_id))


def nowpayments_mint_matcher(order_id: str):
    """Predicate: is a mint source THIS crypto order's own mint? Shared by the
    lookups and by refund revocation, so a refund reaches exactly the rows the
    exactly-once check would count."""
    def matches(src: str) -> bool:
        # "nowpayments:<invoice>:<order_id>": the kind, ONE colon-free invoice
        # part, and then everything left must BE the order id. Anchored at the
        # front, so a trailing fragment of someone else's order id never
        # answers (a suffix match let "7" find "shop:ord:7").
        kind, _, rest = src.partition(":")
        _invoice, sep, tail = rest.partition(":")
        if kind != "nowpayments":
            return False
        if sep:
            if tail == order_id:
                return True
            # A row written before the invoice part was escaped may carry ":"
            # inside the invoice. For an order id the server minted (np_…,
            # never containing ":") its LAST part is still unambiguous. Only
            # for those: for any other query a last-part match would let a
            # fragment of someone else's order id ("7" of "shop:ord:7") answer.
            return (order_id.startswith("np_") and ":" not in order_id
                    and src.rpartition(":")[2] == order_id)
        # Two-part scaffold row "nowpayments:<invoice or order>" (abc1d14):
        # it answers only when that part is a server-minted order id (np_…);
        # NOWPayments invoice ids are numeric and must never answer.
        return rest == order_id and order_id.startswith("np_")

    return matches


def find_mint_by_exact_source(sources: set[str]) -> dict | None:
    """The first mint row whose `source` IS one of `sources`, or None.

    For "has this exact purchase been delivered?": the WHOLE source string,
    prefix included, so a gift delivery and a card delivery of one session are
    named separately. `find_claim_code_by_source` above answers the looser
    "is there a mint carrying this id" and returns the most recent one.
    """
    if not sources:
        return None
    for row, source, delta in _mint_rows(lambda source: source in sources):
        return _mint_projection(row, source, delta)
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
            for row in _ledger_rows(f):
                email_value = row.get("email") or ""
                row_email = email_value.strip().lower() if isinstance(email_value, str) else ""
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


def revoke_credits_by_source(source_token: str, revoke_source: str,
                             mint_matches=None) -> list[dict]:
    """Revoke unused credits for every claim_code minted with a matching source.

    With `mint_matches`, a mint row belongs to the purchase when
    mint_matches(source) is true (crypto refunds pass
    nowpayments_mint_matcher(order_id)) and `source_token` is only required to
    be non-empty. Otherwise `source_token` is matched as a WHOLE
    colon-delimited part of the `source` field of original add_credits rows: "cs_abc" matches both `stripe:cs_abc`
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
            # Pass 1: which codes did the refunded purchase mint? Only rows
            # whose (string) source matches are parsed. The file is streamed
            # twice rather than held in memory under the ledger lock.
            with LEDGER_PATH.open() as f:
                rows = (r for r in _ledger_rows(f) if r.get("claim_code"))
                for row in rows:
                    src = row.get("source", "") or ""
                    if not isinstance(src, str):
                        continue
                    is_mint_of_it = (mint_matches(src) if mint_matches is not None
                                     else _source_has_token(src, source_token))
                    if is_mint_of_it and parse_delta(row) > 0:
                        matching_codes.add(row["claim_code"])
                    # If the same revoke_source has already been written for
                    # this code, mark it so we skip (idempotency).
                    if src == revoke_source:
                        already_revoked.add(row["claim_code"])
            # Pass 2: balances of THOSE codes only, so a bad row belonging to
            # another customer cannot stop this refund.
            with LEDGER_PATH.open() as f:
                for row in _ledger_rows(f):
                    code = row.get("claim_code")
                    if code and code in matching_codes:
                        balances[code] = balances.get(code, 0) + parse_delta(row)

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
            current = _scan(only=claim_code).get(claim_code, 0)
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
