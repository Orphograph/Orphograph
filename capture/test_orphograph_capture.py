"""capture/test_orphograph_capture.py — offline test suite for the capture daemon.

Covers the real risk surface of orphograph_capture.py:
  1. new-file detection → exactly one anchor call with the correct SHA-256
  2. burst of 10+ simultaneous saves → no crash / miss / duplicate
  3. PRIVACY GUARD — outbound payload is hashes-only by default; the filename
     rides in `client_label` only when include_filename is opted in
  4. partial-write / settle logic (min_age mtime debounce)
  5. restart idempotency — dedup state lives in seen.jsonl on disk; a fresh
     scan pass (== fresh process) never re-anchors recorded paths

All network is stubbed. An autouse guard makes any accidental real
urllib.request.urlopen call fail loudly.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

CAPTURE_DIR = Path(__file__).resolve().parent
if str(CAPTURE_DIR) not in sys.path:
    sys.path.insert(0, str(CAPTURE_DIR))

import orphograph_capture as oc  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

FAKE_RECEIPT = {
    "receipt_id": "RCPT_TEST_0001",
    "hash_hex": "",       # filled per-call by the fake
    "sha512_hex": "",
    "created_at": "2026-08-02T00:00:00+00:00",
    "calendars_ok": 5,
    "calendars_total": 5,
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _age_file(p: Path, seconds: int = 3600) -> None:
    """Push a file's mtime into the past so the min_age debounce passes."""
    past = time.time() - seconds
    os.utime(p, (past, past))


def _write_capture_file(folder: Path, name: str, data: bytes, aged: bool = True) -> Path:
    p = folder / name
    p.write_bytes(data)
    if aged:
        _age_file(p)
    return p


class _AnchorRecorder:
    """Stand-in for oc.anchor_hash that records every outbound call."""

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[dict] = []

    def __call__(self, endpoint: str, hash_hex: str, sha512_hex: str,
                 label: str, api_key: str):
        self.calls.append({
            "endpoint": endpoint,
            "hash_hex": hash_hex,
            "sha512_hex": sha512_hex,
            "label": label,
            "api_key": api_key,
        })
        if not self.ok:
            return False, {"error": "simulated outage"}
        receipt = dict(FAKE_RECEIPT)
        receipt["receipt_id"] = f"RCPT_{len(self.calls):04d}"
        receipt["hash_hex"] = hash_hex
        receipt["sha512_hex"] = sha512_hex
        return True, receipt


def _scan(watch: Path, *, include_filename: bool = False,
          extensions: set[str] | None = None, min_age: int = 2) -> dict:
    exts = oc.DEFAULT_EXTENSIONS if extensions is None else extensions
    return oc.scan_once([watch], exts, include_filename,
                        "https://example.invalid", "sk_test", min_age)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Any un-stubbed urlopen is a test failure — the suite must run offline."""
    def _blocked(*a, **k):  # pragma: no cover — only fires on a bug
        raise AssertionError("test attempted a real network call via urlopen")
    monkeypatch.setattr(oc.urllib.request, "urlopen", _blocked)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Redirect the daemon's on-disk state (seen.jsonl, log) into tmp."""
    state = tmp_path / "state"
    monkeypatch.setattr(oc, "STATE_DIR", state)
    monkeypatch.setattr(oc, "SEEN_DB", state / "seen.jsonl")
    monkeypatch.setattr(oc, "LOG_FILE", state / "capture.log")
    yield state


@pytest.fixture
def watch_dir(tmp_path):
    d = tmp_path / "watched"
    d.mkdir()
    return d


@pytest.fixture
def recorder(monkeypatch):
    rec = _AnchorRecorder()
    monkeypatch.setattr(oc, "anchor_hash", rec)
    return rec


# --------------------------------------------------------------------------- #
# 0. Hash correctness (foundation for everything anchored)
# --------------------------------------------------------------------------- #


def test_hash_file_matches_hashlib(tmp_path):
    data = b"orphograph capture test payload" * 1000
    p = tmp_path / "sample.bin"
    p.write_bytes(data)
    s256, s512 = oc.hash_file(p)
    assert s256 == hashlib.sha256(data).hexdigest()
    assert s512 == hashlib.sha512(data).hexdigest()


# --------------------------------------------------------------------------- #
# 1. Watcher detects a new file → exactly one anchor with correct SHA-256
# --------------------------------------------------------------------------- #


def test_new_file_anchored_once_with_correct_sha256(watch_dir, recorder):
    data = b"\xff\xd8 fake jpeg bytes"
    f = _write_capture_file(watch_dir, "photo.jpg", data)

    counts = _scan(watch_dir)

    assert counts["anchored"] == 1
    assert counts["failed"] == 0
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["hash_hex"] == _sha256(data)
    assert recorder.calls[0]["sha512_hex"] == hashlib.sha512(data).hexdigest()
    # Receipt sidecar written next to the original.
    sidecar = watch_dir / "photo.jpg.orpho.json"
    assert sidecar.is_file()
    payload = json.loads(sidecar.read_text())
    assert payload["sha256"] == _sha256(data)
    assert payload["receipt_id"] == "RCPT_0001"
    assert f.read_bytes() == data  # original untouched


def test_non_capture_extension_skipped(watch_dir, recorder):
    _write_capture_file(watch_dir, "cache.sqlite", b"not capture-worthy")
    counts = _scan(watch_dir)
    assert counts["anchored"] == 0
    assert counts["skipped_ext"] == 1
    assert recorder.calls == []


def test_sidecar_files_never_anchored(watch_dir, recorder):
    # Even with --all-extensions (empty set = all files), .orpho.json is filtered by name.
    _write_capture_file(watch_dir, "photo.jpg.orpho.json", b"{}")
    counts = _scan(watch_dir, extensions=set())
    assert counts["anchored"] == 0
    assert recorder.calls == []


def test_subdirectories_are_not_scanned(watch_dir, recorder):
    # Documented behavior: iterdir() only — the watch is shallow, not recursive.
    sub = watch_dir / "album"
    sub.mkdir()
    _write_capture_file(sub, "nested.jpg", b"nested capture")
    counts = _scan(watch_dir)
    assert counts["anchored"] == 0
    assert recorder.calls == []


# --------------------------------------------------------------------------- #
# 2. Burst of 10+ simultaneous saves — no crash / miss / duplicate
# --------------------------------------------------------------------------- #


def test_burst_of_files_all_anchored_exactly_once(watch_dir, recorder):
    n = 12
    expected = {}
    for i in range(n):
        data = f"burst-frame-{i}".encode() * 50
        _write_capture_file(watch_dir, f"burst_{i:02d}.png", data)
        expected[_sha256(data)] = f"burst_{i:02d}.png"

    counts = _scan(watch_dir)

    # No miss: every file anchored in the single pass.
    assert counts["anchored"] == n
    assert counts["failed"] == 0
    # No duplicate: one call per file, each with a distinct correct hash.
    assert len(recorder.calls) == n
    seen_hashes = [c["hash_hex"] for c in recorder.calls]
    assert len(set(seen_hashes)) == n
    assert set(seen_hashes) == set(expected)
    # Every file got its sidecar.
    assert len(list(watch_dir.glob("*.orpho.json"))) == n

    # Backpressure behavior (documented): processing is synchronous and
    # serial within one scan pass — there is no queue to overflow, so a
    # second pass anchors nothing new.
    counts2 = _scan(watch_dir)
    assert counts2["anchored"] == 0
    assert counts2["skipped_seen"] == n
    assert len(recorder.calls) == n  # still exactly n — no re-anchor


def test_failed_anchor_is_retried_next_pass_not_marked_seen(watch_dir, monkeypatch):
    rec = _AnchorRecorder(ok=False)
    monkeypatch.setattr(oc, "anchor_hash", rec)
    _write_capture_file(watch_dir, "flaky.jpg", b"payload")

    counts = _scan(watch_dir)
    assert counts["failed"] == 1
    assert counts["anchored"] == 0
    assert not oc.SEEN_DB.exists() or "flaky.jpg" not in oc.SEEN_DB.read_text()

    # Endpoint recovers → the same file is picked up again (retry, not loss).
    rec.ok = True
    counts2 = _scan(watch_dir)
    assert counts2["anchored"] == 1
    assert len(rec.calls) == 2


# --------------------------------------------------------------------------- #
# 3. PRIVACY GUARD — hashes-only outbound by default (wire-level assertion)
# --------------------------------------------------------------------------- #


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _capture_wire(monkeypatch) -> list:
    """Patch urlopen with a fake that records the actual Request objects."""
    requests: list = []

    def fake_urlopen(req, timeout=None):
        requests.append(req)
        body = dict(FAKE_RECEIPT)
        sent = json.loads(req.data.decode("utf-8"))
        body["receipt_id"] = "RCPT_WIRE"
        body["hash_hex"] = sent.get("hash_hex", "")
        body["sha512_hex"] = sent.get("sha512_hex", "")
        return _FakeResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(oc.urllib.request, "urlopen", fake_urlopen)
    return requests


def test_privacy_default_payload_is_hashes_only(watch_dir, monkeypatch):
    """Highest-priority guard: with the default flags, the real HTTP body
    must contain the hashes and nothing that identifies the file."""
    requests = _capture_wire(monkeypatch)
    secret_name = "client_contract_ACME.pdf"
    data = b"%PDF-1.7 secret contract"
    _write_capture_file(watch_dir, secret_name, data)

    counts = _scan(watch_dir, include_filename=False)
    assert counts["anchored"] == 1
    assert len(requests) == 1

    req = requests[0]
    sent = json.loads(req.data.decode("utf-8"))
    # Exact outbound schema — nothing beyond hashes + (empty) label.
    assert set(sent.keys()) == {"hash_hex", "sha512_hex", "client_label"}
    assert sent["hash_hex"] == _sha256(data)
    assert sent["client_label"] == ""  # filename NOT sent by default
    # Neither the filename nor any path fragment appears anywhere on the wire.
    raw = req.data.decode("utf-8")
    assert secret_name not in raw
    assert "ACME" not in raw
    assert str(watch_dir) not in raw
    assert req.full_url == "https://example.invalid/api/anchor"


def test_privacy_optin_flag_sends_filename_as_label(watch_dir, monkeypatch):
    requests = _capture_wire(monkeypatch)
    _write_capture_file(watch_dir, "credit.jpg", b"opt-in payload")

    counts = _scan(watch_dir, include_filename=True)
    assert counts["anchored"] == 1
    sent = json.loads(requests[0].data.decode("utf-8"))
    assert sent["client_label"] == "credit.jpg"          # name only...
    assert str(watch_dir) not in sent["client_label"]     # ...never the path


def test_user_agent_is_not_browser_spoofing(watch_dir, monkeypatch):
    requests = _capture_wire(monkeypatch)
    _write_capture_file(watch_dir, "ua.jpg", b"ua check")
    _scan(watch_dir)
    ua = requests[0].get_header("User-agent", "")
    assert ua == oc.USER_AGENT
    assert "Mozilla" not in ua


def test_file_bytes_never_leave_machine(watch_dir, monkeypatch):
    requests = _capture_wire(monkeypatch)
    marker = b"UNIQUE-CONTENT-MARKER-9f3a"
    _write_capture_file(watch_dir, "content.txt", marker * 3)
    _scan(watch_dir)
    assert b"UNIQUE-CONTENT-MARKER-9f3a" not in requests[0].data


# --------------------------------------------------------------------------- #
# 4. Partial-write handling (min_age mtime debounce)
# --------------------------------------------------------------------------- #


def test_file_still_being_written_is_not_hashed(watch_dir, recorder):
    # Fresh mtime (just written / mid-write) → skipped this pass.
    _write_capture_file(watch_dir, "in_flight.mov", b"partial", aged=False)
    counts = _scan(watch_dir, min_age=2)
    assert counts["skipped_young"] == 1
    assert counts["anchored"] == 0
    assert recorder.calls == []


def test_settled_file_hashed_with_final_content_only(watch_dir, recorder):
    # Simulate a slow write: partial content lands, scan runs, write completes,
    # file settles, next scan hashes the FINAL bytes exactly once.
    p = _write_capture_file(watch_dir, "slow.raw", b"first-half", aged=False)
    counts1 = _scan(watch_dir, min_age=2)
    assert counts1["anchored"] == 0 and counts1["skipped_young"] == 1

    final = b"first-half" + b"second-half"
    p.write_bytes(final)          # writer finishes
    _age_file(p)                  # settles past min_age
    counts2 = _scan(watch_dir, min_age=2)
    assert counts2["anchored"] == 1
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["hash_hex"] == _sha256(final)  # never the partial hash


def test_min_age_zero_disables_debounce(watch_dir, recorder):
    _write_capture_file(watch_dir, "instant.jpg", b"go now", aged=False)
    counts = _scan(watch_dir, min_age=0)
    assert counts["anchored"] == 1


# --------------------------------------------------------------------------- #
# 5. Restart / idempotency — dedup state is seen.jsonl on disk
# --------------------------------------------------------------------------- #


def test_restart_over_processed_folder_does_not_reanchor(watch_dir, recorder):
    for i in range(3):
        _write_capture_file(watch_dir, f"day1_{i}.jpg", f"shot {i}".encode())
    counts1 = _scan(watch_dir)
    assert counts1["anchored"] == 3

    # scan_once holds no in-memory dedup state between calls — it re-reads
    # seen.jsonl from disk every pass (oc._load_seen), so a fresh call IS the
    # restart case: same code path a new daemon process takes.
    counts2 = _scan(watch_dir)
    assert counts2["anchored"] == 0
    assert counts2["skipped_seen"] == 3
    assert len(recorder.calls) == 3

    # The state file exists, is append-only JSONL, and keys on absolute path.
    rows = [json.loads(l) for l in oc.SEEN_DB.read_text().splitlines() if l.strip()]
    assert len(rows) == 3
    assert all(row["path"].startswith(str(watch_dir)) for row in rows)
    assert all(len(row["sha256"]) == 64 for row in rows)


def test_new_file_after_restart_is_still_picked_up(watch_dir, recorder):
    _write_capture_file(watch_dir, "old.jpg", b"old")
    _scan(watch_dir)
    _write_capture_file(watch_dir, "new.jpg", b"new")
    counts = _scan(watch_dir)
    assert counts["anchored"] == 1
    assert counts["skipped_seen"] == 1
    assert recorder.calls[-1]["hash_hex"] == _sha256(b"new")


def test_seen_db_corrupt_lines_are_tolerated(watch_dir, recorder):
    _write_capture_file(watch_dir, "keep.jpg", b"keep")
    _scan(watch_dir)
    # Corrupt the state file with garbage + blank lines; dedup must survive.
    with oc.SEEN_DB.open("a") as f:
        f.write("NOT JSON AT ALL\n\n{truncated\n")
    counts = _scan(watch_dir)
    assert counts["skipped_seen"] == 1
    assert counts["anchored"] == 0
    assert len(recorder.calls) == 1


def test_modified_file_at_same_path_is_reanchored(watch_dir, recorder):
    # Regression (was pinned as a documented bug): the seen-tracker keyed on
    # PATH, so new content at an already-anchored path silently never got a
    # proof. Dedup now keys on content — v2 gets its own anchor.
    p = _write_capture_file(watch_dir, "edit.jpg", b"version 1")
    _scan(watch_dir)
    p.write_bytes(b"version 2 -- different content")
    _age_file(p)
    counts = _scan(watch_dir)
    assert counts["anchored"] == 1          # v2 gets its own anchor
    assert len(recorder.calls) == 2
    assert recorder.calls[1]["hash_hex"] == _sha256(b"version 2 -- different content")


# --------------------------------------------------------------------------- #
# Status + missing-dir robustness
# --------------------------------------------------------------------------- #


def test_status_reflects_seen_db(watch_dir, recorder):
    assert oc.status()["total_anchored"] == 0
    _write_capture_file(watch_dir, "a.jpg", b"a")
    _write_capture_file(watch_dir, "b.jpg", b"b")
    _scan(watch_dir)
    st = oc.status()
    assert st["total_anchored"] == 2
    assert st["last_anchor_at"] is not None


def test_nonexistent_watch_dir_is_skipped_without_crash(tmp_path, recorder):
    ghost = tmp_path / "does_not_exist"
    counts = oc.scan_once([ghost], oc.DEFAULT_EXTENSIONS, False,
                          "https://example.invalid", "", 0)
    assert counts["anchored"] == 0
    assert counts["checked"] == 0


# --------------------------------------------------------------------------- #
# Content-keyed dedup — an edited file is a NEW version and gets a new anchor
# --------------------------------------------------------------------------- #


def test_edited_file_is_reanchored_as_new_version(watch_dir, recorder):
    # Regression: seen-tracking used to key on path alone, so a file edited
    # in place was never re-anchored — every version after the first lost
    # its proof. Dedup must key on content.
    f = _write_capture_file(watch_dir, "draft.md", b"version one")
    counts = _scan(watch_dir)
    assert counts["anchored"] == 1

    _write_capture_file(watch_dir, "draft.md", b"version two, edited")
    counts = _scan(watch_dir)
    assert counts["anchored"] == 1, "edited content must get a fresh anchor"
    assert len(recorder.calls) == 2
    assert recorder.calls[0]["hash_hex"] != recorder.calls[1]["hash_hex"]
    assert recorder.calls[1]["hash_hex"] == _sha256(b"version two, edited")

    # Third pass: current version already anchored — nothing new.
    counts = _scan(watch_dir)
    assert counts["anchored"] == 0
    assert counts["skipped_seen"] == 1
    assert len(recorder.calls) == 2


def test_touch_only_mtime_change_is_not_reanchored(watch_dir, recorder):
    f = _write_capture_file(watch_dir, "photo.jpg", b"stable bytes")
    _scan(watch_dir)
    assert len(recorder.calls) == 1

    # Same content, new mtime (e.g. `touch`, backup-restore) — must re-hash
    # once, recognise the content, and NOT anchor again.
    t = time.time() - 60
    os.utime(f, (t, t))
    counts = _scan(watch_dir)
    assert counts["anchored"] == 0
    assert counts["skipped_seen"] == 1
    assert len(recorder.calls) == 1

    # And the refreshed row restores the no-rehash fast path.
    counts = _scan(watch_dir)
    assert counts["skipped_seen"] == 1
    assert len(recorder.calls) == 1
