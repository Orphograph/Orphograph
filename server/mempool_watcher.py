#!/usr/bin/env python3
"""mempool_watcher.py — poll mempool.space for incoming BTC on watch addresses.

Stdlib only. No keys, no signing — pure read-only mempool/chain queries via
the public mempool.space API (no auth required).

Public API:
    address_balance_sats(address: str) -> tuple[int, int]
        Returns (confirmed_sats, unconfirmed_sats) for a single address.

    watch_balances(addresses: list[str]) -> dict[str, dict]
        Returns {address: {"confirmed": int, "unconfirmed": int, "tx_count": int}}
        for each address. Stops on first error per address rather than aborting.

    persist_snapshot(path: Path, balances: dict, swept: int) -> None
        Append-only ledger so we have a time series of hot-balance evolution.

The watcher does NOT track which payment maps to which customer order — that
matching lives in btc_payments.py against the order ledger. This module is
strictly about "how much BTC is currently sitting in our hot wallet".
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MEMPOOL_BASE = os.environ.get("ORPHO_MEMPOOL_BASE", "https://mempool.space/api")
BLOCKSTREAM_BASE = "https://blockstream.info/api"  # fallback
HTTP_TIMEOUT_SEC = 12
USER_AGENT = "orphograph-mempool-watcher/0.1 (stdlib)"

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
BALANCE_LEDGER = Path(os.environ.get("ORPHO_BALANCE_LEDGER",
                                     str(DATA_DIR / "balance_snapshots.jsonl")))


def _get_json(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError):
        return None


def address_balance_sats(address: str) -> tuple[int, int, int]:
    """Return (confirmed_sats, unconfirmed_sats, tx_count) for `address`.

    Tries mempool.space first, blockstream.info as redundant fallback.
    Returns (-1, -1, -1) only if BOTH explorers fail — degraded mode.

    The mempool.space response shape:
        {
          "address": "...",
          "chain_stats": {"funded_txo_sum": int, "spent_txo_sum": int, ...},
          "mempool_stats": {"funded_txo_sum": int, "spent_txo_sum": int, ...}
        }
    Same shape on blockstream.info — the two share the BIP-301-ish API.
    """
    for base in (MEMPOOL_BASE, BLOCKSTREAM_BASE):
        data = _get_json(f"{base}/address/{address}")
        if not data:
            continue
        try:
            chain = data["chain_stats"]
            mem = data["mempool_stats"]
            confirmed = chain["funded_txo_sum"] - chain["spent_txo_sum"]
            unconfirmed = mem["funded_txo_sum"] - mem["spent_txo_sum"]
            tx_count = chain.get("tx_count", 0) + mem.get("tx_count", 0)
            return int(confirmed), int(unconfirmed), int(tx_count)
        except (KeyError, TypeError):
            continue
    return -1, -1, -1


def watch_balances(addresses: list[str]) -> dict[str, dict]:
    """Query all addresses, return per-address balance dict.

    Polite to mempool.space: 200ms sleep between requests so a 100-address
    pool doesn't look like a DoS.
    """
    out: dict[str, dict] = {}
    for i, addr in enumerate(addresses):
        if i > 0:
            time.sleep(0.2)
        confirmed, unconfirmed, tx_count = address_balance_sats(addr)
        out[addr] = {
            "confirmed": confirmed,
            "unconfirmed": unconfirmed,
            "tx_count": tx_count,
            "error": confirmed == -1,
        }
    return out


def total_hot_balance(addresses: list[str]) -> dict[str, int]:
    """Aggregate confirmed + unconfirmed across all addresses."""
    balances = watch_balances(addresses)
    total_confirmed = sum(b["confirmed"] for b in balances.values() if not b["error"])
    total_unconfirmed = sum(b["unconfirmed"] for b in balances.values() if not b["error"])
    error_count = sum(1 for b in balances.values() if b["error"])
    return {
        "confirmed_sats": total_confirmed,
        "unconfirmed_sats": total_unconfirmed,
        "total_sats": total_confirmed + total_unconfirmed,
        "addresses_polled": len(addresses),
        "addresses_error": error_count,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def persist_snapshot(snapshot: dict) -> None:
    BALANCE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with BALANCE_LEDGER.open("a") as f:
        f.write(json.dumps(snapshot, separators=(",", ":")) + "\n")


def latest_snapshot() -> dict | None:
    if not BALANCE_LEDGER.exists():
        return None
    last_line = ""
    try:
        with BALANCE_LEDGER.open() as f:
            for line in f:
                if line.strip():
                    last_line = line
    except OSError:
        return None
    if not last_line:
        return None
    try:
        return json.loads(last_line)
    except json.JSONDecodeError:
        return None


if __name__ == "__main__":
    # CLI smoke-test: python3 mempool_watcher.py <address1> [address2 ...]
    if len(sys.argv) < 2:
        print("usage: mempool_watcher.py <bc1q...> [<bc1q...> ...]", file=sys.stderr)
        sys.exit(2)
    addrs = sys.argv[1:]
    snap = total_hot_balance(addrs)
    print(json.dumps(snap, indent=2))
    persist_snapshot(snap)
