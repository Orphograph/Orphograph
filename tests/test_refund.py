from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import credits

REPO_ROOT = Path(__file__).resolve().parent.parent
REFUND_SCRIPT = REPO_ROOT / "scripts" / "refund_pack.py"


@pytest.fixture
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    yield tmp_path


def _run(args, ledger_path):
    env = {
        "PATH": "/usr/bin:/bin",
        "ORPHO_CREDIT_LEDGER": str(ledger_path),
    }
    proc = subprocess.run(
        [sys.executable, str(REFUND_SCRIPT), *args],
        capture_output=True, text=True, env=env,
    )
    return proc


def test_refund_zeros_remaining_balance(isolated_ledger):
    ledger = isolated_ledger / "credit_ledger.jsonl"
    credits.add_credits("pk_abc", "buyer@example.com", 10, "stripe:cs_test")
    credits.consume_credit("pk_abc")  # 9 left
    credits.consume_credit("pk_abc")  # 8 left
    assert credits.balance("pk_abc") == 8

    proc = _run(["--claim-code", "pk_abc", "--reason", "chargeback"], ledger)
    assert proc.returncode == 0, proc.stderr
    # Re-load the ledger in this process to verify
    import importlib
    importlib.reload(credits)
    credits.LEDGER_PATH = ledger
    assert credits.balance("pk_abc") == 0


def test_refund_dry_run_does_not_modify(isolated_ledger):
    ledger = isolated_ledger / "credit_ledger.jsonl"
    credits.add_credits("pk_xyz", "buyer@example.com", 10, "stripe:cs_x")
    before = ledger.read_text()
    proc = _run(["--claim-code", "pk_xyz", "--reason", "chargeback", "--dry-run"], ledger)
    assert proc.returncode == 0, proc.stderr
    after = ledger.read_text()
    assert before == after, "dry-run must not modify the ledger"
    assert '"would_zero"' in proc.stdout


def test_refund_by_email_zeros_all_codes(isolated_ledger):
    ledger = isolated_ledger / "credit_ledger.jsonl"
    credits.add_credits("pk_a", "alice@b.com", 10, "stripe:cs_1")
    credits.add_credits("pk_b", "alice@b.com", 10, "stripe:cs_2")
    credits.add_credits("pk_c", "bob@b.com", 10, "stripe:cs_3")
    proc = _run(["--email", "alice@b.com", "--reason", "fraud"], ledger)
    assert proc.returncode == 0, proc.stderr
    import importlib
    importlib.reload(credits)
    credits.LEDGER_PATH = ledger
    assert credits.balance("pk_a") == 0
    assert credits.balance("pk_b") == 0
    # bob's pack untouched
    assert credits.balance("pk_c") == 10


def test_refund_email_with_no_purchases_returns_2(isolated_ledger):
    ledger = isolated_ledger / "credit_ledger.jsonl"
    proc = _run(["--email", "ghost@example.com", "--reason", "manual"], ledger)
    assert proc.returncode == 2


def test_refund_already_zero_is_noop(isolated_ledger):
    ledger = isolated_ledger / "credit_ledger.jsonl"
    credits.add_credits("pk_zero", "x@y.com", 5, "test")
    for _ in range(5):
        credits.consume_credit("pk_zero")
    proc = _run(["--claim-code", "pk_zero", "--reason", "manual"], ledger)
    assert proc.returncode == 0
    assert '"noop"' in proc.stdout
