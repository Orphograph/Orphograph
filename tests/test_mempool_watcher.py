"""test_mempool_watcher.py — unit tests for the mempool/blockstream poller.

Stdlib + pytest only. All network calls are monkeypatched via urllib.request.urlopen.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

import mempool_watcher


# ---------------------------------------------------------------------------
# Helpers — fake urlopen context-manager
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


def _mempool_payload(funded=120_000, spent=20_000, mem_funded=5_000, mem_spent=0,
                     chain_tx=4, mem_tx=1):
    return {
        # No "address" echo by default: the watcher accepts its absence, and
        # several tests serve one payload for MANY queried addresses. A test
        # exercising the subject-mismatch guard sets the key explicitly.
        "chain_stats": {
            "funded_txo_sum": funded,
            "spent_txo_sum": spent,
            "tx_count": chain_tx,
        },
        "mempool_stats": {
            "funded_txo_sum": mem_funded,
            "spent_txo_sum": mem_spent,
            "tx_count": mem_tx,
        },
    }


@pytest.fixture(autouse=True)
def _redirect_ledger(tmp_path, monkeypatch):
    """Redirect the BALANCE_LEDGER to tmp_path so tests can't pollute real data."""
    monkeypatch.setattr(mempool_watcher, "BALANCE_LEDGER", tmp_path / "balance_snapshots.jsonl")
    # Disable inter-address sleep so the suite stays fast.
    monkeypatch.setattr(mempool_watcher.time, "sleep", lambda *_a, **_k: None)
    yield


# ---------------------------------------------------------------------------
# address_balance_sats
# ---------------------------------------------------------------------------

def test_address_balance_sats_returns_tuple_on_success(monkeypatch):
    payload = _mempool_payload(funded=120_000, spent=20_000,
                                mem_funded=5_000, mem_spent=0,
                                chain_tx=4, mem_tx=1)

    def fake_urlopen(req, timeout=0):
        return _FakeResp(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    confirmed, unconfirmed, tx_count = mempool_watcher.address_balance_sats("bc1qfake")
    assert confirmed == 100_000
    assert unconfirmed == 5_000
    assert tx_count == 5


def test_address_balance_sats_falls_back_to_blockstream(monkeypatch):
    """First call (mempool.space) raises URLError; blockstream.info succeeds."""
    calls = []
    payload = _mempool_payload(funded=200_000, spent=100_000,
                                mem_funded=0, mem_spent=0,
                                chain_tx=2, mem_tx=0)

    def fake_urlopen(req, timeout=0):
        calls.append(req.full_url)
        if mempool_watcher.MEMPOOL_BASE in req.full_url:
            raise urllib.error.URLError("network down")
        return _FakeResp(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    confirmed, unconfirmed, tx_count = mempool_watcher.address_balance_sats("bc1qfake")
    assert confirmed == 100_000
    assert unconfirmed == 0
    assert tx_count == 2
    # Must have tried mempool first, then blockstream.
    assert len(calls) == 2
    assert mempool_watcher.MEMPOOL_BASE in calls[0]
    assert "blockstream.info" in calls[1]


def test_address_balance_sats_returns_neg_when_both_fail(monkeypatch):
    def fake_urlopen(req, timeout=0):
        raise urllib.error.URLError("everything is down")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    confirmed, unconfirmed, tx_count = mempool_watcher.address_balance_sats("bc1qfake")
    assert (confirmed, unconfirmed, tx_count) == (-1, -1, -1)


def test_address_balance_sats_returns_neg_on_bad_shape(monkeypatch):
    """If the JSON parses but lacks the expected keys, treat as failure."""
    def fake_urlopen(req, timeout=0):
        return _FakeResp(json.dumps({"unexpected": "shape"}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert mempool_watcher.address_balance_sats("bc1qfake") == (-1, -1, -1)


# ---------------------------------------------------------------------------
# watch_balances
# ---------------------------------------------------------------------------

def test_watch_balances_returns_per_address_dict(monkeypatch):
    payload = _mempool_payload(funded=50_000, spent=0,
                                mem_funded=0, mem_spent=0,
                                chain_tx=1, mem_tx=0)

    def fake_urlopen(req, timeout=0):
        return _FakeResp(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = mempool_watcher.watch_balances(["bc1qaaa", "bc1qbbb"])
    assert set(result.keys()) == {"bc1qaaa", "bc1qbbb"}
    for addr, row in result.items():
        assert row["confirmed"] == 50_000
        assert row["unconfirmed"] == 0
        assert row["tx_count"] == 1
        assert row["error"] is False


def test_watch_balances_marks_error_when_lookup_fails(monkeypatch):
    def fake_urlopen(req, timeout=0):
        raise urllib.error.URLError("fail")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = mempool_watcher.watch_balances(["bc1qdead"])
    assert result["bc1qdead"]["error"] is True
    assert result["bc1qdead"]["confirmed"] == -1


# ---------------------------------------------------------------------------
# total_hot_balance
# ---------------------------------------------------------------------------

def test_total_hot_balance_aggregates_correctly(monkeypatch):
    """Two healthy addresses, each with 100k confirmed + 5k unconfirmed."""
    payload = _mempool_payload(funded=120_000, spent=20_000,
                                mem_funded=5_000, mem_spent=0,
                                chain_tx=2, mem_tx=1)

    def fake_urlopen(req, timeout=0):
        return _FakeResp(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    snap = mempool_watcher.total_hot_balance(["bc1qaaa", "bc1qbbb"])
    assert snap["confirmed_sats"] == 200_000
    assert snap["unconfirmed_sats"] == 10_000
    assert snap["total_sats"] == 210_000
    assert snap["addresses_polled"] == 2
    assert snap["addresses_error"] == 0
    assert "ts" in snap


def test_total_hot_balance_excludes_errored_addresses(monkeypatch):
    """One address resolves, the other always errors — totals only include success."""
    good_payload = _mempool_payload(funded=300_000, spent=0,
                                     mem_funded=0, mem_spent=0,
                                     chain_tx=1, mem_tx=0)

    def fake_urlopen(req, timeout=0):
        if "bc1qgood" in req.full_url:
            return _FakeResp(json.dumps(good_payload).encode())
        raise urllib.error.URLError("dead")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    snap = mempool_watcher.total_hot_balance(["bc1qgood", "bc1qbad"])
    assert snap["confirmed_sats"] == 300_000
    assert snap["unconfirmed_sats"] == 0
    assert snap["total_sats"] == 300_000
    assert snap["addresses_polled"] == 2
    assert snap["addresses_error"] == 1


# ---------------------------------------------------------------------------
# persist_snapshot + latest_snapshot round-trip
# ---------------------------------------------------------------------------

def test_persist_and_latest_snapshot_roundtrip():
    snap1 = {"total_sats": 100, "ts": "2026-05-14T00:00:00+00:00"}
    snap2 = {"total_sats": 200, "ts": "2026-05-14T01:00:00+00:00"}

    mempool_watcher.persist_snapshot(snap1)
    mempool_watcher.persist_snapshot(snap2)

    latest = mempool_watcher.latest_snapshot()
    assert latest == snap2


def test_latest_snapshot_returns_none_when_no_file():
    assert mempool_watcher.latest_snapshot() is None


def test_latest_snapshot_skips_blank_lines():
    """A jsonl file with trailing blank lines should still resolve to the last
    real record."""
    mempool_watcher.BALANCE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    mempool_watcher.BALANCE_LEDGER.write_text(
        json.dumps({"total_sats": 7}) + "\n\n\n"
    )
    latest = mempool_watcher.latest_snapshot()
    assert latest == {"total_sats": 7}


def test_address_balance_sats_rejects_a_response_naming_a_different_address(monkeypatch):
    """Wire lens (the PR #215 class): the response names its own subject, and
    the parser must check it against the address the REQUEST named. A cache,
    proxy, or misconfigured ORPHO_MEMPOOL_BASE answering 200 with the right
    shape for the wrong address must not be counted as this address's balance."""
    payload = _mempool_payload()
    payload["address"] = "bc1qfake"       # response names a different subject
    def fake_urlopen(req, timeout=0):
        return _FakeResp(json.dumps(payload).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert mempool_watcher.address_balance_sats("bc1qother") == (-1, -1, -1)


def test_address_balance_sats_accepts_a_response_without_the_subject_field(monkeypatch):
    """Negative control for the guard: an explorer variant that omits the
    `address` echo entirely is still accepted — the guard fires only on a
    CONTRADICTION, never on absence."""
    payload = _mempool_payload()
    assert "address" not in payload
    def fake_urlopen(req, timeout=0):
        return _FakeResp(json.dumps(payload).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    confirmed, _, _ = mempool_watcher.address_balance_sats("bc1qfake")
    assert confirmed == 100_000
