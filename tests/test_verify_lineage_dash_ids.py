"""verify_lineage.py must accept a receipt id that begins with '-'.

Receipt ids are url-safe base64 (secrets.token_urlsafe(12)); about one in 64
begins with '-'. argparse reads such a value as an option, so the documented
`--tip RID` failed with "expected one argument" for those receipts, and the
offline-export tests that pass a fresh id that way failed CI about once in 64
runs (seen on PR #267's first run).
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "dist" / "orphograph-verify" / "verify_lineage.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True,
                          text=True, timeout=60)


def test_a_tip_that_begins_with_a_dash_reaches_the_lookup(tmp_path):
    (tmp_path / "SomeReceipt01").mkdir()
    (tmp_path / "SomeReceipt01" / "receipt.json").write_text(
        '{"receipt_id": "SomeReceipt01", "hash_hex": "' + "ab" * 32 + '"}')
    out = _run("--chain", str(tmp_path), "--tip", "-AbCdEfGhIjKlMnO")
    assert "expected one argument" not in out.stderr, out.stderr
    assert "not found in chain dir" in out.stderr, out.stderr  # the id was read


def test_a_dir_whose_rid_begins_with_a_dash_is_read(tmp_path):
    out = _run("--chain", str(tmp_path), "--dir", f"-AbCdEfGhIjKlMnO={tmp_path}")
    assert "expected one argument" not in out.stderr, out.stderr


def test_a_missing_value_is_still_reported_missing(tmp_path):
    out = _run("--chain", str(tmp_path), "--tip", "--ots-check")
    assert out.returncode == 2 and "expected one argument" in out.stderr, out.stderr


def test_joining_is_limited_to_value_options():
    spec = importlib.util.spec_from_file_location("verify_lineage_t", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._join_dash_values(["--tip", "-x", "--chain", "c"]) == ["--tip=-x", "--chain", "c"]
    assert mod._join_dash_values(["--tip", "--chain", "c"]) == ["--tip", "--chain", "c"]
    assert mod._join_dash_values(["--dir", "-r=/p"]) == ["--dir=-r=/p"]
    assert mod._join_dash_values(["--ots-check", "-x"]) == ["--ots-check", "-x"]
