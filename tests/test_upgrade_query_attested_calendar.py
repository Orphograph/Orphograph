#!/usr/bin/env python3
"""test_upgrade_query_attested_calendar.py — the upgrade worker must ask the
calendar the proof NAMES, not the URL it submitted to (2026-08-30).

Two of the five calendars are pool aliases (a.pool / b.pool). The pending
attestation the pool writes into the proof names the real calendar behind it
(alice / bob), and /timestamp/<commitment> is only known THERE: the pool alias
answers 404 forever. The worker queried the submit URL, so every receipt
stalled at 3 of 5, hit MAX_UPGRADE_STALLS and froze — and the freeze comment
described the symptom as "a pool calendar's commitment permanently 404s"
instead of fixing it. Verified live on the public sample receipt: a.pool → 404,
alice → 200 with a Bitcoin-attested body.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import upgrade_worker  # noqa: E402
from conftest import PINNED_BODY, make_pending_ots  # noqa: E402

POOL = "https://a.pool.opentimestamps.org"
REAL = "https://alice.btc.calendar.opentimestamps.org"


def _pending_ots_naming(uri: str) -> bytes:
    """A pending blob whose attestation names `uri` (what a pool alias writes)."""
    u = uri.encode()
    payload = bytes([len(u)]) + u
    return (upgrade_worker.OTS_HEADER_MAGIC + upgrade_worker.OTS_VERSION
            + upgrade_worker.OTS_TAG_SHA256 + b"\x44" * 32 + b"\xf0\x02\xab\xcd\x08"
            + upgrade_worker.PENDING_ATTESTATION_MARKER + bytes([len(payload)]) + payload)


def _receipt(tmp_path, monkeypatch, blob: bytes, extra: dict | None = None):
    monkeypatch.setattr(upgrade_worker, "UPGRADE_LOG", tmp_path / "up.jsonl")
    rd = tmp_path / "receipts" / "rid_pool_1"
    rd.mkdir(parents=True)
    (rd / "a.ots").write_bytes(blob)
    record = {"receipt_id": "rid_pool_1", "hash_hex": "44" * 32, "status": "pending",
              "successes": [{"calendar": POOL}], **(extra or {})}
    (rd / "receipt.json").write_text(json.dumps(record))
    return rd, record


def test_pending_attestation_uri_is_read_from_the_real_sample():
    blob = (ROOT / "tests" / "fixtures" / "ots" / "XwTULwlh76PcCst9_alice_pending.ots").read_bytes()
    _c, idx = upgrade_worker._commitment_for_pending(blob)
    assert upgrade_worker._pending_calendar_url(blob, idx) == REAL


def test_worker_queries_the_calendar_the_proof_names(tmp_path, monkeypatch):
    rd, record = _receipt(tmp_path, monkeypatch, _pending_ots_naming(REAL))
    asked = []

    def fetch(url, commitment):
        asked.append(url)
        return (True, PINNED_BODY) if url == REAL else (False, "HTTP 404")
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", fetch)
    result = upgrade_worker._upgrade_one(rd, dict(record))
    assert asked == [REAL], asked
    assert result["status"] == "pinned"
    assert result["upgrades"][0]["queried"] == REAL


def test_falls_back_to_submit_url_when_attestation_names_nothing_usable(tmp_path, monkeypatch):
    rd, record = _receipt(tmp_path, monkeypatch, make_pending_ots(b"\x44" * 32))  # URI "x"
    asked = []
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda u, c: (asked.append(u), (False, "HTTP 404"))[1])
    upgrade_worker._upgrade_one(rd, dict(record))
    assert asked == [POOL]


def test_frozen_receipts_thaw_once_under_the_new_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade_worker, "RECEIPTS_DIR", tmp_path / "receipts")
    rd, record = _receipt(tmp_path, monkeypatch, _pending_ots_naming(REAL), {
        "upgrade_frozen": True, "upgrade_stalls": 24, "upgrade_frozen_at": "x", "upgrade_frozen_reason": "y",
    })
    import os, time
    old = time.time() - 7200
    os.utime(rd / "receipt.json", (old, old))
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda u, c: (True, PINNED_BODY))
    summary = upgrade_worker.upgrade_all(min_age_sec=60)
    assert summary["scanned"] == 1 and summary["upgraded"] == 1, summary
    stored = json.loads((rd / "receipt.json").read_text())
    assert stored["status"] == "pinned"
    assert stored["upgrade_frozen"] is False
    assert stored["upgrade_schema"] == upgrade_worker.UPGRADE_SCHEMA
    # Second walk: pinned → skipped; the thaw is one-shot, not a loop.
    summary = upgrade_worker.upgrade_all(min_age_sec=60)
    assert summary["skipped"] == 1 and summary["upgraded"] == 0


def test_frozen_receipt_already_on_new_schema_stays_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade_worker, "RECEIPTS_DIR", tmp_path / "receipts")
    rd, record = _receipt(tmp_path, monkeypatch, _pending_ots_naming(REAL), {
        "upgrade_frozen": True, "upgrade_stalls": 24, "upgrade_schema": upgrade_worker.UPGRADE_SCHEMA,
    })
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda u, c: (_ for _ in ()).throw(AssertionError("must not fetch")))
    summary = upgrade_worker.upgrade_all(min_age_sec=0)
    assert summary["skipped"] == 1


# ── 2026-08-31: host allowlist · non-destructive splice · legacy audit ─────

EVIL = "https://evil.example"


def test_attestation_naming_an_unknown_host_is_not_queried(tmp_path, monkeypatch):
    """The worker follows the URL EMBEDDED IN THE STORED PROOF. A mangled or
    hostile pending attestation naming an arbitrary https host must not
    receive our GET on every cron run — fall back to the submit URL."""
    rd, record = _receipt(tmp_path, monkeypatch, _pending_ots_naming(EVIL))
    asked = []
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade",
                        lambda u, c: (asked.append(u), (False, "HTTP 404"))[1])
    upgrade_worker._upgrade_one(rd, dict(record))
    assert asked == [POOL], asked


def test_every_shipped_calendar_host_stays_allowlisted():
    """Negative control: the hosts we ship, and the real calendars the pool
    aliases write into attestations, must all pass the allowlist."""
    for url in (POOL, REAL, "https://b.pool.opentimestamps.org",
                "https://bob.btc.calendar.opentimestamps.org",
                "https://finney.calendar.eternitywall.com",
                "https://btc.calendar.catallaxy.com"):
        blob = _pending_ots_naming(url)
        _c, idx = upgrade_worker._commitment_for_pending(blob)
        assert upgrade_worker._pending_calendar_url(blob, idx) == url, url


def test_splice_keeps_the_original_pending_proof(tmp_path, monkeypatch):
    """The pending original is the only thing a correct re-upgrade could be
    derived from; splicing in place made a wrong-body 200 irrecoverable. The
    first splice must preserve the original beside the proof."""
    blob = _pending_ots_naming(REAL)
    rd, record = _receipt(tmp_path, monkeypatch, blob)
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda u, c: (True, PINNED_BODY))
    result = upgrade_worker._upgrade_one(rd, dict(record))
    assert result["status"] == "pinned"
    prev = rd / "a.ots.prev"
    assert prev.exists() and prev.read_bytes() == blob
    assert not prev.name.endswith(".ots")   # export globs must not ship it


def test_legacy_mislabeled_pinned_receipt_is_audited_once(tmp_path, monkeypatch):
    """upgrade_all skipped on status=='pinned' BEFORE any parse, so garbage a
    pre-guard worker spliced in stayed 'pinned' forever. A pinned record not
    yet stamped with the current schema is audited (no network: a markerless
    blob never reaches a calendar), then stamped so the next run skips it."""
    import os
    import time as _t
    monkeypatch.setattr(upgrade_worker, "UPGRADE_LOG", tmp_path / "up.jsonl")
    monkeypatch.setattr(upgrade_worker, "RECEIPTS_DIR", tmp_path / "receipts")
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade",
                        lambda u, c: (_ for _ in ()).throw(AssertionError("network hit")))
    rd = tmp_path / "receipts" / "rid_legacy"
    rd.mkdir(parents=True)
    (rd / "a.ots").write_bytes(b"<html>challenge page</html>")
    record = {"receipt_id": "rid_legacy", "hash_hex": "44" * 32, "status": "pinned",
              "btc_pinned_at": "2026-08-01T00:00:00+00:00",
              "successes": [{"calendar": POOL}]}
    (rd / "receipt.json").write_text(json.dumps(record))
    old = _t.time() - 7200
    os.utime(rd / "receipt.json", (old, old))
    upgrade_worker.upgrade_all(min_age_sec=3600)
    after = json.loads((rd / "receipt.json").read_text())
    assert after.get("proof_malformed"), after
    assert after.get("status") != "pinned"
    assert int(after.get("upgrade_schema", 0) or 0) >= upgrade_worker.UPGRADE_SCHEMA
