#!/usr/bin/env python3
"""subscriptions.py — Personal-tier subscription state, derived from Stripe.

Data model: append-only JSONL of subscription events. Each row has
(stripe_customer, email, status, current_period_end). Latest event
per customer wins.

A separate stripe_customer → email map is maintained because Stripe
subscription event payloads carry the customer ID but not the email.
We capture (customer, email) at checkout.session.completed time when
the email IS in the payload, then subsequent subscription.* events
look up email by customer.

Public API:
    record_customer_email(stripe_customer, email) -> None
    record_subscription_event(stripe_customer, status, current_period_end, sub_id) -> None
    is_active(email) -> bool
    status_for(email) -> dict | None
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_lock import locked  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
SUB_LEDGER = Path(os.environ.get("ORPHO_SUB_LEDGER", str(DATA_DIR / "subscriptions.jsonl")))
CUSTOMER_MAP = Path(os.environ.get("ORPHO_CUSTOMER_MAP", str(DATA_DIR / "stripe_customer_emails.jsonl")))

ACTIVE_STATUSES = {"active", "trialing"}


def _now_unix() -> float:
    return datetime.now(timezone.utc).timestamp()


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(path: Path, row: dict) -> None:
    with locked(path, mode="a", exclusive=True) as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _read_all(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def record_customer_email(stripe_customer: str, email: str) -> None:
    if not stripe_customer or not email:
        return
    _append(CUSTOMER_MAP, {
        "ts": _iso(),
        "stripe_customer": stripe_customer,
        "email": email,
    })


def _email_for_customer(stripe_customer: str) -> str | None:
    rows = _read_all(CUSTOMER_MAP)
    latest = None
    for row in rows:
        if row.get("stripe_customer") == stripe_customer:
            latest = row
    return latest.get("email") if latest else None


def record_subscription_event(
    stripe_customer: str,
    status: str,
    current_period_end: float | None,
    sub_id: str = "",
    event_type: str = "",
    cancel_at_period_end: bool = False,
) -> None:
    if not stripe_customer or not status:
        return
    _append(SUB_LEDGER, {
        "ts": _iso(),
        "event_type": event_type,
        "stripe_customer": stripe_customer,
        "stripe_sub": sub_id,
        "email": _email_for_customer(stripe_customer) or "",
        "status": status,
        "current_period_end": current_period_end,
        "cancel_at_period_end": cancel_at_period_end,
    })


def _customers_for_email(email: str) -> set[str]:
    """Return every stripe_customer ID ever mapped to this email.

    The customer→email map is the source of truth for the email link;
    subscription events sometimes arrive BEFORE that mapping is written
    (Stripe dispatch order is not guaranteed), so the sub row's own
    `email` field can be empty even though the customer is real.
    """
    out: set[str] = set()
    if not email:
        return out
    for row in _read_all(CUSTOMER_MAP):
        if row.get("email") == email and row.get("stripe_customer"):
            out.add(row["stripe_customer"])
    return out


def _latest_for_email(email: str) -> dict | None:
    if not email:
        return None
    rows = _read_all(SUB_LEDGER)
    customers = _customers_for_email(email)
    latest = None
    for row in rows:
        # Match by stored email first, falling back to the customer→email
        # map so out-of-order events (subscription.created before
        # checkout.session.completed) still resolve correctly.
        row_email = row.get("email")
        row_customer = row.get("stripe_customer")
        if row_email == email or (not row_email and row_customer in customers):
            latest = row
    return latest


def status_for(email: str) -> dict | None:
    return _latest_for_email(email)


def stripe_subscription_id_for(email: str) -> str:
    """Return the most recently seen Stripe sub_xxx id for this email."""
    latest = _latest_for_email(email)
    return (latest or {}).get("stripe_sub", "") or ""


def is_active(email: str) -> bool:
    latest = _latest_for_email(email)
    if not latest:
        return False
    status = latest.get("status", "")
    if status not in ACTIVE_STATUSES:
        return False
    end = latest.get("current_period_end")
    if end is None:
        # No period end given (e.g., trial without explicit end): treat as active.
        return True
    try:
        return float(end) > _now_unix()
    except (TypeError, ValueError):
        return False
