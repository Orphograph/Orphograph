"""Tests for server/btc_price.py — fallback chain, caching, source labels.

We mock urllib.request.urlopen to simulate each oracle responding or failing,
then verify:
  - the fallback chain runs in order mempool → coinbase → kraken
  - the 60s in-process cache short-circuits subsequent calls
  - get_usd_per_btc() always returns a float (never None)
  - get_usd_per_btc_source() returns the correct source label per scenario
"""
from __future__ import annotations

import io
import json
import urllib.error
from typing import Callable

import pytest

import btc_price


# ---------------------------------------------------------------------------
# Fake HTTP response plumbing
# ---------------------------------------------------------------------------

class _FakeResponse:
    """Mimics the context-manager response from urllib.request.urlopen."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


def _make_urlopen(handlers: dict[str, Callable[[], _FakeResponse]]):
    """Build a fake urlopen that dispatches by URL prefix.

    handlers maps a hostname substring (e.g. "mempool.space") to a zero-arg
    callable that either returns a _FakeResponse or raises.
    """
    def _fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for needle, factory in handlers.items():
            if needle in url:
                return factory()
        raise urllib.error.URLError(f"no handler for {url}")
    return _fake_urlopen


def _ok(body: dict):
    """Handler factory: return a JSON 200."""
    encoded = json.dumps(body).encode("utf-8")
    return lambda: _FakeResponse(encoded)


def _fail(exc: Exception | None = None):
    """Handler factory: raise (default URLError)."""
    def _raise():
        raise exc or urllib.error.URLError("simulated outage")
    return _raise


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    btc_price._reset_cache_for_tests()
    yield
    btc_price._reset_cache_for_tests()


# ---------------------------------------------------------------------------
# Happy-path: each oracle individually
# ---------------------------------------------------------------------------

def test_mempool_primary_used_when_healthy(monkeypatch):
    handlers = {
        "mempool.space": _ok({"USD": 60000, "EUR": 55000}),
        "coinbase.com": _fail(),
        "kraken.com": _fail(),
    }
    monkeypatch.setattr(btc_price.urllib.request, "urlopen", _make_urlopen(handlers))

    price, source = btc_price.get_usd_per_btc_source()
    assert price == 60000.0
    assert source == "mempool"
    assert isinstance(price, float)


def test_coinbase_fallback_when_mempool_fails(monkeypatch):
    handlers = {
        "mempool.space": _fail(),
        "coinbase.com": _ok({"data": {"amount": "61234.56", "currency": "USD"}}),
        "kraken.com": _fail(),
    }
    monkeypatch.setattr(btc_price.urllib.request, "urlopen", _make_urlopen(handlers))

    price, source = btc_price.get_usd_per_btc_source()
    assert price == 61234.56
    assert source == "coinbase"


def test_kraken_fallback_when_mempool_and_coinbase_fail(monkeypatch):
    handlers = {
        "mempool.space": _fail(),
        "coinbase.com": _fail(),
        "kraken.com": _ok({
            "error": [],
            "result": {"XXBTZUSD": {"c": ["62345.7", "0.123"]}},
        }),
    }
    monkeypatch.setattr(btc_price.urllib.request, "urlopen", _make_urlopen(handlers))

    price, source = btc_price.get_usd_per_btc_source()
    assert price == 62345.7
    assert source == "kraken"


def test_all_oracles_fail_returns_zero_and_none(monkeypatch):
    handlers = {
        "mempool.space": _fail(),
        "coinbase.com": _fail(),
        "kraken.com": _fail(),
    }
    monkeypatch.setattr(btc_price.urllib.request, "urlopen", _make_urlopen(handlers))

    price, source = btc_price.get_usd_per_btc_source()
    assert price == 0.0
    assert source == "none"
    # Must always be a float, never None.
    assert isinstance(price, float)


# ---------------------------------------------------------------------------
# Fallback chain ordering
# ---------------------------------------------------------------------------

def test_fallback_order_mempool_preferred_over_coinbase(monkeypatch):
    # Both healthy — mempool wins.
    handlers = {
        "mempool.space": _ok({"USD": 60000}),
        "coinbase.com": _ok({"data": {"amount": "70000.00"}}),
        "kraken.com": _ok({"result": {"XXBTZUSD": {"c": ["80000.0", "0.1"]}}}),
    }
    monkeypatch.setattr(btc_price.urllib.request, "urlopen", _make_urlopen(handlers))

    price, source = btc_price.get_usd_per_btc_source()
    assert source == "mempool"
    assert price == 60000.0


def test_fallback_order_coinbase_preferred_over_kraken(monkeypatch):
    handlers = {
        "mempool.space": _fail(),
        "coinbase.com": _ok({"data": {"amount": "70000.00"}}),
        "kraken.com": _ok({"result": {"XXBTZUSD": {"c": ["80000.0", "0.1"]}}}),
    }
    monkeypatch.setattr(btc_price.urllib.request, "urlopen", _make_urlopen(handlers))

    price, source = btc_price.get_usd_per_btc_source()
    assert source == "coinbase"
    assert price == 70000.0


def test_zero_price_from_oracle_treated_as_failure(monkeypatch):
    # Mempool returns USD=0 (degenerate) → should fall through to coinbase.
    handlers = {
        "mempool.space": _ok({"USD": 0}),
        "coinbase.com": _ok({"data": {"amount": "55555.55"}}),
        "kraken.com": _fail(),
    }
    monkeypatch.setattr(btc_price.urllib.request, "urlopen", _make_urlopen(handlers))

    price, source = btc_price.get_usd_per_btc_source()
    assert price == 55555.55
    assert source == "coinbase"


def test_malformed_json_triggers_fallback(monkeypatch):
    # Mempool returns non-dict garbage → fall through.
    bad_body = b"not json at all"
    handlers = {
        "mempool.space": lambda: _FakeResponse(bad_body),
        "coinbase.com": _ok({"data": {"amount": "44444.44"}}),
        "kraken.com": _fail(),
    }
    monkeypatch.setattr(btc_price.urllib.request, "urlopen", _make_urlopen(handlers))

    price, source = btc_price.get_usd_per_btc_source()
    assert price == 44444.44
    assert source == "coinbase"


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def test_cache_holds_for_60_seconds(monkeypatch):
    calls = {"n": 0}

    def _counted_urlopen(req, timeout=None):
        calls["n"] += 1
        return _FakeResponse(json.dumps({"USD": 60000}).encode("utf-8"))

    monkeypatch.setattr(btc_price.urllib.request, "urlopen", _counted_urlopen)

    # Freeze time.
    fake_now = [1_000_000.0]
    monkeypatch.setattr(btc_price.time, "time", lambda: fake_now[0])

    price1, source1 = btc_price.get_usd_per_btc_source()
    assert price1 == 60000.0
    assert source1 == "mempool"
    assert calls["n"] == 1

    # +30s later — still cached.
    fake_now[0] += 30
    price2, source2 = btc_price.get_usd_per_btc_source()
    assert price2 == 60000.0
    assert source2 == "cache"
    assert calls["n"] == 1  # no new HTTP call

    # +59s total — still cached.
    fake_now[0] += 29
    price3, source3 = btc_price.get_usd_per_btc_source()
    assert source3 == "cache"
    assert calls["n"] == 1


def test_cache_expires_after_60_seconds(monkeypatch):
    calls = {"n": 0}

    def _counted_urlopen(req, timeout=None):
        calls["n"] += 1
        return _FakeResponse(json.dumps({"USD": 60000 + calls["n"]}).encode("utf-8"))

    monkeypatch.setattr(btc_price.urllib.request, "urlopen", _counted_urlopen)

    fake_now = [1_000_000.0]
    monkeypatch.setattr(btc_price.time, "time", lambda: fake_now[0])

    price1, _ = btc_price.get_usd_per_btc_source()
    assert price1 == 60001.0
    assert calls["n"] == 1

    # 61s later — cache stale, refetch.
    fake_now[0] += 61
    price2, source2 = btc_price.get_usd_per_btc_source()
    assert price2 == 60002.0
    assert source2 == "mempool"
    assert calls["n"] == 2


def test_failed_lookup_not_cached(monkeypatch):
    """If all oracles fail, the next call must retry (don't cache failures)."""
    calls = {"n": 0}

    def _all_fail(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.URLError("down")

    monkeypatch.setattr(btc_price.urllib.request, "urlopen", _all_fail)

    fake_now = [1_000_000.0]
    monkeypatch.setattr(btc_price.time, "time", lambda: fake_now[0])

    p1, s1 = btc_price.get_usd_per_btc_source()
    assert p1 == 0.0 and s1 == "none"
    n_after_first = calls["n"]

    # Immediate retry — must hit oracles again (3 more calls), not return cache.
    p2, s2 = btc_price.get_usd_per_btc_source()
    assert p2 == 0.0 and s2 == "none"
    assert calls["n"] > n_after_first


# ---------------------------------------------------------------------------
# Return-type contract
# ---------------------------------------------------------------------------

def test_get_usd_per_btc_always_returns_float(monkeypatch):
    # Healthy case.
    monkeypatch.setattr(
        btc_price.urllib.request, "urlopen",
        _make_urlopen({"mempool.space": _ok({"USD": 60000})}),
    )
    p = btc_price.get_usd_per_btc()
    assert isinstance(p, float)
    assert p == 60000.0

    # Failure case.
    btc_price._reset_cache_for_tests()
    monkeypatch.setattr(
        btc_price.urllib.request, "urlopen",
        _make_urlopen({
            "mempool.space": _fail(),
            "coinbase.com": _fail(),
            "kraken.com": _fail(),
        }),
    )
    p2 = btc_price.get_usd_per_btc()
    assert isinstance(p2, float)
    assert p2 == 0.0
    assert p2 is not None


# ---------------------------------------------------------------------------
# Endpoint shape regression guards
# ---------------------------------------------------------------------------

def test_coinbase_amount_is_parsed_as_string(monkeypatch):
    # Coinbase returns amount as a STRING — must parse via float().
    monkeypatch.setattr(
        btc_price.urllib.request, "urlopen",
        _make_urlopen({
            "mempool.space": _fail(),
            "coinbase.com": _ok({"data": {"amount": "12345.67", "currency": "USD"}}),
        }),
    )
    p, s = btc_price.get_usd_per_btc_source()
    assert p == 12345.67
    assert s == "coinbase"


def test_kraken_c_array_first_element_is_used(monkeypatch):
    # Kraken's c[] is [last_trade_price, last_trade_volume]; we want c[0].
    monkeypatch.setattr(
        btc_price.urllib.request, "urlopen",
        _make_urlopen({
            "mempool.space": _fail(),
            "coinbase.com": _fail(),
            "kraken.com": _ok({
                "error": [],
                "result": {"XXBTZUSD": {
                    "c": ["99999.9", "0.42"],
                    "a": ["100000.0", "1", "1.000"],
                    "b": ["99998.0", "1", "1.000"],
                }},
            }),
        }),
    )
    p, s = btc_price.get_usd_per_btc_source()
    assert p == 99999.9
    assert s == "kraken"
