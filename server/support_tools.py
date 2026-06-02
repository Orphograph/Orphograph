#!/usr/bin/env python3
"""support_tools.py — founder-only customer support utilities.

Provides:
  • Customer lookup by email (anchors, purchases, subscription status)
  • Refund processing (via refund_pack.py)
  • Ledger audit export (for accounting)
  • Abuse detection (IP clustering, claim code guessing)

Exported for founder-only endpoints:
  GET /api/founder/customer?email=... — customer profile
  POST /api/founder/refund — process refund
  GET /api/founder/ledger?from=...&to=... — audit export
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))


def _read_jsonl(path: Path) -> list[dict]:
    """Read append-only JSONL ledger."""
    if not path.exists():
        return []
    lines = []
    try:
        with path.open() as f:
            for line in f:
                if line.strip():
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except IOError:
        pass
    return lines


def lookup_customer(email: str) -> dict | None:
    """Look up a customer by email.

    Returns:
    {
      "email": "buyer@example.com",
      "anchors": [{"id": "...", "hash": "...", "created": "...", "label": "..."}],
      "purchases": [{"charge_id": "...", "amount": "...", "credits": 10, "created": "..."}],
      "subscription": { "status": "active|canceled", "created": "...", "current_period_end": "..." },
      "total_spent": 123.45,
      "anchor_count": 25,
    }
    """
    if not email or "@" not in email:
        return None

    # Load subscription state
    subs_path = DATA_DIR / "subscriptions.json"
    subscription = None
    if subs_path.exists():
        try:
            with subs_path.open() as f:
                all_subs = json.load(f)
                subscription = all_subs.get(email)
        except (json.JSONDecodeError, IOError):
            pass

    # Load anchors (from ledger.jsonl or custom anchors ledger)
    anchors_path = DATA_DIR / "anchors.jsonl"
    if not anchors_path.exists():
        anchors_path = DATA_DIR / "ledger.jsonl"

    anchors = []
    if anchors_path.exists():
        for entry in _read_jsonl(anchors_path):
            if entry.get("email_id") == email or entry.get("email") == email:
                anchors.append({
                    "id": entry.get("receipt_id", ""),
                    "hash": entry.get("hash", "")[:16] + "...",
                    "created": entry.get("timestamp", ""),
                    "label": entry.get("label", ""),
                })

    # Load purchases (from Stripe events)
    purchases = []
    stripe_events_path = DATA_DIR / "stripe_processed_events.jsonl"
    if stripe_events_path.exists():
        for event in _read_jsonl(stripe_events_path):
            if event.get("type") == "charge.succeeded":
                charge = event.get("data", {}).get("object", {})
                charge_email = charge.get("metadata", {}).get("email") or ""
                if charge_email == email:
                    purchases.append({
                        "charge_id": charge.get("id", ""),
                        "amount": charge.get("amount", 0) / 100,
                        "created": charge.get("created", ""),
                    })

    total_spent = sum(p.get("amount", 0) for p in purchases)

    # Pack claim codes minted to this email — from the append-only credit
    # ledger, which covers BOTH Stripe and crypto (NOWPayments) purchases.
    # Without this a crypto buyer's claim code is invisible to support and a
    # "paid but never got my code" ticket cannot be resolved from the dashboard.
    pack_claims = []
    ledger_path = DATA_DIR / "credit_ledger.jsonl"
    if ledger_path.exists():
        for row in _read_jsonl(ledger_path):
            if not isinstance(row, dict) or row.get("email") != email:
                continue
            try:
                delta = int(row.get("credits_delta", 0))
            except (TypeError, ValueError):
                continue  # tolerate a hand-corrupted ledger row
            if delta > 0:
                pack_claims.append({
                    "claim_code": row.get("claim_code", ""),
                    "credits": delta,
                    "source": row.get("source", ""),
                    "ts": row.get("ts", ""),
                })

    return {
        "email": email,
        "anchors": anchors,
        "purchases": purchases,
        "pack_claims": pack_claims,
        "subscription": subscription,
        "total_spent": round(total_spent, 2),
        "anchor_count": len(anchors),
    }


def ledger_audit(days_back: int = 30) -> list[dict]:
    """Export ledger entries for accounting audit.

    Returns list of {timestamp, email_id, type, amount, description}
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days_back)

    entries = []

    # Stripe events (charges, refunds)
    stripe_events_path = DATA_DIR / "stripe_processed_events.jsonl"
    for event in _read_jsonl(stripe_events_path):
        event_time = event.get("created")
        if event_time and datetime.fromisoformat(event_time.replace("Z", "+00:00")) > cutoff:
            if event.get("type") == "charge.succeeded":
                charge = event.get("data", {}).get("object", {})
                entries.append({
                    "timestamp": event_time,
                    "email": charge.get("metadata", {}).get("email", ""),
                    "type": "charge",
                    "amount": charge.get("amount", 0) / 100,
                    "source": "stripe",
                    "id": charge.get("id", ""),
                })
            elif event.get("type") == "charge.refunded":
                charge = event.get("data", {}).get("object", {})
                entries.append({
                    "timestamp": event_time,
                    "email": charge.get("metadata", {}).get("email", ""),
                    "type": "refund",
                    "amount": -(charge.get("amount_refunded", 0) / 100),
                    "source": "stripe",
                    "id": charge.get("id", ""),
                })

    return sorted(entries, key=lambda x: x.get("timestamp", ""))


def detect_abuse() -> dict:
    """Scan for abuse patterns.

    Returns:
    {
      "bot_ips": [{"ip_prefix": "1.2.3.0/24", "anchor_count": 150, "hours": 1}],
      "failed_verifications": [{"claim_code": "...", "attempts": 23}],
      "token_reuse": [{"pack_token": "...", "emails": ["...", "..."]}]
    }
    """
    # Placeholder: real implementation would analyze ledgers for:
    # 1. Many anchors from same IP in short time
    # 2. Many 404 hits on /api/receipt/ID (claim code guessing)
    # 3. Same Pack token used by multiple emails
    return {
        "bot_ips": [],
        "failed_verifications": [],
        "token_reuse": [],
    }
