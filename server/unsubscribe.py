#!/usr/bin/env python3
"""unsubscribe.py — global suppression list for marketing email.

Compliance:
    CAN-SPAM (US 15 USC § 7704(a)(4)): unsubscribe must work within 10 days.
        We honor it instantly.
    EU PECR + GDPR Art. 21: right to object to direct marketing. Instant.
    CASL (Canada): consent withdrawal must be honored within 10 business days.
    LGPD (Brazil) Art. 18(IX): right to revoke consent. Instant.
    RFC 8058 / Gmail+Yahoo 2024 bulk-sender rules: one-click unsubscribe.

Mechanism: append-only suppression ledger. mailer.py marketing path
consults is_unsubscribed(email) before sending. Transactional email
(receipts, sign-in links, pack codes) is exempt — CAN-SPAM §7704(a)(5)(A)
permits transactional mail without an unsubscribe.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from file_lock import locked


class SuppressionUnavailable(RuntimeError):
    """The consent ledger could not be read, so consent is UNKNOWN.

    Never collapse this to "not suppressed". A read failure used to return
    False, i.e. "go ahead and email them" — and on this system unreadable
    /data files are not hypothetical (root-owned api_keys.jsonl 2026-07-27,
    webhooks.jsonl 2026-07-28). The consequence of getting this wrong is
    mailing people who unsubscribed or who filed a spam complaint.
    """


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
SUPPRESS_PATH = Path(os.environ.get("ORPHO_SUPPRESSIONS", str(DATA_DIR / "suppressions.jsonl")))


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def add(email: str, source: str = "user") -> bool:
    """Mark an email as unsubscribed. Idempotent — second call returns False."""
    email = _norm(email)
    if "@" not in email or len(email) > 320:
        return False
    if is_unsubscribed(email):
        return False
    with locked(SUPPRESS_PATH, mode="a", exclusive=True) as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "email": email,
            "source": source,
        }, separators=(",", ":")) + "\n")
    return True


def is_unsubscribed(email: str) -> bool:
    email = _norm(email)
    if not SUPPRESS_PATH.exists():
        return False
    try:
        with SUPPRESS_PATH.open() as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if _norm(row.get("email", "")) == email:
                    return True
    except OSError as e:
        # Fail LOUD, not open. The caller decides — consent-based mail must
        # skip the recipient; a transactional receipt may still go out.
        raise SuppressionUnavailable(f"unsubscribe ledger unreadable: {e}") from e
    return False
