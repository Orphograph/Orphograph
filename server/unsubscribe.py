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


def _is_new(email: str) -> bool:
    """A well-formed address that is not already suppressed."""
    email = _norm(email)
    if "@" not in email or len(email) > 320:
        return False
    return not is_unsubscribed(email)


def ensure_writable() -> None:
    """Raise SuppressionUnavailable unless the ledger can be opened for
    appending right now. Writes nothing and takes no lock.

    For the unsubscribe POST, which must refuse before `add`'s
    already-suppressed shortcut: with a ledger that could not be opened a new
    address got 503 and a suppressed one got success, which told anyone which
    one it was. That case is all this closes. Not covered: the file opens and
    the write itself fails (a full volume). `add` then still answers a
    suppressed address with success and a new one with 503.

    No lock, because only whether the file can be opened matters here; `add`
    takes the lock when it writes. Taking it made a POST for an address that
    is already suppressed, the one mailbox providers retry, wait for whoever
    held it (review, 2026-09-27). The steps mirror file_lock.locked up to the
    lock and must stay in step with it.

    Not file_lock.can_append: that is a pre-check that guesses, and it
    refuses a directory this process owns and `locked` repairs, so a one-click
    unsubscribe the writer would have recorded got 503 (review, 2026-09-27)."""
    path = SUPPRESS_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        with open(path, "a"):
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    except OSError as e:
        raise SuppressionUnavailable(f"suppression ledger is not writable: {e}") from e


def add(email: str, source: str = "user") -> bool:
    """Mark an email as unsubscribed. Idempotent — second call returns False."""
    if not _is_new(email):
        return False
    email = _norm(email)
    try:
        with locked(SUPPRESS_PATH, mode="a", exclusive=True) as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "email": email,
                "source": source,
            }, separators=(",", ":")) + "\n")
    except OSError as e:
        # Not recorded. The caller must be able to SAY so: an OSError escaping
        # here closed the socket with no response, and someone unsubscribing
        # could not tell whether it had worked.
        raise SuppressionUnavailable(f"could not record the unsubscribe: {e}") from e
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
