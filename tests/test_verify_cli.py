from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VERIFY_CLI = REPO_ROOT / "server" / "verify_cli.py"
SAMPLE_DIR = REPO_ROOT / "web" / "sample"


def _run(*args: str) -> tuple[int, str]:
    """Run verify_cli.py and return (exit_code, stdout)."""
    cmd = [sys.executable, str(VERIFY_CLI), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def test_sample_receipt_validates_clean():
    receipt = SAMPLE_DIR / "receipt.json"
    sample = SAMPLE_DIR / "sample.txt"
    if not receipt.exists() or not sample.exists():
        pytest.skip("sample receipt not present in web/sample/")
    code, out = _run(str(receipt), "--file", str(sample))
    assert code == 0, out
    assert "file matches:   YES" in out


def test_missing_receipt_exits_2(tmp_path):
    code, out = _run(str(tmp_path / "does-not-exist.json"))
    assert code == 2


def test_file_mismatch_exits_3(tmp_path):
    receipt = SAMPLE_DIR / "receipt.json"
    if not receipt.exists():
        pytest.skip("sample receipt not present in web/sample/")
    wrong = tmp_path / "wrong.txt"
    wrong.write_text("definitely not the sample contents")
    code, out = _run(str(receipt), "--file", str(wrong))
    assert code == 3, out
    assert "file matches:   NO" in out


def test_tampered_ots_exits_4(tmp_path):
    receipt = SAMPLE_DIR / "receipt.json"
    if not receipt.exists():
        pytest.skip("sample receipt not present in web/sample/")
    fake = tmp_path / "receipt-dir"
    fake.mkdir()
    record = json.loads(receipt.read_text())
    (fake / "receipt.json").write_text(json.dumps(record))
    for ots in SAMPLE_DIR.glob("*.ots"):
        target = fake / ots.name
        data = ots.read_bytes()
        # zero out the embedded hash bytes to force a magic+hash mismatch
        target.write_bytes(data[:50] + b"\x00" * 32 + data[82:])
    code, out = _run(str(fake / "receipt.json"))
    assert code == 4, out


def test_empty_bundle_fails_instead_of_vacuous_pass(tmp_path):
    # Regression: with zero .ots files the validation loop never ran, `bad`
    # stayed 0, and an empty bundle printed "all receipts valid" with exit 0.
    # A bundle with nothing to verify must fail loudly.
    bundle = tmp_path / "empty-bundle"
    bundle.mkdir()
    (bundle / "receipt.json").write_text(json.dumps({
        "receipt_id": "EMPTY00000000000",
        "created_at": "2026-08-03T00:00:00+00:00",
        "hash_hex": "ab" * 32,
    }))
    code, out = _run(str(bundle / "receipt.json"))
    assert code == 4, out
    assert "proves nothing" in out
    assert "all receipts valid" not in out
