"""Dropping notify_email from a finished public receipt changes nothing a
caller can see: not a stranger's view of the receipt, not its owner's list.

The address was never part of a public answer (receipt_export keeps it out of
every export), and nothing decides ownership from it. So the bytes a stranger
downloads and the rows the owner's account lists must be the same before and
after the upgrade worker's pass removes it. Drives a real server; the worker
runs in-process between the two reads, on a receipt it finished on an earlier
pass, so the pass needs no calendar and sends no mail.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import pytest

import _srv
from conftest import write_fixture_receipt

OWNER = "owner-notice-canary@example.test"
SECRET = "notify-strip-test-hmac-secret"
ACCOUNT = hmac.new(SECRET.encode(), OWNER.encode(), hashlib.sha256).hexdigest()[:16]
SESSION = "notify-strip-owner-session"
OWNER_HEADERS = {"Cookie": "orpho_sid=" + SESSION}
RID = "PubNotified00001"
# What a stranger reads. The .zip is compared member by member: its member
# timestamps are the time it was built.
PUBLIC_PATHS = [f"/r/{RID}", f"/api/receipt/{RID}", f"/api/verify/{RID}",
                f"/api/receipt/{RID}/summary", f"/api/verify/{RID}/summary"]
ZIP_PATHS = [f"/api/receipt/{RID}.zip", f"/api/verify/{RID}.zip"]
OWNER_PATHS = ["/api/me/anchors", "/api/me/anchors.csv", "/api/me"]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("notify_strip_http")
    d = write_fixture_receipt(data_dir / "receipts", RID)
    rec = json.loads((d / "receipt.json").read_text())
    rec.update({
        "source": "sub:" + ACCOUNT, "account_id": ACCOUNT, "notify_email": OWNER,
        "status": "pinned", "btc_pinned_at": "2026-01-02T05:00:00+00:00",
        "pin_email_sent_at": "2026-01-02T05:00:01+00:00",
        "pinned_count": rec["calendars_ok"], "pinned_total": rec["calendars_ok"],
        "upgrade_attempts": 1, "upgrade_schema": 2, "upgrade_stalls": 0,
    })
    (d / "receipt.json").write_text(json.dumps(rec, indent=2))
    (data_dir / "auth_sessions.jsonl").write_text(json.dumps({
        "event": "created", "session_hash": hashlib.sha256(SESSION.encode()).hexdigest(),
        "email": OWNER, "expires_unix": time.time() + 3600,
    }) + "\n")
    for base in _srv.server_processes(data_dir, stub_calendars=True, ORPHO_HMAC_SECRET=SECRET):
        yield base, data_dir


def _snapshot(base: str) -> dict:
    out = {}
    for path in PUBLIC_PATHS:
        status, body, _h = _srv.request(base, path, timeout=15)
        assert status == 200, (path, status, body[:200])
        out[path] = body
    for path in ZIP_PATHS:
        status, body, _h = _srv.request(base, path, timeout=15)
        assert status == 200, (path, status, body[:200])
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            out[path] = {n: z.read(n) for n in z.namelist()}
    for path in OWNER_PATHS:
        status, body, _h = _srv.request(base, path, headers=OWNER_HEADERS, timeout=15)
        assert status == 200, (path, status, body[:200])
        out[path] = body
    return out


def _run_worker(data_dir: Path, monkeypatch) -> dict:
    receipts = data_dir / "receipts"
    monkeypatch.setenv("ORPHO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ORPHO_RECEIPTS_DIR", str(receipts))
    monkeypatch.setenv("ORPHO_UPGRADE_LOG", str(data_dir / "upgrade_log.jsonl"))
    saved = sys.modules.pop("upgrade_worker", None)
    try:
        import upgrade_worker

        # The server seeds a sample receipt that is still partial, so the pass
        # polls it. Answer "still pending" so nothing leaves this machine.
        monkeypatch.setattr(upgrade_worker, "_fetch_upgrade",
                            lambda url, h: (False, "HTTP 404"))
        return upgrade_worker.upgrade_all(min_age_sec=0)
    finally:
        sys.modules.pop("upgrade_worker", None)
        if saved is not None:
            sys.modules["upgrade_worker"] = saved


def test_e_f_what_callers_receive_is_unchanged_when_the_address_goes(server, monkeypatch):
    base, data_dir = server
    rfile = data_dir / "receipts" / RID / "receipt.json"
    before = _snapshot(base)
    # Control: the owner's list really holds this receipt, and nothing public
    # carried the address even before the pass.
    assert RID.encode() in before["/api/me/anchors"]
    assert json.loads(before["/api/me"])["anchor_count"] == 1
    assert OWNER.encode() not in b"".join(
        b if isinstance(b, bytes) else b"".join(b.values())
        for p, b in before.items() if p not in OWNER_PATHS)
    summary = _run_worker(data_dir, monkeypatch)
    on_disk = json.loads(rfile.read_text())
    # Without this the comparison below proves nothing.
    assert "notify_email" not in on_disk, "the worker did not remove the address"
    assert summary["notify_email_removed"] == 1
    after = _snapshot(base)
    changed = [path for path in before if after[path] != before[path]]
    assert changed == [], f"what callers receive changed when the address went: {changed}"
    assert on_disk["account_id"] == ACCOUNT, "removing the address changed the owner"
