#!/usr/bin/env python3
"""gdpr.py — data export + delete for the authed user.

Honors the Privacy Policy commitment that EU/UK/CA users can request
their data or delete it. Append-only ledgers mean "delete" is a
tombstone event rather than a row-level wipe — we can't safely remove
historical rows without risking integrity of multi-event aggregates,
so we append a `deleted_email` event in every relevant ledger and the
read paths honor it by treating the email as zeroed-out.

Public API:
    export_for_email(email) -> dict
    delete_for_email(email) -> dict  # counts how many ledgers touched
    is_email_deleted(email) -> bool
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import auth
import credits
import subscriptions
from file_lock import locked

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
DELETIONS_LEDGER = Path(os.environ.get("ORPHO_DELETIONS", str(DATA_DIR / "gdpr_deletions.jsonl")))


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _filter_by_email(rows: list[dict], email: str) -> list[dict]:
    return [r for r in rows if r.get("email") == email]


def export_for_email(email: str) -> dict:
    """Return everything we hold for this email. Read-only."""
    if not email:
        return {"email": "", "items": {}}
    return {
        "email": email,
        "exported_at": _iso_now(),
        "items": {
            "credit_ledger": _filter_by_email(_read_rows(credits.LEDGER_PATH), email),
            "subscription_ledger": _filter_by_email(_read_rows(subscriptions.SUB_LEDGER), email),
            "customer_email_map": _filter_by_email(_read_rows(subscriptions.CUSTOMER_MAP), email),
            "auth_tokens": _filter_by_email(_read_rows(auth.TOKEN_LEDGER), email),
            "deletion_events": _filter_by_email(_read_rows(DELETIONS_LEDGER), email),
        },
        "notes": (
            "Sessions are stored as SHA-256 hashes keyed by random session IDs, "
            "not by email — we have no link back to your email for individual "
            "sessions. Receipt files (receipts/<id>/receipt.json) carry an "
            "HMAC-derived sub_id, not the email itself."
        ),
    }


def delete_for_email(email: str) -> dict:
    """Tombstone the email across every ledger. Append-only — we don't
    modify historical rows. Read paths must honor the deletion event."""
    if not email:
        return {"email": "", "events_appended": 0}
    targets = [
        ("credit_ledger", credits.LEDGER_PATH),
        ("subscription_ledger", subscriptions.SUB_LEDGER),
        ("customer_email_map", subscriptions.CUSTOMER_MAP),
        ("auth_tokens", auth.TOKEN_LEDGER),
    ]
    events_appended = 0
    for ledger_name, path in targets:
        with locked(path, mode="a", exclusive=True) as f:
            f.write(json.dumps({
                "ts": _iso_now(),
                "event": "email_deleted",
                "email": email,
                "ledger": ledger_name,
            }, separators=(",", ":")) + "\n")
            events_appended += 1
    # Master record of the deletion request.
    with locked(DELETIONS_LEDGER, mode="a", exclusive=True) as f:
        f.write(json.dumps({
            "ts": _iso_now(),
            "email": email,
            "ledgers_touched": [n for n, _ in targets],
        }, separators=(",", ":")) + "\n")
    return {"email": email, "events_appended": events_appended}


def is_email_deleted(email: str) -> bool:
    if not email:
        return False
    for row in _read_rows(DELETIONS_LEDGER):
        if row.get("email") == email:
            return True
    return False
