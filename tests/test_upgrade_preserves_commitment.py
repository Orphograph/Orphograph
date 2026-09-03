"""Stage 3e lens `mutation`: upgrade_worker rewrites receipt.json on every
OTS upgrade and splices new bytes into the .ots proofs on the pinned path.
The frozen set is renewal.CORE_ALWAYS — the fields a renewal commitment
covers, so a worker that mutated any of them would silently void every
renewal record — plus each .ots file's embedded digest at the fixed offset
engine.verify_receipt reads. Both rewrite paths are driven through the real
`_upgrade_one`; three negative controls prove each branch of the checker
can fail (a field, the calendar list, a proof byte)."""
import json
from pathlib import Path

import pytest

from conftest import PINNED_BODY, write_fixture_receipt

import engine  # noqa: E402  (server/ on sys.path via conftest)
import renewal  # noqa: E402
import upgrade_worker  # noqa: E402

FROZEN = renewal.CORE_ALWAYS
DIGEST_OFFSET = len(engine.OTS_HEADER_MAGIC) + 2


def embedded_digest(blob: bytes) -> bytes:
    return blob[DIGEST_OFFSET:DIGEST_OFFSET + 32] if blob.startswith(engine.OTS_HEADER_MAGIC) else b""


def _snapshot(rdir: Path) -> dict:
    rec = json.loads((rdir / "receipt.json").read_text())
    return {"fields": {k: rec.get(k) for k in FROZEN},
            "ots_digests": {p.name: embedded_digest(p.read_bytes()) for p in sorted(rdir.glob("*.ots"))}}


def assert_commitment_preserved(before: dict, after: dict) -> None:
    changed = [k for k in FROZEN if before["fields"].get(k) != after["fields"].get(k)]
    assert not changed, f"renewal-committed fields changed across rewrite: {changed}"
    assert set(after["ots_digests"]) == set(before["ots_digests"]), "proof files appeared/vanished"
    for name, digest in before["ots_digests"].items():
        assert digest and after["ots_digests"][name] == digest, f"{name}: embedded digest changed"


@pytest.fixture
def receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade_worker, "UPGRADE_LOG", tmp_path / "up.jsonl")
    rdir = write_fixture_receipt(tmp_path / "receipts")
    return rdir, json.loads((rdir / "receipt.json").read_text())


def test_no_progress_rewrite_preserves_commitment(receipt, monkeypatch):
    rdir, rec = receipt
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda c, h: (False, "HTTP 404"))
    before = _snapshot(rdir)
    result = upgrade_worker._upgrade_one(rdir, rec)
    assert result["status"] != "pinned" and result["stalls"] == 1   # the rewrite really happened
    assert_commitment_preserved(before, _snapshot(rdir))


def test_pinned_rewrite_preserves_commitment(receipt, monkeypatch):
    rdir, rec = receipt
    monkeypatch.setattr(upgrade_worker, "_fetch_upgrade", lambda c, h: (True, PINNED_BODY))
    before = _snapshot(rdir)
    result = upgrade_worker._upgrade_one(rdir, rec)
    assert result["status"] == "pinned", result
    after = _snapshot(rdir)
    assert_commitment_preserved(before, after)
    # The proof bytes DID change (that is the upgrade) — only the digest is frozen.
    assert any((rdir / n).read_bytes() != b"" for n in after["ots_digests"])


def test_checker_fails_on_a_mutated_field(receipt):
    rdir, rec = receipt
    before = _snapshot(rdir)
    rec["hash_hex"] = "ab" * 32
    (rdir / "receipt.json").write_text(json.dumps(rec))
    with pytest.raises(AssertionError, match="hash_hex"):
        assert_commitment_preserved(before, _snapshot(rdir))


def test_checker_fails_on_a_mutated_calendar_list(receipt):
    rdir, rec = receipt
    before = _snapshot(rdir)
    rec["successes"][0]["calendar"] = "https://evil.example/"
    (rdir / "receipt.json").write_text(json.dumps(rec))
    with pytest.raises(AssertionError, match="successes"):
        assert_commitment_preserved(before, _snapshot(rdir))


def test_checker_fails_on_a_corrupted_proof_digest(receipt):
    rdir, _ = receipt
    before = _snapshot(rdir)
    ots = sorted(rdir.glob("*.ots"))[0]
    b = bytearray(ots.read_bytes())
    b[DIGEST_OFFSET] ^= 0xFF
    ots.write_bytes(bytes(b))
    with pytest.raises(AssertionError, match="embedded digest"):
        assert_commitment_preserved(before, _snapshot(rdir))
