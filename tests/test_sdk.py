#!/usr/bin/env python3
"""Tests for the orphograph Python SDK (sdk/orphograph). Offline — the HTTP
transport is injected, so no network and we can assert the privacy invariant
(only hashes are transmitted)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sdk"))

import orphograph as og  # noqa: E402


def _recorder(status=200, resp=None):
    """Return (transport, calls). Transport records each request."""
    calls = []

    def transport(method, url, body, headers):
        calls.append({"method": method, "url": url, "body": body, "headers": headers})
        return status, (resp if resp is not None else {
            "receipt_id": "RSDK1", "calendars_ok": 5, "calendars_total": 5,
            "created_at": "2026-05-31T00:00:00+00:00"})
    return transport, calls


def test_anchor_text_hashes_locally_and_posts_only_hashes():
    t, calls = _recorder()
    c = og.Client(_transport=t)
    text = "secret agent output that must stay local"
    r = c.anchor_text(text, label="run-42")
    assert isinstance(r, og.Receipt)
    assert r.receipt_id == "RSDK1"
    assert r.receipt_url.endswith("/r/RSDK1")
    assert r.calendars_ok == 5
    body = calls[0]["body"]
    assert calls[0]["method"] == "POST" and calls[0]["url"].endswith("/api/anchor")
    assert body["hash_hex"] == hashlib.sha256(text.encode()).hexdigest()
    assert body["sha512_hex"] == hashlib.sha512(text.encode()).hexdigest()
    # privacy invariant: the content never appears in the request
    assert text not in json.dumps(body)
    assert set(body.keys()) <= {"hash_hex", "sha512_hex", "client_label"}


def test_anchor_bytes_and_file_match(tmp_path):
    data = b"\x00\x01binary content\xff"
    t, calls = _recorder()
    c = og.Client(_transport=t)
    c.anchor_bytes(data)
    p = tmp_path / "f.bin"
    p.write_bytes(data)
    c.anchor_file(str(p))
    # both produced the same content hash
    assert calls[0]["body"]["hash_hex"] == calls[1]["body"]["hash_hex"] \
        == hashlib.sha256(data).hexdigest()


def test_api_key_sets_header():
    t, calls = _recorder()
    og.Client(api_key="key_xyz", _transport=t).anchor_text("x")
    assert calls[0]["headers"].get("X-Orpho-Api-Key") == "key_xyz"


def test_label_truncated_to_200():
    t, calls = _recorder()
    og.Client(_transport=t).anchor_text("x", label="L" * 500)
    assert len(calls[0]["body"]["client_label"]) == 200


def test_non_2xx_raises_orphograph_error():
    t, _ = _recorder(status=429, resp={"error": "rate limited"})
    with pytest.raises(og.OrphographError):
        og.Client(_transport=t).anchor_text("x")


def test_error_field_in_200_still_raises():
    t, _ = _recorder(status=200, resp={"error": "nope"})
    with pytest.raises(og.OrphographError):
        og.Client(_transport=t).anchor_bytes(b"x")


def test_network_error_raises_orphograph_error():
    def boom(method, url, body, headers):
        raise og.OrphographError("network error: simulated")
    with pytest.raises(og.OrphographError):
        og.Client(_transport=boom).anchor_text("x")


def test_verify_and_get_receipt_hit_correct_paths():
    t, calls = _recorder(resp={"receipt_id": "RSDK1", "checks": [{"ok": True}]})
    c = og.Client(_transport=t)
    c.verify("RSDK1")
    c.get_receipt("RSDK1")
    assert calls[0]["url"].endswith("/api/verify/RSDK1") and calls[0]["method"] == "GET"
    assert calls[1]["url"].endswith("/api/receipt/RSDK1")


def test_receipt_id_sanitized_in_path():
    t, calls = _recorder()
    og.Client(_transport=t).verify("../../etc/passwd?x=1")
    # only alnum/_/- survive → no path traversal
    assert "/api/verify/etcpasswdx1" in calls[0]["url"]


def test_public_surface_exported():
    # the module-level convenience fns construct a Client internally (so they
    # use the real transport in production); here just assert the public API
    # surface is exported and versioned.
    for name in ("anchor_file", "anchor_bytes", "anchor_text", "verify", "get_receipt"):
        assert callable(getattr(og, name)), name
    assert og.__version__ and og.DEFAULT_ENDPOINT.startswith("https://")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
