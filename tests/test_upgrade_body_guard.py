#!/usr/bin/env python3
"""test_upgrade_body_guard.py — a calendar body must be a well-formed
OpenTimestamps timestamp carrying a Bitcoin attestation BEFORE it replaces
the bytes of an issued proof (2026-08-30, Stage 3e mutation lens).

`upgrade_worker._upgrade_one` splices whatever `/timestamp/<commitment>`
returns with HTTP 200 straight into `<calendar>.ots` and marks the calendar
`pinned`. Nothing parsed the body. A 200 that is not a timestamp — an HTML
challenge page from a proxy, an empty body, a truncated read — destroyed the
pending proof in place, flipped `status` to pinned/partial, set
`btc_pinned_at`, and fired the pin email. On the next run the blob had no
pending marker, so the corruption read as "already upgraded" forever.

The two real proofs under tests/fixtures/ots/ are the canonical public sample
receipt's, fetched from the live export on 2026-08-30: one Bitcoin-attested
(block 949156) and one still pending. They are the guard's negative controls
in both directions — a parser that rejects everything would freeze every
upgrade and look just as "safe".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import upgrade_worker  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "ots"
BLOB_PREFIX_LEN = (len(upgrade_worker.OTS_HEADER_MAGIC)
                   + len(upgrade_worker.OTS_VERSION)
                   + len(upgrade_worker.OTS_TAG_SHA256) + 32)
BITCOIN_TAG = b"\x05\x88\x96\x0d\x73\xd7\x19\x01"
PENDING_TAG = b"\x83\xdf\xe3\x0d\x2e\xf9\x0c\x8e"


def _leb128(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _bitcoin_attestation(height: int = 949156) -> bytes:
    payload = _leb128(height)
    return b"\x00" + BITCOIN_TAG + _leb128(len(payload)) + payload


def _pending_attestation(uri: bytes = b"https://alice.btc.calendar.opentimestamps.org") -> bytes:
    payload = _leb128(len(uri)) + uri
    return b"\x00" + PENDING_TAG + _leb128(len(payload)) + payload


def _good_body() -> bytes:
    # append(4 bytes) · sha256 · Bitcoin attestation — the shape a calendar
    # returns once the commitment is in a block.
    return b"\xf0\x04\xde\xad\xbe\xef\x08" + _bitcoin_attestation()


def _pending_ots() -> bytes:
    return (upgrade_worker.OTS_HEADER_MAGIC + upgrade_worker.OTS_VERSION
            + upgrade_worker.OTS_TAG_SHA256 + (b"\x22" * 32)
            + b"\xf0\x02\xab\xcd\x08"
            + upgrade_worker.PENDING_ATTESTATION_MARKER + b"\x00\x00")


def _receipt(tmp_path: Path, monkeypatch) -> tuple[Path, dict, bytes]:
    monkeypatch.setattr(upgrade_worker, "UPGRADE_LOG", tmp_path / "up.jsonl")
    rd = tmp_path / "receipts" / "rid_guard_1"
    rd.mkdir(parents=True)
    blob = _pending_ots()
    (rd / "alice.ots").write_bytes(blob)
    record = {
        "receipt_id": "rid_guard_1",
        "hash_hex": "22" * 32,
        "status": "pending",
        "successes": [{"calendar": "https://alice.btc.calendar.opentimestamps.org"}],
    }
    (rd / "receipt.json").write_text(json.dumps(record))
    return rd, record, blob


# --- the guard itself ------------------------------------------------------

def test_real_upgraded_proof_is_accepted():
    blob = (FIX / "XwTULwlh76PcCst9_btc_upgraded.ots").read_bytes()
    ok, why = upgrade_worker.calendar_body_verdict(blob[BLOB_PREFIX_LEN:])
    assert ok, why


def test_real_pending_proof_is_rejected_for_missing_bitcoin_attestation():
    blob = (FIX / "XwTULwlh76PcCst9_alice_pending.ots").read_bytes()
    ok, why = upgrade_worker.calendar_body_verdict(blob[BLOB_PREFIX_LEN:])
    assert not ok
    assert "Bitcoin" in why


@pytest.mark.parametrize("body,fragment", [
    (b"", "empty"),
    (b"<html><title>Attention Required! | Cloudflare</title></html>", "well-formed"),
    (b"\x08", "well-formed"),                      # op with nothing after it
    (_good_body() + b"\x00", "trailing"),          # valid timestamp + junk
    (b"\x08" + _pending_attestation(), "Bitcoin"),  # parses, but still pending
    (b"\xf0\x04\xde\xad", "well-formed"),          # truncated varbytes
])
def test_malformed_or_unpinned_bodies_are_rejected(body, fragment):
    ok, why = upgrade_worker.calendar_body_verdict(body)
    assert not ok
    assert fragment in why


def test_fork_with_bitcoin_branch_is_accepted():
    body = b"\xff" + b"\x08" + _pending_attestation() + _good_body()
    ok, why = upgrade_worker.calendar_body_verdict(body)
    assert ok, why


# --- driven through the real entry point ------------------------------------

def test_html_200_does_not_replace_the_proof_or_claim_pinned(tmp_path, monkeypatch):
    rd, record, before = _receipt(tmp_path, monkeypatch)
    html = b"<html><title>Attention Required! | Cloudflare</title></html>"
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda c, h: (True, html))
    result = upgrade_worker._upgrade_one(rd, dict(record))
    assert (rd / "alice.ots").read_bytes() == before
    assert result["status"] == "pending"
    assert result["pinned_count"] == 0
    stored = json.loads((rd / "receipt.json").read_text())
    assert "btc_pinned_at" not in stored
    assert result["upgrades"][0]["pinned"] is False
    assert "well-formed" in result["upgrades"][0]["reason"]


def test_empty_200_does_not_strip_the_pending_marker(tmp_path, monkeypatch):
    rd, record, before = _receipt(tmp_path, monkeypatch)
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda c, h: (True, b""))
    result = upgrade_worker._upgrade_one(rd, dict(record))
    assert (rd / "alice.ots").read_bytes() == before
    assert upgrade_worker._commitment_for_pending((rd / "alice.ots").read_bytes())[0] is not None
    assert result["status"] == "pending"


def test_bitcoin_attested_body_is_spliced_and_pinned(tmp_path, monkeypatch):
    rd, record, before = _receipt(tmp_path, monkeypatch)
    body = _good_body()
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda c, h: (True, body))
    result = upgrade_worker._upgrade_one(rd, dict(record))
    after = (rd / "alice.ots").read_bytes()
    marker = before.find(upgrade_worker.PENDING_ATTESTATION_MARKER)
    assert after == before[:marker] + body
    assert result["status"] == "pinned"
    assert result["upgrades"][0] == {
        "calendar": "https://alice.btc.calendar.opentimestamps.org",
        "pinned": True, "changed": True,
    }
