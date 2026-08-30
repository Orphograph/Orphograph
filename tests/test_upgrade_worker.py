#!/usr/bin/env python3
"""test_upgrade_worker.py — stuck-partial freeze guard + upgrade-log rotation.

These cover the polling-cadence safeguards added so a receipt whose pool
calendar commitment permanently 404s stops being re-fetched on every cron run
(the historical ~5,200-wasted-call + unbounded-log bug). They deliberately
monkey-patch the network (`_fetch_upgrade`) and never touch the commitment
walk or the proof bytes — verify_cli.py stays the authoritative Bitcoin check.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import upgrade_worker  # noqa: E402


def _pinned_body() -> bytes:
    """A minimal calendar body the guard accepts: sha256 then a Bitcoin
    attestation for block 949156 (varint 0xa4 0xf7 0x39, payload len 3)."""
    return b"\x08\x00" + upgrade_worker.BITCOIN_ATTESTATION_TAG + b"\x03\xa4\xf7\x39"


def _make_pending_ots() -> bytes:
    """A minimal but well-formed pending .ots blob that _commitment_for_pending
    parses successfully: header + version + SHA256 tag + 32-byte commitment +
    the pending-attestation marker (zero ops between, which is valid)."""
    return (
        upgrade_worker.OTS_HEADER_MAGIC
        + upgrade_worker.OTS_VERSION
        + upgrade_worker.OTS_TAG_SHA256
        + (b"\x11" * 32)
        + upgrade_worker.PENDING_ATTESTATION_MARKER
        + b"\x00\x00"
    )


def _write_receipt(receipts_dir: Path, rid: str, record: dict, ots: dict[str, bytes]) -> Path:
    rd = receipts_dir / rid
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "receipt.json").write_text(json.dumps(record, indent=2))
    for name, blob in ots.items():
        (rd / name).write_bytes(blob)
    return rd


def test_make_pending_ots_is_parseable():
    """Guard the fixture itself: the blob must yield a real commitment + marker,
    otherwise the freeze tests would pass for the wrong reason (no-op pinned)."""
    commitment_hex, marker_idx = upgrade_worker._commitment_for_pending(_make_pending_ots())
    assert commitment_hex == "11" * 32
    assert marker_idx > 0


def test_upgrade_freezes_after_max_stalls(tmp_path, monkeypatch):
    """A receipt whose calendar permanently 404s is frozen after MAX_UPGRADE_STALLS
    no-progress runs, so it stops being re-fetched forever (the stuck-partial bug)."""
    monkeypatch.setattr(upgrade_worker, "MAX_UPGRADE_STALLS", 3)
    monkeypatch.setattr(upgrade_worker, "UPGRADE_LOG", tmp_path / "up.jsonl")
    rdir = tmp_path / "rz"
    rdir.mkdir()
    rdir.joinpath("a.ots").write_bytes(_make_pending_ots())
    rec = {
        "receipt_id": "rz",
        "status": "pending",
        "successes": [{"calendar": "https://a.pool.opentimestamps.org"}],
    }
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda c, h: (False, "HTTP 404"))
    for i in range(1, 4):
        upgrade_worker._upgrade_one(rdir, rec)
        if i < 3:
            assert not rec.get("upgrade_frozen"), f"frozen too early at attempt {i}"
        else:
            assert rec.get("upgrade_frozen") is True
    assert rec["upgrade_stalls"] >= 3
    assert rec.get("upgrade_frozen_at")
    assert "polling halted" in rec.get("upgrade_frozen_reason", "")
    # status never advanced past pending, but the receipt is no longer eligible.
    assert rec["status"] == "pending"


def test_progress_resets_stall_counter(tmp_path, monkeypatch):
    """A run that actually advances a proof clears accumulated stalls."""
    monkeypatch.setattr(upgrade_worker, "MAX_UPGRADE_STALLS", 3)
    monkeypatch.setattr(upgrade_worker, "UPGRADE_LOG", tmp_path / "up.jsonl")
    rdir = tmp_path / "rp"
    rdir.mkdir()
    rdir.joinpath("a.ots").write_bytes(_make_pending_ots())
    rec = {
        "receipt_id": "rp",
        "status": "pending",
        "upgrade_stalls": 2,  # already two stalls deep
        "successes": [{"calendar": "https://a.pool.opentimestamps.org"}],
    }
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda c, h: (True, _pinned_body()))
    upgrade_worker._upgrade_one(rdir, rec)
    assert rec["upgrade_stalls"] == 0
    assert not rec.get("upgrade_frozen")
    assert rec["status"] == "pinned"


def test_upgrade_all_skips_frozen(tmp_path, monkeypatch):
    """upgrade_all() must skip a frozen receipt and never touch the network for it."""
    monkeypatch.setattr(upgrade_worker, "RECEIPTS_DIR", tmp_path / "receipts")
    monkeypatch.setattr(upgrade_worker, "UPGRADE_LOG", tmp_path / "up.jsonl")
    (tmp_path / "receipts").mkdir()
    rec = {
        "receipt_id": "rf",
        "status": "partial",
        "upgrade_frozen": True,
        "successes": [{"calendar": "https://a.pool.opentimestamps.org"}],
    }
    _write_receipt(tmp_path / "receipts", "rf", rec, {"a.ots": _make_pending_ots()})

    def _boom(cal, commitment_hex):
        raise AssertionError("frozen receipt must not be fetched")

    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", _boom)
    out = upgrade_worker.upgrade_all()
    assert out["scanned"] == 1
    assert out["skipped"] == 1
    assert out["upgraded"] == 0
    assert out["results"] == []


def test_upgrade_log_rotation_bounds_file(tmp_path, monkeypatch):
    """The append-only upgrade log rotates to a .1 backup once it passes the cap."""
    log = tmp_path / "up.jsonl"
    monkeypatch.setattr(upgrade_worker, "UPGRADE_LOG", log)
    monkeypatch.setattr(upgrade_worker, "UPGRADE_LOG_MAX_BYTES", 200)
    for i in range(50):
        upgrade_worker._log({"ts": "2026-05-30T00:00:00+00:00", "i": i, "pad": "x" * 40})
    backup = log.with_suffix(log.suffix + ".1")
    assert backup.exists(), "expected a rotated .1 backup once the cap was exceeded"
    # The live log was rotated, so it holds only post-rotation lines — far under
    # the unbounded size it would have reached (50 * ~70 bytes).
    assert log.stat().st_size < 50 * 70
    # The most recent line is still the last one written (health.py reads it).
    last = json.loads(log.read_text().splitlines()[-1])
    assert last["i"] == 49
