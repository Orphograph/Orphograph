"""test_stats_demand_counters.py — /api/stats demand counters, counts only.

Three aggregate counters in the public anchors block (2026-11-04):
x402_anchors (paid via x402), agent_label_anchors (client_label starts with
"agent-", any case) and run_anchors (a folder anchor whose manifest paths are
all step-NNNNNN, the agent-run shape mcp/orphograph_mcp.py builds). Driven
through the real server (tests/_srv.py) with stubbed calendars, the mock x402
facilitator and the egress guard. Each test reads the counters before and
after, so the seeded sample receipts do not matter. Every counter has a
control: a request one step outside its definition that must not move it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

import _srv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import merkle  # noqa: E402
import x402  # noqa: E402

PAY_TO = "0x00000000000000000000000000000000000000be"
COUNTERS = ("x402_anchors", "agent_label_anchors", "run_anchors")
ENV = {
    "ORPHO_X402_BACKEND": "mock",
    "ORPHO_X402_ALLOW_MOCK": "1",
    "ORPHO_X402_PAY_TO_ADDRESS": PAY_TO,
    "ORPHO_STATS_CACHE_SEC": "0",
    "ORPHO_HEALTH_CACHE_SEC": "0",
}


@pytest.fixture()
def server(tmp_path):
    yield from _srv.server_processes(tmp_path, stub_calendars=True, egress_guard=True, **ENV)


def _stats(base: str) -> dict:
    return _srv.ok_json(*_srv.get_json(base, "/api/stats", timeout=15))


def _counts(base: str) -> dict:
    anchors = _stats(base)["anchors"]
    return {k: anchors[k] for k in COUNTERS}


def _manifest(paths: list[str]) -> dict:
    leaves, hashes = [], []
    for p in paths:
        digest = hashlib.sha256(("output of " + p).encode()).digest()
        leaf = merkle._leaf_hash(p, digest)
        hashes.append(leaf)
        leaves.append({"path": p, "file_sha256_hex": digest.hex(),
                       "leaf_hex": leaf.hex(), "size_bytes": 1})
    root = merkle._build_levels(hashes)[-1][0].hex()
    return {"algorithm": merkle.ALGORITHM, "version": merkle.VERSION,
            "root_hex": root, "leaves": leaves}


def _folder(base: str, paths: list[str], label: str | None = None) -> dict:
    body = {"manifest": _manifest(paths)}
    if label is not None:
        body["client_label"] = label
    return _srv.ok_json(*_srv.post_json(base, "/api/anchor_folder", body, timeout=15))


def _anchor(base: str, hash_hex: str, label: str | None = None, headers=None) -> dict:
    body = {"hash_hex": hash_hex}
    if label is not None:
        body["client_label"] = label
    code, rec = _srv.anchor(base, body, headers=headers, timeout=15)
    assert code == 200, rec
    return rec


def _x402_headers(nonce: str) -> dict:
    payload = {
        "x402Version": 2,
        "accepted": {"scheme": "exact", "network": "eip155:84532",
                     "asset": x402.USDC_BASE_SEPOLIA, "amount": "50000",
                     "payTo": PAY_TO, "maxTimeoutSeconds": 60, "extra": {}},
        "payload": {"signature": "0x" + "ab" * 65,
                    "authorization": {"from": "0xagentPayer0000000000000000000000000001",
                                      "to": PAY_TO, "value": "50000", "validAfter": "0",
                                      "validBefore": "9999999999", "nonce": nonce},
                    "mock_outcome": "ok"},
    }
    return {x402.PAYMENT_SIGNATURE_HEADER:
            base64.b64encode(json.dumps(payload).encode()).decode()}


def _delta(before: dict, after: dict) -> dict:
    return {k: after[k] - before[k] for k in COUNTERS}


def test_x402_paid_anchors_are_counted_and_free_ones_are_not(server):
    before = _counts(server)
    _anchor(server, "e1" * 32)                                     # control: free
    assert _delta(before, _counts(server)) == dict.fromkeys(COUNTERS, 0)
    rec = _anchor(server, "e2" * 32, headers=_x402_headers("st1"))
    assert rec.get("x402_settled") is True, rec
    assert _delta(before, _counts(server)) == {**dict.fromkeys(COUNTERS, 0), "x402_anchors": 1}


def test_agent_labels_count_case_insensitively_and_only_as_a_prefix(server):
    before = _counts(server)
    _anchor(server, "e3" * 32, label="agent-run-42")
    _anchor(server, "e4" * 32, label="AGENT-nightly")
    _folder(server, ["a.txt"], label="Agent-folder")               # folders count too
    # Controls: the word elsewhere, no hyphen, no label.
    _anchor(server, "e5" * 32, label="my-agent-run")
    _anchor(server, "e6" * 32, label="agentrun")
    _anchor(server, "e7" * 32)
    assert _delta(before, _counts(server)) == {**dict.fromkeys(COUNTERS, 0),
                                               "agent_label_anchors": 3}


def test_run_anchors_need_every_path_to_be_a_step(server):
    before = _counts(server)
    _folder(server, ["step-000001", "step-000002", "step-000003"])
    _folder(server, ["step-000001"])
    # Controls: one non-step path, a short step number, a plain folder.
    _folder(server, ["step-000001", "notes.txt"])
    _folder(server, ["step-1", "step-2"])
    _folder(server, ["docs/a.md", "docs/b.md"])
    # A single anchor is never a run.
    _anchor(server, "e8" * 32, label="step-000001")
    assert _delta(before, _counts(server)) == {**dict.fromkeys(COUNTERS, 0), "run_anchors": 2}


def test_stats_carry_counts_only_never_the_labels_or_paths(server):
    _anchor(server, "e9" * 32, label="agent-secret-label-xyz")
    _folder(server, ["step-000001", "step-000777"])
    stats = _stats(server)
    text = json.dumps(stats)
    assert "agent-secret-label-xyz" not in text and "step-000777" not in text
    for k in COUNTERS:
        assert isinstance(stats["anchors"][k], int) and not isinstance(stats["anchors"][k], bool)
