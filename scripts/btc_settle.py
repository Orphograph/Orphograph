#!/usr/bin/env python3
"""btc_settle.py — sweep pending BTC orders and credit on confirmation.

Runs periodically (every 5 minutes via Fly cron). For each pending
order:

1. Query mempool.space for transactions that pay BTC_RECEIVE_ADDRESS.
2. Match by exact amount_sats (unique per order, see btc_price.py).
3. If a transaction matches AND has ≥1 confirmation, mark the order
   settled, mint a claim code, and email it to the customer.
4. If the order's TTL has expired with no payment, mark it expired
   (auditable but no action needed).

This script reads only public chain data + the orders ledger. It
NEVER signs anything. It NEVER touches a private key. Even a hostile
operator running this script can't move funds.

Stdlib only. Run from anywhere with network access; defaults to
the production data dir on Fly.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import btc_payments  # noqa: E402
import credits  # noqa: E402
import mailer  # noqa: E402

MEMPOOL_BASE = os.environ.get("MEMPOOL_API", "https://mempool.space/api")
HTTP_TIMEOUT = 12
USER_AGENT = "orphograph-btc-settle/0.1 (stdlib)"
MIN_CONFIRMATIONS = int(os.environ.get("BTC_MIN_CONFIRMATIONS", "1"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _http_get_json(path: str) -> dict | list | None:
    url = MEMPOOL_BASE.rstrip("/") + path
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout,
            ssl.SSLError, json.JSONDecodeError, ConnectionError, OSError) as e:
        sys.stderr.write(f"[btc_settle] {url}: {type(e).__name__}\n")
        return None


def _address_txs(address: str) -> list[dict]:
    """Recent transactions involving the address. mempool.space returns
    the latest 25 confirmed + all mempool by default."""
    data = _http_get_json(f"/address/{address}/txs")
    if not isinstance(data, list):
        return []
    return data


def _current_block_height() -> int:
    data = _http_get_json("/blocks/tip/height")
    try:
        return int(data) if data is not None else 0
    except (ValueError, TypeError):
        return 0


def _confirmations_for(tx: dict, tip: int) -> int:
    """Given a tx from /address/<a>/txs, return how many confirmations it has."""
    status = tx.get("status", {}) or {}
    if not status.get("confirmed"):
        return 0
    block_height = status.get("block_height")
    if not isinstance(block_height, int) or block_height <= 0:
        return 0
    return max(0, tip - block_height + 1)


def _sats_to_address(tx: dict, address: str) -> int:
    """Sum of all outputs in tx paying to `address`."""
    total = 0
    for vout in tx.get("vout", []) or []:
        addr = vout.get("scriptpubkey_address", "")
        if addr == address:
            total += int(vout.get("value", 0) or 0)
    return total


def settle_all() -> dict:
    if not btc_payments.is_configured():
        return {"ok": False, "error": "BTC_RECEIVE_ADDRESS not configured"}
    addr = btc_payments.address()
    pending = btc_payments.pending_orders(include_expired=False)
    if not pending:
        return {"ok": True, "scanned": 0, "settled": 0, "address": addr}

    txs = _address_txs(addr)
    if not txs:
        sys.stderr.write(f"[btc_settle] no txs for {addr} or mempool API down\n")
        return {"ok": True, "scanned": 0, "settled": 0, "note": "no chain data"}

    tip = _current_block_height()
    if tip == 0:
        sys.stderr.write("[btc_settle] could not get current tip height; aborting\n")
        return {"ok": False, "error": "no block tip"}

    # Index txs by amount paid TO our address (after confirmations).
    settled_count = 0
    summary: list[dict] = []
    for order in pending:
        order_id = order["order_id"]
        expected = int(order["amount_sats"])
        match_tx = None
        match_confs = 0
        for tx in txs:
            paid = _sats_to_address(tx, addr)
            if paid != expected:
                continue
            confs = _confirmations_for(tx, tip)
            if confs < MIN_CONFIRMATIONS:
                continue
            # Found a matching confirmed tx.
            match_tx = tx
            match_confs = confs
            break

        if not match_tx:
            continue

        tx_hash = match_tx.get("txid", "")
        if not tx_hash:
            continue

        # Mint the claim code + email the buyer.
        claim = credits.new_claim_code()
        credits.add_credits(claim, order.get("email", ""), 10, f"btc:{order_id}")
        try:
            mailer.send_pack_claim_email(order.get("email", ""), claim, 10)
        except Exception as e:
            sys.stderr.write(f"[btc_settle] mail failed for {order_id}: {e}\n")

        btc_payments.mark_settled(order_id, tx_hash, sats_received=expected)
        settled_count += 1
        summary.append({
            "order_id": order_id,
            "tx_hash": tx_hash,
            "confs": match_confs,
            "sats": expected,
        })

    out = {
        "ok": True,
        "ts": _now(),
        "scanned": len(pending),
        "settled": settled_count,
        "address": addr,
        "results": summary,
    }
    sys.stdout.write(json.dumps(out, indent=2) + "\n")
    return out


if __name__ == "__main__":
    result = settle_all()
    sys.exit(0 if result.get("ok") else 1)
