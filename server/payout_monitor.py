#!/usr/bin/env python3
"""payout_monitor.py — daily cron: poll hot balance, ping if ≥ threshold.

Reads:
    btc_payments.address pool (Phantom-friendly path 2)
    OR btc_payments.BTC_RECEIVE_ADDRESS (single-address fallback)
    Hot balance via mempool_watcher

Writes:
    data/balance_snapshots.jsonl (time series)
    data/payout_pings.jsonl (record of every ping sent — avoid spam)

Notifies:
    ~/.claude/notifier.py (Telegram, per project_notifier.md)
    Only ONCE per threshold-crossing — re-sends after a cooldown if balance
    re-crosses upward after a sweep.

Stdlib only. Designed to be invoked from launchd or `python3 -m payout_monitor`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import btc_payments  # noqa: E402
import mempool_watcher  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
PING_LEDGER = Path(os.environ.get("ORPHO_PING_LEDGER", str(DATA_DIR / "payout_pings.jsonl")))
COLD_ADDRESS_FILE = Path(os.environ.get("ORPHO_COLD_ADDRESS_FILE",
                                        str(DATA_DIR / "cold_wallet_address.txt")))
NOTIFIER = Path.home() / ".claude" / "notifier.py"


def _cold_address() -> str:
    """Read the configured cold-storage / payout destination address.

    This is where the founder sweeps the hot wallet to. Could be:
      • A hardware wallet address (Coldcard, Trezor)
      • A custodial BTC account (PayPal BTC, Strike, Cash App)
      • A second Phantom on a "never-browse" device
    The server never sends to this address — only the founder, manually,
    from Phantom. We use the address only to show it in the Telegram ping.
    """
    env_val = os.environ.get("ORPHO_COLD_ADDRESS", "").strip()
    if env_val:
        return env_val
    try:
        return COLD_ADDRESS_FILE.read_text().strip()
    except (OSError, FileNotFoundError):
        return ""

SWEEP_THRESHOLD_SATS = int(os.environ.get("ORPHO_SWEEP_THRESHOLD_SATS", "500000"))
PING_COOLDOWN_SEC = int(os.environ.get("ORPHO_PING_COOLDOWN_SEC", "21600"))  # 6 hours


def _watch_addresses() -> list[str]:
    """All addresses we should poll mempool.space for.

    Priority: HD-derived recent indices (TODO) + pool + single fallback.
    For now, we cover the pool + single fallback. HD-derived addresses are
    only generated lazily by btc_payments — we'd need a parallel index
    counter to enumerate all-ever-issued. That's the next iteration.
    """
    addrs = btc_payments._load_pool()
    if btc_payments.BTC_RECEIVE_ADDRESS and btc_payments.BTC_RECEIVE_ADDRESS not in addrs:
        addrs.append(btc_payments.BTC_RECEIVE_ADDRESS)
    return [a for a in addrs if a]


def _last_ping_ts() -> float:
    """Unix timestamp of the most-recent ping we sent. 0 if never."""
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


def _send_telegram(message: str) -> bool:
    """Fire-and-forget via the shared notifier. Returns True on subprocess success."""
    if not NOTIFIER.exists():
        sys.stderr.write(f"[payout_monitor:warn] notifier not found at {NOTIFIER}\n")
        return False
    # ~/.claude/notifier.py accepts the message as positional argv (see its
    # docstring + the telegram_bridge facade). NOT --text, which earlier
    # iterations of this file used incorrectly.
    try:
        result = subprocess.run(
            ["python3", str(NOTIFIER), message],
            capture_output=True, text=True, timeout=20,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as e:
        sys.stderr.write(f"[payout_monitor:error] notifier subprocess: {e}\n")
        return False


def _persist_ping(total_sats: int, threshold_sats: int) -> None:
    PING_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with PING_LEDGER.open("a") as f:
        now = datetime.now(timezone.utc)
        f.write(json.dumps({
            "ts": now.isoformat(timespec="seconds"),
            "ts_unix": now.timestamp(),
            "total_sats": total_sats,
            "threshold_sats": threshold_sats,
            "btc_equiv": round(total_sats / 100_000_000, 8),
        }, separators=(",", ":")) + "\n")


def check_once(force_ping: bool = False) -> dict:
    """One round: poll, snapshot, maybe ping. Returns the snapshot dict."""
    addrs = _watch_addresses()
    if not addrs:
        return {"skipped": "no addresses configured", "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    snap = mempool_watcher.total_hot_balance(addrs)
    mempool_watcher.persist_snapshot(snap)

    total = snap.get("total_sats", 0)
    should_ping = total >= SWEEP_THRESHOLD_SATS
    cooldown_ok = (datetime.now(timezone.utc).timestamp() - _last_ping_ts()) >= PING_COOLDOWN_SEC

    if (should_ping and cooldown_ok) or force_ping:
        btc = total / 100_000_000
        cold = _cold_address()
        dest_line = f"Send to: {cold}" if cold else "Send to: (no ORPHO_COLD_ADDRESS configured)"
        msg = (
            f"📥 Orphograph hot wallet: {total:,} sats ({btc:.6f} BTC).\n"
            f"Threshold: {SWEEP_THRESHOLD_SATS:,} sats reached.\n"
            f"Open Phantom → BTC → Send.\n"
            f"{dest_line}\n"
            f"Snapshot: {snap.get('ts','')}"
        )
        sent = _send_telegram(msg)
        if sent:
            _persist_ping(total, SWEEP_THRESHOLD_SATS)
            snap["pinged"] = True
        else:
            snap["pinged"] = False
            snap["ping_error"] = "notifier subprocess failed or notifier missing"
    else:
        snap["pinged"] = False
        snap["reason"] = (
            "below_threshold" if not should_ping
            else "within_cooldown"
        )

    return snap


def payout_status() -> dict:
    """Snapshot the founder's account dashboard would render."""
    snap = mempool_watcher.latest_snapshot() or {}
    total = snap.get("total_sats", 0)
    last_ping_unix = _last_ping_ts()
    last_ping_iso = (
        datetime.fromtimestamp(last_ping_unix, timezone.utc).isoformat(timespec="seconds")
        if last_ping_unix else None
    )
    return {
        "hot_balance_sats": total,
        "hot_balance_btc": round(total / 100_000_000, 8),
        "threshold_sats": SWEEP_THRESHOLD_SATS,
        "threshold_btc": round(SWEEP_THRESHOLD_SATS / 100_000_000, 8),
        "ready_to_sweep": total >= SWEEP_THRESHOLD_SATS,
        "pool_size": btc_payments.pool_size(),
        "cold_destination": _cold_address(),
        "last_ping_at": last_ping_iso,
        "last_snapshot_at": snap.get("ts"),
        "addresses_polled": snap.get("addresses_polled", 0),
        "addresses_error": snap.get("addresses_error", 0),
    }


if __name__ == "__main__":
    force = "--force" in sys.argv or "-f" in sys.argv
    if "--status" in sys.argv:
        print(json.dumps(payout_status(), indent=2))
        sys.exit(0)
    result = check_once(force_ping=force)
    print(json.dumps(result, indent=2))
