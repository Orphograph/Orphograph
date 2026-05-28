#!/usr/bin/env python3
"""analytics.py — founder-only revenue + churn analytics.

Aggregates data from:
  • stripe_processed_events.jsonl — completed Stripe events (subscriptions, charges)
  • subscriptions.json — active subscription state
  • credits.ledger — all credit transactions (Packs, refunds, grants)

Public API:
  metrics(days_back=90) -> dict
    Returns MRR, ARR, churn, customer counts, LTV estimates.

Exported for founder-only endpoints:
  GET /api/founder/metrics — JSON metrics for dashboard
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
STRIPE_EVENTS_PATH = DATA_DIR / "stripe_processed_events.jsonl"
SUBSCRIPTIONS_PATH = DATA_DIR / "subscriptions.json"
CREDITS_LEDGER = DATA_DIR / "credits.ledger" if (DATA_DIR / "credits.ledger").is_file() else DATA_DIR / "ledger.jsonl"

# Event tracking (page views, conversions). Privacy-safe: no email,
# no full IP, no full URL — only event name, coerced page name, truncated
# IP prefix, and referer hostname.
EVENTS_PATH = DATA_DIR / "events.jsonl"
ALLOWED_EVENTS = ("page_view", "anchor_click", "buy_pack_click", "verify_click")
ALLOWED_PAGES = ("landing", "verify", "account", "pricing", "docs", "blog",
                 "status", "stats", "about", "press", "compare", "affiliate")


def record(
    event: str,
    page: str,
    ip_prefix: str,
    referer_host: str | None = None,
) -> bool:
    """Append a privacy-safe analytics event to EVENTS_PATH.

    Returns True if recorded, False if rejected (unknown event name).
    Caller is responsible for IP truncation; this module only caps length.
    """
    if event not in ALLOWED_EVENTS:
        return False
    page_coerced = page if page in ALLOWED_PAGES else "other"
    ip_prefix_safe = (ip_prefix or "")[:64]
    ref_host_safe = (referer_host or "")[:128] if referer_host else ""
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
        "page": page_coerced,
        "ip_prefix": ip_prefix_safe,
        "ref_host": ref_host_safe,
    }
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_PATH.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return True


def _parse_iso_date(s: str) -> datetime:
    """Parse ISO 8601 timestamp."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)


def _current_subscriptions() -> dict[str, dict]:
    """Load active subscription state from subscriptions.json.

    Returns: { email -> { 'status': 'active|canceled', 'current_period_end': '2026-...' } }
    """
    if not SUBSCRIPTIONS_PATH.exists():
        return {}
    try:
        with SUBSCRIPTIONS_PATH.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _stripe_events() -> list[dict]:
    """Load all completed Stripe events from ledger."""
    if not STRIPE_EVENTS_PATH.exists():
        return []
    events = []
    try:
        with STRIPE_EVENTS_PATH.open() as f:
            for line in f:
                if line.strip():
                    try:
                        event = json.loads(line)
                        events.append(event)
                    except json.JSONDecodeError:
                        pass
    except IOError:
        pass
    return events


def _subscription_events() -> dict[str, list[dict]]:
    """Group subscription events by email.

    Returns: { email -> [{ 'type': 'created|canceled', 'created': timestamp }] }
    """
    sub_events: dict[str, list[dict]] = defaultdict(list)
    for event in _stripe_events():
        if event.get("type") == "customer.subscription.created":
            sub = event.get("data", {}).get("object", {})
            email = sub.get("metadata", {}).get("email") or ""
            if email:
                sub_events[email].append({
                    "type": "created",
                    "created": _parse_iso_date(sub.get("created", "")),
                })
        elif event.get("type") == "customer.subscription.deleted":
            sub = event.get("data", {}).get("object", {})
            email = sub.get("metadata", {}).get("email") or ""
            if email:
                sub_events[email].append({
                    "type": "canceled",
                    "canceled_at": _parse_iso_date(sub.get("canceled_at", "")),
                })
    return sub_events


def metrics(days_back: int = 90) -> dict:
    """Calculate founder metrics for the last N days.

    Returns: {
      "timestamp": "2026-05-14T20:45:00Z",
      "period_days": 90,
      "mrr": 1234.56,  # current month subscription revenue
      "arr": 14814.72,  # annualized run rate
      "churn_rate": 0.05,  # % of subscribers churned this month
      "customers": { "active": 12, "churned_this_month": 3, "total": 15 },
      "ltv": 1500.00,  # estimated lifetime value
    }
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days_back)

    sub_events = _subscription_events()

    # Count active + churned subscriptions
    active_count = 0
    churned_count = 0

    for email, events in sub_events.items():
        latest = max(events, key=lambda e: e.get("created"), default={})
        if latest.get("type") == "created":
            active_count += 1
        elif latest.get("type") == "canceled":
            cancel_date = latest.get("canceled_at")
            if cancel_date and cancel_date > cutoff:
                churned_count += 1

    # MRR: assume $9/mo per active subscription (placeholder; use actual plan amounts in production)
    monthly_revenue = active_count * 9.0
    arr = monthly_revenue * 12
    churn_rate = churned_count / max(active_count + churned_count, 1)
    ltv = (monthly_revenue / max(churn_rate, 0.01)) if churn_rate > 0 else (monthly_revenue * 12)

    return {
        "timestamp": now.isoformat(),
        "period_days": days_back,
        "mrr": round(monthly_revenue, 2),
        "arr": round(arr, 2),
        "churn_rate": round(churn_rate, 4),
        "customers": {
            "active": active_count,
            "churned_this_month": churned_count,
            "total": active_count + churned_count,
        },
        "ltv": round(ltv, 2),
    }


if __name__ == "__main__":
    import sys
    m = metrics()
    print(json.dumps(m, indent=2))
    sys.exit(0)
