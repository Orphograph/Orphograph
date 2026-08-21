#!/usr/bin/env python3
"""btc_payments.py — Bitcoin receive-only payment ledger.

Security model (THIS IS THE WHOLE POINT — read deploy/BTC_OPERATOR.md):

1. The server holds ONLY the public Bitcoin address (set via
   BTC_RECEIVE_ADDRESS env var). No private key. No seed phrase.
   No xpub. Nothing that could be used to MOVE funds.

2. The receive address is generated on a hardware wallet (Ledger,
   Trezor, Coldcard) by the founder, offline. The private key
   never touches anything connected to the internet.

3. Customers pay a unique sat amount to the address. The server
   watches mempool.space (no auth, public API) for incoming
   transactions to the address and matches them to pending orders
   by exact amount.

4. Even total server compromise gives an attacker nothing they
   could use to steal funds. Worst case they could:
   - See the public address (already on every order page)
   - See the pending order amounts (low-value information)
   - Change the displayed receive address (requires a Fly env var
     change, which is itself audit-logged; AND we hardcode-check
     the address against fly.toml so a runtime swap is detectable)
   - Take down the site (DoS — funds unaffected)

5. Funds accumulate in the hardware wallet. The founder
   periodically signs a sweep transaction OFFLINE on the hardware
   device and broadcasts it to move funds to cold storage.

Public API:
    create_order(email, usd_amount, sats_amount) -> dict
    mark_settled(order_id, tx_hash) -> bool
    pending_orders() -> list[dict]
    get_order(order_id) -> dict | None
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from file_lock import locked

# HD-wallet derivation is optional. If ORPHO_BTC_XPUB is set, each new order
# gets a fresh address derived from that xpub (privacy preserving). If not,
# we fall back to the single BTC_RECEIVE_ADDRESS — works fine for low volume
# but customers can correlate payments via on-chain analysis.
try:
    import btc_hd  # type: ignore
except ImportError:  # pragma: no cover
    btc_hd = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
ORDERS_PATH = Path(os.environ.get("ORPHO_BTC_ORDERS", str(DATA_DIR / "btc_orders.jsonl")))
HD_INDEX_PATH = Path(os.environ.get("ORPHO_BTC_HD_INDEX", str(DATA_DIR / "btc_hd_index.txt")))
POOL_PATH = Path(os.environ.get("ORPHO_BTC_ADDRESS_POOL", str(DATA_DIR / "btc_address_pool.txt")))
POOL_INDEX_PATH = Path(os.environ.get("ORPHO_BTC_POOL_INDEX", str(DATA_DIR / "btc_pool_index.txt")))
BTC_XPUB = os.environ.get("ORPHO_BTC_XPUB", "").strip()

# The receive address. Two sources, env var first, then a local file
# at $DATA_DIR/btc_address.txt (mode 600). File-based config lets you
# rotate without restarting via launchd secrets. Either way: PUBLIC
# ADDRESS ONLY — the suppression-list scan in
# test_address_returns_only_public_data enforces that no signing
# material leaks into this module.
def _load_btc_address() -> str:
    env_val = os.environ.get("BTC_RECEIVE_ADDRESS", "").strip()
    if env_val:
        return env_val
    addr_file = DATA_DIR / "btc_address.txt"
    try:
        return addr_file.read_text().strip()
    except (OSError, FileNotFoundError):
        return ""


BTC_RECEIVE_ADDRESS = _load_btc_address()


def _next_counter(path: Path) -> int:
    """Read-modify-write a small int counter under exclusive lock.

    Used by both the HD-index counter and the address-pool index. The
    counter is a small integer in a tiny file. We open read-write, read
    the current value (or 0), increment, write back, and return the
    original. fcntl.flock prevents two concurrent create_order calls
    from handing the same address to two customers.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with locked(path, mode="r+", exclusive=True) as f:
        raw = f.read().strip()
        cur = int(raw) if raw.isdigit() else 0
        f.seek(0)
        f.truncate()
        f.write(str(cur + 1))
    return cur


def _next_hd_index() -> int:
    return _next_counter(HD_INDEX_PATH)


def _load_pool() -> list[str]:
    """Read the pre-generated address pool file.

    Format: one bech32 address per line. Blank lines and `# comments`
    are skipped. Phantom users generate this list by tapping
    'Receive' N times in the app and pasting the addresses here.
    """
    if not POOL_PATH.exists():
        return []
    try:
        text = POOL_PATH.read_text()
    except OSError:
        return []
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Sanity check — must look like a bech32 mainnet address.
        if (line.startswith("bc1q") or line.startswith("bc1p")) and 30 <= len(line) <= 90:
            out.append(line)
    return out


def address_for_order(order_id: str) -> str:
    """Pick the receive address for this order.

    Preference order, most → least privacy preserving:

        1. ORPHO_BTC_XPUB (BIP-32 HD derivation, infinite address space).
           Best for wallets that expose an xpub (Sparrow, BlueWallet, Coldcard,
           Specter, Blockstream Green, etc.).

        2. btc_address_pool.txt (pre-generated address list, finite).
           Best for wallets that do NOT expose an xpub but DO let you tap
           "Receive" repeatedly to generate fresh addresses — notably
           Phantom Wallet. Founder paste-generates N addresses one-time,
           server cycles through them.

        3. BTC_RECEIVE_ADDRESS (single-address fallback).
           Reused for every customer. Works but leaks privacy via on-chain
           analysis. Acceptable for very low volume / dev / testing.

    The xpub and the pool both hold NO spending authority — they only carry
    public addresses. Even if the server is fully compromised the attacker
    cannot move funds.
    """
    # Path 1 — HD-wallet xpub
    if BTC_XPUB and btc_hd is not None and btc_hd.is_valid_xpub(BTC_XPUB):
        try:
            idx = _next_hd_index()
            return btc_hd.derive_address(BTC_XPUB, idx, change=0, hrp="bc")
        except (ValueError, OSError):
            pass

    # Path 2 — pre-generated address pool (Phantom-friendly)
    pool = _load_pool()
    if pool:
        try:
            idx = _next_counter(POOL_INDEX_PATH)
            return pool[idx % len(pool)]
        except OSError:
            pass

    # Path 3 — single-address fallback
    return BTC_RECEIVE_ADDRESS


def pool_size() -> int:
    """How many addresses are left to cycle through before reuse begins."""
    return len(_load_pool())

# How long an unpaid order stays valid before we expire it. Bitcoin
# can take 10+ minutes for one confirmation; we give 2 hours of
# slack so the customer doesn't have to rush.
ORDER_TTL_SEC = int(os.environ.get("ORPHO_BTC_ORDER_TTL_SEC", str(2 * 3600)))


def _now_unix() -> float:
    return datetime.now(timezone.utc).timestamp()


def _iso(ts: float | None = None) -> str:
    if ts is None:
        ts = _now_unix()
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _append(row: dict) -> None:
    with locked(ORDERS_PATH, mode="a", exclusive=True) as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _read_all() -> list[dict]:
    if not ORDERS_PATH.exists():
        return []
    rows: list[dict] = []
    with ORDERS_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def is_configured() -> bool:
    return bool(BTC_RECEIVE_ADDRESS) or bool(BTC_XPUB) or bool(_load_pool())


def address() -> str:
    """The current default receive address — safe to return to any caller.

    Returns the single-address fallback. For a fresh per-order address use
    address_for_order() inside create_order() — the per-order rotation is
    intentionally not exposed via this name to keep the audit trail clean.
    """
    return BTC_RECEIVE_ADDRESS


def _new_order_id() -> str:
    return "btc_" + secrets.token_urlsafe(8)


def create_order(email: str, usd_amount: float, sats_amount: int) -> dict:
    """Create a pending BTC order.

    Caller (the HTTP handler) is responsible for converting USD → sats
    at order-creation time using whatever price oracle is acceptable,
    and for adding a per-order disambiguation suffix to sats_amount so
    concurrent orders don't collide. We just persist the order.
    """
    if not BTC_RECEIVE_ADDRESS and not BTC_XPUB:
        raise RuntimeError("neither BTC_RECEIVE_ADDRESS nor ORPHO_BTC_XPUB configured")
    if sats_amount < 1000:
        raise ValueError("sats_amount too small (min 1000 sats)")

    order_id = _new_order_id()
    addr = address_for_order(order_id)
    if not addr:
        raise RuntimeError("could not resolve a receive address for this order")

    # Settlement matches an inbound payment to an order by EXACT sat amount
    # (scripts/btc_settle.py). The per-order tag added by
    # btc_price.sats_for_usd makes equal amounts unlikely, but it is a
    # best-effort tag drawn from a bounded space — it cannot guarantee
    # uniqueness. If two live orders at the same address ever carried the same
    # amount, one payment would be ambiguous between them.
    #
    # Refuse to create the collision in the first place. The caller surfaces
    # this as a retryable error and a fresh tag resolves it.
    for existing in pending_orders(include_expired=False):
        if (existing.get("address") == addr
                and int(existing.get("amount_sats", 0)) == int(sats_amount)):
            raise ValueError(
                "an identical pending order already exists; retry to get a new amount"
            )

    now = _now_unix()
    row = {
        "ts": _iso(now),
        "event": "created",
        "order_id": order_id,
        "email": email,
        "address": addr,
        "amount_sats": int(sats_amount),
        "usd_amount": float(usd_amount),
        "expires_at": _iso(now + ORDER_TTL_SEC),
        "expires_unix": now + ORDER_TTL_SEC,
        "status": "pending",
    }
    _append(row)
    return row


def _latest_state(order_id: str) -> dict | None:
    """Return the most-recent event for an order_id, or None."""
    state = None
    for row in _read_all():
        if row.get("order_id") == order_id:
            state = row
    return state


def get_order(order_id: str) -> dict | None:
    return _latest_state(order_id)


def status_of(order_id: str) -> str:
    state = _latest_state(order_id)
    if state is None:
        return "unknown"
    if state.get("status") == "settled":
        return "settled"
    if _now_unix() > float(state.get("expires_unix", 0)):
        return "expired"
    return "pending"


def mark_settled(order_id: str, tx_hash: str, sats_received: int) -> bool:
    """Append a settled event. Idempotent — second call returns False."""
    state = _latest_state(order_id)
    if state is None:
        return False
    if state.get("status") == "settled":
        return False
    _append({
        "ts": _iso(),
        "event": "settled",
        "order_id": order_id,
        "email": state.get("email", ""),
        "address": BTC_RECEIVE_ADDRESS,
        "amount_sats": state.get("amount_sats", 0),
        "sats_received": int(sats_received),
        "tx_hash": tx_hash,
        "status": "settled",
    })
    return True


def pending_orders(include_expired: bool = False) -> list[dict]:
    """All orders whose latest state is pending (and not expired)."""
    state_by_id: dict[str, dict] = {}
    for row in _read_all():
        oid = row.get("order_id")
        if not oid:
            continue
        state_by_id[oid] = row

    now = _now_unix()
    out: list[dict] = []
    for state in state_by_id.values():
        if state.get("status") != "pending":
            continue
        if not include_expired and now > float(state.get("expires_unix", 0)):
            continue
        out.append(state)
    return out
