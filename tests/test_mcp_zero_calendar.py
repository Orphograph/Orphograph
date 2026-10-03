"""The MCP tools do not report an anchor no calendar accepted as a success.

A 200 with calendars_ok 0 is a receipt with no Bitcoin commitment, and it never
gets one. The GitHub Action, the agent CLI and the dataset CLI already count it
as not anchored (#283); the MCP tools answered "ok": true for it, so an agent
told its user the work was anchored. _http is stubbed, so nothing leaves the
machine.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))

import orphograph_mcp as mcp  # noqa: E402


def _answer(calendars_ok):
    def fake_http(method, path, body=None):
        out = {"receipt_id": "RZEROCAL01", "calendars_total": 5,
               "calendars_distinct_ok": 3, "calendars_distinct_total": 4, "low_redundancy": False}
        if calendars_ok is not None:
            out["calendars_ok"] = calendars_ok
        return out
    return fake_http


def _call(tool, tmp_path):
    if tool == "file":
        f = tmp_path / "deliverable.txt"
        f.write_text("work")
        return mcp.tool_anchor_file({"path": str(f)})
    if tool == "output":
        return mcp.tool_anchor_output({"text": "agent output"})
    d = tmp_path / "folder"
    d.mkdir()
    (d / "a.txt").write_text("a")
    return mcp.tool_anchor_folder({"path": str(d)})


@pytest.mark.parametrize("tool", ["file", "output", "folder"])
def test_no_calendar_accepted_is_not_ok(tool, tmp_path, monkeypatch):
    monkeypatch.setattr(mcp, "_http", _answer(0))
    out = _call(tool, tmp_path)
    assert out["ok"] is False, out
    assert "no calendar accepted" in out["error"], out
    assert out.get("receipt_id") == "RZEROCAL01", "the receipt id is still reported"


@pytest.mark.parametrize("calendars_ok", [1, None], ids=["one-calendar", "field-absent"])
@pytest.mark.parametrize("tool", ["file", "output", "folder"])
def test_a_committed_or_older_answer_is_still_ok(tool, calendars_ok, tmp_path, monkeypatch):
    # The honest callers on the same path: one calendar commits, and a server
    # answer without the field (older servers) is not treated as a refusal.
    monkeypatch.setattr(mcp, "_http", _answer(calendars_ok))
    assert _call(tool, tmp_path)["ok"] is True


def test_the_folder_note_names_every_field_sent(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp, "_http", _answer(1))
    note = _call("folder", tmp_path)["note"].lower()
    for field in ("relative paths", "sizes", "sha-256 digests", "merkle root"):
        assert field in note, (field, note)


def test_the_shipped_copy_is_the_same_file():
    # web/mcp/orphograph_mcp.py is the curl-install target.
    assert (ROOT / "web" / "mcp" / "orphograph_mcp.py").read_bytes() == \
        (ROOT / "mcp" / "orphograph_mcp.py").read_bytes()



@pytest.mark.parametrize("tool", ["file", "output", "folder"])
def test_the_distinct_calendar_counts_reach_the_agent(tool, tmp_path, monkeypatch):
    # @mcp-distinct-passthrough: low_redundancy is measured on distinct
    # calendars, and the tools dropped the two counts it is measured on.
    monkeypatch.setattr(mcp, "_http", _answer(5))
    out = _call(tool, tmp_path)
    assert (out["calendars_distinct_ok"], out["calendars_distinct_total"]) == (3, 4), out
    assert out["low_redundancy"] is False
