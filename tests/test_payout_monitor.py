"""test_payout_monitor.py — the founder-only historical balance READER.

The direct-BTC order rail was retired on 2026-09-19. payout_monitor lost its
COLLECTOR with it: `_watch_addresses()` sourced every address it polled from
`btc_payments` (the address pool, the single-address fallback, and the
per-order HD-derived addresses in the orders ledger), and that module is
deleted. Nothing can issue an address, so there is nothing new to poll, and
`check_once()` / the Telegram sweep ping went with it.

What this file still covers is what survived and is still reachable:
`/api/founder/payout-status` renders `payout_status()`, and the founder must
go on being able to read historical balances. The tests that exercised
`_watch_addresses` and `check_once` are gone with their subjects — the
retirement itself is covered by tests/test_direct_btc_rail_is_gone.py.

Ledgers are read-only here. Nothing under data/ is written, deleted or
rewritten by this module or these tests; every path is redirected to tmp_path.

Stdlib + pytest only.
"""
from __future__ import annotations

import json

import pytest

import mempool_watcher
import payout_monitor


# ---------------------------------------------------------------------------
# Fixture: redirect every disk-touching path to tmp_path, isolate env
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    # Redirect mempool ledger (shared with mempool_watcher) and payout ledger.
    monkeypatch.setattr(mempool_watcher, "BALANCE_LEDGER", tmp_path / "balance_snapshots.jsonl")
    monkeypatch.setattr(payout_monitor, "PING_LEDGER", tmp_path / "payout_pings.jsonl")
    monkeypatch.setattr(payout_monitor, "COLD_ADDRESS_FILE", tmp_path / "cold_wallet_address.txt")
    monkeypatch.delenv("ORPHO_COLD_ADDRESS", raising=False)
    yield


# ---------------------------------------------------------------------------
# the collector is gone
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ("_watch_addresses", "check_once",
                                  "_send_telegram", "_persist_ping"))
def test_the_collector_is_gone(name) -> None:
    """Each of these either polled addresses the retired rail issued, or fired
    a sweep ping about them. Their absence is asserted rather than assumed: a
    re-added `check_once` would silently start polling mempool.space again."""
    assert not hasattr(payout_monitor, name), (
        f"payout_monitor.{name} came back — it belonged to the retired rail")


def test_the_module_no_longer_imports_the_deleted_order_ledger() -> None:
    """NEGATIVE CONTROL for the assertions above: the module imports cleanly
    and still exposes the reader, so `hasattr` is answering about a real,
    loaded module rather than about an import that quietly failed."""
    assert callable(payout_monitor.payout_status)
    assert not hasattr(payout_monitor, "btc_payments")


# ---------------------------------------------------------------------------
# _cold_address
# ---------------------------------------------------------------------------

def test_cold_address_reads_env_var_first(monkeypatch):
    monkeypatch.setenv("ORPHO_COLD_ADDRESS", "bc1qenv" + "0" * 30)
    payout_monitor.COLD_ADDRESS_FILE.write_text("bc1qfile" + "0" * 30 + "\n")
    assert payout_monitor._cold_address() == "bc1qenv" + "0" * 30


def test_cold_address_falls_back_to_file(monkeypatch):
    monkeypatch.delenv("ORPHO_COLD_ADDRESS", raising=False)
    payout_monitor.COLD_ADDRESS_FILE.write_text("bc1qfile" + "0" * 30 + "\n")
    assert payout_monitor._cold_address() == "bc1qfile" + "0" * 30


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
# payout_status — the kept founder-only read
# ---------------------------------------------------------------------------

def test_payout_status_returns_expected_keys(monkeypatch):
    # Seed one snapshot so latest_snapshot returns it.
    mempool_watcher.BALANCE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    snap = {"total_sats": 1234, "ts": "2026-05-14T00:00:00+00:00",
            "addresses_polled": 2, "addresses_error": 0}
    mempool_watcher.BALANCE_LEDGER.write_text(json.dumps(snap) + "\n")

    monkeypatch.setenv("ORPHO_COLD_ADDRESS", "bc1qcold" + "0" * 30)

    status = payout_monitor.payout_status()
    assert "last_known_balance_sats" in status
    assert "cold_destination" in status
    assert status["last_known_balance_sats"] == 1234
    assert status["cold_destination"] == "bc1qcold" + "0" * 30
    assert status["observed_at"] == "2026-05-14T00:00:00+00:00"
    assert status["snapshot_is_final"] is True


def test_payout_status_reads_a_historical_snapshot_written_before_retirement():
    """The whole reason this endpoint is kept. A snapshot recorded while the
    rail was live must still be readable afterwards — retiring the rail must
    not make the founder's own history unreadable."""
    mempool_watcher.BALANCE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    mempool_watcher.BALANCE_LEDGER.write_text(
        json.dumps({"total_sats": 250_000, "ts": "2026-06-01T00:00:00+00:00",
                    "addresses_polled": 7, "addresses_error": 1}) + "\n")
    status = payout_monitor.payout_status()
    assert status["last_known_balance_sats"] == 250_000
    assert status["observed_at"] == "2026-06-01T00:00:00+00:00"
    assert status["addresses_polled"] == 7
    assert status["addresses_error"] == 1


def test_payout_status_says_the_rail_is_retired():
    status = payout_monitor.payout_status()
    assert status["rail"] == "retired"


def test_payout_status_drops_the_address_pool_size():
    """`address_pool_size` was a property of the retired order rail, not of the
    balance history, and it read through the deleted btc_payments module."""
    assert "pool_size" not in payout_monitor.payout_status()
    assert "address_pool_size" not in payout_monitor.payout_status()


def test_a_stale_snapshot_can_never_produce_an_action_flag():
    """REPLACES test_payout_status_ready_to_sweep_when_above_threshold, which
    pinned the stale behaviour instead of catching it.

    `ready_to_sweep` was `total >= SWEEP_THRESHOLD_SATS` over the newest
    snapshot on disk. The collector is deleted, so that snapshot is frozen: the
    moment the founder swept the wallet, this endpoint would have gone on
    saying "ready to sweep: yes" forever against a balance that is really zero.

    A large balance from an old snapshot must therefore produce NO action flag
    and NO threshold — only the number, when it was observed, and how old that
    observation is."""
    mempool_watcher.BALANCE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    snap = {"total_sats": 9_000_000, "ts": "2026-05-14T00:00:00+00:00"}
    mempool_watcher.BALANCE_LEDGER.write_text(json.dumps(snap) + "\n")

    status = payout_monitor.payout_status()

    for banned in ("ready_to_sweep", "threshold_sats", "threshold_btc",
                   "hot_balance_sats", "hot_balance_btc"):
        assert banned not in status, (
            f"{banned} is derived from a snapshot that can never refresh")

    assert status["last_known_balance_sats"] == 9_000_000
    assert status["observed_at"] == "2026-05-14T00:00:00+00:00"
    assert status["observation_age_days"] is not None
    assert status["observation_age_days"] > 0, (
        "a 2026-05-14 snapshot cannot be zero days old")
    assert status["rail"] == "retired"


def test_the_observation_age_is_measured_not_hardcoded():
    """NEGATIVE CONTROL for observation_age_days. A fresh snapshot must read as
    0 days old, or the field is a constant rather than a measurement."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    mempool_watcher.BALANCE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    mempool_watcher.BALANCE_LEDGER.write_text(
        json.dumps({"total_sats": 5, "ts": now}) + "\n")
    assert payout_monitor.payout_status()["observation_age_days"] == 0


def test_a_missing_snapshot_reports_no_observation_rather_than_zero():
    """No ledger at all must not read as "observed zero sats just now"."""
    status = payout_monitor.payout_status()
    assert status["observed_at"] is None
    assert status["observation_age_days"] is None


def test_the_sweep_threshold_constant_is_gone():
    """It existed only to compute the action flag; leaving it invites it back."""
    assert not hasattr(payout_monitor, "SWEEP_THRESHOLD_SATS")
