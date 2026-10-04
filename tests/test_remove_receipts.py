"""scripts/remove_receipts.py takes named anonymous free receipts off the service, reversibly.

Founder decision 2026-10-03: remove the three public receipts a review agent's
test run made on production that morning (throwaway content). Every public
answer for a receipt is built from DATA_DIR/receipts/<id>/, so moving that
directory into quarantine removes it everywhere; nothing is deleted and the
ledger is never touched. Temp data dirs only; nothing leaves the machine.
"""
from __future__ import annotations

import fcntl
import hashlib
import importlib
import importlib.util
import json
import os
import socket
import stat
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"
_spec = importlib.util.spec_from_file_location("remove_receipts", ROOT / "scripts" / "remove_receipts.py")
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)

BAD = ["XUmId7he6sN6BWFj", "gilDxAI0gsaZdz_I", "KpqYAgFLYjBPJPtD"]
CONTROL = "CtrlCtrlCtrl0001"
CHILD = "ChildChildChild1"
PAID = "PaidPaidPaidPai1"
DAY = "2026-10-03"


def _rec(rid, **kw):
    r = {"receipt_id": rid, "created_at": f"{DAY}T05:20:11+00:00",
         "hash_hex": "ab" * 32, "source": "free", "private": False,
         "owner_id": None, "calendars_ok": 3, "calendars_total": 5,
         "successes": [], "failures": [], "kind": "folder", "leaf_count": 2,
         "client_label": "throwaway-label"}
    r.update(kw)
    return r


def _mk(data: Path, rid: str, rec: dict, lock=True):
    d = data / "receipts" / rid
    d.mkdir(parents=True)
    (d / "receipt.json").write_text(json.dumps(rec, indent=2))
    (d / "manifest.json").write_text("{}")
    (d / "alice.ots").write_bytes(b"\x00ots")
    (d / "alice.ots.prev").write_bytes(b"\x00prev")
    if lock:
        (d / ".upgrade.lock").write_bytes(b"")


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    for rid in BAD:
        _mk(d, rid, _rec(rid))
    _mk(d, CONTROL, _rec(CONTROL, created_at="2026-09-01T00:00:00+00:00"))
    _mk(d, CHILD, _rec(CHILD, lineage={"parent_receipt_id": BAD[0], "parent_root": "cd" * 32}))
    ledger = b"".join((json.dumps(_rec(r), separators=(",", ":")) + "\n").encode()
                      for r in BAD + [CONTROL, CHILD])
    (d / "ledger.jsonl").write_bytes(ledger)
    return d


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def _run(data, *extra, ids=BAD):
    return rr.run(["--data-dir", str(data), "--server-dir", str(SERVER), *extra, "--", *ids])


def _apply(data, ids=BAD):
    return _run(data, "--apply", "--created-on", DAY, ids=ids)


def test_dry_run_changes_nothing(data, capsys):
    before = _tree_hash(data)
    assert _run(data) == 0
    assert _tree_hash(data) == before
    out = capsys.readouterr().out
    assert out.count("would move") == 3 and "nothing changed" in out


def test_apply_moves_the_three_and_keeps_the_control_the_child_and_the_ledger(data):
    ledger_before = (data / "ledger.jsonl").read_bytes()
    assert _apply(data) == 0
    for rid in BAD:
        assert not (data / "receipts" / rid).exists()
    assert (data / "receipts" / CONTROL / "receipt.json").exists()
    assert (data / "receipts" / CHILD / "receipt.json").exists()
    [run_dir] = list((data / "quarantine").iterdir())
    for rid in BAD:
        assert {p.name for p in (run_dir / "receipts" / rid).iterdir()} == {
            "receipt.json", "manifest.json", "alice.ots", "alice.ots.prev", ".upgrade.lock"}
    meta = json.loads((run_dir / "removal.json").read_text())
    assert sorted(meta["moved"]) == sorted(BAD) and len(meta["undo"]) == 3
    assert (data / "ledger.jsonl").read_bytes() == ledger_before   # the books are not mutated


def test_quarantine_is_private_to_the_server_user(data):
    assert _apply(data) == 0
    q = data / "quarantine"
    [run_dir] = list(q.iterdir())
    for p in (q, run_dir, run_dir / "receipts"):
        assert stat.S_IMODE(p.stat().st_mode) & 0o077 == 0, p


def test_the_printed_undo_puts_everything_back(data, capsys):
    before = _tree_hash(data / "receipts")
    assert _apply(data) == 0
    undo = [ln.split(": ", 1)[1] for ln in capsys.readouterr().out.splitlines() if ln.strip().startswith("undo ")]
    assert len(undo) == 3
    for cmd in undo:
        mv, flag, src, dst = cmd.split(" ")
        assert (mv, flag) == ("mv", "-T")      # never into an existing dir at dst
        os.rename(src, dst)                    # rename(2) is what mv -T does
    assert _tree_hash(data / "receipts") == before


def test_held_lock_skips_that_id_and_moves_the_rest(data):
    held = os.open(str(data / "receipts" / BAD[0] / ".upgrade.lock"), os.O_WRONLY)
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert _apply(data) == 4
    finally:
        os.close(held)
    assert (data / "receipts" / BAD[0] / "receipt.json").exists()
    assert not (data / "receipts" / BAD[1]).exists()
    assert _apply(data, ids=BAD[:1]) == 0                 # run again once the worker lets go


def test_a_lock_file_it_had_to_create_is_not_left_behind(data, monkeypatch):
    # Round of the 2026-10-03 analysis: under `fly ssh` the script runs as root,
    # and a root-owned .upgrade.lock left in a receipt the upgrade worker (orpho)
    # still owns would stop that receipt's upgrades.
    (data / "receipts" / BAD[0] / ".upgrade.lock").unlink()

    def refuse(src, dst):
        raise OSError("planted rename failure")
    monkeypatch.setattr(rr.os, "rename", refuse)
    with pytest.raises(OSError):
        _apply(data, ids=BAD[:1])
    assert not (data / "receipts" / BAD[0] / ".upgrade.lock").exists()
    assert (data / "receipts" / BAD[0] / "receipt.json").exists()


def test_a_lock_file_it_created_does_not_travel_into_quarantine(data, capsys):
    # Review of PR #285: the lock this run creates is root-owned under fly ssh.
    # Carried into quarantine, the printed undo put it back, and the upgrade
    # worker (uid orpho) could no longer open it.
    (data / "receipts" / BAD[2] / ".upgrade.lock").unlink()
    assert _apply(data) == 0
    [run_dir] = list((data / "quarantine").iterdir())
    assert not (run_dir / "receipts" / BAD[2] / ".upgrade.lock").exists()
    assert (run_dir / "receipts" / BAD[0] / ".upgrade.lock").exists()   # one that was already there stays
    meta = json.loads((run_dir / "removal.json").read_text())
    assert ".upgrade.lock" not in meta["files"][BAD[2]]


def test_a_busy_id_is_reported_busy_not_moved(data, capsys):
    held = os.open(str(data / "receipts" / BAD[0] / ".upgrade.lock"), os.O_WRONLY)
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert _apply(data) == 4
    finally:
        os.close(held)
    out = capsys.readouterr().out
    assert f"BUSY {BAD[0]}" in out and f"moved {BAD[0]}" not in out and f"moved {BAD[1]}" in out


def test_a_failure_mid_run_still_records_what_moved(data, monkeypatch):
    real = os.rename
    calls = []

    def second_fails(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise OSError("planted failure on the second move")
        real(src, dst)
    monkeypatch.setattr(rr.os, "rename", second_fails)
    with pytest.raises(OSError):
        _apply(data)
    [run_dir] = list((data / "quarantine").iterdir())
    meta = json.loads((run_dir / "removal.json").read_text())
    assert meta["moved"] == [BAD[0]] and len(meta["undo"]) == 1


def test_a_stub_the_worker_recreates_mid_run_is_not_a_failed_post_check(data, monkeypatch, capsys):
    real = os.rename

    def rename_then_worker_recreates(src, dst):
        real(src, dst)
        Path(src).mkdir()
        (Path(src) / ".upgrade.lock").write_bytes(b"")
    monkeypatch.setattr(rr.os, "rename", rename_then_worker_recreates)
    assert _apply(data, ids=BAD[:1]) == 0
    assert "lock stub" in capsys.readouterr().err
    monkeypatch.setattr(rr.os, "rename", real)
    assert _apply(data, ids=BAD[:1]) == 0                  # the rerun clears the stub
    assert not (data / "receipts" / BAD[0]).exists()


def test_second_apply_is_zero_changes(data, capsys):
    assert _apply(data) == 0
    snap = _tree_hash(data)
    assert _apply(data) == 0
    assert _tree_hash(data) == snap
    assert capsys.readouterr().out.count("already removed") == 3


def test_a_lock_stub_the_worker_recreated_is_cleaned(data):
    assert _apply(data) == 0
    stub = data / "receipts" / BAD[2]
    stub.mkdir()
    (stub / ".upgrade.lock").write_bytes(b"")
    assert _apply(data) == 0
    assert not stub.exists()


@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "XUmId7he6sN6BWF", "XUmId7he6sN6BWFjX",
    "Ａ" * 16, "XUmId7he6sN6BWFé", "XUmId7he6sN6BW/j", "",
])
def test_non_id_arguments_are_refused(data, bad):
    before = _tree_hash(data)
    assert _run(data, "--apply", "--created-on", DAY, ids=[bad]) == 2
    assert _tree_hash(data) == before


def test_an_id_that_starts_with_a_dash_is_an_id_after_double_dash(data):
    rid = "-dashDASHdash-01"
    _mk(data, rid, _rec(rid))
    assert _apply(data, ids=[rid]) == 0
    assert not (data / "receipts" / rid).exists()


@pytest.mark.parametrize("field", [
    {"source": "pack:abcd1234"}, {"source": "sub:x"}, {"source": "api:k"}, {"account_id": "acct"},
    {"owner_id": "o"}, {"private": True}, {"notify_email": "n"},
    {"office_signature": "0" * 128}, {"client_label": "weekly-2026-10-04"},
    {"created_at": "2026-10-02T23:59:59+00:00"},
])
def test_any_paid_owned_signed_or_other_day_receipt_refuses_the_whole_run(data, field):
    _mk(data, PAID, _rec(PAID, **field))
    before = _tree_hash(data)
    assert _apply(data, ids=BAD + [PAID]) == 3
    assert _tree_hash(data) == before


@pytest.mark.parametrize("day", ["2", "2026", "2026-10", "2026-10-3", "2026-10-03T05", " 2026-10-03"])
def test_created_on_must_be_a_whole_date(data, day):
    # Round of the 2026-10-03 analysis: a prefix match let --created-on 2 pass
    # a receipt from any day starting with "2".
    before = _tree_hash(data)
    assert _run(data, "--apply", "--created-on", day) == 2
    assert _tree_hash(data) == before


def test_a_standing_record_id_is_refused(data):
    sys.path.insert(0, str(SERVER))
    import standing_record
    hist = sorted(standing_record.HISTORICAL_RECEIPT_IDS)[0]
    _mk(data, hist, _rec(hist))
    before = _tree_hash(data)
    assert _apply(data, ids=BAD + [hist]) == 3
    assert _tree_hash(data) == before


def test_the_guard_list_failing_to_load_refuses(data, tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "_load_historical", lambda server_dir: (_ for _ in ()).throw(ImportError("x")))
    before = _tree_hash(data)
    assert _apply(data) == 2
    assert _tree_hash(data) == before


def test_a_mistyped_id_stops_the_run_before_anything_moves(data):
    before = _tree_hash(data)
    assert _apply(data, ids=BAD + ["XUmId7he6sN6BWFk"]) == 6
    assert _tree_hash(data) == before


def test_a_directory_without_receipt_json_is_left_alone(data):
    odd = data / "receipts" / "OddOddOddOddOdd1"
    odd.mkdir()
    (odd / "something.ots").write_bytes(b"x")
    before = _tree_hash(data)
    assert _apply(data, ids=BAD + ["OddOddOddOddOdd1"]) == 3
    assert _tree_hash(data) == before


def test_a_receipts_dir_outside_the_data_dir_is_refused(data, tmp_path, monkeypatch):
    monkeypatch.setenv("ORPHO_RECEIPTS_DIR", str(tmp_path / "elsewhere"))
    assert _run(data) == 2


def test_apply_requires_created_on(data):
    assert _run(data, "--apply") == 2


def test_list_free_on_prints_counts_first_and_no_label_or_hash(data, capsys):
    other = "StrangerStranger"
    _mk(data, other, _rec(other, created_at=f"{DAY}T14:00:00+00:00"))
    before = _tree_hash(data)
    assert rr.run(["--data-dir", str(data), "--list-free-on", DAY, "--except", *BAD]) == 0
    out = capsys.readouterr().out
    assert "free receipts created on 2026-10-03 (UTC): 5" in out   # 3 + child + stranger
    assert "not in --except: 2" in out and "hour 14Z: 1" in out
    assert "other sources that day (not removable by this tool): 0" in out
    assert other not in out                                       # ids only on request
    assert rr.run(["--data-dir", str(data), "--list-free-on", DAY, "--except", *BAD, "--show-ids"]) == 0
    out = capsys.readouterr().out
    assert other in out and "throwaway-label" not in out and "ab" * 32 not in out
    assert _tree_hash(data) == before


def test_the_upgrade_worker_never_sees_quarantine(data, monkeypatch):
    def blocked(*a, **k):
        raise AssertionError("network blocked")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    with pytest.raises(AssertionError):                    # control: the guard blocks
        urllib.request.urlopen("https://a.pool.opentimestamps.org")
    monkeypatch.setenv("ORPHO_DATA_DIR", str(data))
    monkeypatch.setenv("ORPHO_UPGRADE_LOG", str(data / "upgrade_log.jsonl"))
    sys.path.insert(0, str(SERVER))
    uw = importlib.import_module("upgrade_worker")
    monkeypatch.setattr(uw, "RECEIPTS_DIR", data / "receipts")
    monkeypatch.setattr(uw, "UPGRADE_LOG", data / "upgrade_log.jsonl")
    assert _apply(data) == 0
    out = uw.upgrade_all(min_age_sec=10**9)               # nothing old enough: no calendar call
    assert out["scanned"] == 2                            # control + child only
    assert not any(rid in json.dumps(out["results"]) for rid in BAD)


def test_list_free_on_counts_other_sources_by_prefix_only(data, capsys):
    # Review of PR #285: a same-day accidental anchor through another rail
    # (x402, pack, key) is not free, so the free count alone could not rule
    # out a fourth.
    _mk(data, "X402X402X402X401", _rec("X402X402X402X401", source="x402:0xabc", client_label="secret-label"))
    assert rr.run(["--data-dir", str(data), "--list-free-on", DAY, "--except", *BAD]) == 0
    out = capsys.readouterr().out
    assert "other sources that day (not removable by this tool): 1 {'x402': 1}" in out
    assert "0xabc" not in out and "secret-label" not in out and "X402X402X402X401" not in out


def test_one_unopenable_lock_does_not_stop_the_upgrade_pass(data, monkeypatch):
    # Review of PR #285: try_locked opens the lock file before locking it, and
    # a file the worker cannot open (left root-owned) raised out of
    # upgrade_all, so every receipt after it stopped upgrading.
    def blocked(*a, **k):
        raise AssertionError("network blocked")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setenv("ORPHO_DATA_DIR", str(data))
    sys.path.insert(0, str(SERVER))
    uw = importlib.import_module("upgrade_worker")
    monkeypatch.setattr(uw, "RECEIPTS_DIR", data / "receipts")
    monkeypatch.setattr(uw, "UPGRADE_LOG", data / "upgrade_log.jsonl")
    real = uw.try_locked
    first = sorted(p.name for p in (data / "receipts").iterdir())[0]

    def unopenable(path, *a, **k):
        if path.parent.name == first:
            raise PermissionError(13, "Permission denied")
        return real(path, *a, **k)
    monkeypatch.setattr(uw, "try_locked", unopenable)
    out = uw.upgrade_all(min_age_sec=10**9)
    assert out["lock_open_failed"] == 1
    assert out["scanned"] == 5                     # every receipt was reached
