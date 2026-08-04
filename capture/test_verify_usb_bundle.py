"""capture/test_verify_usb_bundle.py — offline roundtrip tests for verify_usb_bundle.py.

Additive-only suite proving Hermes's checklist item: an on-drive .ots bundle
roundtrips through server/verify_cli.py fully offline. A tmp_path directory
stands in for the mounted USB drive; the sidecar (.orphograph/index.jsonl +
receipts/<rid>.json + receipts/<rid>/ bundle) is synthesized exactly the way
orphograph_usb.py writes it, with mock anchor responses and hand-built .ots
blobs that satisfy verify_cli's structural checks. No network anywhere.

Run:  python3 -m pytest -q -p no:anchorpy capture/test_verify_usb_bundle.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

import pytest

CAPTURE_DIR = Path(__file__).resolve().parent
if str(CAPTURE_DIR) not in sys.path:
    sys.path.insert(0, str(CAPTURE_DIR))

import verify_usb_bundle as vub  # noqa: E402  (also exposes vub.verify_cli)

ORPHO = vub.ORPHO_DIR
OTS_MAGIC = vub.verify_cli.OTS_HEADER_MAGIC
ENDPOINT = "https://example.invalid"


# --------------------------------------------------------------------------- #
# Offline guard: no test may touch the network. Neither module under test
# does any I/O beyond the local filesystem; this trips if that ever changes.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover — tripping it is the failure
        raise AssertionError("test attempted a real network call via urlopen")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    yield


# --------------------------------------------------------------------------- #
# Synthetic sidecar builder (mirrors orphograph_usb.py's on-drive layout and
# the FakeAnchor response shape used in test_orphograph_usb.py)
# --------------------------------------------------------------------------- #
def _ots_blob(sha256_hex: str, calendar: str = "alice") -> bytes:
    """Minimal blob passing verify_cli's structural checks: header magic,
    2 bytes (version varint + hashop tag), then the 32-byte digest."""
    return (OTS_MAGIC + b"\x01\x08" + bytes.fromhex(sha256_hex)
            + b"\xf0\x10" + calendar.encode())  # opaque trailing ops


def make_anchored_record(mount: Path, rel: str, data: bytes, rid: str,
                         n_ots: int = 2) -> dict:
    """Write file + index line + anchor response + offline proof bundle,
    exactly as a successful scan_once(..., fetch_proofs=True) leaves them."""
    f = mount / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    sha256 = hashlib.sha256(data).hexdigest()
    sha512 = hashlib.sha512(data).hexdigest()

    # Mock anchor response (what /api/anchor returned).
    resp = {"receipt_id": rid, "created_at": "2026-08-02T00:00:00+00:00",
            "hash_hex": sha256, "sha512_hex": sha512,
            "calendars_ok": n_ots, "calendars_total": 5}
    base = mount / ORPHO
    receipts = base / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    (receipts / f"{rid}.json").write_text(json.dumps(resp, indent=2))

    # Offline proof bundle: receipts/<rid>/receipt.json + *.ots.
    bundle = receipts / rid
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "receipt.json").write_text(json.dumps(resp, indent=2))
    for i in range(n_ots):
        (bundle / f"calendar{i}.ots").write_bytes(_ots_blob(sha256, f"cal{i}"))

    # Index row (same fields scan_once appends).
    row = {"sha256": sha256, "sha512": sha512, "relpath": rel,
           "receipt_id": rid, "receipt_url": f"{ENDPOINT}/r/{rid}",
           "anchored_at": resp["created_at"], "calendars_ok": n_ots,
           "status": "anchored", "ts": resp["created_at"]}
    with (base / "index.jsonl").open("a") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    return row


def _append_row(mount: Path, row: dict) -> None:
    base = mount / ORPHO
    base.mkdir(parents=True, exist_ok=True)
    with (base / "index.jsonl").open("a") as fh:
        fh.write(json.dumps(row) + "\n")


# --------------------------------------------------------------------------- #
# 1. Happy roundtrip
# --------------------------------------------------------------------------- #
def test_happy_roundtrip_all_verified(tmp_path, capsys):
    make_anchored_record(tmp_path, "photos/img.jpg", b"jpeg bytes", "RID0001")
    make_anchored_record(tmp_path, "docs/note.txt", b"text bytes", "RID0002")
    assert vub.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.count("[PASS]") == 2
    assert "[FAIL]" not in out
    assert "2 verified, 0 failed" in out


def test_roundtrip_verifies_through_verify_cli_itself(tmp_path):
    # The bundle must satisfy the vendored verifier DIRECTLY (the checklist
    # item), not just our wrapper: verify() == 0 with the original file.
    row = make_anchored_record(tmp_path, "a.bin", b"\x00\xffpayload", "RIDX")
    bundle_receipt = tmp_path / ORPHO / "receipts" / "RIDX" / "receipt.json"
    assert vub.verify_cli.verify(bundle_receipt, tmp_path / row["relpath"]) == 0


def test_pending_and_failed_records_do_not_fail_the_run(tmp_path, capsys):
    make_anchored_record(tmp_path, "ok.txt", b"anchored fine", "RID0001")
    _append_row(tmp_path, {"sha256": "ab" * 32, "sha512": "cd" * 64,
                           "relpath": "queued.txt", "status": "pending",
                           "reason": "rate_limited", "ts": "t"})
    assert vub.main([str(tmp_path)]) == 0
    assert "1 pending/failed record(s) skipped" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 2. Tampered file detected
# --------------------------------------------------------------------------- #
def test_tampered_file_detected(tmp_path, capsys):
    make_anchored_record(tmp_path, "contract.pdf", b"original bytes", "RID0001")
    (tmp_path / "contract.pdf").write_bytes(b"TAMPERED bytes")
    assert vub.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] contract.pdf" in out
    assert "verify_cli exit 3" in out  # file/hash mismatch, straight from verify()


def test_tampered_ots_digest_detected(tmp_path, capsys):
    # Proof bytes claiming a DIFFERENT hash than the receipt -> verify_cli 4.
    make_anchored_record(tmp_path, "a.txt", b"real content", "RID0001")
    bundle = tmp_path / ORPHO / "receipts" / "RID0001"
    (bundle / "calendar0.ots").write_bytes(_ots_blob("11" * 32))
    assert vub.main([str(tmp_path)]) == 1
    assert "verify_cli exit 4" in capsys.readouterr().out


def test_tamper_only_flags_the_tampered_file(tmp_path, capsys):
    make_anchored_record(tmp_path, "good.txt", b"good", "RID0001")
    make_anchored_record(tmp_path, "bad.txt", b"bad", "RID0002")
    (tmp_path / "bad.txt").write_bytes(b"mutated")
    assert vub.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "[PASS] good.txt" in out
    assert "[FAIL] bad.txt" in out
    assert "1 verified, 1 failed" in out


# --------------------------------------------------------------------------- #
# 3. Missing .ots detected
# --------------------------------------------------------------------------- #
def test_missing_ots_files_detected(tmp_path, capsys):
    # All .ots deleted but receipt.json still present. verify_cli alone would
    # return 0 on this empty bundle (its loop never runs) — the wrapper must
    # catch it.
    make_anchored_record(tmp_path, "a.txt", b"alpha", "RID0001")
    bundle = tmp_path / ORPHO / "receipts" / "RID0001"
    for ots in bundle.glob("*.ots"):
        ots.unlink()
    assert vub.main([str(tmp_path)]) == 1
    assert ".ots proofs missing" in capsys.readouterr().out


def test_missing_bundle_dir_detected(tmp_path, capsys):
    # Index says anchored but the offline bundle was never fetched
    # (--no-proofs or the download failed): flat receipts/<rid>.json exists,
    # receipts/<rid>/ does not.
    make_anchored_record(tmp_path, "a.txt", b"alpha", "RID0001")
    bundle = tmp_path / ORPHO / "receipts" / "RID0001"
    for child in bundle.iterdir():
        child.unlink()
    bundle.rmdir()
    assert (tmp_path / ORPHO / "receipts" / "RID0001.json").is_file()
    assert vub.main([str(tmp_path)]) == 1
    assert "no offline proof bundle" in capsys.readouterr().out


def test_missing_source_file_detected(tmp_path, capsys):
    make_anchored_record(tmp_path, "a.txt", b"alpha", "RID0001")
    (tmp_path / "a.txt").unlink()
    assert vub.main([str(tmp_path)]) == 1
    assert "file missing on drive" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 4. Usage errors (exit 2) + edge cases
# --------------------------------------------------------------------------- #
def test_nonexistent_root_exits_2(tmp_path):
    assert vub.main([str(tmp_path / "no-such-dir")]) == 2


def test_dir_without_sidecar_exits_2(tmp_path):
    (tmp_path / "random.txt").write_text("not an orphograph drive")
    assert vub.main([str(tmp_path)]) == 2


def test_no_args_exits_2():
    with pytest.raises(SystemExit) as exc:
        vub.main([])
    assert exc.value.code == 2  # argparse usage error


def test_bundle_receipt_hash_mismatch_with_index_detected(tmp_path, capsys):
    # A bundle swapped in from a different anchor must not pass just because
    # its .ots blobs are internally consistent.
    row = make_anchored_record(tmp_path, "a.txt", b"alpha", "RID0001")
    other_sha = hashlib.sha256(b"other content").hexdigest()
    bundle = tmp_path / ORPHO / "receipts" / "RID0001"
    receipt = json.loads((bundle / "receipt.json").read_text())
    receipt["hash_hex"] = other_sha
    (bundle / "receipt.json").write_text(json.dumps(receipt))
    for i, ots in enumerate(sorted(bundle.glob("*.ots"))):
        ots.write_bytes(_ots_blob(other_sha, f"cal{i}"))
    assert row["sha256"] != other_sha
    assert vub.main([str(tmp_path)]) == 1
    assert "does not match the index sha256" in capsys.readouterr().out
