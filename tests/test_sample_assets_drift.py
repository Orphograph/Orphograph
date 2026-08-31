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


def test_ots_bytes_records_anchor_time_size_never_larger_than_the_file():
    # successes[*].ots_bytes is the ANCHOR-TIME proof size; upgrade_worker
    # rewrites the .ots in place but never this field, and the live record
    # keeps the same May values. Pin the semantic so the gap between the
    # recorded size and the (larger) upgraded file reads as intended, not as
    # tampering — and so a future writer that starts syncing it breaks here.
    r = json.loads((SAMPLE / "receipt.json").read_text())
    for entry in r["successes"]:
        name = entry["ots_path"].rsplit("/", 1)[-1]
        blob = (SAMPLE / name).read_bytes()
        assert 0 < entry["ots_bytes"] <= len(blob), (name, entry["ots_bytes"], len(blob))


def test_the_second_bundle_copy_is_byte_identical():
    """web/verify/examples/sample/ is the SAME canonical bundle served at a
    second path (the offline-verifier walkthrough curls it). It sat with May
    PENDING proofs while /sample/index.json declared the receipt pinned 5/5 —
    a stale proof beside a fresh index, on the exact artifact that exists to
    disprove that drift. Every file must be byte-identical to web/sample/."""
    second = ROOT / "web" / "verify" / "examples" / "sample"
    names = sorted(p.name for p in SAMPLE.iterdir())
    assert names == sorted(p.name for p in second.iterdir())
    for name in names:
        assert (SAMPLE / name).read_bytes() == (second / name).read_bytes(), name


def test_ots_walkthrough_describes_the_shipped_upgraded_proof():
    """The article curls this mutable sample path and must narrate its state."""
    article = (ROOT / "web" / "blog" / "reading-ots-file-by-hand.html").read_text()
    feed = (ROOT / "web" / "blog" / "atom.xml").read_text()
    index = json.loads((SAMPLE / "index.json").read_text())
    a_block = next(c["bitcoin_block"] for c in index["calendars"]
                   if c["file"] == "a.ots")
    expected = f"BitcoinBlockHeaderAttestation({a_block})"
    for published_copy in (article, feed):
        assert expected in published_copy
        assert "ots upgrade a.ots" not in published_copy
        assert "alice.btc.calendar.opentimestamps.org" not in published_copy
