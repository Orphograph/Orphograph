#!/usr/bin/env python3
"""api_keys.py — Creator-tier API key issuance, validation, revocation.

A Creator-tier subscriber generates one API key (rotatable) bound
to their email. Anchor requests carrying the key in the
`X-Orpho-Api-Key` header bypass the rate limit and are tagged
`api:<key_prefix>` in the receipt source field.

Storage: append-only JSONL of (issued, revoked, last_used) events.
Keys stored only as SHA-256 hashes — never plaintext.

Public API:
    issue(email) -> str                 # returns the plaintext key once
    revoke(email) -> bool               # True if any live key was revoked
    email_for_key(key) -> str | None    # validate + identify
    active_key_prefix(email) -> str     # for UI display ("orpho_xxxxx…")
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from email_fold import fold_email, needs_lowercase
from file_lock import locked

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
KEY_LEDGER = Path(os.environ.get("ORPHO_API_KEYS", str(DATA_DIR / "api_keys.jsonl")))

# issue() and revoke() each read the ledger, decide which keys are live, and
# append. Without one critical section around all three, a double click on
# "Generate key" let both requests see the same old key, revoke it twice and
# each append a new key: two live keys, 10 times out of 10 (2026-09-25). The
# threading lock covers handler threads in one process; the file lock covers
# other processes and machines that share the data volume.
_write_lock = threading.Lock()


def _lock_path() -> Path:
    """Sibling .lock of the key ledger, held across read, revoke and append.
    A separate file from the ledger because _append flocks the ledger itself,
    and a second flock on the same file from this process would wait on the
    first forever. Resolved at call time so an override of KEY_LEDGER moves
    the lock with it."""
    return KEY_LEDGER.with_suffix(KEY_LEDGER.suffix + ".lock")


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_rows() -> list[dict]:
    if not KEY_LEDGER.exists():
        return []
    rows: list[dict] = []
    with KEY_LEDGER.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _append(row: dict) -> None:
    with locked(KEY_LEDGER, mode="a", exclusive=True) as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _live_keys(rows: list[dict], email: str) -> list[dict]:
    """The email's issued rows that no row revokes, oldest first.

    One pass over rows already read. issue() used to re-read the whole ledger
    once per key the account had ever held, so 2,000 rotations cost 41 s per
    call on a server thread. A key is dead once any row revokes it, which is
    what the old nested scan checked too.

    Rows match on fold_email, not the exact string. Sign-in keeps the case
    typed, so "Alice@x" and "alice@x" each held a live key, and a revoke from
    one spelling left the other key working, while the issuance limiter
    already counted both spellings as one account (2026-09-26)."""
    me = fold_email(email)
    if not me:
        return []
    revoked = {r.get("key_hash") for r in rows if r.get("event") == "revoked"}
    return [
        r for r in rows
        if r.get("event") == "issued" and fold_email(r.get("email")) == me
        and isinstance(r.get("key_hash"), str) and r["key_hash"] not in revoked
    ]


def issue(email: str) -> str:
    """Mint a fresh API key for the email. Supersedes every prior live key.
    Returns the plaintext key once — caller must surface it to the user
    immediately because we cannot retrieve it later.
    """
    if not email:
        raise ValueError("email required")
    plaintext = "orpho_" + secrets.token_urlsafe(24)
    with _write_lock, locked(_lock_path(), exclusive=True):
        # Revoke every live key for this email — one key per user. Every,
        # not the newest: a ledger written before this lock existed can hold
        # two live keys from one double click.
        for row in _live_keys(_read_rows(), email):
            _append({
                "ts": _iso(),
                "event": "revoked",
                "key_hash": row["key_hash"],
                "email": email,
                "reason": "superseded by new key",
            })
        _append({
            "ts": _iso(),
            "event": "issued",
            "key_hash": _hash(plaintext),
            "key_prefix": plaintext[:14],  # first 14 chars for UI display
            "email": email,
        })
    return plaintext


def revoke(email: str) -> bool:
    """Revoke every live API key of the user. Returns True if any was revoked.

    Looking only at the newest issued key let an older live key survive (the
    one a double click left behind) while revoke answered revoked:false from
    then on, so the user could not kill it at all."""
    if not email:
        return False
    with _write_lock, locked(_lock_path(), exclusive=True):
        live = _live_keys(_read_rows(), email)
        for row in live:
            _append({
                "ts": _iso(),
                "event": "revoked",
                "key_hash": row["key_hash"],
                "email": email,
                "reason": "user revoked",
            })
    return bool(live)


def prefix_issuers() -> dict[str, list[tuple[str, str]]]:
    """Every issued key's receipt prefix (its first 10 chars, what
    `source="api:<key[:10]>"` records) -> [(issued ts, owner email), ...].

    Rotated and revoked keys stay in: a receipt made with a key before it was
    revoked is still that key owner's. Read once per listing, not per row."""
    issuers: dict[str, list[tuple[str, str]]] = {}
    for row in _read_rows():
        if row.get("event") != "issued":
            continue
        kp = row.get("key_prefix")
        owner = row.get("email")
        if isinstance(kp, str) and len(kp) >= 10:
            ts = row.get("ts")
            issuers.setdefault(kp[:10], []).append((
                ts if isinstance(ts, str) else "",
                owner.lower() if isinstance(owner, str) else ""))
    return issuers


def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def prefix_owner(issued: list[tuple[str, str]], made_at) -> str | None:
    """The one account that held a key with this prefix when a receipt was
    made, or None when that is not exactly one account.

    Keys issued AFTER the receipt cannot have made it, so they do not count.
    Counting every key ever issued meant a later key that happened to share
    the 10-char prefix (4 random chars) made it ambiguous, and the rightful
    owner lost a legacy receipt from their vault for good. A row or receipt
    whose time cannot be read counts as possibly earlier, which can only
    deny, never grant."""
    made = _parse_ts(made_at)
    owners = set()
    for ts, owner in issued:
        t = _parse_ts(ts)
        if made is None or t is None or t <= made:
            owners.add(owner)
    return next(iter(owners)) if len(owners) == 1 else None


def email_for_key(key: str) -> str | None:
    """Return the email a key belongs to if the key is currently active,
    else None. Constant work cost regardless of validity."""
    if not key:
        return None
    kh = _hash(key)
    rows = _read_rows()
    issued_row = None
    revoked = False
    for row in rows:
        if row.get("key_hash") != kh:
            continue
        if row.get("event") == "issued":
            issued_row = row
        elif row.get("event") == "revoked":
            revoked = True
    if issued_row is None or revoked:
        return None
    # Issued before sign-in refused such a spelling (2026-10-03): its holder
    # would act, by email_id, as the address it lowercases to.
    if needs_lowercase(issued_row.get("email")):
        return None
    return issued_row.get("email")


def active_key_prefix(email: str) -> str:
    """Return the prefix of the user's active key for UI display (so we
    can show "orpho_xxxxx…" without having the plaintext). Empty if none."""
    if not email:
        return ""
    # The latest issued key for email that isn't revoked. Same one-pass scan
    # as issue(): this runs on every /api/me.
    live = _live_keys(_read_rows(), email)
    return live[-1].get("key_prefix", "") if live else ""
