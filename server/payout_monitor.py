#!/usr/bin/env python3
"""payout_monitor.py — founder-only READ of the historical hot-wallet balance.

This module used to do two jobs: poll mempool.space for every address the
direct-BTC order rail had issued (the COLLECTOR), and render what it had
collected (the READER).

The direct-BTC order rail was retired on 2026-09-19. The collector is gone
with it: `_watch_addresses()` sourced its address list from
`btc_payments` — the address pool, the single-address fallback and the
per-order HD-derived addresses in the orders ledger — and that module no
longer exists. No new address can be issued, so there is nothing new to poll.

The READER stays, and deliberately. `data/balance_snapshots.jsonl` and
`data/payout_pings.jsonl` are historical records; the founder must still be
able to read what the rail did while it existed, and `/api/founder/payout-
status` (token-gated) is the endpoint that shows it. Nothing here writes,
sends, signs or sweeps — it never did, and now it cannot poll either.

Ledgers are read-only from here. Nothing under data/ is deleted or rewritten.

Stdlib only.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mempool_watcher  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
PING_LEDGER = Path(os.environ.get("ORPHO_PING_LEDGER", str(DATA_DIR / "payout_pings.jsonl")))
COLD_ADDRESS_FILE = Path(os.environ.get("ORPHO_COLD_ADDRESS_FILE",
                                        str(DATA_DIR / "cold_wallet_address.txt")))


def _cold_address() -> str:
    """Read the configured cold-storage / payout destination address.

    This is where the founder swept the hot wallet to. The server never sent
    to this address — only the founder, manually, from their own wallet. It is
    shown in the founder-only status readout so a historical balance can be
    reconciled against where it went.
    """
    env_val = os.environ.get("ORPHO_COLD_ADDRESS", "").strip()
    if env_val:
        return env_val
    try:
        return COLD_ADDRESS_FILE.read_text().strip()
    except (OSError, FileNotFoundError):
        return ""


SWEEP_THRESHOLD_SATS = int(os.environ.get("ORPHO_SWEEP_THRESHOLD_SATS", "500000"))


def _last_ping_ts() -> float:
    """Unix timestamp of the most-recent sweep ping ever sent. 0 if never."""
    if not PING_LEDGER.exists():
        return 0.0
    last_ts = 0.0
    try:
        with PING_LEDGER.open() as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = row.get("ts_unix", 0)
                if isinstance(ts, (int, float)) and ts > last_ts:
                    last_ts = float(ts)
    except OSError:
        return 0.0
    return last_ts


def payout_status() -> dict:
    """The founder-only historical readout behind /api/founder/payout-status.

    `address_pool_size` is deliberately absent: the pool was a property of the
    retired order rail, not of the balance history. Every other field reads
    from a ledger that still exists.
    """
    snap = mempool_watcher.latest_snapshot() or {}
    total = snap.get("total_sats", 0)
    last_ping_unix = _last_ping_ts()
    last_ping_iso = (
        datetime.fromtimestamp(last_ping_unix, timezone.utc).isoformat(timespec="seconds")
        if last_ping_unix else None
    )
    return {
        "rail": "retired",
        "hot_balance_sats": total,
        "hot_balance_btc": round(total / 100_000_000, 8),
        "threshold_sats": SWEEP_THRESHOLD_SATS,
        "threshold_btc": round(SWEEP_THRESHOLD_SATS / 100_000_000, 8),
        "ready_to_sweep": total >= SWEEP_THRESHOLD_SATS,
        "cold_destination": _cold_address(),
        "last_ping_at": last_ping_iso,
        "last_snapshot_at": snap.get("ts"),
        "addresses_polled": snap.get("addresses_polled", 0),
        "addresses_error": snap.get("addresses_error", 0),
    }


if __name__ == "__main__":
    print(json.dumps(payout_status(), indent=2))
