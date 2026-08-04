"""capture/test_orphograph_usb.py — unit/integration tests for the USB sidecar recorder.

Additive-only test suite for orphograph_usb.py. All anchor/proof HTTP calls are
mocked — the suite passes fully offline. A tmp_path directory stands in for the
mounted USB drive; several tests assert behavior that matters on the real
FAT32 target (case-insensitive names, renames/moves, Windows/macOS junk dirs,
no reliance on symlinks or xattrs).

Run:  python3 -m pytest capture/test_orphograph_usb.py -q
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import zipfile
from pathlib import Path

import pytest

CAPTURE_DIR = Path(__file__).resolve().parent
if str(CAPTURE_DIR) not in sys.path:
    sys.path.insert(0, str(CAPTURE_DIR))

import orphograph_usb as usb  # noqa: E402


# --------------------------------------------------------------------------- #
# Offline guard: no test may touch the network. Tests that exercise the HTTP
# wrappers override urlopen themselves with a local fake.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover — tripping it is the failure
        raise AssertionError("test attempted a real network call via urlopen")

    monkeypatch.setattr(usb.urllib.request, "urlopen", _boom)
    yield


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
ENDPOINT = "https://example.invalid"


class FakeAnchor:
    """Stands in for anchor_hash. Records every call; scripted responses."""

    def __init__(self, responses=None):
        self.calls: list[dict] = []
        self._responses = list(responses) if responses else None

    def __call__(self, endpoint, sha256, sha512, label, api_key):
        self.calls.append({"endpoint": endpoint, "sha256": sha256,
                           "sha512": sha512, "label": label, "api_key": api_key})
        if self._responses:
            return self._responses.pop(0)
        rid = f"RID{len(self.calls):04d}"
        return True, {"receipt_id": rid, "created_at": "2026-08-02T00:00:00+00:00",
                      "calendars_ok": 5, "calendars_total": 5}


def _write(mount: Path, rel: str, data: bytes, age_sec: int = 3600) -> Path:
    p = mount / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    past = time.time() - age_sec
    os.utime(p, (past, past))
    return p


def _scan(mount: Path, anchor, **kw):
    defaults = dict(endpoint=ENDPOINT, api_key="", include_names=False,
                    extensions=set(), min_age=0, anchor_fn=anchor)
    defaults.update(kw)
    return usb.scan_once(mount, **defaults)


def _index_lines(mount: Path) -> list[dict]:
    index_file = mount / usb.ORPHO_DIR / "index.jsonl"
    if not index_file.exists():
        return []
    return [json.loads(l) for l in index_file.read_text().splitlines() if l.strip()]


class _FakeHTTPResponse(io.BytesIO):
    """Context-manager response for a monkeypatched urlopen."""


# --------------------------------------------------------------------------- #
# 1. Hashing correctness
# --------------------------------------------------------------------------- #
def test_hash_file_matches_hashlib(tmp_path):
    data = b"orphograph usb recorder \x00\xff" * 33
    p = _write(tmp_path, "sample.bin", data)
    sha256, sha512 = usb.hash_file(p)
    assert sha256 == hashlib.sha256(data).hexdigest()
    assert sha512 == hashlib.sha512(data).hexdigest()


def test_hash_file_multi_chunk_stream(tmp_path):
    # Larger than one 4MB read chunk to exercise the streaming loop.
    data = os.urandom(4 * 1024 * 1024 + 4096)
    p = _write(tmp_path, "big.bin", data)
    sha256, sha512 = usb.hash_file(p)
    assert sha256 == hashlib.sha256(data).hexdigest()
    assert sha512 == hashlib.sha512(data).hexdigest()


def test_recorded_sha256_matches_hashlib_end_to_end(tmp_path):
    data = b"receipt-worthy bytes"
    _write(tmp_path, "doc.txt", data)
    anchor = FakeAnchor()
    counts = _scan(tmp_path, anchor)
    assert counts["anchored"] == 1
    assert anchor.calls[0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert anchor.calls[0]["sha512"] == hashlib.sha512(data).hexdigest()
    assert _index_lines(tmp_path)[0]["sha256"] == hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# 2. Sidecar layout on the drive
# --------------------------------------------------------------------------- #
def test_sidecar_layout_created_on_drive(tmp_path):
    _write(tmp_path, "photos/img.jpg", b"jpegish")
    counts = _scan(tmp_path, FakeAnchor())
    assert counts["anchored"] == 1
    base = tmp_path / usb.ORPHO_DIR
    assert base.is_dir()
    assert (base / "index.jsonl").is_file()
    assert (base / "receipts" / "RID0001.json").is_file()
    receipt = json.loads((base / "receipts" / "RID0001.json").read_text())
    assert receipt["receipt_id"] == "RID0001"


def test_index_record_fields(tmp_path):
    _write(tmp_path, "photos/img.jpg", b"jpegish")
    _scan(tmp_path, FakeAnchor())
    rows = _index_lines(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "anchored"
    assert row["relpath"] == str(Path("photos") / "img.jpg")
    assert row["receipt_id"] == "RID0001"
    assert row["receipt_url"] == f"{ENDPOINT}/r/RID0001"
    assert row["calendars_ok"] == 5
    assert row["sha256"] == hashlib.sha256(b"jpegish").hexdigest()


def test_orphograph_dir_is_never_rescanned(tmp_path):
    _write(tmp_path, "a.txt", b"a")
    anchor = FakeAnchor()
    _scan(tmp_path, anchor)
    # Second pass: receipts/*.json inside .orphograph must not be treated as files.
    counts = _scan(tmp_path, anchor)
    assert counts["checked"] == 1  # only a.txt, not the sidecar contents
    assert len(anchor.calls) == 1


def test_write_receipt_without_receipt_id_writes_nothing(tmp_path):
    usb._write_receipt(tmp_path, {"created_at": "now"})
    assert not (tmp_path / usb.ORPHO_DIR / "receipts").exists()


# --------------------------------------------------------------------------- #
# 3. Idempotency
# --------------------------------------------------------------------------- #
def test_rescan_unchanged_drive_adds_no_records(tmp_path):
    _write(tmp_path, "a.txt", b"alpha")
    _write(tmp_path, "b.txt", b"beta")
    anchor = FakeAnchor()
    first = _scan(tmp_path, anchor)
    assert first["anchored"] == 2
    second = _scan(tmp_path, anchor)
    assert second["anchored"] == 0
    assert second["skipped_seen"] == 2
    assert len(anchor.calls) == 2          # no re-anchor
    assert len(_index_lines(tmp_path)) == 2  # no duplicate index lines


def test_duplicate_content_within_one_pass_anchored_once(tmp_path):
    _write(tmp_path, "one.txt", b"same bytes")
    _write(tmp_path, "two.txt", b"same bytes")
    anchor = FakeAnchor()
    counts = _scan(tmp_path, anchor)
    assert counts["anchored"] == 1
    assert counts["skipped_seen"] == 1
    assert len(anchor.calls) == 1


# --------------------------------------------------------------------------- #
# 4. New-file detection
# --------------------------------------------------------------------------- #
def test_new_file_adds_exactly_one_record(tmp_path):
    _write(tmp_path, "a.txt", b"alpha")
    anchor = FakeAnchor()
    _scan(tmp_path, anchor)
    _write(tmp_path, "new/later.txt", b"newcomer")
    counts = _scan(tmp_path, anchor)
    assert counts["anchored"] == 1
    assert counts["skipped_seen"] == 1
    assert len(anchor.calls) == 2
    rows = _index_lines(tmp_path)
    assert len(rows) == 2  # a.txt + later.txt, nothing else
    assert rows[-1]["relpath"] == str(Path("new") / "later.txt")


# --------------------------------------------------------------------------- #
# 5. Error / rate-limit paths
# --------------------------------------------------------------------------- #
def test_anchor_failure_records_failed_and_sidecar_stays_parseable(tmp_path):
    _write(tmp_path, "a.txt", b"alpha")
    anchor = FakeAnchor(responses=[(False, {"error": "HTTP 500: boom"})])
    counts = _scan(tmp_path, anchor)
    assert counts["failed"] == 1
    assert counts["anchored"] == 0
    rows = _index_lines(tmp_path)  # every line valid JSON or this raises
    assert rows[0]["status"] == "failed"
    assert "boom" in rows[0]["reason"]
    assert not (tmp_path / usb.ORPHO_DIR / "receipts").exists()


def test_failed_file_is_retried_next_pass_and_upgrades_to_anchored(tmp_path):
    _write(tmp_path, "a.txt", b"alpha")
    anchor = FakeAnchor(responses=[(False, {"error": "HTTP 500: boom"})])
    _scan(tmp_path, anchor)
    counts = _scan(tmp_path, anchor)  # scripted responses exhausted -> success
    assert counts["anchored"] == 1
    assert len(anchor.calls) == 2
    # load_index is last-write-wins: the sha's effective status is now anchored.
    sha = hashlib.sha256(b"alpha").hexdigest()
    assert usb.load_index(tmp_path)[sha]["status"] == "anchored"
    # The append-only file keeps both rows (failed then anchored).
    assert [r["status"] for r in _index_lines(tmp_path)] == ["failed", "anchored"]


def test_rate_limit_aborts_pass_and_marks_pending(tmp_path):
    for i in range(3):
        _write(tmp_path, f"f{i}.txt", f"payload {i}".encode())
    anchor = FakeAnchor(responses=[(False, {"status_code": 429, "error": "rate limit"})])
    counts = _scan(tmp_path, anchor)
    assert counts["rate_limited"] == 1
    assert len(anchor.calls) == 1  # pass stopped: the other 2 files never attempted
    rows = _index_lines(tmp_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["reason"] == "rate_limited"


def test_pending_file_is_retried_after_rate_limit(tmp_path):
    _write(tmp_path, "a.txt", b"alpha")
    anchor = FakeAnchor(responses=[(False, {"status_code": 429})])
    _scan(tmp_path, anchor)
    counts = _scan(tmp_path, anchor)
    assert counts["anchored"] == 1
    sha = hashlib.sha256(b"alpha").hexdigest()
    assert usb.load_index(tmp_path)[sha]["status"] == "anchored"


def test_is_rate_limited_by_status_code_and_message(tmp_path):
    assert usb._is_rate_limited({"status_code": 429}) is True
    assert usb._is_rate_limited({"error": "Rate limit exceeded"}) is True
    assert usb._is_rate_limited({"status_code": 500, "error": "server"}) is False
    assert usb._is_rate_limited({}) is False


def test_is_rate_limited_no_substring_overmatch():
    # Regression: the fallback used a bare `"rate" in error` substring check,
    # so errors containing "operate"/"separate"/"moderate" were misclassified
    # as rate limits, aborting the whole scan pass instead of recording a
    # plain failure. Only genuine rate-limit phrasing may match.
    assert usb._is_rate_limited({"error": "cannot operate on closed file"}) is False
    assert usb._is_rate_limited({"error": "failed to generate receipt"}) is False
    assert usb._is_rate_limited({"error": "Rate limit exceeded"}) is True
    assert usb._is_rate_limited({"error": "rate-limited, retry later"}) is True
    assert usb._is_rate_limited({"error": "429 Too Many Requests"}) is True


# --------------------------------------------------------------------------- #
# 6. Privacy flags (filenames leaving the machine)
# --------------------------------------------------------------------------- #
def test_default_privacy_label_is_empty(tmp_path):
    _write(tmp_path, "secret/Client Contract.pdf", b"pdfish")
    anchor = FakeAnchor()
    _scan(tmp_path, anchor, include_names=False)
    assert anchor.calls[0]["label"] == ""  # filename never leaves the machine


def test_include_names_sends_relative_path(tmp_path):
    _write(tmp_path, "secret/Client Contract.pdf", b"pdfish")
    anchor = FakeAnchor()
    _scan(tmp_path, anchor, include_names=True)
    assert anchor.calls[0]["label"] == str(Path("secret") / "Client Contract.pdf")


def test_relpath_stays_in_local_index_even_when_private(tmp_path):
    # Privacy flag controls the outbound label only; the on-drive index (the
    # user's own drive) always keeps the relative path for local lookup.
    _write(tmp_path, "secret/name.txt", b"x")
    anchor = FakeAnchor()
    _scan(tmp_path, anchor, include_names=False)
    assert anchor.calls[0]["label"] == ""
    assert _index_lines(tmp_path)[0]["relpath"] == str(Path("secret") / "name.txt")


# --------------------------------------------------------------------------- #
# FAT32-relevant behavior (real target drive: no symlinks/xattrs, case-insensitive)
# --------------------------------------------------------------------------- #
def test_fat32_uppercase_extension_matches_filter(tmp_path):
    # FAT32/Windows drives commonly carry upper-cased names (IMG_0001.JPG).
    _write(tmp_path, "IMG_0001.JPG", b"jpeg bytes")
    _write(tmp_path, "notes.TXT", b"text bytes")
    _write(tmp_path, "skipme.exe", b"binary")
    anchor = FakeAnchor()
    counts = _scan(tmp_path, anchor, extensions={".jpg", ".txt"})
    assert counts["anchored"] == 2
    assert counts["skipped_ext"] == 1


def test_fat32_rename_or_move_does_not_reanchor(tmp_path):
    # Files on a USB get moved/renamed (incl. case-only renames on the
    # case-insensitive FAT32 target). Dedup keys on content, not path.
    p = _write(tmp_path, "Photo.JPG", b"same pixels")
    anchor = FakeAnchor()
    _scan(tmp_path, anchor)
    dest = tmp_path / "sorted" / "photo_renamed.jpg"
    dest.parent.mkdir()
    p.rename(dest)
    counts = _scan(tmp_path, anchor)
    assert counts["anchored"] == 0
    assert counts["skipped_seen"] == 1
    assert len(anchor.calls) == 1
    assert len(_index_lines(tmp_path)) == 1  # index untouched; original relpath kept


def test_fat32_windows_and_macos_junk_never_anchored(tmp_path):
    # FAT32 sticks shuttled between OSes accumulate junk: Windows recycle bin,
    # System Volume Information, Thumbs.db, and AppleDouble ._ files (FAT32 has
    # no xattrs, so macOS drops ._ sidecars next to every file).
    _write(tmp_path, "$RECYCLE.BIN/deleted.txt", b"trash")
    _write(tmp_path, "System Volume Information/IndexerVolumeGuid", b"guid")
    _write(tmp_path, ".Spotlight-V100/store.db", b"spotlight")
    _write(tmp_path, "Thumbs.db", b"thumbs")
    _write(tmp_path, ".DS_Store", b"ds")
    _write(tmp_path, "._photo.jpg", b"appledouble resource fork")
    _write(tmp_path, "photo.jpg", b"the real file")
    anchor = FakeAnchor()
    counts = _scan(tmp_path, anchor)
    assert counts["anchored"] == 1
    assert len(anchor.calls) == 1
    assert anchor.calls[0]["sha256"] == hashlib.sha256(b"the real file").hexdigest()


def test_fat32_names_with_spaces_and_parens_roundtrip(tmp_path):
    name = "Family Photo (1).JPG"
    _write(tmp_path, name, b"pic")
    anchor = FakeAnchor()
    _scan(tmp_path, anchor, include_names=True)
    assert anchor.calls[0]["label"] == name
    assert _index_lines(tmp_path)[0]["relpath"] == name


# --------------------------------------------------------------------------- #
# Scan filters: min_age, dry-run
# --------------------------------------------------------------------------- #
def test_min_age_skips_freshly_written_file(tmp_path):
    _write(tmp_path, "fresh.txt", b"still being written", age_sec=0)
    anchor = FakeAnchor()
    counts = _scan(tmp_path, anchor, min_age=60)
    assert counts["skipped_young"] == 1
    assert counts["anchored"] == 0
    assert anchor.calls == []


def test_dry_run_hashes_but_never_anchors_or_writes(tmp_path):
    _write(tmp_path, "a.txt", b"alpha")
    anchor = FakeAnchor()
    counts = _scan(tmp_path, anchor, dry_run=True)
    assert counts["dry_run"] == 1
    assert counts["anchored"] == 0
    assert anchor.calls == []
    assert not (tmp_path / usb.ORPHO_DIR).exists()  # nothing written to the drive


# --------------------------------------------------------------------------- #
# Index loading robustness + status
# --------------------------------------------------------------------------- #
def test_load_index_last_write_wins_and_ignores_garbage(tmp_path):
    base = tmp_path / usb.ORPHO_DIR
    base.mkdir()
    sha = "ab" * 32
    lines = [
        json.dumps({"sha256": sha, "status": "pending"}),
        "NOT JSON {{{",
        "",
        json.dumps({"no_sha_key": True}),
        json.dumps({"sha256": sha, "status": "anchored", "receipt_id": "R1"}),
    ]
    (base / "index.jsonl").write_text("\n".join(lines) + "\n")
    idx = usb.load_index(tmp_path)
    assert list(idx) == [sha]
    assert idx[sha]["status"] == "anchored"


def test_load_index_missing_file_returns_empty(tmp_path):
    assert usb.load_index(tmp_path) == {}


def test_status_reports_counts_and_mount_state(tmp_path):
    _write(tmp_path, "a.txt", b"alpha")
    _write(tmp_path, "b.txt", b"beta")
    anchor = FakeAnchor(responses=[
        (True, {"receipt_id": "ROK", "created_at": "t", "calendars_ok": 5}),
        (False, {"error": "HTTP 500"}),
    ])
    _scan(tmp_path, anchor)
    st = usb.status(tmp_path)
    assert st["mounted"] is True
    assert st["anchored"] == 1
    assert st["pending_or_failed"] == 1
    assert st["index"].endswith(str(Path(usb.ORPHO_DIR) / "index.jsonl"))
    missing = usb.status(tmp_path / "not-a-mount")
    assert missing["mounted"] is False
    assert missing["anchored"] == 0


# --------------------------------------------------------------------------- #
# Proof-bundle fetch (mocked) + zip-slip guard
# --------------------------------------------------------------------------- #
def test_fetch_proofs_uses_on_drive_receipts_dir(tmp_path):
    _write(tmp_path, "a.txt", b"alpha")
    fetched = []

    def fake_fetch(endpoint, rid, dest_dir, api_key):
        fetched.append((endpoint, rid, dest_dir, api_key))
        return True

    counts = _scan(tmp_path, FakeAnchor(), fetch_proofs=True, fetch_fn=fake_fetch)
    assert counts["proofs_fetched"] == 1
    endpoint, rid, dest_dir, _ = fetched[0]
    assert endpoint == ENDPOINT
    assert rid == "RID0001"
    assert dest_dir == tmp_path / usb.ORPHO_DIR / "receipts"


def test_fetch_proof_failure_is_nonfatal(tmp_path):
    _write(tmp_path, "a.txt", b"alpha")
    counts = _scan(tmp_path, FakeAnchor(), fetch_proofs=True,
                   fetch_fn=lambda *a, **k: False)
    assert counts["anchored"] == 1
    assert counts["proofs_fetched"] == 0
    assert _index_lines(tmp_path)[0]["status"] == "anchored"


def test_fetch_proof_bundle_extracts_zip_and_blocks_zip_slip(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("receipt.json", json.dumps({"receipt_id": "RID9"}))
        z.writestr("alice.ots", "ots-bytes")
        z.writestr("../evil.txt", "escape attempt")
    blob = buf.getvalue()

    def fake_urlopen(req, timeout=None):
        assert "/api/receipt/RID9.zip" in req.full_url
        return _FakeHTTPResponse(blob)

    monkeypatch.setattr(usb.urllib.request, "urlopen", fake_urlopen)
    dest = tmp_path / "receipts"
    dest.mkdir()
    assert usb.fetch_proof_bundle(ENDPOINT, "RID9", dest) is True
    assert (dest / "RID9" / "receipt.json").is_file()
    assert (dest / "RID9" / "alice.ots").is_file()
    # zip-slip member must not escape (nor be extracted at all)
    assert not (dest / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()
    assert not (dest / "RID9" / "evil.txt").exists()


def test_fetch_proof_bundle_bad_zip_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(usb.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeHTTPResponse(b"not a zip"))
    assert usb.fetch_proof_bundle(ENDPOINT, "RIDX", tmp_path) is False


# --------------------------------------------------------------------------- #
# anchor_hash HTTP wrapper (urlopen mocked — offline)
# --------------------------------------------------------------------------- #
def test_anchor_hash_success_posts_expected_body(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["body"] = json.loads(req.data.decode())
        seen["api_key"] = req.headers.get("X-orpho-api-key")
        return _FakeHTTPResponse(json.dumps({"receipt_id": "R1"}).encode())

    monkeypatch.setattr(usb.urllib.request, "urlopen", fake_urlopen)
    ok, resp = usb.anchor_hash(ENDPOINT + "/", "aa" * 32, "bb" * 64, "lbl", "KEY123")
    assert ok is True
    assert resp == {"receipt_id": "R1"}
    assert seen["url"] == ENDPOINT + "/api/anchor"  # trailing slash stripped
    assert seen["method"] == "POST"
    assert seen["body"] == {"hash_hex": "aa" * 32, "sha512_hex": "bb" * 64,
                            "client_label": "lbl"}
    assert seen["api_key"] == "KEY123"


def test_anchor_hash_http_error_carries_status_code(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {},
                                     io.BytesIO(b'{"error":"rate limit"}'))

    monkeypatch.setattr(usb.urllib.request, "urlopen", fake_urlopen)
    ok, resp = usb.anchor_hash(ENDPOINT, "aa" * 32, "bb" * 64, "", "")
    assert ok is False
    assert resp["status_code"] == 429
    assert usb._is_rate_limited(resp) is True


def test_anchor_hash_network_error_returns_error_dict(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(usb.urllib.request, "urlopen", fake_urlopen)
    ok, resp = usb.anchor_hash(ENDPOINT, "aa" * 32, "bb" * 64, "", "")
    assert ok is False
    assert "URLError" in resp["error"]
    assert usb._is_rate_limited(resp) is False
