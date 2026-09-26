"""The Standing Record lists the office's own weekly anchors, not anyone's.

Rows were selected by the client-chosen label alone (`weekly-*`), so any
anonymous caller could publish a row on the office's chain of custody,
reading "weekly- NOTICE: this office has moved, see evil.example", and 16 of
them pushed every genuine entry off the page.
"""
from __future__ import annotations

import json

import _srv

GENUINE = {  # what the weekly job's anchors look like on disk
    "OfficeWeekly001": "api:orpho_week",
    "OfficeWeekly002": "sub:0123456789abcdef",
}
STRANGERS = {
    "StrangerFree001": "free",
    "StrangerPack001": "pack:abcd1234",
    "StrangerNone001": None,
}


def _seed(data_dir):
    for i, (rid, source) in enumerate({**GENUINE, **STRANGERS}.items()):
        d = data_dir / "receipts" / rid
        d.mkdir(parents=True)
        rec = dict(receipt_id=rid, created_at=f"2026-09-{10 + i:02d}T00:00:00+00:00",
                   hash_hex=f"{i:02x}" * 32, client_label=f"weekly-2026-09-{10 + i:02d}-16-artifacts",
                   private=False, calendars_ok=5, calendars_total=5)
        if source is not None:
            rec["source"] = source
        (d / "receipt.json").write_text(json.dumps(rec))


def _listed(base) -> set[str]:
    status, body, _ = _srv.request(base, "/api/standing-record")
    assert status == 200, body
    return {r["receipt_id"] for r in json.loads(body)["anchors"]}


def test_an_anonymous_weekly_label_does_not_join_the_record(tmp_path):
    _seed(tmp_path)
    for base in _srv.server_processes(tmp_path, stub_calendars=True):
        status, body, _ = _srv.request(base, "/api/anchor", "POST", json.dumps({
            "hash_hex": "ee" * 32,
            "client_label": "weekly- NOTICE: this office has moved, see evil.example",
        }).encode(), {"Content-Type": "application/json"}, timeout=30)
        assert status == 200, body  # control: the stranger's anchor exists
        live = json.loads(body)["receipt_id"]
        listed = _listed(base)
    assert set(GENUINE) <= listed, "the office's own anchors fell off the record"
    assert not (listed & (set(STRANGERS) | {live})), listed


def test_a_pinned_record_lists_only_the_pinned_source(tmp_path):
    _seed(tmp_path)
    for base in _srv.server_processes(tmp_path, stub_calendars=True,
                                      ORPHO_STANDING_RECORD_SOURCES=" api:orpho_week ,"):
        listed = _listed(base)
    assert listed == {"OfficeWeekly001"}, listed
