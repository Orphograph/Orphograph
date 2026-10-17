"""integrations/watch-folder/orphograph_watch.py wrote every receipt link as
https://orphograph.com/r/<id>, whatever --base it anchored against: a run
against any other server logged links to a host that never issued the
receipt. The link now names the issuing server, preferring the server's own
receipt_url (@action-receipt-url, swept to this sibling integration)."""
from __future__ import annotations

import importlib.util
import json
import os
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "orphograph_watch_link", ROOT / "integrations" / "watch-folder" / "orphograph_watch.py")
ow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ow)

BASE = "https://anchors.internal.test"


def _scan(tmp_path, monkeypatch, answer):
    root = tmp_path / "Deliveries"
    root.mkdir()
    f = root / "cut.mov"
    f.write_bytes(b"final cut")
    old = time.time() - 3600
    os.utime(f, (old, old))
    monkeypatch.setattr(ow, "post_anchor", lambda *a, **k: answer)
    monkeypatch.setattr(ow, "log", lambda msg: None)
    args = types.SimpleNamespace(interval=0, dry_run=False, base=BASE,
                                 api_key=None, pack_token=None)
    assert ow.run_scan(str(root), args, {}, {"until": 0, "backoff": 0}) == 1
    lines = (root / ow.STATE_DIR_NAME / ow.RECEIPTS_FILE).read_text().splitlines()
    return json.loads(lines[-1])["receipt_url"]


def _answer(**extra):
    return {"receipt_id": "AbC_12-x", "calendars_ok": 2, "calendars_total": 5, **extra}


def test_without_a_served_link_the_link_names_the_base(tmp_path, monkeypatch):
    assert _scan(tmp_path, monkeypatch, _answer()) == BASE + "/r/AbC_12-x"


def test_the_servers_own_link_wins(tmp_path, monkeypatch):
    served = "https://receipts.example/r/AbC_12-x"
    assert _scan(tmp_path, monkeypatch, _answer(receipt_url=served)) == served


@pytest.mark.parametrize("served", [
    "javascript:alert(1)//r/AbC_12-x",
    "https://elsewhere.example/r/SomeOtherId",
    "https://x.example/r/AbC_12-x\n## injected",
    None,
])
def test_a_served_link_that_is_not_this_receipt_is_ignored(tmp_path, monkeypatch, served):
    assert _scan(tmp_path, monkeypatch, _answer(receipt_url=served)) == BASE + "/r/AbC_12-x"
