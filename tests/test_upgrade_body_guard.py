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
from conftest import make_pending_ots  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "ots"
import ots_timestamp  # noqa: E402

BLOB_PREFIX_LEN = ots_timestamp.PROOF_PREFIX_LEN
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
    return make_pending_ots(b"\x22" * 32, ops=b"\xf0\x02\xab\xcd\x08")


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


def test_long_linear_op_chain_is_not_a_recursion_error():
    # Real proofs are long linear chains; the walker must not recurse per op.
    body = b"\x08" * 5000 + _bitcoin_attestation()
    ok, why = upgrade_worker.calendar_body_verdict(body)
    assert ok, why


def test_deep_fork_nesting_is_rejected_not_crashed():
    body = b"\xff" * 100 + _good_body()
    ok, why = upgrade_worker.calendar_body_verdict(body)
    assert not ok
    assert "well-formed" in why


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


# --- follow-ups from the #213 review -----------------------------------------

def test_reference_size_caps_are_enforced():
    # python-opentimestamps rejects varbytes > 4096 and payloads > 8192; a
    # body we accept must be one `ots` can read back.
    ok, why = upgrade_worker.calendar_body_verdict(b"\xf0" + _leb128(4096) + b"a" * 4096 + _bitcoin_attestation())
    assert ok, why
    ok, why = upgrade_worker.calendar_body_verdict(b"\xf0" + _leb128(4097) + b"a" * 4097 + _bitcoin_attestation())
    assert not ok and "4096" in why
    big = b"\x00" + b"\x01" * 8 + _leb128(8193) + b"z" * 8193
    ok, why = upgrade_worker.calendar_body_verdict(b"\xff" + big + _good_body())
    assert not ok and "8192" in why


@pytest.mark.parametrize("payload", [b"", b"\xff", b"\xa4\xf7\x39\x00"])
def test_bitcoin_attestation_payload_must_be_one_block_height(payload):
    body = b"\x08\x00" + BITCOIN_TAG + _leb128(len(payload)) + payload
    ok, why = upgrade_worker.calendar_body_verdict(body)
    assert not ok
    assert "well-formed" in why


def test_stored_proof_without_marker_is_pinned_only_if_it_parses(tmp_path, monkeypatch):
    # Pre-#213 corruption: header + digest + ops + HTML, no pending marker.
    rd, record, _ = _receipt(tmp_path, monkeypatch)
    corrupted = (upgrade_worker.OTS_HEADER_MAGIC + upgrade_worker.OTS_VERSION
                 + upgrade_worker.OTS_TAG_SHA256 + b"\x22" * 32 + b"\xf0\x02\xab\xcd\x08"
                 + b"<html>Attention Required!</html>")
    (rd / "alice.ots").write_bytes(corrupted)
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda c, h: (_ for _ in ()).throw(AssertionError("must not fetch")))
    result = upgrade_worker._upgrade_one(rd, dict(record))
    assert result["status"] == "pending"
    assert result["pinned_count"] == 0
    assert result["upgrades"][0]["pinned"] is False
    assert "stored proof malformed" in result["upgrades"][0]["reason"]
    stored = json.loads((rd / "receipt.json").read_text())
    assert stored["proof_malformed"] == ["alice"]
    assert "btc_pinned_at" not in stored
    # The real upgraded proof (11 forks, one Bitcoin attestation) IS pinned.
    (rd / "alice.ots").write_bytes((FIX / "XwTULwlh76PcCst9_btc_upgraded.ots").read_bytes())
    result = upgrade_worker._upgrade_one(rd, dict(record))
    assert result["status"] == "pinned"
    assert result["upgrades"][0] == {"calendar": record["successes"][0]["calendar"], "pinned": True, "changed": False}
    assert "proof_malformed" not in json.loads((rd / "receipt.json").read_text())


def test_rejected_200_counts_as_a_stall_and_never_resets(tmp_path, monkeypatch):
    rd, record, before = _receipt(tmp_path, monkeypatch)
    monkeypatch.setattr(upgrade_worker, "MAX_UPGRADE_STALLS", 3)
    html = b"<html>Attention Required!</html>"
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda c, h: (True, html))
    rec = dict(record)
    for n in (1, 2):
        result = upgrade_worker._upgrade_one(rd, rec)
        rec = json.loads((rd / "receipt.json").read_text())
        assert rec["upgrade_stalls"] == n
        assert not result["frozen"]
    result = upgrade_worker._upgrade_one(rd, rec)
    rec = json.loads((rd / "receipt.json").read_text())
    assert rec["upgrade_stalls"] == 3 and rec["upgrade_frozen"] is True and result["frozen"]
    assert (rd / "alice.ots").read_bytes() == before


def test_walker_reads_varint_lengths_like_the_guard():
    # A 130-byte append is legal OTS (<= 4096); the old single-byte read misparsed it.
    blob = make_pending_ots(b"\x33" * 32, ops=b"\xf0" + _leb128(130) + b"n" * 130 + b"\x08")
    commitment, idx = upgrade_worker._commitment_for_pending(blob)
    import hashlib
    assert commitment == hashlib.sha256(b"\x33" * 32 + b"n" * 130).hexdigest()
    assert idx > 0
