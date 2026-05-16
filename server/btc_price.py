#!/usr/bin/env python3
"""btc_price.py — current BTC/USD price for order creation.

Queries three public spot oracles in order of preference:
  1. mempool.space   /api/v1/prices                 (whole-dollar int)
  2. Coinbase        /v2/prices/spot?currency=USD   (string `data.amount`)
  3. Kraken          /0/public/Ticker?pair=XBTUSD   (string `result.XXBTZUSD.c[0]`)

The first oracle to return a non-zero price wins. Results are cached in-process
for 60 seconds. If all three oracles fail and the cache is empty/stale, the
public callers receive 0.0 (and handle order rejection upstream — we never
raise).

Public API:
    get_usd_per_btc() -> float
        Returns current BTC/USD spot, or 0.0 on total oracle failure.
    get_usd_per_btc_source() -> tuple[float, str]
        Returns (price, source) where source is one of:
        "mempool" | "coinbase" | "kraken" | "cache" | "none".
    current_price_usd() -> float
        Legacy alias for get_usd_per_btc(). Preserved for existing callers.
    sats_for_usd(usd_amount, suffix=None) -> int
        Convert USD → sats at the current BTC/USD price, with optional
        4-digit deterministic suffix so concurrent orders don't collide
        on-chain.

Stdlib only. No third-party imports. Loopback-only by virtue of being a
module: callers run on localhost.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request

MEMPOOL_URL = "https://mempool.space/api/v1/prices"
COINBASE_URL = "https://api.coinbase.com/v2/prices/spot?currency=USD"
KRAKEN_URL = "https://api.kraken.com/0/public/Ticker?pair=XBTUSD"

HTTP_TIMEOUT = 5  # seconds per oracle request
CACHE_SEC = 60    # one-minute in-process cache

_lock = threading.Lock()
# Single global cached value (no key — there's only one BTC/USD price).
_cache: dict = {
    "price": 0.0,
    "source": "none",
    "ts": 0.0,
}


def _http_get_json(url: str) -> dict | None:
    """Fetch a URL and decode the JSON body. Returns None on any failure."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "orphograph/0.1 (stdlib)"},
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, ValueError, OSError, TimeoutError):
        return None


def _fetch_mempool() -> float:
    """Primary: mempool.space. Response shape: {"USD": 60000, ...}"""
    data = _http_get_json(MEMPOOL_URL)
    if not isinstance(data, dict):
        return 0.0
    try:
        return float(data.get("USD", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _fetch_coinbase() -> float:
    """Secondary: Coinbase. Response shape: {"data": {"amount": "60000.00"}}"""
    data = _http_get_json(COINBASE_URL)
    if not isinstance(data, dict):
        return 0.0
    try:
        amount = data["data"]["amount"]
        return float(amount)
    except (KeyError, TypeError, ValueError):
        return 0.0


def _fetch_kraken() -> float:
    """Tertiary: Kraken. Response shape:
    {"result": {"XXBTZUSD": {"c": ["60000.0", "0.123"], ...}}}
    """
    data = _http_get_json(KRAKEN_URL)
    if not isinstance(data, dict):
        return 0.0
    try:
        c = data["result"]["XXBTZUSD"]["c"]
        return float(c[0])
    except (KeyError, TypeError, ValueError, IndexError):
        return 0.0


# Fallback chain in order of preference.
_SOURCES = (
    ("mempool", _fetch_mempool),
    ("coinbase", _fetch_coinbase),
    ("kraken", _fetch_kraken),
)


def _log(msg: str) -> None:
    """Log to stderr (callers can redirect)."""
    print(f"[btc_price] {msg}", file=sys.stderr)


def get_usd_per_btc_source() -> tuple[float, str]:
    """Return (price, source) tuple.

    source ∈ {"mempool", "coinbase", "kraken", "cache", "none"}.
    Never raises; on total failure returns (0.0, "none").
    """
    now = time.time()
    with _lock:
        cached_price = _cache["price"]
        cached_ts = _cache["ts"]
    if cached_price > 0 and (now - cached_ts) < CACHE_SEC:
        return cached_price, "cache"

    for name, fetcher in _SOURCES:
        price = fetcher()
        if price > 0:
            with _lock:
                _cache["price"] = price
                _cache["source"] = name
                _cache["ts"] = now
            _log(f"price ${price:,.2f} from {name}")
            return price, name

    _log("all oracles failed; returning 0.0")
    return 0.0, "none"


def cached_usd_per_btc_source() -> tuple[float, str]:
    """Return the in-process cached BTC/USD price without network I/O.

    Public health/status endpoints call this so polling them cannot fan out
    to third-party price oracles or hang on network timeouts.
    """
    with _lock:
        price = float(_cache.get("price") or 0.0)
        source = str(_cache.get("source") or "none")
    return price, source if price > 0 else "none"


def get_usd_per_btc() -> float:
    """Cached BTC/USD price. Returns 0.0 if every oracle is unreachable."""
    price, _ = get_usd_per_btc_source()
    return price


# --- Legacy aliases preserved for existing callers ------------------------

def current_price_usd() -> float:
    """Legacy name preserved for existing callers in server/app.py."""
    return get_usd_per_btc()


def sats_for_usd(usd_amount: float, suffix: int | None = None) -> int:
    """Convert USD → sats at current BTC/USD price.

    Returns 0 if the price feed is unreachable (caller should reject
    the order and ask the user to try again in a minute).

    If `suffix` is provided (0–9999), it replaces the last 4 sat digits
    so each order has a unique exact amount. Without a suffix, two
    concurrent orders for the same USD value would be indistinguishable
    on-chain.
    """
    if usd_amount <= 0:
        return 0
    price = get_usd_per_btc()
    if price <= 0:
        return 0
    # 1 BTC = 100_000_000 sats
    sats = int(round((usd_amount / price) * 100_000_000))
    # Floor: 1000 sats minimum (~$0.50 at $50k/BTC) to keep above dust.
    if sats < 1000:
        sats = 1000
    if suffix is not None:
        suffix = int(suffix) % 10000
        # Strip the last 4 digits and replace.
        base = (sats // 10000) * 10000
        sats = base + suffix
    return sats


def _reset_cache_for_tests() -> None:
    """Test helper: clear the module-level cache."""
    with _lock:
        _cache["price"] = 0.0
        _cache["source"] = "none"
        _cache["ts"] = 0.0
