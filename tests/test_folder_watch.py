"""test_folder_watch.py — exercise the scan + process logic without
making real network calls. We monkey-patch _anchor to fake the
Orphograph API response, so the test verifies the local-state
behavior (idempotency, sidecar writing, extension filtering)
without hitting the wire.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make sure scripts/folder_watch.py is importable.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import folder_watch as fw


@pytest.fixture
def tmp_folder(tmp_path, monkeypatch):
    folder = tmp_path / "photos"
    folder.mkdir()
    state = tmp_path / "state.jsonl"
    monkeypatch.setattr(fw, "STATE_PATH", state)
    yield folder, state


def _fake_receipt(receipt_id="abc123"):
    return {
        "receipt_id": receipt_id,
        "hash_hex": "0" * 64,
        "sha512_hex": "0" * 128,
        "calendars_ok": 5,
        "calendars_total": 5,
    }


def test_scan_finds_new_image_files(tmp_folder):
    folder, state = tmp_folder
    (folder / "a.jpg").write_bytes(b"jpg-data")
    (folder / "b.png").write_bytes(b"png-data")
    (folder / "ignore.txt").write_bytes(b"text")
    (folder / ".hidden.jpg").write_bytes(b"hidden")
    cands = fw._scan(folder, fw.ALLOWED_EXT, set())
    names = {c.name for c in cands}
    assert names == {"a.jpg", "b.png"}


def test_scan_skips_files_with_existing_sidecar(tmp_folder):
    folder, state = tmp_folder
    (folder / "a.jpg").write_bytes(b"jpg-data")
    (folder / "a.jpg.orpho.json").write_text("{}")
    cands = fw._scan(folder, fw.ALLOWED_EXT, set())
    assert cands == []


def test_scan_skips_files_already_in_state(tmp_folder):
    folder, state = tmp_folder
    p = folder / "a.jpg"
    p.write_bytes(b"data")
    anchored = {str(p.resolve())}
    cands = fw._scan(folder, fw.ALLOWED_EXT, anchored)
    assert cands == []


def test_process_writes_sidecar_and_records_state(tmp_folder, monkeypatch):
    folder, state = tmp_folder
    photo = folder / "shot.jpg"
    photo.write_bytes(b"shot-content")

    def fake_anchor(base, key, path):
        return _fake_receipt("zzz9")

    monkeypatch.setattr(fw, "_anchor", fake_anchor)
    ok, rid = fw._process("https://test", "orpho_xxx", photo, state, verbose=False)
    assert ok is True
    assert rid == "zzz9"

    sidecar = photo.with_suffix(photo.suffix + ".orpho.json")
    assert sidecar.exists()
    data = json.loads(sidecar.read_text())
    assert data["receipt_id"] == "zzz9"

    state_rows = [json.loads(l) for l in state.read_text().splitlines() if l.strip()]
    assert len(state_rows) == 1
    assert state_rows[0]["receipt_id"] == "zzz9"


def test_process_is_idempotent_with_existing_sidecar(tmp_folder, monkeypatch):
    folder, state = tmp_folder
    photo = folder / "shot.jpg"
    photo.write_bytes(b"shot")
    sidecar = photo.with_suffix(photo.suffix + ".orpho.json")
    sidecar.write_text('{"receipt_id":"old"}')

    called = {"n": 0}
    def fake_anchor(base, key, path):
        called["n"] += 1
        return _fake_receipt("new")
    monkeypatch.setattr(fw, "_anchor", fake_anchor)

    ok, msg = fw._process("https://test", "orpho_xxx", photo, state, verbose=False)
    assert ok is True
    assert "already" in msg
    assert called["n"] == 0  # never called the network
    # Sidecar still has the old receipt id.
    assert json.loads(sidecar.read_text())["receipt_id"] == "old"


def test_process_skips_empty_files(tmp_folder, monkeypatch):
    folder, state = tmp_folder
    photo = folder / "empty.jpg"
    photo.write_bytes(b"")
    monkeypatch.setattr(fw, "_anchor", lambda *a, **kw: _fake_receipt())
    ok, msg = fw._process("https://test", "key", photo, state, verbose=False)
    assert ok is False
    assert "empty" in msg


def test_anchor_handles_server_400(tmp_folder, monkeypatch):
    """Server returning 400 with an error message should propagate cleanly."""
    folder, state = tmp_folder
    photo = folder / "shot.jpg"
    photo.write_bytes(b"shot")

    import urllib.error
    import io
    def fake_anchor(base, key, path):
        err_body = json.dumps({"error": "invalid hash"}).encode()
        raise urllib.error.HTTPError(
            url="https://test/api/anchor",
            code=400, msg="Bad Request",
            hdrs={}, fp=io.BytesIO(err_body),
        )
    monkeypatch.setattr(fw, "_anchor", fake_anchor)
    ok, msg = fw._process("https://test", "key", photo, state, verbose=False)
    assert ok is False
    assert "invalid hash" in msg


def test_hashes_produce_correct_lengths(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello orphograph")
    s256, s512, size = fw._hashes(p)
    assert len(s256) == 64
    assert len(s512) == 128
    assert size == len(b"hello orphograph")
