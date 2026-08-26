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
import hashlib
import hmac
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from file_lock import locked

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
STRIPE_EVENTS_PATH = DATA_DIR / "stripe_processed_events.jsonl"
SUBSCRIPTIONS_PATH = Path(os.environ.get(
    "ORPHO_SUB_LEDGER", str(DATA_DIR / "subscriptions.jsonl")
))
CREDITS_LEDGER = DATA_DIR / "credits.ledger" if (DATA_DIR / "credits.ledger").is_file() else DATA_DIR / "ledger.jsonl"

# Event tracking (page views, conversions). Privacy-safe: no email,
# no full IP, no full URL — only event name, coerced page name, truncated
# IP prefix, and referer hostname.
EVENTS_PATH = DATA_DIR / "events.jsonl"
DEMAND_EVENTS_PATH = Path(os.environ.get(
    "ORPHO_DEMAND_EVENTS", str(DATA_DIR / "demand_events.jsonl")
))
ALLOWED_EVENTS = ("page_view", "anchor_click", "buy_pack_click", "verify_click")
ALLOWED_PAGES = ("landing", "verify", "account", "pricing", "docs", "blog",
                 "status", "stats", "about", "press", "compare", "affiliate")

DEMAND_EVENT_VERSION = 1
DEMAND_EVENTS = frozenset({
    "anchor_succeeded",
    "free_limit_reached",
    "checkout_created",
    "payment_confirmed",
    "entitlement_activated",
})
ORIGIN_CLASSES = frozenset({
    "office_automation",
    "external_authenticated",
    "external_anonymous",
    "unknown",
})
AUTH_PATHS = frozenset({"free", "pack", "subscription", "api_key", "l402", "none"})
SURFACES = frozenset({"single", "batch", "folder", "stripe", "nowpayments"})
OUTCOMES = frozenset({"success", "limited", "uncommitted", "failed"})
_OFFER_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


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


def _internal_key_hashes() -> tuple[str, ...]:
    """Validated SHA-256 digests of office-only API credentials.

    Production receives these through ORPHO_INTERNAL_API_KEY_HASHES as a
    comma-separated list. Raw credentials are never written to analytics.
    """
    raw = os.environ.get("ORPHO_INTERNAL_API_KEY_HASHES", "")
    return tuple(
        item for item in (part.strip().lower() for part in raw.split(","))
        if _HEX64_RE.fullmatch(item)
    )


def classify_origin(*, api_key: str = "", authenticated: bool = False,
                    paid: bool = False) -> str:
    """Classify demand from server-known authentication facts only."""
    if api_key:
        digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        if any(hmac.compare_digest(digest, known) for known in _internal_key_hashes()):
            return "office_automation"
    if authenticated or paid:
        return "external_authenticated"
    return "external_anonymous"


def privacy_safe_cohort(client_key: str, *, now: datetime | None = None) -> str:
    """Monthly rotating, non-reversible cohort id; empty when unconfigured."""
    secret = os.environ.get("ORPHO_ANALYTICS_HMAC_SECRET", "")
    if not secret or not client_key:
        return ""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m")
    payload = f"{stamp}:{client_key}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()[:20]


def _offer_version() -> str:
    value = os.environ.get("ORPHO_OFFER_VERSION", "control-v1").strip().lower()
    return value if _OFFER_VERSION_RE.fullmatch(value) else "invalid"


def record_demand(
    event: str,
    *,
    origin_class: str,
    auth_path: str,
    surface: str,
    outcome: str,
    client_key: str = "",
) -> bool:
    """Append one closed-schema, privacy-safe server-side demand event.

    Best effort by design: an analytics disk error must never make anchoring
    or checkout fail. False is returned so health/readout callers can expose
    degraded instrumentation rather than mistaking it for zero demand.
    """
    if (event not in DEMAND_EVENTS or origin_class not in ORIGIN_CLASSES
            or auth_path not in AUTH_PATHS or surface not in SURFACES
            or outcome not in OUTCOMES):
        return False
    cohort = privacy_safe_cohort(client_key)
    # Do not create an attribution ledger until its privacy secret exists.
    # A row with a raw client bucket is forbidden, and a row with no cohort
    # cannot support the conversion/repeat-use question this ledger exists to
    # answer. The founder readout reports a missing ledger as UNAVAILABLE.
    if not cohort:
        return False
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event_version": DEMAND_EVENT_VERSION,
        "event": event,
        "origin_class": origin_class,
        "auth_path": auth_path,
        "surface": surface,
        "offer_version": _offer_version(),
        "outcome": outcome,
        "privacy_safe_cohort": cohort,
        "data_quality": "complete",
    }
    try:
        with locked(DEMAND_EVENTS_PATH, mode="a", exclusive=True) as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError:
        return False
    return True


def demand_summary(days_back: int = 90) -> dict:
    """Aggregate the internal demand ledger without counting office as demand."""
    if not DEMAND_EVENTS_PATH.exists():
        return {
            "data_quality": "unavailable",
            "error": "demand ledger missing (this is not zero)",
        }
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    events: Counter[str] = Counter()
    origins: Counter[str] = Counter()
    surfaces: Counter[str] = Counter()
    auth_paths: Counter[str] = Counter()
    malformed = 0
    incomplete_cohorts = 0
    try:
        with DEMAND_EVENTS_PATH.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                    when = datetime.fromisoformat(
                        str(row.get("ts", "")).replace("Z", "+00:00")
                    )
                    if when.tzinfo is None:
                        raise ValueError("timestamp must carry a timezone")
                except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
                    malformed += 1
                    continue
                if when < cutoff or row.get("event") not in DEMAND_EVENTS:
                    continue
                origin = row.get("origin_class", "unknown")
                surface = row.get("surface", "")
                auth_path = row.get("auth_path", "")
                events[row["event"]] += 1
                origins[origin if origin in ORIGIN_CLASSES else "unknown"] += 1
                if surface in SURFACES:
                    surfaces[surface] += 1
                if auth_path in AUTH_PATHS:
                    auth_paths[auth_path] += 1
                if row.get("data_quality") != "complete":
                    incomplete_cohorts += 1
    except OSError as exc:
        return {"data_quality": "unavailable", "error": f"demand ledger unreadable: {exc}"}
    external = origins["external_authenticated"] + origins["external_anonymous"]
    return {
        "data_quality": "degraded" if (malformed or incomplete_cohorts) else "complete",
        "period_days": days_back,
        "total_events": sum(events.values()),
        "events": dict(events),
        "origins": {
            "external": external,
            "office_automation": origins["office_automation"],
            "unknown": origins["unknown"],
        },
        "surfaces": dict(surfaces),
        "auth_paths": dict(auth_paths),
        "malformed_rows": malformed,
        "incomplete_cohorts": incomplete_cohorts,
        "office_excluded_from_external": True,
    }


def _parse_iso_date(s: str) -> datetime:
    """Parse ISO 8601 timestamp; malformed ledger dates sort oldest."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _current_subscriptions() -> list[dict]:
    """Load the append-only subscription ledger.

    The live subscription module writes JSONL. Keep this reader aligned with
    that contract; silently falling back to an obsolete JSON snapshot makes
    the founder dashboard look healthy while ignoring current events.
    """
    if not SUBSCRIPTIONS_PATH.exists():
        return []
    try:
        with SUBSCRIPTIONS_PATH.open() as f:
            return [json.loads(line) for line in f if line.strip()]
    except (json.JSONDecodeError, IOError):
        return []


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

    Revenue fields remain null until a normalized payment ledger can support
    them. Demand includes an explicit data-quality state and office/external
    split.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days_back)

    sub_events = _subscription_events()

    # Count active + churned subscriptions
    active_count = 0
    churned_count = 0

    for email, events in sub_events.items():
        latest = max(
            events,
            key=lambda e: (
                e.get("created") or e.get("canceled_at")
                or datetime.min.replace(tzinfo=timezone.utc)
            ),
            default={},
        )
        if latest.get("type") == "created":
            active_count += 1
        elif latest.get("type") == "canceled":
            cancel_date = latest.get("canceled_at")
            if cancel_date and cancel_date > cutoff:
                churned_count += 1

    # Revenue cannot be inferred from a subscriber count. Stripe event
    # payloads in older ledgers do not consistently contain a normalized
    # recurring unit amount, so report revenue as unavailable until the
    # payment ledger provides one. Never manufacture MRR from a plan default.
    monthly_revenue = None
    churn_rate = churned_count / max(active_count + churned_count, 1)

    return {
        "timestamp": now.isoformat(),
        "period_days": days_back,
        "mrr": monthly_revenue,
        "arr": None,
        "revenue_data_quality": "unavailable",
        "churn_rate": round(churn_rate, 4),
        "customers": {
            "active": active_count,
            "churned_this_month": churned_count,
            "total": active_count + churned_count,
        },
        "ltv": None,
        "demand": demand_summary(days_back=days_back),
    }


if __name__ == "__main__":
    import sys
    m = metrics()
    print(json.dumps(m, indent=2))
    sys.exit(0)
