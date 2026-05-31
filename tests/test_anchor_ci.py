#!/usr/bin/env python3
"""Tests for the CI composite action's anchorer (.github/actions/anchor/anchor_ci.py).
Offline — the anchor call is injected. Locks the hashing, the file/text/both/neither
branches, the privacy invariant, and the $GITHUB_OUTPUT writer.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".github" / "actions" / "anchor"))

import anchor_ci as ci  # noqa: E402


def _fake(ok=True):
    calls = []

    def fake_anchor(endpoint, sha256, sha512, label, api_key):
        calls.append({"sha256": sha256, "sha512": sha512, "label": label, "api_key": api_key})
        if not ok:
            return False, {"status_code": 429, "error": "rate limited"}
        return True, {"receipt_id": "RCI1", "calendars_ok": 5}
    return fake_anchor, calls


def test_anchor_text(monkeypatch):
    fake, calls = _fake()
    env = {"ORPHO_TEXT": "release sha abc123", "ORPHO_LABEL": "release v1"}
    code, res = ci.run(env, anchor_fn=fake)
    assert code == 0 and res["ok"] is True
    assert res["receipt_id"] == "RCI1"
    assert res["receipt_url"].endswith("/r/RCI1")
    assert calls[0]["sha256"] == hashlib.sha256(b"release sha abc123").hexdigest()
    assert calls[0]["label"] == "release v1"


def test_anchor_file(tmp_path):
    p = tmp_path / "dist.tar.gz"
    p.write_bytes(b"artifact-bytes")
    fake, calls = _fake()
    code, res = ci.run({"ORPHO_FILE": str(p)}, anchor_fn=fake)
    assert code == 0
    assert calls[0]["sha256"] == hashlib.sha256(b"artifact-bytes").hexdigest()


def test_both_inputs_is_error():
    fake, calls = _fake()
    code, res = ci.run({"ORPHO_FILE": "/x", "ORPHO_TEXT": "y"}, anchor_fn=fake)
    assert code == 2 and not res["ok"]
    assert calls == []


def test_neither_input_is_error():
    fake, calls = _fake()
    code, res = ci.run({}, anchor_fn=fake)
    assert code == 2 and not res["ok"]
    assert calls == []


def test_missing_file_is_error():
    fake, calls = _fake()
    code, res = ci.run({"ORPHO_FILE": "/no/such/file"}, anchor_fn=fake)
    assert code == 2
    assert calls == []


def test_anchor_failure_exit_1():
    fake, _ = _fake(ok=False)
    code, res = ci.run({"ORPHO_TEXT": "x"}, anchor_fn=fake)
    assert code == 1 and res["ok"] is False


def test_privacy_only_hashes_passed():
    fake, calls = _fake()
    secret = "CONFIDENTIAL release notes that must not leave the runner"
    ci.run({"ORPHO_TEXT": secret}, anchor_fn=fake)
    assert set(calls[0].keys()) == {"sha256", "sha512", "label", "api_key"}
    # the content is never one of the passed values
    assert secret not in str(calls[0])


def test_api_key_threaded_through():
    fake, calls = _fake()
    ci.run({"ORPHO_TEXT": "x", "ORPHOGRAPH_API_KEY": "key_99"}, anchor_fn=fake)
    assert calls[0]["api_key"] == "key_99"


def test_write_github_output(tmp_path, monkeypatch):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    ci.write_github_output({"receipt-id": "RCI1", "receipt-url": "https://x/r/RCI1"})
    txt = out.read_text()
    assert "receipt-id=RCI1" in txt and "receipt-url=https://x/r/RCI1" in txt


def test_write_github_output_noop_without_env(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    # must not raise when GITHUB_OUTPUT is unset (local runs)
    ci.write_github_output({"receipt-id": "x"})


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
