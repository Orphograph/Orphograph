from __future__ import annotations

import hashlib
import json

import pytest

import engine
from conftest import PENDING_BODY  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ROOT", tmp_path)
    monkeypatch.setattr(engine, "RECEIPTS_DIR", tmp_path / "receipts")
    monkeypatch.setattr(engine, "LEDGER", tmp_path / "ledger.jsonl")
    yield


def _fake_submit_all_ok(_url, hash_bytes):
    return True, PENDING_BODY


def _fake_submit_all_fail(_url, _hash_bytes):
    return False, "HTTP 500: simulated"


def _hash_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_anchor_with_invalid_hex_raises():
    with pytest.raises(ValueError):
        engine.anchor_hash("not-hex")


def test_anchor_with_wrong_length_raises():
    with pytest.raises(ValueError):
        engine.anchor_hash("abcd")


def test_anchor_with_invalid_sha512_raises(monkeypatch):
    monkeypatch.setattr(engine, "_submit", _fake_submit_all_ok)
    with pytest.raises(ValueError):
        engine.anchor_hash(_hash_of("x"), sha512_hex="too-short")


def test_anchor_stores_sha512_sibling(monkeypatch):
    monkeypatch.setattr(engine, "_submit", _fake_submit_all_ok)
    sha512 = "a" * 128
    rec = engine.anchor_hash(_hash_of("hello"), sha512_hex=sha512)
    assert rec["sha512_hex"] == sha512
    result = engine.verify_receipt(rec["receipt_id"])
    assert result["sha512_hex"] == sha512


def test_anchor_without_sha512_keeps_none(monkeypatch):
    monkeypatch.setattr(engine, "_submit", _fake_submit_all_ok)
    rec = engine.anchor_hash(_hash_of("hello"))
    assert rec.get("sha512_hex") is None


def test_anchor_writes_receipt_and_ots(monkeypatch):
    monkeypatch.setattr(engine, "_submit", _fake_submit_all_ok)
    rec = engine.anchor_hash(_hash_of("hello"), client_label="t.txt")
    assert rec["calendars_ok"] == len(engine.CALENDARS)
    assert rec["calendars_total"] == len(engine.CALENDARS)
    receipt_dir = engine.RECEIPTS_DIR / rec["receipt_id"]
    assert (receipt_dir / "receipt.json").exists()
    assert len(list(receipt_dir.glob("*.ots"))) == len(engine.CALENDARS)


def test_verify_after_anchor_returns_all_ok(monkeypatch):
    monkeypatch.setattr(engine, "_submit", _fake_submit_all_ok)
    rec = engine.anchor_hash(_hash_of("verify-me"))
    result = engine.verify_receipt(rec["receipt_id"])
    assert result["found"] is True
    assert result["calendars_ok"] == result["calendars_total"]
    assert all(c["ok"] for c in result["checks"])


def test_verify_missing_receipt():
    result = engine.verify_receipt("nonexistent")
    assert result["found"] is False


def test_partial_calendar_failure_recorded(monkeypatch):
    calls = {"n": 0}

    def half_ok(_url, hash_bytes):
        calls["n"] += 1
        if calls["n"] % 2 == 0:
            return False, "HTTP 503"
        return True, PENDING_BODY

    monkeypatch.setattr(engine, "_submit", half_ok)
    rec = engine.anchor_hash(_hash_of("partial"))
    assert rec["calendars_ok"] > 0
    assert rec["calendars_ok"] < len(engine.CALENDARS)
    assert len(rec["failures"]) > 0


def test_ledger_appended(monkeypatch):
    monkeypatch.setattr(engine, "_submit", _fake_submit_all_ok)
    engine.anchor_hash(_hash_of("a"))
    engine.anchor_hash(_hash_of("b"))
    lines = engine.LEDGER.read_text().strip().split("\n")
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # parseable
