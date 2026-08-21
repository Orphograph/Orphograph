"""Tests for expire_worker's age clock, error accounting, and run provenance.

This is the only code path in the product that permanently destroys customer
data, and it had no test file at all before 2026-08-21.
"""
import importlib
import json
import os
import socket
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def worker(tmp_path, monkeypatch):
    monkeypatch.setenv("ORPHO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ORPHO_RECEIPTS_DIR", str(tmp_path / "receipts"))
    monkeypatch.setenv("ORPHO_EXPIRY_LOG", str(tmp_path / "expiry_log.jsonl"))
    import server.expire_worker as ew
    importlib.reload(ew)
    return ew


def _mk(tmp_path, rid, *, source="free", created_days_ago=None, mtime_days_ago=0,
        body=None):
    d = tmp_path / "receipts" / rid
    d.mkdir(parents=True)
    rec = {"receipt_id": rid, "source": source}
    if created_days_ago is not None:
        rec["created_at"] = (
            datetime.now(timezone.utc) - timedelta(days=created_days_ago)
        ).isoformat(timespec="seconds")
    f = d / "receipt.json"
    f.write_text(body if body is not None else json.dumps(rec))
    ts = (datetime.now(timezone.utc) - timedelta(days=mtime_days_ago)).timestamp()
    os.utime(f, (ts, ts))
    return d


# --- G1: the clock is created_at, not mtime ------------------------------

def test_old_receipt_expires_even_when_mtime_is_fresh(worker, tmp_path):
    """The upgrade worker rewrites receipt.json, resetting mtime. Under the old
    mtime clock this receipt was immortal."""
    _mk(tmp_path, "old-but-touched", created_days_ago=90, mtime_days_ago=0)
    s = worker.expire_old_free(days=30, dry_run=True)
    assert s["expired"] == 1, "created_at must drive expiry, not mtime"
    assert s["clock_basis"] == {"created_at": 1}


def test_fresh_receipt_survives_even_when_mtime_is_ancient(worker, tmp_path):
    _mk(tmp_path, "new-but-stale-mtime", created_days_ago=1, mtime_days_ago=400)
    s = worker.expire_old_free(days=30, dry_run=True)
    assert s["expired"] == 0
    assert s["skipped_fresh"] == 1


def test_mtime_is_the_fallback_when_created_at_absent(worker, tmp_path):
    _mk(tmp_path, "legacy", created_days_ago=None, mtime_days_ago=90)
    s = worker.expire_old_free(days=30, dry_run=True)
    assert s["expired"] == 1
    assert s["clock_basis"] == {"mtime": 1}, "basis must be reported, not guessed at"


def test_naive_created_at_is_read_as_utc(worker, tmp_path):
    d = tmp_path / "receipts" / "naive"
    d.mkdir(parents=True)
    naive = (datetime.now(timezone.utc) - timedelta(days=90)).replace(
        tzinfo=None).isoformat(timespec="seconds")
    (d / "receipt.json").write_text(
        json.dumps({"receipt_id": "naive", "source": "free", "created_at": naive}))
    s = worker.expire_old_free(days=30, dry_run=True)
    assert s["expired"] == 1
    assert s["clock_basis"] == {"created_at": 1}


def test_unparseable_created_at_falls_back_and_does_not_crash(worker, tmp_path):
    d = tmp_path / "receipts" / "bad-date"
    d.mkdir(parents=True)
    (d / "receipt.json").write_text(
        json.dumps({"receipt_id": "bad-date", "source": "free",
                    "created_at": "not-a-date"}))
    os.utime(d / "receipt.json", (0, 0))
    s = worker.expire_old_free(days=30, dry_run=True)
    assert s["expired"] == 1
    assert s["clock_basis"] == {"mtime": 1}


# --- paid receipts are never touched -------------------------------------

@pytest.mark.parametrize("source", ["sub:abc", "pack:xyz", "api:k", "ln:h", "unknown"])
def test_non_free_sources_are_never_pruned(worker, tmp_path, source):
    _mk(tmp_path, f"paid-{source[:3]}", source=source, created_days_ago=999)
    s = worker.expire_old_free(days=30, dry_run=True)
    assert s["expired"] == 0
    assert s["skipped_paid"] == 1


# --- G4: failures are counted, never silent ------------------------------

def test_malformed_receipt_counts_as_error_not_silence(worker, tmp_path):
    _mk(tmp_path, "broken", body="{not json")
    s = worker.expire_old_free(days=30, dry_run=True)
    assert s["errors"] == 1, "a malformed receipt must be visible in the summary"
    assert s["expired"] == 0


def test_one_broken_receipt_does_not_abort_the_run(worker, tmp_path):
    _mk(tmp_path, "a-broken", body="{not json")
    _mk(tmp_path, "b-good", created_days_ago=90)
    s = worker.expire_old_free(days=30, dry_run=True)
    assert s["errors"] == 1 and s["expired"] == 1


def test_undeletable_receipt_is_counted_and_run_completes(worker, tmp_path, monkeypatch):
    _mk(tmp_path, "a-locked", created_days_ago=90)
    _mk(tmp_path, "b-fine", created_days_ago=90)

    real = worker.shutil.rmtree

    def flaky(path, **kw):
        if "a-locked" in str(path):
            raise OSError("permission denied")
        return real(path, **kw)

    monkeypatch.setattr(worker.shutil, "rmtree", flaky)
    s = worker.expire_old_free(days=30)
    assert s["errors"] == 1
    assert s["expired"] == 1, "the healthy receipt must still be pruned"
    assert (tmp_path / "expiry_log.jsonl").exists(), "the run must still be logged"


# --- run provenance ------------------------------------------------------

def test_summary_records_host_and_receipts_dir(worker, tmp_path):
    _mk(tmp_path, "x", created_days_ago=1)
    s = worker.expire_old_free(days=30, dry_run=True)
    assert s["host"] == socket.gethostname()
    assert s["receipts_dir"] == str(tmp_path / "receipts")


def test_logged_line_carries_provenance(worker, tmp_path):
    _mk(tmp_path, "x", created_days_ago=1)
    worker.expire_old_free(days=30, dry_run=True)
    line = json.loads((tmp_path / "expiry_log.jsonl").read_text().splitlines()[-1])
    assert "host" in line and "receipts_dir" in line, (
        "a log without provenance lets a laptop run read as production evidence")


def test_missing_receipts_dir_returns_typed_empty_summary(worker, tmp_path):
    s = worker.expire_old_free(days=30, dry_run=True)
    assert s["errors"] == 0 and s["clock_basis"] == {}


# --- dry run really is dry ----------------------------------------------

def test_dry_run_deletes_nothing(worker, tmp_path):
    d = _mk(tmp_path, "keepme", created_days_ago=90)
    s = worker.expire_old_free(days=30, dry_run=True)
    assert s["expired"] == 1 and d.exists()


def test_wet_run_actually_deletes(worker, tmp_path):
    d = _mk(tmp_path, "goodbye", created_days_ago=90)
    s = worker.expire_old_free(days=30)
    assert s["expired"] == 1 and not d.exists()


# --- directory scanning edge cases ---------------------------------------

def test_stray_file_in_receipts_dir_is_ignored(worker, tmp_path):
    (tmp_path / "receipts").mkdir(parents=True)
    (tmp_path / "receipts" / ".DS_Store").write_text("junk")
    s = worker.expire_old_free(days=30, dry_run=True)
    assert s["scanned"] == 0 and s["errors"] == 0


def test_receipt_dir_without_receipt_json_is_not_scanned(worker, tmp_path):
    (tmp_path / "receipts" / "empty-dir").mkdir(parents=True)
    s = worker.expire_old_free(days=30, dry_run=True)
    assert s["scanned"] == 0 and s["expired"] == 0


def test_unreadable_age_counts_as_error_never_a_deletion(worker, tmp_path, monkeypatch):
    """If the age cannot be established at all, the receipt is an error — never
    a silent skip, and above all never treated as expired."""
    d = _mk(tmp_path, "unreadable-age", created_days_ago=None, mtime_days_ago=90)

    def boom(record, receipt_file):
        raise OSError("stat failed")

    monkeypatch.setattr(worker, "_age_basis", boom)
    s = worker.expire_old_free(days=30)
    assert s["errors"] == 1 and s["expired"] == 0
    assert d.exists(), "an unknown age must never license deletion"


# --- the CLI entry point launchd actually invokes ------------------------

def test_main_dry_run_reports_without_deleting(worker, tmp_path, monkeypatch, capsys):
    d = _mk(tmp_path, "cli-dry", created_days_ago=90)
    monkeypatch.setattr(worker.sys, "argv", ["expire_worker.py", "--dry-run"])
    assert worker.main() == 0
    out = capsys.readouterr().out
    assert "would expire 1 receipt(s)" in out
    assert d.exists()
    assert "expired_ids" not in out, "receipt ids must not leak to stdout/log files"


def test_main_default_invocation_is_wet(worker, tmp_path, monkeypatch, capsys):
    """launchd calls the script with no arguments — that path deletes."""
    d = _mk(tmp_path, "cli-wet", created_days_ago=90)
    monkeypatch.setattr(worker.sys, "argv", ["expire_worker.py"])
    assert worker.main() == 0
    assert not d.exists()
    assert json.loads(capsys.readouterr().out)["expired"] == 1


def test_null_source_is_retained_and_does_not_abort_the_run(worker, tmp_path):
    """A receipt with "source": null used to raise AttributeError on
    .startswith and kill the scan. It must be kept (not free) and the run
    must continue to the next receipt."""
    d = tmp_path / "receipts" / "null-source"
    d.mkdir(parents=True)
    (d / "receipt.json").write_text(json.dumps({"receipt_id": "null-source",
                                                "source": None}))
    _mk(tmp_path, "zz-free-old", created_days_ago=90)
    s = worker.expire_old_free(days=30, dry_run=True)
    assert s["skipped_paid"] == 1, "null source is retained, never pruned"
    assert s["expired"] == 1, "the scan continued past the bad receipt"
