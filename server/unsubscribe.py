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
import sys
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
    one it was. That case is all this closes. The file opening and the write
    itself failing (a full volume) is closed by `add(prove_write=True)`,
    which the POST passes: a suppressed address then tries a write as long as
    a new one's row, and is refused where the row would be.

    No lock, because only whether the file can be opened matters here; `add`
    takes the lock when it writes. Taking it made a POST for an address that
    is already suppressed, the one mailbox providers retry, wait for whoever
    held it (review, 2026-09-27). With prove_write that POST writes, so it
    waits in `add` for the lock as a new address does; this check still does
    not. The steps mirror file_lock.locked up to the lock and must stay in
    step with it.

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


def _prove_append(f, length: int) -> None:
    """Append `length` blank bytes to the open ledger, then cut them off.

    As long as the row, not one byte: a full volume still has the unused rest
    of the file's last block, and one byte fits there long after a row has
    stopped fitting (measured on a full volume, 2026-09-28). The answer has
    to be the one a row would get.

    Cut off again, because nothing limits how often a stranger may POST and
    the ledger is read through on every marketing send. Only when the file is
    exactly that much longer than it was: a writer that does not take the
    lock (scripts/reply_router.py) may have appended in between, and then the
    blanks stay rather than its row being cut. Blanks also stay when the
    write fails part-way. Every reader skips them, and `_ends_mid_line`
    starts the next row on a fresh line.

    Not covered: a filesystem that reports a full volume only when the file
    is closed (a network mount). The blanks are gone again by then."""
    before = os.fstat(f.fileno()).st_size
    f.write(" " * (length - 1) + "\n")
    f.flush()
    try:
        if os.fstat(f.fileno()).st_size == before + length:
            os.ftruncate(f.fileno(), before)
    except OSError as e:
        # The write is proven, which is what was asked. The blanks stay.
        print(f"[unsubscribe] proof left in the ledger: {type(e).__name__}",
              file=sys.stderr, flush=True)


def add(email: str, source: str = "user", prove_write: bool = False) -> bool:
    """Mark an email as unsubscribed. Idempotent — second call returns False.

    `prove_write` is for a caller that answers a stranger (the unsubscribe
    POST). With nothing to record this returned before it touched the ledger,
    so on a full volume an address already suppressed got success and a new
    one SuppressionUnavailable, which told anyone which one it was. With
    prove_write it tries the write a new address would make, under the same
    lock, and a failed write raises for both. The ledger is left as it was."""
    new = _is_new(email)
    if not new and not prove_write:
        return False
    email = _norm(email)
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
            if new:
                f.write(row)
            else:
                _prove_append(f, len(row))
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
