#!/usr/bin/env python3
"""Tests for health._reconciliation_snapshot — the read-only money-integrity
counts surfaced in /api/health so a silently-failed webhook is detectable.

Must be crash-safe: missing files → 0, malformed rows → skipped, unreadable → None.
"""
from __future__ import annotations

import json

import pytest

import health


@pytest.fixture(autouse=True)
def _tmp_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("ORPHO_CREDIT_LEDGER", str(tmp_path / "credit_ledger.jsonl"))
    monkeypatch.setenv("ORPHO_PROCESSED_EVENTS", str(tmp_path / "stripe.jsonl"))
    monkeypatch.setenv("ORPHO_NOWPAYMENTS_PROCESSED_EVENTS", str(tmp_path / "nowpay.jsonl"))
    yield tmp_path


def test_all_zero_when_no_ledgers(_tmp_ledgers):
    snap = health._reconciliation_snapshot()
    assert snap["credit_ledger_rows"] == 0
    assert snap["stripe_processed_events"] == 0
    assert snap["nowpayments_processed_events"] == 0
    assert snap["nowpayments_mint_markers"] == 0


def test_counts_rows_and_mint_markers(_tmp_ledgers, monkeypatch):
    tmp = _tmp_ledgers
    (tmp / "credit_ledger.jsonl").write_text(
        '{"credits_delta":10}\n{"credits_delta":50}\n\n{"credits_delta":10}\n')
    (tmp / "stripe.jsonl").write_text('{"event_id":"evt_1"}\n{"event_id":"evt_2"}\n')
    (tmp / "nowpay.jsonl").write_text(
        '{"event_id":"o1:confirmed"}\n'
        '{"event_id":"mint:o1"}\n'
        '{"event_id":"o2:finished"}\n'
        '{"event_id":"mint:o2"}\n')
    snap = health._reconciliation_snapshot()
    assert snap["credit_ledger_rows"] == 3          # blank line ignored
    assert snap["stripe_processed_events"] == 2
    assert snap["nowpayments_processed_events"] == 4
    assert snap["nowpayments_mint_markers"] == 2     # only the mint: rows


def test_malformed_rows_skipped_not_fatal(_tmp_ledgers):
    tmp = _tmp_ledgers
    (tmp / "nowpay.jsonl").write_text(
        '{"event_id":"mint:ok"}\nnot json at all\n{"event_id":"x:confirmed"}\n')
    snap = health._reconciliation_snapshot()
    # _count_jsonl counts non-blank lines (2 valid-ish + the junk line = 3),
    # but mint-marker counting parses JSON and skips the junk line.
    assert snap["nowpayments_mint_markers"] == 1
    assert isinstance(snap["nowpayments_processed_events"], int)


def test_snapshot_includes_reconciliation_key():
    # the public snapshot must carry the new section
    snap = health.snapshot()
    assert "reconciliation" in snap
    assert "credit_ledger_rows" in snap["reconciliation"]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
