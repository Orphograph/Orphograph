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
    is_past_due(email) -> bool
    status_for(email) -> dict | None
    subscriptions_for(email) -> list[dict]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_lock import locked  # noqa: E402
from email_fold import fold_email  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
SUB_LEDGER = Path(os.environ.get("ORPHO_SUB_LEDGER", str(DATA_DIR / "subscriptions.jsonl")))
CUSTOMER_MAP = Path(os.environ.get("ORPHO_CUSTOMER_MAP", str(DATA_DIR / "stripe_customer_emails.jsonl")))

ACTIVE_STATUSES = {"active", "trialing"}
# Stripe statuses a subscription never leaves. It bills nothing more, and
# Stripe refuses to update it, so there is nothing left to cancel.
ENDED_STATUSES = {"canceled", "incomplete_expired"}
# The Stripe status of a subscription whose payment failed and is still being
# retried. It gives no access, but it still exists at Stripe: it can still be
# cancelled, and a retry that succeeds is charged. With nothing active it is
# the subscription the account is on (founder decision 2026-09-28). Only this
# status: `incomplete` and `unpaid` are not preferred (see _current_row).
PAST_DUE_STATUS = "past_due"

# gdpr.delete_for_email appends a row carrying this event and the email to
# both ledgers this module reads. It is recognised here by its shape rather
# than by asking gdpr, because gdpr imports this module.
DELETED_EVENT = "email_deleted"


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


def _is_deletion(row: dict) -> bool:
    return row.get("event") == DELETED_EVENT and bool(row.get("email"))


def _links() -> tuple[dict[str, str], dict[str, set[str]], set[tuple[str, str]]]:
    """Read the customer map once, deletions included.

    Returns (current, since_deleted, severed):
      current        customer -> the email it resolves to now
      since_deleted  email -> customers mapped to it since it was last deleted
      severed        (customer, email) pairs a deletion of that email cut

    A severed pair stays cut. Stripe makes a new customer for every checkout
    (create_checkout_session never sends one), so the same customer mapped to
    the deleted email again can only be a late event about the deleted
    account's own old checkout: the webhook writes the mapping before it
    notices the session was already delivered. Letting that re-link brought
    the old subscription back for whoever holds the address now. A new
    customer id mapped after the deletion is a new subscription, and links."""
    current: dict[str, str] = {}
    since_deleted: dict[str, set[str]] = {}
    severed: set[tuple[str, str]] = set()
    for row in _read_all(CUSTOMER_MAP):
        # Stripe keeps the case the buyer typed; sign-in keeps its own. One
        # mailbox is one key (founder decision 2026-09-27, 3A).
        email = fold_email(row.get("email"))
        if not email:
            continue
        if _is_deletion(row):
            for customer in since_deleted.pop(email, set()):
                severed.add((customer, email))
                if current.get(customer) == email:
                    del current[customer]
            continue
        customer = row.get("stripe_customer")
        if not customer or (customer, email) in severed:
            continue
        since_deleted.setdefault(email, set()).add(customer)
        current[customer] = email
    return current, since_deleted, severed


def _email_for_customer(stripe_customer: str) -> str | None:
    """The email a Stripe customer is linked to, or None.

    A deletion of that email unlinks the customer for good (see _links).
    Without this, the deleted account's own subscription events (Stripe
    keeps sending them until the subscription ends) were stamped with the
    deleted email again, and the account's subscription came back for
    whoever signed in with that address next."""
    if not stripe_customer:
        return None
    return _links()[0].get(stripe_customer)


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


def _customer_links(email: str) -> tuple[set[str], set[str]]:
    """(linked, unlinked) stripe_customer IDs for this email.

    Linked: every customer mapped to the email since it was last deleted.
    Unlinked: customers a deletion of the email cut off. They stay cut off.
    """
    email = fold_email(email)
    if not email:
        return set(), set()
    _current, since_deleted, severed = _links()
    return (set(since_deleted.get(email, set())),
            {customer for customer, cut in severed if cut == email})


def _customers_for_email(email: str) -> set[str]:
    """Return every stripe_customer ID mapped to this email since it was
    last deleted.

    The customer→email map is the source of truth for the email link;
    subscription events sometimes arrive BEFORE that mapping is written
    (Stripe dispatch order is not guaranteed), so the sub row's own
    `email` field can be empty even though the customer is real.
    """
    return _customer_links(email)[0]


def _rows_for_email(email: str) -> list[dict]:
    """Every subscription row that describes whoever holds this address now,
    oldest first. Addresses compare folded (email_fold.fold_email)."""
    email = fold_email(email)
    if not email:
        return []
    rows = _read_all(SUB_LEDGER)
    customers, unlinked = _customer_links(email)
    matched: list[dict] = []
    for row in rows:
        # Match by stored email first, falling back to the customer→email
        # map so out-of-order events (subscription.created before
        # checkout.session.completed) still resolve correctly.
        row_email = fold_email(row.get("email"))
        row_customer = row.get("stripe_customer")
        if _is_deletion(row) and row_email == email:
            # Nothing written before the deletion describes whoever holds
            # this address now; a later sign-in with it is a new account.
            # Unlinking customers does not cover this: a row can carry the
            # email for a customer the map has no line for.
            matched = []
            continue
        if row_customer and row_customer in unlinked:
            # The deleted account's customer. Its events can still carry the
            # email: a webhook that looked the email up just before the
            # deletion landed appends just after it.
            continue
        if row_email == email or (not row_email and row_customer in customers):
            matched.append(row)
    return matched


def _latest_for_email(email: str) -> dict | None:
    rows = _rows_for_email(email)
    return rows[-1] if rows else None


def subscriptions_for(email: str) -> list[dict]:
    """The newest row of each Stripe subscription this address holds, in the
    order they first appeared.

    One address can pay for more than one subscription: checkout does not
    ask whether the buyer already subscribes, and Stripe makes a new customer
    for each checkout. The newest row of all (status_for) says nothing about
    the others, so anything that must stop all the billing, like account
    deletion, reads this instead. A row with no subscription id names
    nothing that could be cancelled, so it is left out."""
    latest: dict[str, dict] = {}
    for row in _rows_for_email(email):
        sub_id = row.get("stripe_sub")
        if sub_id:
            latest[sub_id] = row
    return list(latest.values())


def _row_is_active(row: dict) -> bool:
    if row.get("status", "") not in ACTIVE_STATUSES:
        return False
    end = row.get("current_period_end")
    if end is None:
        # No period end given (e.g., trial without explicit end): treat as active.
        return True
    try:
        return float(end) > _now_unix()
    except (TypeError, ValueError):
        return False


def active_subscription_ids(email: str) -> list[str]:
    """The Stripe subscriptions this address holds that are active now, each
    judged by its own newest row (see subscriptions_for)."""
    return [row["stripe_sub"] for row in subscriptions_for(email) if _row_is_active(row)]


def row_is_past_due(row: dict) -> bool:
    """Does this row say a subscription is past due? A row with no
    subscription id names nothing Cancel could reach, so it does not count."""
    return row.get("status", "") == PAST_DUE_STATUS and bool(row.get("stripe_sub"))


def _current_row(email: str) -> dict | None:
    """The row describing the subscription this address is on now. The first
    of these that finds one decides:

      1. the most recently updated ACTIVE subscription;
      2. the most recently updated PAST-DUE subscription;
      3. the newest row of all.

    Each subscription is judged by its own newest row. Judging the newest row
    across all of them let one subscription's cancellation hide another that
    is still being paid for (what cancelling a duplicate produced), and every
    reader must agree on the choice: the page, cancel, reactivate, the refund
    request and the support lookup (found in review, 2026-09-27).

    The second is there for the same reason (founder decision 2026-09-28).
    With nothing active, the newest row of all could be the cancellation of
    one subscription, or an abandoned attempt, while another was past due.
    Stripe keeps retrying a past-due payment, and Cancel could not reach it.

    It is past due and nothing wider. Preferring any subscription still open
    at Stripe was tried and removed: an `incomplete` row never ages out here,
    so an abandoned attempt spoke for the account. `unpaid` is left where it
    was; the decision covers past due only.

    A row with no subscription id (hand-written; the webhook always records
    the id) names no subscription of its own, so it keeps the meaning it
    always had: it speaks only while it is the newest row of all."""
    rows = _rows_for_email(email)
    newest: dict[str, tuple[int, dict]] = {}
    for i, row in enumerate(rows):
        newest[row.get("stripe_sub") or ""] = (i, row)
    last = len(rows) - 1
    speaking = [pair for sub, pair in newest.items() if sub or pair[0] == last]
    for in_tier in (_row_is_active, row_is_past_due):
        tier = [pair for pair in speaking if in_tier(pair[1])]
        if tier:
            return max(tier, key=lambda pair: pair[0])[1]
    return rows[-1] if rows else None


def status_for(email: str) -> dict | None:
    return _current_row(email)


def stripe_subscription_id_for(email: str) -> str:
    """The Stripe sub_xxx id this address is on now (see _current_row)."""
    return (_current_row(email) or {}).get("stripe_sub", "") or ""


def is_active(email: str) -> bool:
    """Does this address hold ANY active subscription now? (see _current_row)"""
    current = _current_row(email)
    return bool(current) and _row_is_active(current)


def is_past_due(email: str) -> bool:
    """Is the subscription this address is on now past due? (see _current_row)

    It gives no access: is_active stays False. It says there is a subscription
    Stripe is still trying to charge, which Cancel can reach."""
    return row_is_past_due(_current_row(email) or {})
