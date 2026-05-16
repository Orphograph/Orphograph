"""test_payout_monitor.py — unit tests for the hot-wallet payout monitor.

Stdlib + pytest only. HTTP calls are routed through monkeypatched urlopen,
Telegram notifier subprocess calls are stubbed.
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request

import pytest

import btc_payments
import mempool_watcher
import payout_monitor


# ---------------------------------------------------------------------------
# Fake urlopen helpers
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _mempool_payload(confirmed_net=0, unconfirmed_net=0, chain_tx=0, mem_tx=0):
    return {
        "address": "bc1qfake",
        "chain_stats": {
            "funded_txo_sum": confirmed_net,
            "spent_txo_sum": 0,
            "tx_count": chain_tx,
        },
        "mempool_stats": {
            "funded_txo_sum": unconfirmed_net,
            "spent_txo_sum": 0,
            "tx_count": mem_tx,
        },
    }


def _payload_urlopen(payload_dict):
    def _fake(req, timeout=0):
        return _FakeResp(json.dumps(payload_dict).encode())
    return _fake


def _stub_subprocess_run(monkeypatch, calls):
    def fake_run(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(
            args=args[0] if args else [], returncode=0, stdout="", stderr=""
        )
    monkeypatch.setattr(subprocess, "run", fake_run)


# ---------------------------------------------------------------------------
# Fixture: redirect every disk-touching path to tmp_path, isolate env
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    # Redirect mempool ledger (shared with mempool_watcher) and payout ledger.
    monkeypatch.setattr(mempool_watcher, "BALANCE_LEDGER", tmp_path / "balance_snapshots.jsonl")
    monkeypatch.setattr(payout_monitor, "PING_LEDGER", tmp_path / "payout_pings.jsonl")
    monkeypatch.setattr(payout_monitor, "COLD_ADDRESS_FILE", tmp_path / "cold_wallet_address.txt")

    # Suppress polite sleep in watch_balances.
    monkeypatch.setattr(mempool_watcher.time, "sleep", lambda *_a, **_k: None)

    # Clear payment env vars so each test can opt in.
    monkeypatch.delenv("ORPHO_BTC_XPUB", raising=False)
    monkeypatch.delenv("ORPHO_COLD_ADDRESS", raising=False)

    # Clear module-level state on btc_payments — these are bound at import time.
    monkeypatch.setattr(btc_payments, "BTC_XPUB", "")
    monkeypatch.setattr(btc_payments, "BTC_RECEIVE_ADDRESS", "")
    # Point the address-pool file at a tmp location so tests can write to it.
    monkeypatch.setattr(btc_payments, "POOL_PATH", tmp_path / "btc_address_pool.txt")

    yield


# ---------------------------------------------------------------------------
# _watch_addresses
# ---------------------------------------------------------------------------

def test_watch_addresses_returns_pool_when_xpub_unset(monkeypatch, tmp_path):
    """Pool file populated + a single BTC_RECEIVE_ADDRESS — both should be included,
    no duplicates."""
    pool = ["bc1qaaa" + "0" * 30, "bc1qbbb" + "0" * 30]
    btc_payments.POOL_PATH.write_text("\n".join(pool) + "\n")
    monkeypatch.setattr(btc_payments, "BTC_RECEIVE_ADDRESS", "bc1qccc" + "0" * 30)

    addrs = payout_monitor._watch_addresses()
    assert pool[0] in addrs
    assert pool[1] in addrs
    assert ("bc1qccc" + "0" * 30) in addrs
    assert len(addrs) == 3


def test_watch_addresses_dedupes_single_address(monkeypatch):
    """If BTC_RECEIVE_ADDRESS is already in the pool, don't double-list it."""
    addr = "bc1qaaa" + "0" * 30
    btc_payments.POOL_PATH.write_text(addr + "\n")
    monkeypatch.setattr(btc_payments, "BTC_RECEIVE_ADDRESS", addr)

    addrs = payout_monitor._watch_addresses()
    assert addrs.count(addr) == 1


def test_watch_addresses_empty_when_nothing_configured():
    assert payout_monitor._watch_addresses() == []


# ---------------------------------------------------------------------------
# _cold_address
# ---------------------------------------------------------------------------

def test_cold_address_reads_env_var_first(monkeypatch):
    monkeypatch.setenv("ORPHO_COLD_ADDRESS", "bc1qcoldfromenv" + "0" * 30)
    # Even if the file exists, env wins.
    payout_monitor.COLD_ADDRESS_FILE.write_text("bc1qcoldfromfile" + "0" * 30)
    assert payout_monitor._cold_address() == "bc1qcoldfromenv" + "0" * 30


def test_cold_address_falls_back_to_file(monkeypatch):
    monkeypatch.delenv("ORPHO_COLD_ADDRESS", raising=False)
    payout_monitor.COLD_ADDRESS_FILE.write_text("bc1qcoldfromfile" + "0" * 30 + "\n")
    assert payout_monitor._cold_address() == "bc1qcoldfromfile" + "0" * 30


def test_cold_address_empty_when_neither_set(monkeypatch):
    monkeypatch.delenv("ORPHO_COLD_ADDRESS", raising=False)
    # File does not exist — _cold_address must return "".
    assert payout_monitor._cold_address() == ""


# ---------------------------------------------------------------------------
# _last_ping_ts
# ---------------------------------------------------------------------------

def test_last_ping_ts_zero_when_empty():
    assert payout_monitor._last_ping_ts() == 0.0


def test_last_ping_ts_returns_latest():
    payout_monitor.PING_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with payout_monitor.PING_LEDGER.open("w") as f:
        f.write(json.dumps({"ts_unix": 100.0, "total_sats": 1}) + "\n")
        f.write(json.dumps({"ts_unix": 500.0, "total_sats": 2}) + "\n")
        f.write(json.dumps({"ts_unix": 300.0, "total_sats": 3}) + "\n")
    assert payout_monitor._last_ping_ts() == 500.0


def test_last_ping_ts_skips_malformed_lines():
    payout_monitor.PING_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with payout_monitor.PING_LEDGER.open("w") as f:
        f.write("not json at all\n")
        f.write("\n")
        f.write(json.dumps({"ts_unix": 42.0}) + "\n")
    assert payout_monitor._last_ping_ts() == 42.0


# ---------------------------------------------------------------------------
# payout_status
# ---------------------------------------------------------------------------

def test_payout_status_returns_expected_keys(monkeypatch):
    # Seed one snapshot so latest_snapshot returns it.
    mempool_watcher.BALANCE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    snap = {"total_sats": 1234, "ts": "2026-05-14T00:00:00+00:00",
            "addresses_polled": 2, "addresses_error": 0}
    mempool_watcher.BALANCE_LEDGER.write_text(json.dumps(snap) + "\n")

    monkeypatch.setenv("ORPHO_COLD_ADDRESS", "bc1qcold" + "0" * 30)

    status = payout_monitor.payout_status()
    assert "hot_balance_sats" in status
    assert "ready_to_sweep" in status
    assert "cold_destination" in status
    assert status["hot_balance_sats"] == 1234
    assert status["cold_destination"] == "bc1qcold" + "0" * 30
    assert status["ready_to_sweep"] is False  # 1234 < default 500_000
    assert status["last_snapshot_at"] == "2026-05-14T00:00:00+00:00"


def test_payout_status_ready_to_sweep_when_above_threshold(monkeypatch):
    mempool_watcher.BALANCE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    snap = {"total_sats": 9_000_000, "ts": "2026-05-14T00:00:00+00:00"}
    mempool_watcher.BALANCE_LEDGER.write_text(json.dumps(snap) + "\n")

    status = payout_monitor.payout_status()
    assert status["ready_to_sweep"] is True
    assert status["hot_balance_sats"] == 9_000_000


# ---------------------------------------------------------------------------
# check_once
# ---------------------------------------------------------------------------

def test_check_once_skips_when_no_addresses_configured():
    result = payout_monitor.check_once(force_ping=False)
    assert result.get("skipped") == "no addresses configured"


def test_check_once_below_threshold_does_not_telegram(monkeypatch):
    """Hot balance well below threshold — must not invoke the notifier."""
    addr = "bc1qaaa" + "0" * 30
    monkeypatch.setattr(btc_payments, "BTC_RECEIVE_ADDRESS", addr)

    payload = _mempool_payload(confirmed_net=1000, unconfirmed_net=0,
                                chain_tx=1, mem_tx=0)
    monkeypatch.setattr(urllib.request, "urlopen", _payload_urlopen(payload))

    calls = []
    _stub_subprocess_run(monkeypatch, calls)

    snap = payout_monitor.check_once(force_ping=False)
    assert snap["pinged"] is False
    assert snap.get("reason") == "below_threshold"
    assert snap["total_sats"] == 1000
    # subprocess.run MUST NOT have been invoked.
    assert calls == []


def test_check_once_force_ping_invokes_notifier(monkeypatch):
    addr = "bc1qaaa" + "0" * 30
    monkeypatch.setattr(btc_payments, "BTC_RECEIVE_ADDRESS", addr)

    payload = _mempool_payload(confirmed_net=200, unconfirmed_net=0,
                                chain_tx=1, mem_tx=0)
    monkeypatch.setattr(urllib.request, "urlopen", _payload_urlopen(payload))

    # Pretend the notifier exists at its expected path.
    monkeypatch.setattr(payout_monitor.NOTIFIER.__class__, "exists",
                        lambda self: True)

    calls = []
    _stub_subprocess_run(monkeypatch, calls)

    snap = payout_monitor.check_once(force_ping=True)
    assert snap["pinged"] is True
    assert len(calls) == 1
    # First positional arg is the subprocess command list.
    cmd = calls[0]["args"][0]
    assert "python3" in cmd[0] or cmd[0].endswith("python3")
    # The message is passed as positional argv (not --text flag).
    # Confirm by checking one of its phrases appears in the cmd list.
    assert any("Orphograph hot wallet" in arg for arg in cmd), f"message not in cmd: {cmd}"
    # Ping ledger should have one row now.
    assert payout_monitor.PING_LEDGER.exists()


def test_check_once_above_threshold_pings_once(monkeypatch):
    """Total sats >= threshold + cooldown clear -> notifier fires exactly once."""
    addr = "bc1qaaa" + "0" * 30
    monkeypatch.setattr(btc_payments, "BTC_RECEIVE_ADDRESS", addr)

    # 600_000 sats > default 500_000 threshold.
    payload = _mempool_payload(confirmed_net=600_000, unconfirmed_net=0,
                                chain_tx=2, mem_tx=0)
    monkeypatch.setattr(urllib.request, "urlopen", _payload_urlopen(payload))

    monkeypatch.setattr(payout_monitor.NOTIFIER.__class__, "exists",
                        lambda self: True)

    calls = []
    _stub_subprocess_run(monkeypatch, calls)

    snap = payout_monitor.check_once(force_ping=False)
    assert snap["pinged"] is True
    assert snap["total_sats"] == 600_000
    assert len(calls) == 1
