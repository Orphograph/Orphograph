"""A public receipt stops carrying notify_email once its pin notice is settled.

notify_email is the address the Bitcoin-confirmation notice goes to. The
upgrade worker reads it on one pass only, the pass that first sets
btc_pinned_at, and it used to leave the address in receipt.json for good:
production held 23 public receipts that still carried a buyer's address long
after the one notice it was given for had gone out (or had failed, and no
later pass retries it). The founder decision (2026-09-28): public receipts
drop it once the notice is settled; private receipts keep it.

Drives upgrade_worker.upgrade_all on a temp data dir. Calendars are patched at
the worker's own fetch seam; the notice goes through the real mailer with
urlopen patched, so "sent exactly once" counts real send attempts.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from conftest import PINNED_BODY

ROOT = Path(__file__).resolve().parent.parent
EMAIL = "notice-canary@example.test"
CALS = ["https://a.pool.opentimestamps.org"]
RESEND_URL = "https://api.resend.com/emails"
# auth stays: conftest has already pointed its secret at a temp file.
_MODULES = ("upgrade_worker", "mailer", "webhooks")


def _receipt(receipts: Path, rid: str, *, private: bool = False, notify: bool = True,
             **fields) -> Path:
    """A receipt laid out the way the worker reads it: receipt.json plus one
    .ots per calendar. `fields` override the pending defaults."""
    d = receipts / rid
    d.mkdir(parents=True)
    successes = []
    for cal in CALS:
        short = cal.split("//", 1)[1].split(".", 1)[0]
        (d / f"{short}.ots").write_bytes(b"OTS_BLOB_PLACEHOLDER")
        successes.append({"calendar": cal, "ots_path": f"receipts/{rid}/{short}.ots"})
    rec = {
        "receipt_id": rid, "created_at": "2026-09-01T00:00:00+00:00",
        "hash_hex": "a" * 64, "sha512_hex": None, "client_label": None,
        "source": "pack:demo", "private": private, "owner_id": "f" * 16 if private else None,
        "attestation": None, "metadata": None,
        "calendars_ok": len(successes), "calendars_total": len(CALS),
        "successes": successes, "failures": [], "status": "pending",
    }
    if notify:
        rec["notify_email"] = EMAIL
    rec.update(fields)
    (d / "receipt.json").write_text(json.dumps(rec, indent=2))
    return d


def _backlog(receipts: Path, rid: str, *, notified: bool = True, **kw) -> Path:
    """A receipt the worker finished on an earlier pass: pinned, stamped with
    the current schema, and so skipped by every later pass."""
    fields = {"status": "pinned", "btc_pinned_at": "2026-09-01T02:00:00+00:00",
              "pinned_count": 1, "pinned_total": 1, "upgrade_attempts": 1,
              "upgrade_schema": 2, "upgrade_stalls": 0}
    if notified:
        fields["pin_email_sent_at"] = "2026-09-01T02:00:01+00:00"
    fields.update(kw.pop("fields", {}))
    return _receipt(receipts, rid, **kw, **fields)


def _disk(d: Path) -> dict:
    return json.loads((d / "receipt.json").read_text())


def _stamp(d: Path) -> tuple[int, int, bytes]:
    st = (d / "receipt.json").stat()
    return st.st_mtime_ns, st.st_ino, (d / "receipt.json").read_bytes()


class _Resend:
    """Counts the notices the real mailer tries to send."""

    def __init__(self) -> None:
        self.to: list[str] = []

    def __call__(self, req, *a, **kw):
        assert req.full_url == RESEND_URL, req.full_url
        self.to.append(json.loads(req.data.decode())["to"][0])
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b'{"id":"em_test"}'


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fresh worker bound to a temp data dir. The server modules it loads
    are put back as they were afterwards, so later tests see their own."""
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    for k, v in {"RESEND_API_KEY": "test_key_not_real", "ORPHO_DATA_DIR": str(tmp_path),
                 "ORPHO_RECEIPTS_DIR": str(receipts),
                 "ORPHO_UPGRADE_LOG": str(tmp_path / "upgrade_log.jsonl")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ORPHO_INTEGRATION_EMAIL", raising=False)
    saved = {m: sys.modules.pop(m, None) for m in _MODULES}

    def load(**extra_env):
        for k, v in extra_env.items():
            monkeypatch.setenv(k, v)
        sys.modules.pop("upgrade_worker", None)
        import upgrade_worker
        monkeypatch.setattr(upgrade_worker, "_commitment_for_pending",
                            lambda blob: ("c" * 64, len(blob)))
        return upgrade_worker

    resend = _Resend()
    monkeypatch.setattr(urllib.request, "urlopen", resend)

    class Env:
        pass
    e = Env()
    e.tmp, e.receipts, e.load, e.resend = tmp_path, receipts, load, resend
    e.uw = load()
    yield e
    for m, mod in saved.items():
        if mod is None:
            sys.modules.pop(m, None)
        else:
            sys.modules[m] = mod


def _calendars(uw, monkeypatch, pinned: bool) -> None:
    answer = (True, PINNED_BODY) if pinned else (False, "HTTP 404")
    monkeypatch.setattr(uw, "_fetch_upgrade", lambda url, h: answer)


@pytest.mark.parametrize("notified", [True, False], ids=["notice-sent", "notice-failed"])
def test_a_finished_public_receipt_loses_the_address(env, monkeypatch, notified):
    """btc_pinned_at says the one notice attempt has happened: either
    pin_email_sent_at is beside it (sent) or it is not (the send failed and
    no later pass retries). Either way the address has done its job."""
    d = _backlog(env.receipts, "PubDone000000001", notified=notified)
    before = _disk(d)
    _calendars(env.uw, monkeypatch, pinned=True)
    summary = env.uw.upgrade_all(min_age_sec=0)
    after = _disk(d)
    assert "notify_email" not in after, "a finished public receipt still holds the buyer's address"
    # Only the address goes: the proof, the status and the notice stamp stay.
    assert after == {k: v for k, v in before.items() if k != "notify_email"}
    assert env.resend.to == [], "cleaning up must never send a notice"
    assert summary["notify_email_removed"] == 1
    log = (env.tmp / "upgrade_log.jsonl").read_text()
    assert EMAIL not in log, "the upgrade log carries an address"


def test_a_partial_public_receipt_loses_the_address_on_its_next_pass(env, monkeypatch):
    """A partial receipt is still polled every pass (through _upgrade_one);
    its notice went out on the first pin, so the address goes on the write
    that finishes it, and no second notice is sent."""
    d = _backlog(env.receipts, "PubPartial000001", fields={"status": "partial"})
    _calendars(env.uw, monkeypatch, pinned=True)
    env.uw.upgrade_all(min_age_sec=0)
    rec = _disk(d)
    assert rec["status"] == "pinned", "control: the pass really went through _upgrade_one"
    assert "notify_email" not in rec
    assert rec["pin_email_sent_at"] == "2026-09-01T02:00:01+00:00"
    assert env.resend.to == []


def test_b_a_waiting_public_receipt_keeps_the_address_until_its_notice(env, monkeypatch):
    d = _receipt(env.receipts, "PubWaiting000001")
    # Control: still pending, so the notice has not gone out; the address stays.
    _calendars(env.uw, monkeypatch, pinned=False)
    env.uw.upgrade_all(min_age_sec=0)
    assert _disk(d)["notify_email"] == EMAIL, "the address went before its notice did"
    assert env.resend.to == []
    # It pins: the notice goes out once, to that address, and the address goes.
    _calendars(env.uw, monkeypatch, pinned=True)
    env.uw.upgrade_all(min_age_sec=0)
    assert env.resend.to == [EMAIL], "the pin notice must be sent exactly once"
    rec = _disk(d)
    assert rec["status"] == "pinned" and rec.get("pin_email_sent_at")
    assert "notify_email" not in rec, "the address outlived its notice"
    # Later passes send nothing more.
    env.uw.upgrade_all(min_age_sec=0)
    assert env.resend.to == [EMAIL]


def test_c_a_private_receipt_keeps_the_address(env, monkeypatch):
    done = _backlog(env.receipts, "PrivDone00000001", private=True)
    waiting = _receipt(env.receipts, "PrivWaiting00001", private=True)
    done_stamp = _stamp(done)
    _calendars(env.uw, monkeypatch, pinned=True)
    env.uw.upgrade_all(min_age_sec=0)
    env.uw.upgrade_all(min_age_sec=0)
    assert _stamp(done) == done_stamp, "a finished private receipt was rewritten"
    assert _disk(waiting)["notify_email"] == EMAIL
    assert env.resend.to == [EMAIL], "control: the private receipt still got its notice"


def test_d_a_second_pass_rewrites_nothing(env, monkeypatch):
    dirty = _backlog(env.receipts, "PubDirty00000001")
    clean = _backlog(env.receipts, "PubClean00000001", notify=False)
    private = _backlog(env.receipts, "PrivDone00000002", private=True)
    clean_stamp = _stamp(clean)
    _calendars(env.uw, monkeypatch, pinned=True)
    env.uw.upgrade_all(min_age_sec=0)
    assert "notify_email" not in _disk(dirty)
    assert _stamp(clean) == clean_stamp, "a receipt without the address was rewritten"
    stamps = {d.name: _stamp(d) for d in (dirty, clean, private)}
    summary = env.uw.upgrade_all(min_age_sec=0)
    assert {d.name: _stamp(d) for d in (dirty, clean, private)} == stamps, \
        "a second pass rewrote a receipt"
    assert summary["notify_email_removed"] == 0
    leftovers = [p.name for d in env.receipts.iterdir() for p in d.iterdir()
                 if p.name.endswith(".tmp")]
    assert leftovers == [], leftovers


def test_one_failed_write_does_not_stop_the_pass(env, monkeypatch):
    stuck = _backlog(env.receipts, "A_Stuck000000001")
    later = _backlog(env.receipts, "B_Later000000001")
    stuck_bytes = (stuck / "receipt.json").read_bytes()
    # The lock file must exist first, or the worker's lock (not this rule)
    # is what fails in a read-only directory.
    (stuck / ".upgrade.lock").touch()
    os.chmod(stuck, 0o555)
    try:
        _calendars(env.uw, monkeypatch, pinned=True)
        summary = env.uw.upgrade_all(min_age_sec=0)
    finally:
        os.chmod(stuck, 0o755)
    assert "notify_email" not in _disk(later), "one unwritable receipt stopped the pass"
    assert (stuck / "receipt.json").read_bytes() == stuck_bytes, "a failed write damaged the receipt"
    assert summary["notify_email_removed"] == 1
    assert summary["notify_email_remove_failed"] == 1
    env.uw.upgrade_all(min_age_sec=0)
    assert "notify_email" not in _disk(stuck), "the next pass did not retry"


def test_the_cleanup_is_bounded_per_pass(env, monkeypatch):
    uw = env.load(ORPHO_NOTIFY_CLEANUP_MAX_PER_PASS="1")
    first = _backlog(env.receipts, "PubBound00000001")
    second = _backlog(env.receipts, "PubBound00000002")
    _calendars(uw, monkeypatch, pinned=True)
    uw.upgrade_all(min_age_sec=0)
    left = ["notify_email" in _disk(d) for d in (first, second)]
    assert sorted(left) == [False, True], "the cap did not bound the pass"
    uw.upgrade_all(min_age_sec=0)
    assert not any("notify_email" in _disk(d) for d in (first, second))


def test_a_skipped_receipt_is_cleaned_from_what_is_on_disk(env, monkeypatch):
    """upgrade_all thaws an old-schema frozen record in memory before its age
    check. If that record is then skipped for age, the cleanup must write
    what is on disk minus the address, not the half-thawed copy."""
    d = _backlog(env.receipts, "PubFrozen0000001",
                 fields={"status": "partial", "upgrade_schema": 1, "upgrade_frozen": True,
                         "upgrade_frozen_at": "2026-09-02T00:00:00+00:00",
                         "upgrade_frozen_reason": "no progress"})
    before = _disk(d)
    _calendars(env.uw, monkeypatch, pinned=False)
    env.uw.upgrade_all(min_age_sec=3600)
    assert _disk(d) == {k: v for k, v in before.items() if k != "notify_email"}


def test_a_privacy_toggle_during_the_cleanup_is_not_undone(env, monkeypatch):
    """The privacy toggle (app.py) writes receipt.json without the worker's
    lock. If it lands between the cleanup's read and its rename, writing the
    stale public copy back would make the receipt public again. The cleanup
    must leave the toggled file alone and try again next pass."""
    d = _backlog(env.receipts, "PubToggled000001")
    toggled = dict(_disk(d), private=True, owner_id="e" * 16, account_id="e" * 16)
    real_fsync = os.fsync
    fired = []

    def toggle_then_fsync(fd):
        # Same write as the toggle: a sibling tmp file, then one rename.
        if not fired:
            fired.append(True)
            tmp = d / "receipt.json.tmp"
            tmp.write_text(json.dumps(toggled, indent=2))
            os.replace(tmp, d / "receipt.json")
        return real_fsync(fd)
    monkeypatch.setattr(os, "fsync", toggle_then_fsync)
    _calendars(env.uw, monkeypatch, pinned=True)
    summary = env.uw.upgrade_all(min_age_sec=0)
    assert fired, "control: the toggle really landed inside the cleanup's write"
    assert _disk(d) == toggled, "the cleanup wrote a stale public copy over a privacy toggle"
    assert summary["notify_email_remove_failed"] == 1
    # Now private: later passes keep the address.
    env.uw.upgrade_all(min_age_sec=0)
    assert _disk(d) == toggled


def test_the_count_script_prints_four_counts_and_nothing_else(tmp_path):
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    _receipt(receipts, "PubWith000000001")
    _receipt(receipts, "PubWith000000002")
    _receipt(receipts, "PubWithout000001", notify=False)
    _receipt(receipts, "PrivWith00000001", private=True)
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "count_notify_email.py"),
                           str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "public_with_notify_email 2",
        "public_without_notify_email 1",
        "private_with_notify_email 1",
        "private_without_notify_email 0",
    ]
    assert EMAIL not in proc.stdout + proc.stderr
