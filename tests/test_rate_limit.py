from __future__ import annotations

import time

import pytest
from rate_limit import TokenBucket, truncate_ip


def test_bucket_allows_up_to_capacity_then_denies():
    tb = TokenBucket(capacity=3, refill_per_sec=0.01)
    assert tb.check("k")[0] is True
    assert tb.check("k")[0] is True
    assert tb.check("k")[0] is True
    allowed, retry = tb.check("k")
    assert allowed is False
    assert retry > 0


def test_bucket_refills_over_time():
    tb = TokenBucket(capacity=1, refill_per_sec=100.0)
    assert tb.check("k")[0] is True
    assert tb.check("k")[0] is False
    time.sleep(0.05)  # ~5 tokens worth at 100/sec; clamped to capacity=1
    assert tb.check("k")[0] is True


def test_bucket_keys_independent():
    tb = TokenBucket(capacity=1, refill_per_sec=0.001)
    assert tb.check("a")[0] is True
    assert tb.check("b")[0] is True
    assert tb.check("a")[0] is False


def test_bucket_rejects_invalid_construction():
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, refill_per_sec=1.0)
    with pytest.raises(ValueError):
        TokenBucket(capacity=1, refill_per_sec=0)


def test_truncate_ip_ipv4():
    assert truncate_ip("192.168.1.42") == "192.168.1.0/24"
    assert truncate_ip("10.0.0.1") == "10.0.0.0/24"


def test_truncate_ip_ipv6():
    out = truncate_ip("2001:db8:abcd:1234::1")
    assert out.startswith("2001:db8:abcd") and out.endswith("::/48")


def test_truncate_ip_empty_and_malformed():
    assert truncate_ip("") == ""
    assert truncate_ip("not-an-ip") == "unknown"


def test_bucket_persists_across_restart(tmp_path):
    """Restart cannot grant fresh quota."""
    snap = tmp_path / "rl.json"
    tb1 = TokenBucket(capacity=3, refill_per_sec=0.0001, snapshot_path=snap, snapshot_interval_sec=0.0)
    # consume all 3
    assert tb1.check("alice")[0] is True
    assert tb1.check("alice")[0] is True
    assert tb1.check("alice")[0] is True
    tb1.save()
    # simulate restart
    tb2 = TokenBucket(capacity=3, refill_per_sec=0.0001, snapshot_path=snap, snapshot_interval_sec=0.0)
    allowed, retry = tb2.check("alice")
    # should still be denied (only ~ms have passed, refill is tiny)
    assert allowed is False, "restart should not grant fresh quota"


def test_bucket_handles_clock_rollback(tmp_path):
    """If the wall clock jumps backwards, refill should clamp to 0, not overflow."""
    snap = tmp_path / "rl.json"
    # Pretend a previous run saved state from 5 minutes in the future.
    import json, time
    future_ts = time.time() + 300
    snap.write_text(json.dumps({
        "saved_at": future_ts,
        "capacity": 3,
        "refill_per_sec": 1.0,
        "buckets": {"alice": [0.0, future_ts]},  # exhausted at future_ts
    }))
    tb = TokenBucket(capacity=3, refill_per_sec=1.0, snapshot_path=snap, snapshot_interval_sec=0.0)
    # Now we'd compute elapsed = time.time() - future_ts = -300. Without clamping
    # that would be a huge negative refill amount. With clamping, elapsed=0 and
    # tokens stay at 0 (or the rollback time slowly catches up).
    allowed, _ = tb.check("alice")
    assert allowed is False, "clock rollback must not grant tokens"
