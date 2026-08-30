#!/usr/bin/env python3
"""test_anchor_body_guard.py — a calendar's POST /digest body is parsed before
it becomes the customer's proof (follow-up to #213: same defect class, one hop
earlier). A 200 that is not one well-formed timestamp used to be written
verbatim; with no pending marker the upgrade worker then read it as "already
upgraded" on every run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import engine  # noqa: E402

DIGEST = "ab" * 32
# ops + a pending attestation: what a calendar actually returns from /digest.
PENDING_BODY = b"\xf0\x10" + b"\x01" * 16 + b"\x08" + b"\x00\x83\xdf\xe3\x0d\x2e\xf9\x0c\x8e" + b"\x02\x01x"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "RECEIPTS_DIR", tmp_path / "receipts")
    monkeypatch.setattr(engine, "LEDGER", tmp_path / "ledger.jsonl")
    return tmp_path


def _stub(bodies):
    def _submit(cal, h):
        return bodies[cal]
    return _submit


def test_html_200_from_a_calendar_is_a_failure_not_a_proof(isolated, monkeypatch):
    cals = list(engine.CALENDARS)
    bodies = {c: (True, b"<html><title>Attention Required!</title></html>") for c in cals}
    bodies[cals[0]] = (True, PENDING_BODY)
    monkeypatch.setattr(engine, "_submit", _stub(bodies))
    rec = engine.anchor_hash(DIGEST)
    rd = engine.RECEIPTS_DIR / rec["receipt_id"]
    assert rec["calendars_ok"] == 1
    assert [s["calendar"] for s in rec["successes"]] == [cals[0]]
    assert len(rec["failures"]) == len(cals) - 1
    assert all("well-formed" in f["error"] for f in rec["failures"])
    assert sorted(p.name for p in rd.glob("*.ots")) == [engine._calendar_short(cals[0]) + ".ots"]


def test_empty_200_is_a_failure(isolated, monkeypatch):
    monkeypatch.setattr(engine, "_submit", lambda c, h: (True, b""))
    rec = engine.anchor_hash(DIGEST)
    assert rec["calendars_ok"] == 0
    assert all("empty" in f["error"] for f in rec["failures"])


def test_well_formed_pending_body_is_written_as_before(isolated, monkeypatch):
    monkeypatch.setattr(engine, "_submit", lambda c, h: (True, PENDING_BODY))
    rec = engine.anchor_hash(DIGEST)
    assert rec["calendars_ok"] == len(engine.CALENDARS)
    rd = engine.RECEIPTS_DIR / rec["receipt_id"]
    blob = next(rd.glob("*.ots")).read_bytes()
    assert blob == engine._build_ots(bytes.fromhex(DIGEST), PENDING_BODY)
