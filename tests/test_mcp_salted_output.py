#!/usr/bin/env python3
"""Salted output anchors, and the pending/confirmed state on every anchor.

1. A receipt's fingerprint is public. For short or guessable output
   ("approve", an amount) anyone can hash the candidates and confirm which
   one was anchored. `salt: true` anchors SHA-256(salt ‖ text) with a 32-byte
   salt that never leaves the device; the holder later proves the output by
   handing over the text and the salt.
2. Agents act in milliseconds and a Bitcoin commitment takes hours. Every
   anchor reply now says whether the receipt is pending or confirmed.

_http is monkeypatched: these run offline and assert on the REQUEST the tool
would send and the REPLY the agent receives.
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

GUESSABLE = "approve"
CANDIDATES = ["approve", "deny", "yes", "no", "hold", "escalate"]


@pytest.fixture
def anchor_http(monkeypatch):
    seen = {}

    def fake_http(method, path, body=None):
        seen.setdefault("calls", []).append((method, path, body))
        return {"receipt_id": "RSALT1", "calendars_ok": 5, "calendars_total": 5}
    monkeypatch.setattr(mcp, "_http", fake_http)
    return seen


def _anchored(seen) -> str:
    return seen["calls"][-1][2]["hash_hex"]


# ── the attack the salt exists to stop ───────────────────────────────────

def test_unsalted_guessable_output_is_recoverable_from_its_hash(anchor_http):
    """The control: without a salt, the public hash gives the output away."""
    mcp.tool_anchor_output({"text": GUESSABLE})
    public = _anchored(anchor_http)
    hits = [c for c in CANDIDATES if hashlib.sha256(c.encode()).hexdigest() == public]
    assert hits == [GUESSABLE]


def test_salted_output_is_not_recoverable_from_its_hash(anchor_http):
    out = mcp.tool_anchor_output({"text": GUESSABLE, "salt": True})
    public = _anchored(anchor_http)
    hits = [c for c in CANDIDATES if hashlib.sha256(c.encode()).hexdigest() == public]
    assert hits == []
    assert out["salted"] is True


def test_salted_anchor_is_sha256_of_salt_then_text(anchor_http):
    out = mcp.tool_anchor_output({"text": GUESSABLE, "salt": True})
    salt = bytes.fromhex(out["salt_hex"])
    assert len(salt) == 32
    body = anchor_http["calls"][-1][2]
    assert body["hash_hex"] == hashlib.sha256(salt + GUESSABLE.encode()).hexdigest()
    assert body["sha512_hex"] == hashlib.sha512(salt + GUESSABLE.encode()).hexdigest()
    assert out["sha256_hex"] == body["hash_hex"]


def test_salt_and_text_never_leave_the_device(anchor_http):
    out = mcp.tool_anchor_output({"text": GUESSABLE, "salt": True,
                                  "label": "run-7"})
    wire = json.dumps(anchor_http["calls"])
    assert out["salt_hex"] not in wire
    assert GUESSABLE not in wire.replace("run-7", "")


def test_two_salted_anchors_of_the_same_text_differ(anchor_http):
    a = mcp.tool_anchor_output({"text": GUESSABLE, "salt": True})
    b = mcp.tool_anchor_output({"text": GUESSABLE, "salt": True})
    assert a["salt_hex"] != b["salt_hex"]
    assert a["sha256_hex"] != b["sha256_hex"]


def test_caller_supplied_salt_is_used(anchor_http):
    salt_hex = "ab" * 32
    out = mcp.tool_anchor_output({"text": GUESSABLE, "salt_hex": salt_hex})
    assert out["salt_hex"] == salt_hex
    assert _anchored(anchor_http) == hashlib.sha256(
        bytes.fromhex(salt_hex) + GUESSABLE.encode()).hexdigest()


@pytest.mark.parametrize("bad", ["ab" * 31, "AB" * 32, "zz" * 32, 7, ""])
def test_malformed_salt_is_refused_before_anything_is_sent(anchor_http, bad):
    out = mcp.tool_anchor_output({"text": GUESSABLE, "salt_hex": bad})
    assert "error" in out and "salt_hex" in out["error"]
    assert "calls" not in anchor_http


def test_unsalted_note_warns_about_guessable_text(anchor_http):
    out = mcp.tool_anchor_output({"text": GUESSABLE})
    assert out["salted"] is False
    assert "salt: true" in out["note"]
    assert "salt_hex" not in out


def test_unsalted_hash_is_unchanged_from_before(anchor_http):
    """No salt means the plain hash, so existing receipts keep verifying."""
    mcp.tool_anchor_output({"text": "the model said X"})
    assert _anchored(anchor_http) == hashlib.sha256(b"the model said X").hexdigest()


# ── verifying a salted output ────────────────────────────────────────────

@pytest.fixture
def verify_http(monkeypatch):
    state = {}

    def fake_http(method, path, body=None):
        if path.startswith("/api/verify/"):
            return {"receipt_id": "RSALT1", "hash_hex": state["anchored"],
                    "created_at": "2026-11-04T00:00:00Z", "btc_pinned_at": None,
                    "calendars_ok": 5}
        # The office answers 400 for a single (non-folder) anchor.
        return {"error": "http_error", "status": 400}
    monkeypatch.setattr(mcp, "_http", fake_http)
    return state


def test_verify_salted_output_with_its_salt_matches(verify_http):
    salt = bytes.fromhex("cd" * 32)
    verify_http["anchored"] = hashlib.sha256(salt + GUESSABLE.encode()).hexdigest()
    out = mcp.tool_verify_output({"receipt_id": "RSALT1", "text": GUESSABLE,
                                  "salt_hex": salt.hex()})
    assert out["match"] is True


def test_verify_salted_output_without_its_salt_does_not_match(verify_http):
    salt = bytes.fromhex("cd" * 32)
    verify_http["anchored"] = hashlib.sha256(salt + GUESSABLE.encode()).hexdigest()
    out = mcp.tool_verify_output({"receipt_id": "RSALT1", "text": GUESSABLE})
    assert out["match"] is False


def test_verify_with_wrong_salt_does_not_match(verify_http):
    salt = bytes.fromhex("cd" * 32)
    verify_http["anchored"] = hashlib.sha256(salt + GUESSABLE.encode()).hexdigest()
    out = mcp.tool_verify_output({"receipt_id": "RSALT1", "text": GUESSABLE,
                                  "salt_hex": "ce" + "cd" * 31})
    assert out["match"] is False


def test_verify_rejects_malformed_salt(verify_http):
    verify_http["anchored"] = "00" * 32
    out = mcp.tool_verify_output({"receipt_id": "RSALT1", "text": GUESSABLE,
                                  "salt_hex": "nothex"})
    assert "error" in out


def test_round_trip_anchor_then_verify(monkeypatch):
    """The salt the anchor returns is exactly what verify needs."""
    store = {}

    def fake_http(method, path, body=None):
        if method == "POST":
            store["hash"] = body["hash_hex"]
            return {"receipt_id": "RRT", "calendars_ok": 5, "calendars_total": 5}
        return {"receipt_id": "RRT", "hash_hex": store["hash"],
                "created_at": "2026-11-04T00:00:00Z", "calendars_ok": 5}
    monkeypatch.setattr(mcp, "_http", fake_http)
    a = mcp.tool_anchor_output({"text": "amount: 1200", "salt": True})
    v = mcp.tool_verify_output({"receipt_id": "RRT", "text": "amount: 1200",
                                "salt_hex": a["salt_hex"]})
    assert v["match"] is True


# ── pending vs confirmed on every anchor reply ───────────────────────────

def test_fresh_anchor_says_pending(anchor_http):
    out = mcp.tool_anchor_output({"text": "x"})
    assert out["bitcoin_status"] == "pending"
    assert "calendar" in out["bitcoin_note"]


def test_anchor_file_says_pending(anchor_http, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    out = mcp.tool_anchor_file({"path": str(f)})
    assert out["bitcoin_status"] == "pending"


def test_verify_receipt_reports_confirmed_once_pinned(monkeypatch):
    monkeypatch.setattr(mcp, "_http", lambda m, p, b=None: {
        "receipt_id": "R1", "hash_hex": "00" * 32, "checks": [{"ok": True}],
        "btc_pinned_at": "2026-11-04T03:00:00Z"})
    out = mcp.tool_verify_receipt({"receipt_id": "R1"})
    assert out["bitcoin_status"] == "confirmed"


def test_verify_receipt_reports_pending_before_pin(monkeypatch):
    monkeypatch.setattr(mcp, "_http", lambda m, p, b=None: {
        "receipt_id": "R1", "hash_hex": "00" * 32, "checks": [{"ok": True}],
        "btc_pinned_at": None})
    out = mcp.tool_verify_receipt({"receipt_id": "R1"})
    assert out["bitcoin_status"] == "pending"


def test_tool_schema_advertises_salt():
    tools = {t["name"]: t for t in mcp.TOOL_DEFINITIONS}
    props = tools["orphograph_anchor_output"]["inputSchema"]["properties"]
    assert props["salt"]["type"] == "boolean"
    assert "salt_hex" in props
    assert "salt_hex" in tools["orphograph_verify_output"]["inputSchema"]["properties"]


@pytest.mark.parametrize("flag", ["true", 1, "yes"])
def test_non_boolean_salt_flag_is_refused_not_ignored(anchor_http, flag):
    """A string "true" must not anchor UNSALTED in silence."""
    out = mcp.tool_anchor_output({"text": GUESSABLE, "salt": flag})
    assert "error" in out and "boolean" in out["error"]
    assert "calls" not in anchor_http


def test_salt_false_is_plain(anchor_http):
    out = mcp.tool_anchor_output({"text": GUESSABLE, "salt": False})
    assert out["salted"] is False


def test_salt_hex_without_text_is_refused(verify_http):
    verify_http["anchored"] = "00" * 32
    out = mcp.tool_verify_output({"receipt_id": "RSALT1",
                                  "sha256_hex": "00" * 32, "salt_hex": "cd" * 32})
    assert "error" in out and "salt_hex goes with text" in out["error"]
