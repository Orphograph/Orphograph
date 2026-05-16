#!/usr/bin/env python3
"""onboarding.py — 5-email drip sequence triggered on a customer's first anchor.

Sequence (UTC time deltas from first anchor):
    step 0  — Day 0  Receipt (transactional). Handled inline by mailer.send_receipt_email.
    step 1  — Day 1  Use-case examples (marketing).
    step 2  — Day 3  Plugin install nudge (marketing).
    step 3  — Day 7  Hypothetical case study (marketing).
    step 4  — Day 14 Pack upgrade offer (marketing).

Design:
- State is a plain JSONL append-only ledger at data/onboarding_state.jsonl so it
  survives across processes / fly machines (matches credits.py + unsubscribe.py).
- schedule_sequence(email) is idempotent — calling it twice for the same email
  is a no-op (so the anchor hot path can call it unconditionally on first
  anchor without an explicit "do we have this email yet" check).
- due_emails() returns (email, step) pairs that should be sent right now.
- mark_sent(email, step) appends a 'sent' row so the same step is never resent.
- If the user unsubscribes via the marketing-suppression list, due_emails()
  filters them out automatically. We never branch the sequence on PII; the
  ledger keeps only (email, step, timestamp).

Privacy: we store the email + the scheduled-at timestamp. No filenames,
no IPs, no hashes, no client_labels. The marketing-suppression list lives
in unsubscribe.py — we consult it but do not duplicate its data here.

Public API:
    schedule_sequence(email: str) -> bool          # True on first schedule
    due_emails(now: datetime | None) -> list[tuple[str, int]]
    mark_sent(email: str, step: int) -> None
    stats() -> dict                                # founder-token-gated summary
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from file_lock import locked
import unsubscribe

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get(
    "ORPHO_DATA_DIR",
    str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT),
))
STATE_PATH = Path(os.environ.get(
    "ORPHO_ONBOARDING_STATE",
    str(DATA_DIR / "onboarding_state.jsonl"),
))

# Day offsets per marketing step. step 0 (receipt) is sent inline at anchor
# time and is not represented here — this module only manages the drip.
STEP_OFFSETS_DAYS = {
    1: 1,
    2: 3,
    3: 7,
    4: 14,
}
SEQUENCE_STEPS = sorted(STEP_OFFSETS_DAYS.keys())

_lock = threading.RLock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def _iter_rows():
    """Yield each JSON row from the state ledger. Silently skips corrupt lines."""
    if not STATE_PATH.exists():
        return
    try:
        with STATE_PATH.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _append(row: dict) -> None:
    with _lock:
        with locked(STATE_PATH, mode="a", exclusive=True) as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _state_for(email: str) -> dict:
    """Return {'started_at': str|None, 'sent_steps': set[int]} for one email."""
    email = _norm(email)
    started_at: str | None = None
    sent: set[int] = set()
    for row in _iter_rows():
        if _norm(row.get("email", "")) != email:
            continue
        kind = row.get("kind")
        if kind == "start" and started_at is None:
            started_at = row.get("started_at") or row.get("ts")
        elif kind == "sent":
            try:
                sent.add(int(row.get("step", -1)))
            except (TypeError, ValueError):
                continue
    return {"started_at": started_at, "sent_steps": sent}


def schedule_sequence(email: str) -> bool:
    """Record an email's first-anchor timestamp. Idempotent.

    Returns True if a new schedule was written, False if one already existed.
    Safe to call on every anchor — repeat calls are a fast no-op.
    """
    email = _norm(email)
    if "@" not in email or len(email) > 320:
        return False
    with _lock:
        existing = _state_for(email)
        if existing["started_at"]:
            return False
        _append({
            "kind": "start",
            "email": email,
            "started_at": _now().isoformat(timespec="seconds"),
        })
        return True


def _due_step(started_at_iso: str, sent: set[int], now: datetime) -> int | None:
    """Return the smallest step that is due AND not yet sent. None if nothing due."""
    try:
        started_at = datetime.fromisoformat(started_at_iso)
    except ValueError:
        return None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    for step in SEQUENCE_STEPS:
        if step in sent:
            continue
        offset_days = STEP_OFFSETS_DAYS[step]
        if now - started_at >= timedelta(days=offset_days):
            return step
    return None


def due_emails(now: datetime | None = None) -> list[tuple[str, int]]:
    """Return (email, step) pairs that are due to send now.

    - Filters out users on the marketing-suppression list (unsubscribe.py).
    - Returns at most one step per email per call; the next call after
      mark_sent will pick up the following step if also due.
    """
    if now is None:
        now = _now()
    # Build per-email aggregate by walking the ledger once.
    agg: dict[str, dict] = {}
    for row in _iter_rows():
        email = _norm(row.get("email", ""))
        if not email:
            continue
        bucket = agg.setdefault(email, {"started_at": None, "sent_steps": set()})
        kind = row.get("kind")
        if kind == "start" and bucket["started_at"] is None:
            bucket["started_at"] = row.get("started_at") or row.get("ts")
        elif kind == "sent":
            try:
                bucket["sent_steps"].add(int(row.get("step", -1)))
            except (TypeError, ValueError):
                pass

    out: list[tuple[str, int]] = []
    for email, st in agg.items():
        if not st["started_at"]:
            continue
        if unsubscribe.is_unsubscribed(email):
            continue
        step = _due_step(st["started_at"], st["sent_steps"], now)
        if step is not None:
            out.append((email, step))
    # Stable ordering: oldest-started-first → fairness.
    out.sort()
    return out


def mark_sent(email: str, step: int) -> None:
    """Record that `step` has been sent for `email`. Re-running is safe (we
    de-dup at due_emails time on set membership)."""
    email = _norm(email)
    if "@" not in email:
        return
    if step not in STEP_OFFSETS_DAYS:
        raise ValueError(f"invalid step {step}")
    _append({
        "kind": "sent",
        "email": email,
        "step": int(step),
        "ts": _now().isoformat(timespec="seconds"),
    })


def stats() -> dict:
    """Aggregate counts only — never individual emails.

    Returns:
        {
            "scheduled_total": N,
            "unsubscribed_in_sequence": N,
            "by_step_sent": {1: ..., 2: ..., 3: ..., 4: ...},
            "completed": N,            # sent all 4 marketing steps
            "in_progress": N,          # started but not yet completed + not unsub
        }
    """
    agg: dict[str, dict] = {}
    for row in _iter_rows():
        email = _norm(row.get("email", ""))
        if not email:
            continue
        bucket = agg.setdefault(email, {"started_at": None, "sent_steps": set()})
        kind = row.get("kind")
        if kind == "start" and bucket["started_at"] is None:
            bucket["started_at"] = row.get("started_at") or row.get("ts")
        elif kind == "sent":
            try:
                bucket["sent_steps"].add(int(row.get("step", -1)))
            except (TypeError, ValueError):
                pass

    by_step = {s: 0 for s in SEQUENCE_STEPS}
    completed = 0
    unsub_count = 0
    in_progress = 0
    scheduled_total = 0
    for email, st in agg.items():
        if not st["started_at"]:
            continue
        scheduled_total += 1
        for s in st["sent_steps"]:
            if s in by_step:
                by_step[s] += 1
        unsub = unsubscribe.is_unsubscribed(email)
        if unsub:
            unsub_count += 1
        all_sent = all(s in st["sent_steps"] for s in SEQUENCE_STEPS)
        if all_sent:
            completed += 1
        elif not unsub:
            in_progress += 1
    return {
        "scheduled_total": scheduled_total,
        "unsubscribed_in_sequence": unsub_count,
        "by_step_sent": by_step,
        "completed": completed,
        "in_progress": in_progress,
    }
