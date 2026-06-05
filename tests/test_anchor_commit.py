#!/usr/bin/env python3
"""Tests for tools/anchor_commit.py — Bitcoin-anchored git commit attestation.

The git resolver and the anchor HTTP client are injected, so these run offline
with no git repo and no network.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import anchor_commit as ac  # noqa: E402

FAKE_SHA = "a1b2c3d4e5f6071829304a5b6c7d8e9f00112233"
FAKE_TREE = "ffeeddccbbaa99887766554433221100ffeeddcc"
FAKE_SUBJECT = "feat: do the thing"


def _fake_resolve(ref="HEAD"):
    return FAKE_SHA, FAKE_TREE, FAKE_SUBJECT


def _make_anchor(ok=True):
    calls = []

    def fake(endpoint, hash_hex, sha512_hex, label, api_key):
        calls.append({"hash_hex": hash_hex, "sha512_hex": sha512_hex, "label": label})
        if not ok:
            return False, {"status_code": 500, "error": "boom"}
        return True, {"receipt_id": "RCOMMIT1", "created_at": "2026-05-31T00:00:00+00:00",
                      "calendars_ok": 5}
    return fake, calls


def test_descriptor_hash_is_reproducible():
    s256, s512 = ac.descriptor_hashes(FAKE_SHA, FAKE_TREE)
    expect = hashlib.sha256(
        ac.commit_descriptor(FAKE_SHA, FAKE_TREE).encode("utf-8")).hexdigest()
    assert s256 == expect
    # anyone can re-derive it from the public commit+tree ids → verifiable w/o us
    assert s256 == hashlib.sha256(
        f"orphograph-commit-anchor\ncommit {FAKE_SHA}\ntree {FAKE_TREE}\n".encode()).hexdigest()


def test_anchor_commit_happy_writes_receipt(tmp_path):
    fake, calls = _make_anchor()
    res = ac.anchor_commit("HEAD", repo_root=tmp_path,
                           resolve_fn=_fake_resolve, anchor_fn=fake)
    assert res["ok"] is True
    assert res["commit"] == FAKE_SHA and res["tree"] == FAKE_TREE
    assert res["receipt_id"] == "RCOMMIT1"
    # the anchored hash is the descriptor's sha256
    expect256, _ = ac.descriptor_hashes(FAKE_SHA, FAKE_TREE)
    assert calls[0]["hash_hex"] == expect256
    assert calls[0]["label"] == "commit a1b2c3d4e5f6"
    # receipt written into the repo, keyed by commit sha
    receipt = tmp_path / ".orphograph" / "commits" / f"{FAKE_SHA}.json"
    assert receipt.exists()
    saved = json.loads(receipt.read_text())
    assert saved["receipt_id"] == "RCOMMIT1" and saved["descriptor_sha256"] == expect256


def test_dry_run_anchors_nothing(tmp_path):
    fake, calls = _make_anchor()
    res = ac.anchor_commit("HEAD", dry_run=True, resolve_fn=_fake_resolve, anchor_fn=fake)
    assert res["dry_run"] is True
    assert res["descriptor_sha256"] == ac.descriptor_hashes(FAKE_SHA, FAKE_TREE)[0]
    assert calls == [], "dry-run hit the network"


def test_no_label_omits_commit_id(tmp_path):
    fake, calls = _make_anchor()
    ac.anchor_commit("HEAD", repo_root=tmp_path, label=False,
                     resolve_fn=_fake_resolve, anchor_fn=fake)
    assert calls[0]["label"] == ""


def test_anchor_failure_surfaces_not_ok(tmp_path):
    fake, _ = _make_anchor(ok=False)
    res = ac.anchor_commit("HEAD", repo_root=tmp_path,
                           resolve_fn=_fake_resolve, anchor_fn=fake)
    assert res["ok"] is False
    assert res["commit"] == FAKE_SHA


def test_only_hash_is_transmitted(tmp_path):
    """Privacy: the descriptor/content is never sent — only its hashes."""
    fake, calls = _make_anchor()
    ac.anchor_commit("HEAD", repo_root=tmp_path, resolve_fn=_fake_resolve, anchor_fn=fake)
    sent = json.dumps(calls[0])
    assert FAKE_SUBJECT not in sent  # the commit subject is not transmitted
    assert set(calls[0].keys()) == {"hash_hex", "sha512_hex", "label"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
