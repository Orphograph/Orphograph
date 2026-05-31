#!/usr/bin/env python3
"""Tests for the MCP server's orphograph_anchor_output tool — anchor a string of
generated output directly (no file), transmitting only the hashes.

_http is monkeypatched so these run offline and assert the privacy invariant:
the output text is NEVER part of the request payload.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))

import orphograph_mcp as mcp  # noqa: E402


@pytest.fixture
def capture_http(monkeypatch):
    """Replace _http with a recorder that returns a canned receipt."""
    seen = {}

    def fake_http(method, path, body=None):
        seen["method"] = method
        seen["path"] = path
        seen["body"] = body
        return {"receipt_id": "RTEST123", "calendars_ok": 5, "calendars_total": 5,
                "pack_remaining": 9}
    monkeypatch.setattr(mcp, "_http", fake_http)
    return seen


def test_hash_text_matches_hashlib():
    s256, s512, n = mcp.hash_text("hello agent output")
    raw = "hello agent output".encode("utf-8")
    assert s256 == hashlib.sha256(raw).hexdigest()
    assert s512 == hashlib.sha512(raw).hexdigest()
    assert n == len(raw)


def test_anchor_output_happy_path(capture_http):
    out = mcp.tool_anchor_output({"text": "the model said X at 12:00"})
    assert out["ok"] is True
    assert out["receipt_id"] == "RTEST123"
    assert out["receipt_url"].endswith("/r/RTEST123")
    assert out["calendars_ok"] == 5
    # payload carries the hashes, posted to /api/anchor
    assert capture_http["method"] == "POST"
    assert capture_http["path"] == "/api/anchor"
    expect256 = hashlib.sha256("the model said X at 12:00".encode()).hexdigest()
    assert capture_http["body"]["hash_hex"] == expect256
    assert "sha512_hex" in capture_http["body"]


def test_privacy_text_never_in_payload(capture_http):
    secret = "CONFIDENTIAL agent reasoning that must not leave the device"
    mcp.tool_anchor_output({"text": secret})
    body_json = json.dumps(capture_http["body"])
    assert secret not in body_json, "output text leaked into the request payload"
    # only hash fields + optional label
    assert set(capture_http["body"].keys()) <= {"hash_hex", "sha512_hex", "client_label", "c2pa_manifest_hash"}


def test_missing_text_errors_without_network(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(mcp, "_http", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {})
    out = mcp.tool_anchor_output({})
    assert "error" in out
    assert called["n"] == 0, "hit the network on a missing-arg call"


def test_label_included_and_truncated(capture_http):
    mcp.tool_anchor_output({"text": "x", "label": "agent-run-42 " + "z" * 500})
    assert capture_http["body"]["client_label"].startswith("agent-run-42")
    assert len(capture_http["body"]["client_label"]) <= 200


def test_http_error_surfaces_not_ok(monkeypatch):
    monkeypatch.setattr(mcp, "_http", lambda *a, **k: {"error": "http_error", "status": 429})
    out = mcp.tool_anchor_output({"text": "x"})
    assert out["ok"] is False
    assert out["error"] == "http_error"


def test_tool_registered_and_dispatched(capture_http):
    names = [t["name"] for t in mcp.TOOL_DEFINITIONS]
    assert "orphograph_anchor_output" in names
    # the tool def requires `text`
    tdef = next(t for t in mcp.TOOL_DEFINITIONS if t["name"] == "orphograph_anchor_output")
    assert tdef["inputSchema"]["required"] == ["text"]
    # dispatch through handle()
    resp = mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": "orphograph_anchor_output",
                                  "arguments": {"text": "routed"}}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["ok"] is True and payload["receipt_id"] == "RTEST123"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
