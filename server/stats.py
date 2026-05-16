#!/usr/bin/env python3
"""stats.py — public-safe metrics snapshot for the marketing /stats page.

Returns a JSON dict suitable for /api/stats. Cached for STATS_CACHE_SEC
so polling can't degrade performance.

Privacy invariants (enforced by what is NOT included here):
    - never customer emails
    - never filenames / client_labels
    - never IP addresses
    - never per-customer counts
    - never BTC balances (hot/cold)
    - never individual receipt IDs

Public-safe fields (counts + names of public infrastructure only):
    - total anchors lifetime
    - anchors in last 24h
    - anchors in last 7d
    - calendar reachability (URLs are public OTS infra)
    - btc oracle source name + price (public market data)
    - server uptime / boot timestamp
    - last anchor timestamp (no id, no email)

Public API:
    snapshot() -> dict
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import engine
import health

ROOT = Path(__file__).resolve().parent.parent

STATS_CACHE_SEC = float(os.environ.get("ORPHO_STATS_CACHE_SEC", "60"))

_lock = threading.Lock()
_cached: dict | None = None
_cached_at: float = 0.0


def _parse_iso(ts: str | None) -> float | None:
    """Parse an ISO-8601 timestamp into epoch seconds. Returns None on failure."""
    if not isinstance(ts, str) or not ts:
        return None
    # Normalize 'Z' suffix (datetime.fromisoformat handles offsets but not 'Z'
    # until 3.11; orphograph targets 3.11+ so this is just defensive).
    candidate = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _scan_ledger() -> dict:
    """Single pass over the ledger producing the counts the page needs.

    Reads line-by-line so a multi-MB ledger doesn't balloon memory.
    Per-customer fields (emails, claim codes, client labels) are deliberately
    NOT read — we only extract created_at and source.
    """
    ledger: Path = engine.LEDGER
    total = 0
    last_24h = 0
    last_7d = 0
    free_anchors = 0  # source == "free"
    pack_anchors = 0  # source starts with "pack:"
    sub_anchors = 0   # source starts with "sub:" or "api:"
    last_anchor_at: str | None = None
    last_anchor_epoch: float = 0.0
    now = time.time()
    cutoff_24h = now - 86400
    cutoff_7d = now - 7 * 86400

    if not ledger.exists():
        return {
            "total": 0,
            "last_24h": 0,
            "last_7d": 0,
            "free_anchors": 0,
            "pack_anchors": 0,
            "sub_anchors": 0,
            "last_anchor_at": None,
        }

    try:
        with ledger.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1
                ca = row.get("created_at")
                source = row.get("source", "free")
                ts = _parse_iso(ca)
                if ts is None:
                    continue
                # Count by source
                if source == "free":
                    free_anchors += 1
                elif source.startswith("pack:"):
                    pack_anchors += 1
                elif source.startswith(("sub:", "api:")):
                    sub_anchors += 1
                if ts >= cutoff_24h:
                    last_24h += 1
                if ts >= cutoff_7d:
                    last_7d += 1
                if ts > last_anchor_epoch:
                    last_anchor_epoch = ts
                    last_anchor_at = ca
    except OSError:
        pass

    return {
        "total": total,
        "last_24h": last_24h,
        "last_7d": last_7d,
        "free_anchors": free_anchors,
        "pack_anchors": pack_anchors,
        "sub_anchors": sub_anchors,
        "last_anchor_at": last_anchor_at,
    }


def _calendars_public(h: dict) -> dict:
    """Reduce health.calendars to a public-safe summary.

    Includes the short name (alice, finney, btc.catallaxy, a.pool, b.pool)
    which are public OTS infrastructure. No private state.
    """
    rows = h.get("calendars") or []
    reachable = sum(1 for r in rows if r.get("reachable"))
    total = len(rows)
    items = []
    for r in rows:
        url = r.get("url", "")
        # Short name: same convention engine._calendar_short uses.
        short = url.split("//", 1)[-1].split(".", 1)[0] if "//" in url else url
        items.append({"name": short, "reachable": bool(r.get("reachable"))})
    return {
        "reachable": reachable,
        "total": total,
        "items": items,
    }


def _btc_oracle_public(h: dict) -> dict:
    """Reduce btc_oracle to public-safe fields: source name + USD price."""
    o = h.get("btc_oracle") or {}
    if not o.get("available"):
        return {"available": False, "source": None, "usd_per_btc": None}
    return {
        "available": True,
        "source": o.get("source"),
        "usd_per_btc": o.get("usd_per_btc"),
    }


def _compute_snapshot() -> dict:
    h = health.snapshot()
    counts = _scan_ledger()
    return {
        "version": h.get("version"),
        "uptime_sec": h.get("uptime_sec"),
        "boot_at": h.get("boot_at"),
        "anchors": counts,
        "calendars": _calendars_public(h),
        "btc_oracle": _btc_oracle_public(h),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def snapshot() -> dict:
    """Return cached stats snapshot; refresh if older than STATS_CACHE_SEC.

    The response is identical for every viewer — there is no per-customer or
    per-IP variation — so it is keyed by nothing.
    """
    global _cached, _cached_at
    now = time.time()
    with _lock:
        if _cached is not None and (now - _cached_at) < STATS_CACHE_SEC:
            return _cached
    fresh = _compute_snapshot()
    with _lock:
        _cached = fresh
        _cached_at = now
    return fresh


def _reset_cache_for_tests() -> None:
    """Test hook: drop the cached snapshot so the next call recomputes."""
    global _cached, _cached_at
    with _lock:
        _cached = None
        _cached_at = 0.0
