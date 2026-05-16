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
    revoke(email) -> None
    email_for_key(key) -> str | None    # validate + identify
    active_key_prefix(email) -> str     # for UI display ("orpho_xxxxx…")
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from file_lock import locked

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
KEY_LEDGER = Path(os.environ.get("ORPHO_API_KEYS", str(DATA_DIR / "api_keys.jsonl")))


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


def issue(email: str) -> str:
    """Mint a fresh API key for the email. Supersedes any prior active key.
    Returns the plaintext key once — caller must surface it to the user
    immediately because we cannot retrieve it later.
    """
    if not email:
        raise ValueError("email required")
    # Revoke any existing active key for this email — one key per user.
    for row in _read_rows():
        if row.get("email") == email and row.get("event") == "issued":
            # check whether it was revoked subsequently
            still_active = True
            for later in _read_rows():
                if later.get("key_hash") == row.get("key_hash") and later.get("event") == "revoked":
                    still_active = False
                    break
            if still_active:
                _append({
                    "ts": _iso(),
                    "event": "revoked",
                    "key_hash": row["key_hash"],
                    "email": email,
                    "reason": "superseded by new key",
                })
    plaintext = "orpho_" + secrets.token_urlsafe(24)
    _append({
        "ts": _iso(),
        "event": "issued",
        "key_hash": _hash(plaintext),
        "key_prefix": plaintext[:14],  # first 14 chars for UI display
        "email": email,
    })
    return plaintext


def revoke(email: str) -> bool:
    """Revoke the user's active API key. Returns True if a key was revoked."""
    if not email:
        return False
    for row in reversed(_read_rows()):
        if row.get("email") == email and row.get("event") == "issued":
            kh = row["key_hash"]
            # Check it hasn't already been revoked.
            already_revoked = any(
                r.get("key_hash") == kh and r.get("event") == "revoked"
                for r in _read_rows()
            )
            if already_revoked:
                return False
            _append({
                "ts": _iso(),
                "event": "revoked",
                "key_hash": kh,
                "email": email,
                "reason": "user revoked",
            })
            return True
    return False


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
    return issued_row.get("email")


def active_key_prefix(email: str) -> str:
    """Return the prefix of the user's active key for UI display (so we
    can show "orpho_xxxxx…" without having the plaintext). Empty if none."""
    if not email:
        return ""
    rows = _read_rows()
    # Find latest issued key for email that isn't revoked.
    issued = [r for r in rows if r.get("email") == email and r.get("event") == "issued"]
    for row in reversed(issued):
        kh = row["key_hash"]
        revoked = any(
            r.get("key_hash") == kh and r.get("event") == "revoked"
            for r in rows
        )
        if not revoked:
            return row.get("key_prefix", "")
    return ""
