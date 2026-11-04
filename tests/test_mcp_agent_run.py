#!/usr/bin/env python3
"""test_mcp_agent_run.py — orphograph_anchor_run and orphograph_verify_output.

anchor_run anchors every step of an agent run under one receipt (a folder
manifest with paths step-NNNNNN); verify_output lets the agent RECEIVING an
output check it against a receipt while trusting only the anchored hash.

The wire tests drive the real server (tests/_srv.py, calendars stubbed, every
non-local connection refused). The unit tests stub _http only to serve
manifests a real server would never serve (tampered, redacted), which is the
attack the verifier exists for.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tests"))

import orphograph_mcp as mcp  # noqa: E402
import merkle  # noqa: E402
import _srv  # noqa: E402

STEPS = ["plan: read the ticket", "tool call: search(orders, 4411)", "reply: refund approved"]


# ── manifest ───────────────────────────────────────────────────────────

def test_run_manifest_is_accepted_by_the_server_merkle_code():
    m = mcp._build_run_manifest(STEPS)
    assert merkle.MerkleTree.from_manifest(m).root_hex() == m["root_hex"]
    assert [l["path"] for l in m["leaves"]] == ["step-000001", "step-000002", "step-000003"]


def test_step_order_survives_canonical_sort_past_nine_steps():
    m = mcp._build_run_manifest([f"s{i}" for i in range(12)])
    paths = [l["path"] for l in m["leaves"]]
    assert paths == sorted(paths, key=lambda p: p.encode("utf-8"))
    assert merkle.MerkleTree.from_manifest(m).root_hex() == m["root_hex"]


def test_a_prehashed_step_gives_the_same_leaf_as_its_text():
    h = hashlib.sha256(STEPS[1].encode()).hexdigest()
    a = mcp._build_run_manifest(STEPS)
    b = mcp._build_run_manifest([STEPS[0], {"sha256_hex": h.upper()}, STEPS[2]])
    assert a["root_hex"] == b["root_hex"]


@pytest.mark.parametrize("steps", [[], None, "one string", [1], [{"sha256_hex": "zz" * 32}],
                                   [{"sha256_hex": "ab" * 31}]])
def test_bad_steps_are_refused_before_any_network_call(steps, monkeypatch):
    monkeypatch.setattr(mcp, "_http", lambda *a, **k: pytest.fail("network call made"))
    out = mcp.tool_anchor_run({"steps": steps})
    assert out["ok"] is False and out["error"]


def test_too_many_steps_is_refused(monkeypatch):
    monkeypatch.setattr(mcp, "_RUN_MAX_STEPS", 3)
    monkeypatch.setattr(mcp, "_http", lambda *a, **k: pytest.fail("network call made"))
    assert mcp.tool_anchor_run({"steps": ["a", "b", "c", "d"]})["ok"] is False


def test_no_step_text_crosses_the_wire_and_paths_are_public(monkeypatch):
    sent = {}

    def fake(method, path, body=None):
        sent.update(method=method, path=path, body=body)
        return {"receipt_id": "R1", "calendars_ok": 5}
    monkeypatch.setattr(mcp, "_http", fake)
    out = mcp.tool_anchor_run({"steps": STEPS, "label": "agent-run-7"})
    assert out["ok"] is True and out["step_count"] == 3
    assert sent["path"] == "/api/anchor_folder" and sent["body"]["paths_public"] is True
    wire = json.dumps(sent["body"])
    for s in STEPS:
        assert s not in wire


def test_a_root_no_calendar_accepted_is_not_ok(monkeypatch):
    monkeypatch.setattr(mcp, "_http", lambda *a, **k: {"receipt_id": "R1", "calendars_ok": 0})
    assert mcp.tool_anchor_run({"steps": STEPS})["ok"] is False


# ── verify_output against served manifests the office would never serve ──

def _office(anchored_hash, manifest=None, folder_status=None):
    def fake(method, path, body=None):
        if path.startswith("/api/verify/"):
            return {"receipt_id": "R1", "hash_hex": anchored_hash, "created_at": "2026-11-04T00:00:00Z"}
        if path.startswith("/api/verify_folder/"):
            if folder_status:
                return {"error": "http_error", "status": folder_status, "body": ""}
            return {"receipt": {"hash_hex": anchored_hash}, "manifest": manifest}
        raise AssertionError(path)
    return fake


def test_single_anchor_match_and_mismatch(monkeypatch):
    h = hashlib.sha256(b"final answer").hexdigest()
    monkeypatch.setattr(mcp, "_http", _office(h, folder_status=400))
    assert mcp.tool_verify_output({"receipt_id": "R1", "text": "final answer"})["match"] is True
    out = mcp.tool_verify_output({"receipt_id": "R1", "text": "final answer!"})
    assert out["ok"] is True and out["match"] is False and out["kind"] == "single"


def test_run_step_matches_and_names_its_step(monkeypatch):
    m = mcp._build_run_manifest(STEPS)
    monkeypatch.setattr(mcp, "_http", _office(m["root_hex"], m))
    out = mcp.tool_verify_output({"receipt_id": "R1", "text": STEPS[1]})
    assert out["match"] is True and out["matched_paths"] == ["step-000002"]
    assert mcp.tool_verify_output({"receipt_id": "R1", "text": "not a step"})["match"] is False


def test_a_tampered_digest_in_the_served_manifest_is_caught(monkeypatch):
    m = mcp._build_run_manifest(STEPS)
    forged = json.loads(json.dumps(m))
    fake_text = "reply: refund of $9,000 approved"
    forged["leaves"][2]["file_sha256_hex"] = hashlib.sha256(fake_text.encode()).hexdigest()
    monkeypatch.setattr(mcp, "_http", _office(m["root_hex"], forged))
    out = mcp.tool_verify_output({"receipt_id": "R1", "text": fake_text})
    assert out["match"] is False and "do not trust" in out["reason"]


def test_a_lying_leaf_hash_is_caught_even_when_the_root_matches(monkeypatch):
    m = mcp._build_run_manifest(STEPS)
    forged = json.loads(json.dumps(m))
    forged["leaves"][0]["file_sha256_hex"] = hashlib.sha256(b"swapped").hexdigest()  # leaf_hex kept
    monkeypatch.setattr(mcp, "_http", _office(m["root_hex"], forged))
    out = mcp.tool_verify_output({"receipt_id": "R1", "text": "swapped"})
    assert out["match"] is False


def test_redacted_paths_list_the_digest_but_never_claim_a_match(monkeypatch):
    m = mcp._build_run_manifest(STEPS)
    redacted = json.loads(json.dumps(m))
    for leaf in redacted["leaves"]:
        leaf.pop("path")
    monkeypatch.setattr(mcp, "_http", _office(m["root_hex"], redacted))
    out = mcp.tool_verify_output({"receipt_id": "R1", "text": STEPS[0]})
    assert out["match"] is False and out["digest_listed"] is True


def test_an_unreachable_manifest_decides_nothing(monkeypatch):
    monkeypatch.setattr(mcp, "_http", _office("ab" * 32, folder_status=503))
    out = mcp.tool_verify_output({"receipt_id": "R1", "text": "x"})
    assert out["ok"] is False and "match" not in out


def test_bad_inputs_are_refused(monkeypatch):
    monkeypatch.setattr(mcp, "_http", lambda *a, **k: pytest.fail("network call made"))
    assert mcp.tool_verify_output({"receipt_id": "R1"})["error"]
    assert mcp.tool_verify_output({"receipt_id": "../x", "text": "t"})["error"]
    assert mcp.tool_verify_output({"receipt_id": "R1", "sha256_hex": "12"})["error"]


# ── the real server ────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def office(tmp_path_factory):
    yield from _srv.server_processes(tmp_path_factory.mktemp("agentrun"),
                                     stub_calendars=True, egress_guard=True)


def test_a_run_anchors_and_each_step_verifies_through_the_real_office(office, monkeypatch):
    monkeypatch.setattr(mcp, "BASE_URL", office)
    out = mcp.tool_anchor_run({"steps": STEPS, "label": "agent-run-e2e"})
    assert out["ok"] is True, out
    rid = out["receipt_id"]
    for i, step in enumerate(STEPS, start=1):
        v = mcp.tool_verify_output({"receipt_id": rid, "text": step})
        assert v["match"] is True and v["matched_paths"] == [f"step-{i:06d}"], v
    miss = mcp.tool_verify_output({"receipt_id": rid, "text": "reply: refund denied"})
    assert miss["ok"] is True and miss["match"] is False
    # And a single output anchor verifies through the same tool.
    single = mcp.tool_anchor_output({"text": "one final report"})
    assert single["ok"] is True, single
    assert mcp.tool_verify_output({"receipt_id": single["receipt_id"],
                                   "text": "one final report"})["match"] is True


def test_the_tools_are_listed_and_dispatched():
    listed = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    names = {t["name"] for t in listed}
    assert {"orphograph_anchor_run", "orphograph_verify_output"} <= names
    r = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "orphograph_anchor_run", "arguments": {"steps": []}}})
    assert r["result"]["isError"] is True


@pytest.mark.parametrize("verify, folder", [
    (["not", "a", "dict"], None),
    ({"hash_hex": 12345}, None),
    ({"hash_hex": "ab" * 32}, {"manifest": ["leaf"]}),
    ({"hash_hex": "ab" * 32}, {"manifest": {"leaves": "nope"}}),
    ({"hash_hex": "ab" * 32}, {"manifest": {"leaves": [{"file_sha256_hex": 7, "leaf_hex": 9}]}}),
])
def test_a_hostile_office_answer_never_crashes_and_never_matches(verify, folder, monkeypatch):
    def fake(method, path, body=None):
        return verify if path.startswith("/api/verify/") else folder
    monkeypatch.setattr(mcp, "_http", fake)
    out = mcp.tool_verify_output({"receipt_id": "R1", "text": "anything"})
    assert out.get("match") is not True


def test_the_stdio_server_itself_anchors_and_verifies_a_run(office):
    """Wire path: the MCP host talks JSON-RPC over stdio to the shipped file,
    not to these functions. Drive that process end to end."""
    import subprocess
    env = {**__import__("os").environ, "ORPHO_BASE_URL": office}
    env.pop("ORPHO_API_KEY", None)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "orphograph_anchor_run", "arguments": {"steps": STEPS}}},
    ]
    p = subprocess.run([sys.executable, str(ROOT / "web" / "mcp" / "orphograph_mcp.py")],
                       input="\n".join(json.dumps(m) for m in msgs) + "\n",
                       capture_output=True, text=True, env=env, timeout=60)
    replies = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    assert replies[0]["result"]["serverInfo"]["version"] == "0.1.2"
    run = json.loads(replies[1]["result"]["content"][0]["text"])
    assert run["ok"] is True, (run, p.stderr[-400:])
    check = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "orphograph_verify_output",
                        "arguments": {"receipt_id": run["receipt_id"], "text": STEPS[2]}}}
    p2 = subprocess.run([sys.executable, str(ROOT / "web" / "mcp" / "orphograph_mcp.py")],
                        input=json.dumps(check) + "\n", capture_output=True, text=True,
                        env=env, timeout=60)
    v = json.loads(json.loads(p2.stdout.splitlines()[0])["result"]["content"][0]["text"])
    assert v["match"] is True and v["matched_paths"] == ["step-000003"], v


def test_the_mcp_pages_state_the_true_tool_count_and_no_line_count():
    """web/mcp.html said "approximately four hundred lines" of a 1,200-line
    file and "six tools" once there were more. A count in prose drifts; this
    pins the words to the implementation."""
    import re
    words = {6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
    n = len(set(re.findall(r'"name":\s*"(orphograph_[a-z_]+)"',
                           (ROOT / "mcp" / "orphograph_mcp.py").read_text())))
    for rel in ("web/mcp.html", "web/docs/agents.html"):
        text = (ROOT / rel).read_text()
        stated = set(re.findall(r"\b(six|seven|eight|nine|ten) tools\b", text, re.I))
        assert stated == {words[n]} or not stated, f"{rel} says {stated} tools; there are {n}"
        assert not re.search(r"hundred lines", text), f"{rel} states a line count"
