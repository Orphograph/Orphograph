#!/usr/bin/env python3
"""health.py — extended liveness + observability snapshot.

Returns a JSON dict suitable for /api/health. Cached for HEALTH_CACHE_SEC
so polling the status page can't degrade performance.

The data here is intentionally non-sensitive: counts and timestamps,
no emails, no claim codes, no IPs. Safe to expose publicly.

Public API:
    snapshot() -> dict
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import engine
try:
    import btc_price  # type: ignore
except ImportError:  # pragma: no cover
    btc_price = None  # type: ignore
try:
    import btc_payments  # type: ignore
except ImportError:  # pragma: no cover
    btc_payments = None  # type: ignore
try:
    import mempool_watcher  # type: ignore
except ImportError:  # pragma: no cover
    mempool_watcher = None  # type: ignore
try:
    import public_config  # type: ignore
except ImportError:  # pragma: no cover
    public_config = None  # type: ignore
try:
    import mailer  # type: ignore
except ImportError:  # pragma: no cover
    mailer = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))

HEALTH_CACHE_SEC = float(os.environ.get("ORPHO_HEALTH_CACHE_SEC", "30"))
VERSION = os.environ.get("ORPHO_VERSION", "0.1.0")
ACTIVE_PROBES = os.environ.get("ORPHO_HEALTH_ACTIVE_PROBES", "0") == "1"

_lock = threading.Lock()
_cached: dict | None = None
_cached_at: float = 0.0
_boot_time: float = time.time()


def _last_line_ts(path: Path) -> str | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        # Read from the end — for small files (everything here is small),
        # just read the whole thing and take the last JSON line.
        text = path.read_text()
    except OSError:
        return None
    last_line = ""
    for line in text.splitlines():
        line = line.strip()
        if line:
            last_line = line
    if not last_line:
        return None
    try:
        row = json.loads(last_line)
    except json.JSONDecodeError:
        return None
    return row.get("created_at") or row.get("ts")


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _calendar_reachable(url: str) -> bool:
    """Quick HEAD to the calendar root. Cached via the outer snapshot cache."""
    try:
        req = urllib.request.Request(url.rstrip("/"), method="HEAD")
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status < 500
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, ssl.SSLError, OSError):
        return False


def _check_calendars_parallel(urls: list[str]) -> list[dict]:
    """Fan out the HEAD probes so the snapshot doesn't wait sequentially."""
    if not ACTIVE_PROBES:
        return [{"url": u, "reachable": None, "checked": False} for u in urls]
    results: dict[str, bool] = {}
    threads: list[threading.Thread] = []
    def worker(u):
        results[u] = _calendar_reachable(u)
    for u in urls:
        t = threading.Thread(target=worker, args=(u,), daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=4)
    return [{"url": u, "reachable": results.get(u, False)} for u in urls]


def _btc_price_snapshot() -> dict:
    """Optional BTC oracle status — last cached price + which source provided it.

    Does NOT trigger a fresh poll; reads whatever the in-process cache holds.
    Avoids hammering the oracles on every /api/health request.
    """
    if btc_price is None:
        return {"available": False}
    try:
        if hasattr(btc_price, "cached_usd_per_btc_source"):
            price, source = btc_price.cached_usd_per_btc_source()
            return {"available": bool(price), "usd_per_btc": price, "source": source}
        # Fallback: just the price.
        price = 0.0
        return {"available": True, "usd_per_btc": price, "source": "unknown"}
    except Exception as e:  # pragma: no cover — defensive
        return {"available": False, "error": f"{type(e).__name__}"}


def _payout_snapshot() -> dict:
    """Founder-only-relevant info: pool size + cold address configured.

    Hot balance is NOT included here because the public /api/health endpoint
    is not gated. Founder-only details (actual sats balance) live on
    /api/founder/payout-status which IS token-gated.
    """
    if btc_payments is None:
        return {"available": False}
    try:
        pool = btc_payments.pool_size() if hasattr(btc_payments, "pool_size") else 0
        configured = btc_payments.is_configured() if hasattr(btc_payments, "is_configured") else False
        return {
            "configured": configured,
            "address_pool_size": pool,
            "xpub_set": bool(getattr(btc_payments, "BTC_XPUB", "") or ""),
        }
    except Exception as e:  # pragma: no cover — defensive
        return {"configured": False, "error": f"{type(e).__name__}"}


def _email_snapshot() -> dict:
    """Whether transactional email (claim codes, receipts, sign-in links) can be
    delivered. The mailer is INERT without RESEND_API_KEY, so resend_configured
    == False means a paid buyer's claim code would be minted + queued for manual
    recovery rather than emailed. Exposes ONLY a boolean — never the API key
    value — so it is safe on the public, ungated /api/health endpoint."""
    if mailer is None:
        return {"resend_configured": False}
    return {"resend_configured": bool(getattr(mailer, "RESEND_API_KEY", "") or "")}


def _compute_snapshot() -> dict:
    ledger = engine.LEDGER
    upgrade_log = DATA_DIR / "upgrade_log.jsonl"
    expiry_log = DATA_DIR / "expiry_log.jsonl"
    receipts_dir = engine.RECEIPTS_DIR
    receipts_count = 0
    if receipts_dir.exists():
        try:
            receipts_count = sum(1 for c in receipts_dir.iterdir() if c.is_dir())
        except OSError:
            pass
    return {
        "ok": True,
        "version": VERSION,
        "boot_at": datetime.fromtimestamp(_boot_time, timezone.utc).isoformat(timespec="seconds"),
        "uptime_sec": int(time.time() - _boot_time),
        "data_dir": str(DATA_DIR),
        "counts": {
            "receipts_on_disk": receipts_count,
        },
        "ledger_bytes": {
            "anchor_ledger": _file_size(ledger),
            "upgrade_log": _file_size(upgrade_log),
            "expiry_log": _file_size(expiry_log),
        },
        "last": {
            "anchor_at": _last_line_ts(ledger),
            "upgrade_run_at": _last_line_ts(upgrade_log),
            "expiry_run_at": _last_line_ts(expiry_log),
        },
        "calendars": _check_calendars_parallel(list(engine.CALENDARS)),
        "btc_oracle": _btc_price_snapshot(),
        "payout": _payout_snapshot(),
        "checkout": _checkout_snapshot(),
        "email": _email_snapshot(),
        "reconciliation": _reconciliation_snapshot(),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _count_jsonl(path: Path) -> int | None:
    """Count non-blank JSONL rows; None on read error (crash-safe)."""
    try:
        if not path.exists():
            return 0
        n = 0
        with path.open() as f:
            for line in f:
                if line.strip():
                    n += 1
        return n
    except OSError:
        return None


def _count_jsonl_event_prefix(path: Path, prefix: str) -> int | None:
    """Count rows whose `event_id` starts with `prefix`; None on read error."""
    try:
        if not path.exists():
            return 0
        n = 0
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("event_id", "")).startswith(prefix):
                    n += 1
        return n
    except OSError:
        return None


def _reconciliation_snapshot() -> dict:
    """Read-only money-integrity counts so a silently-failed webhook path is
    detectable from /health. Counts only — no emails, codes, or amounts. A
    successful grant leaves exactly one credit-ledger row; a paid NOWPayments
    order leaves exactly one mint marker. Divergence between processed-events
    and grants over time is the cue to run scripts/reconcile_stripe_ledger.py."""
    credit_ledger = Path(os.environ.get(
        "ORPHO_CREDIT_LEDGER", str(DATA_DIR / "credit_ledger.jsonl")))
    stripe_events = Path(os.environ.get(
        "ORPHO_PROCESSED_EVENTS", str(DATA_DIR / "stripe_processed_events.jsonl")))
    nowpay_events = Path(os.environ.get(
        "ORPHO_NOWPAYMENTS_PROCESSED_EVENTS", str(DATA_DIR / "nowpayments_processed_events.jsonl")))
    return {
        "credit_ledger_rows": _count_jsonl(credit_ledger),
        "stripe_processed_events": _count_jsonl(stripe_events),
        "nowpayments_processed_events": _count_jsonl(nowpay_events),
        "nowpayments_mint_markers": _count_jsonl_event_prefix(nowpay_events, "mint:"),
        "note": "counts only; run scripts/reconcile_stripe_ledger.py for full drift detection",
    }


def _checkout_snapshot() -> dict:
    """Is the revenue path actually live? Surfaces config_warnings (e.g. checkout
    enabled but no Stripe Pack URL) so a dead buy button is visible in /health."""
    if public_config is None:
        return {"ready": None, "warnings": ["public_config unavailable"]}
    try:
        cfg = public_config.snapshot()
        warnings = public_config.config_warnings(cfg)
        return {
            # A non-empty placeholder URL ('https://buy.stripe.com/...') is NOT
            # ready — validate the link shape, don't just check for non-empty.
            "ready": public_config.is_live_stripe_url(cfg["stripe"]["pack_url"])
            and not cfg["toggles"]["checkout_disabled"],
            "pack_usd": cfg["pricing"]["pack_usd"],
            "warnings": warnings,
        }
    except Exception as e:  # noqa: BLE001  # pragma: no cover
        return {"ready": None, "warnings": [f"{type(e).__name__}: {e}"]}


def snapshot() -> dict:
    """Return cached health snapshot; refresh if older than HEALTH_CACHE_SEC."""
    global _cached, _cached_at
    now = time.time()
    with _lock:
        if _cached is not None and (now - _cached_at) < HEALTH_CACHE_SEC:
            return _cached
    # Compute outside the lock to keep the critical section short. If two
    # threads race here both will compute; second store wins. Cheap.
    fresh = _compute_snapshot()
    with _lock:
        _cached = fresh
        _cached_at = now
    return fresh
