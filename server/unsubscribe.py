#!/usr/bin/env python3
"""unsubscribe.py — global suppression list for marketing email.

Compliance:
    CAN-SPAM (US 15 USC § 7704(a)(4)): unsubscribe must work within 10 days.
        We honor it instantly.
    EU PECR + GDPR Art. 21: right to object to direct marketing. Instant.
    CASL (Canada): consent withdrawal must be honored within 10 business days.
    LGPD (Brazil) Art. 18(IX): right to revoke consent. Instant.
    RFC 8058 / Gmail+Yahoo 2024 bulk-sender rules: one-click unsubscribe.

Mechanism: append-only suppression ledger. An address may have several
rows; any one of them suppresses it. mailer.py marketing path
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


def _well_formed(email: str) -> bool:
    return "@" in email and len(email) <= 320


def ensure_writable() -> None:
    """Raise SuppressionUnavailable unless the ledger can be opened for
    appending right now. Writes nothing and takes no lock.

    Written for the unsubscribe POST when `add` answered an address already
    suppressed without opening the ledger: with a ledger that could not be
    opened a new address got 503 and a suppressed one got success, which
    told anyone which one it was. The POST now passes `add(reappend=True)`,
    which opens the ledger and writes the row for both addresses, so for the
    POST this is a second check of what `add` does next.

    No lock, because only whether the file can be opened matters here; `add`
    takes the lock when it writes. Taking it here made a POST wait for
    whoever held it (review, 2026-09-27). The steps mirror file_lock.locked
    up to the lock and must stay in step with it.

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


def _ends_mid_line(path: Path) -> bool:
    """Does the file end without a newline (a torn last line)?"""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return False
            f.seek(-1, os.SEEK_END)
            return f.read(1) != b"\n"
    except OSError:
        return False


def add(email: str, source: str = "user", reappend: bool = False) -> bool:
    """Mark an email as unsubscribed. Returns whether it was new: a second
    call returns False.

    `reappend` is for a caller that answers a stranger (the unsubscribe
    POST): it appends the row whether or not the address already has one.
    Without it, an address already suppressed is answered before the ledger
    is touched, so on a full volume it got success where a new one got
    SuppressionUnavailable, which told anyone which one it was. Any row
    suppresses, so a second row changes nothing a reader sees, and the write
    is the same row a new address makes, under the same lock: a failed write
    raises for both.

    The ledger is only ever appended to. 0ce9aa3 got the same answer by
    appending blanks and cutting them off with ftruncate, and a row that a
    writer taking no lock (scripts/reply_router.py) appended in between could
    be cut off with them. The cost of appending is a row for every POST."""
    email = _norm(email)
    if not _well_formed(email):
        return False
    new = not is_unsubscribed(email)
    if not new and not reappend:
        return False
    row = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "email": email,
        "source": source,
    }, separators=(",", ":")) + "\n"
    try:
        with locked(SUPPRESS_PATH, mode="a", exclusive=True) as f:
            # A write that failed part-way leaves a line with no newline. This
            # row would be glued onto it and never read back, while the person
            # is told it worked. Start on a fresh line.
            if _ends_mid_line(SUPPRESS_PATH):
                row = "\n" + row
            f.write(row)
            # Onto the disk while the lock is held. `locked` lets go of the
            # lock before it closes the file, and closing is where a buffered
            # row would otherwise be written: after the next writer has
            # already looked for a torn last line.
            f.flush()
    except OSError as e:
        # Not recorded. The caller must be able to SAY so: an OSError escaping
        # here closed the socket with no response, and someone unsubscribing
        # could not tell whether it had worked.
        raise SuppressionUnavailable(f"could not record the unsubscribe: {e}") from e
    return new


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
