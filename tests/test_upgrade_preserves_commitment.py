"""Stage 3e lens `mutation`: upgrade_worker rewrites receipt.json on every
OTS upgrade (status, btc_pinned_at, pinned_count, stall counters). Nothing
asserted that the COMMITMENT fields — receipt_id, hash_hex, created_at, the
calendar list — and the .ots proof bytes' embedded digest survive that
rewrite. A writer that rebuilt the record from the wrong source would have
passed every existing test. These pin the invariants through the real
`_upgrade_one` on a fixture receipt, for both the no-progress and the
pinned path, and prove the checker can fail on a mutating writer."""
import json
from pathlib import Path

import pytest

from conftest import PINNED_BODY, write_fixture_receipt, FIXTURE_HASH_HEX, FIXTURE_RECEIPT_ID

import upgrade_worker  # noqa: E402  (server/ on sys.path via conftest)

COMMITMENT = ("receipt_id", "hash_hex", "created_at", "client_label", "calendars_ok", "calendars_total")


def _snapshot(rdir: Path) -> dict:
    rec = json.loads((rdir / "receipt.json").read_text())
    ots = {p.name: p.read_bytes() for p in sorted(rdir.glob("*.ots"))}
    return {"fields": {k: rec.get(k) for k in COMMITMENT},
            "calendars": [s["calendar"] for s in rec.get("successes", [])],
            "ots_digests": {n: upgrade_worker._commitment_for_pending(b)[0] for n, b in ots.items()}}


def assert_commitment_preserved(before: dict, after: dict) -> None:
    assert after["fields"] == before["fields"], "commitment fields changed across rewrite"
    assert after["calendars"] == before["calendars"], "calendar list changed across rewrite"
    for name, digest in before["ots_digests"].items():
        assert after["ots_digests"].get(name) == digest, f"{name}: embedded digest changed"


@pytest.fixture
def receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade_worker, "UPGRADE_LOG", tmp_path / "up.jsonl")
    rdir = write_fixture_receipt(tmp_path / "receipts")
    return rdir


def test_no_progress_rewrite_preserves_commitment(receipt, monkeypatch):
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda c, h: (False, "HTTP 404"))
    before = _snapshot(receipt)
    upgrade_worker._upgrade_one(receipt, json.loads((receipt / "receipt.json").read_text()))
    after = _snapshot(receipt)
    rec = json.loads((receipt / "receipt.json").read_text())
    assert rec["upgrade_attempts"] == 1          # the rewrite really happened
    assert_commitment_preserved(before, after)


def test_pinned_rewrite_preserves_commitment(receipt, monkeypatch):
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda c, h: (True, PINNED_BODY))
    before = _snapshot(receipt)
    upgrade_worker._upgrade_one(receipt, json.loads((receipt / "receipt.json").read_text()))
    rec = json.loads((receipt / "receipt.json").read_text())
    assert rec.get("btc_pinned_at"), "pinned path did not run"
    after = _snapshot(receipt)
    assert after["fields"] == before["fields"]
    assert after["calendars"] == before["calendars"]
    assert rec["hash_hex"] == FIXTURE_HASH_HEX and rec["receipt_id"] == FIXTURE_RECEIPT_ID


def test_checker_fails_on_a_mutating_writer(receipt):
    """Negative control: the invariant checker must be able to fail."""
    before = _snapshot(receipt)
    rec = json.loads((receipt / "receipt.json").read_text())
    rec["hash_hex"] = "ab" * 32
    (receipt / "receipt.json").write_text(json.dumps(rec))
    with pytest.raises(AssertionError):
        assert_commitment_preserved(before, _snapshot(receipt))
