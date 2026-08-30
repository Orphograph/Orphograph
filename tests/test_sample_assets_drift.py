#!/usr/bin/env python3
"""test_sample_assets_drift.py — the bundled sample receipt tells the truth
(2026-08-30, drift lens).

web/sample/ is both the seed for RECEIPTS_DIR on first boot
(app._seed_sample_receipt) and served directly at /sample/*. The seed shipped
the May pending proofs while the live receipt evolved to 5/5 Bitcoin-attested,
so visitors downloading /sample/a.ots got a pending proof for a receipt the
site calls anchored — and the press kit promises /sample/index.json carries
per-calendar status it did not have. These pin the attested state and the
internal consistency of the bundle; they fail against the pending seeds.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import ots_timestamp  # noqa: E402

SAMPLE = ROOT / "web" / "sample"


def _proofs():
    files = sorted(SAMPLE.glob("*.ots"))
    assert len(files) == 5, files  # negative control: the glob found the bundle
    return files


def test_every_bundled_proof_is_bitcoin_attested():
    for f in _proofs():
        ok, why = ots_timestamp.proof_verdict(f.read_bytes(), require_bitcoin=True)
        assert ok, (f.name, why)


def test_every_bundled_proof_commits_to_the_receipt_digest():
    digest = json.loads((SAMPLE / "receipt.json").read_text())["hash_hex"]
    for f in _proofs():
        blob = f.read_bytes()
        assert blob[ots_timestamp.PROOF_PREFIX_LEN - 32:ots_timestamp.PROOF_PREFIX_LEN].hex() == digest, f.name


def test_sample_txt_hashes_to_the_anchored_digest():
    digest = json.loads((SAMPLE / "receipt.json").read_text())["hash_hex"]
    assert hashlib.sha256((SAMPLE / "sample.txt").read_bytes()).hexdigest() == digest


def test_receipt_json_reflects_the_pinned_state():
    r = json.loads((SAMPLE / "receipt.json").read_text())
    assert r["status"] == "pinned"
    assert r["pinned_count"] == r["pinned_total"] == 5
    assert r["btc_pinned_at"]


def test_index_json_carries_per_calendar_status_matching_the_bytes():
    # press-kit.html: "/sample/index.json — … and the per-calendar status."
    ix = json.loads((SAMPLE / "index.json").read_text())
    assert ix["status"] == "pinned"
    entries = {e["file"]: e["bitcoin_block"] for e in ix["calendars"]}
    assert set(entries) == {f.name for f in _proofs()}
    for f in _proofs():
        assert entries[f.name] == ots_timestamp.proof_bitcoin_heights(f.read_bytes())[0], f.name


def test_index_and_receipt_agree_on_identity():
    ix = json.loads((SAMPLE / "index.json").read_text())
    r = json.loads((SAMPLE / "receipt.json").read_text())
    assert ix["receipt_id"] == r["receipt_id"]
    assert ix["hash_hex"] == r["hash_hex"]
    assert ix["sha512_hex"] == r["sha512_hex"]
