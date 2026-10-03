"""Both folder watchers retry a file no calendar accepted, instead of filing it as done.

A 200 with calendars_ok 0 is a receipt with no Bitcoin commitment, and it never
gets one. Both watchers treated it as anchored: scripts/folder_watch.py wrote
the receipt sidecar (which marks the file done forever), and
integrations/watch-folder/orphograph_watch.py recorded the file's state, so
neither would ever anchor that file again. The server is stubbed; nothing
leaves the machine.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import folder_watch as fw  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "orphograph_watch", ROOT / "integrations" / "watch-folder" / "orphograph_watch.py")
ow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ow)


def _receipt(calendars_ok):
    return {"receipt_id": "RZEROCAL02", "hash_hex": "0" * 64, "sha512_hex": "0" * 128,
            "calendars_ok": calendars_ok, "calendars_total": 5}


# ── scripts/folder_watch.py ────────────────────────────────────────────────

@pytest.mark.parametrize("calendars_ok,expect_ok", [(0, False), (1, True)], ids=["no-calendar", "one-calendar"])
def test_folder_watch_files_only_a_committed_receipt(tmp_path, monkeypatch, calendars_ok, expect_ok):
    photo = tmp_path / "shot.jpg"
    photo.write_bytes(b"shot-content")
    state = tmp_path / "state.jsonl"
    monkeypatch.setattr(fw, "_anchor", lambda base, key, path: _receipt(calendars_ok))

    ok, msg = fw._process("https://test.invalid", "orpho_x", photo, state, verbose=False)

    sidecar = photo.with_suffix(photo.suffix + ".orpho.json")
    assert ok is expect_ok, msg
    assert sidecar.exists() is expect_ok, "a sidecar marks the file done forever"
    assert state.exists() is expect_ok
    if not expect_ok:
        assert "no calendar accepted" in msg


# ── integrations/watch-folder/orphograph_watch.py ──────────────────────────

def _args():
    return types.SimpleNamespace(interval=0, dry_run=False, base="https://test.invalid",
                                 api_key=None, pack_token=None)


@pytest.mark.parametrize("calendars_ok,expect_filed", [(0, False), (1, True)], ids=["no-calendar", "one-calendar"])
def test_watch_folder_files_only_a_committed_receipt(tmp_path, monkeypatch, calendars_ok, expect_filed):
    root = tmp_path / "Deliveries"
    root.mkdir()
    f = root / "cut.mov"
    f.write_bytes(b"final cut")
    old = time.time() - 3600
    os.utime(f, (old, old))
    monkeypatch.setattr(ow, "post_anchor", lambda *a, **k: _receipt(calendars_ok))
    monkeypatch.setattr(ow, "log", lambda msg: None)

    state, pause = {}, {"until": 0, "backoff": 0}
    anchored = ow.run_scan(str(root), _args(), state, pause)

    receipts = root / ow.STATE_DIR_NAME / ow.RECEIPTS_FILE
    assert ("cut.mov" in state.get("files", {})) is expect_filed
    assert receipts.exists() is expect_filed
    assert anchored == (1 if expect_filed else 0)
    if not expect_filed:
        assert pause["until"] > time.time(), "a refused anchor backs off before retrying"
