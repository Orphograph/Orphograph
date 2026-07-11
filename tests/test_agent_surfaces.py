"""Tests for the agent-facing clients: openclaw anchor CLI + prompt lineage.

Pure-function tests only — no network. The HTTP paths these clients call
(/api/anchor, /api/verify/<id>) are covered by the server test suite.
"""

import hashlib
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


agent_anchor = _load("orpho_agent_anchor",
                     "integrations/openclaw/orpho_agent_anchor.py")
anchor_prompt = _load("anchor_prompt", "tools/anchor_prompt.py")


# ── memory manifest ────────────────────────────────────────────────

def test_memory_manifest_is_deterministic(tmp_path):
    (tmp_path / "MEMORY.md").write_text("alpha")
    sub = tmp_path / "memory"
    sub.mkdir()
    (sub / "b.md").write_text("beta")
    m1 = agent_anchor.build_memory_manifest(str(tmp_path))
    m2 = agent_anchor.build_memory_manifest(str(tmp_path))
    assert m1 == m2
    parsed = json.loads(m1)
    assert parsed["kind"] == "agent-memory-manifest"
    assert [e["path"] for e in parsed["files"]] == ["MEMORY.md", "memory/b.md"]


def test_memory_manifest_skips_hidden_and_non_markdown(tmp_path):
    (tmp_path / "SOUL.md").write_text("x")
    (tmp_path / "secrets.json").write_text("{}")
    hidden = tmp_path / ".orphograph"
    hidden.mkdir()
    (hidden / "receipts.md").write_text("y")
    parsed = json.loads(agent_anchor.build_memory_manifest(str(tmp_path)))
    assert [e["path"] for e in parsed["files"]] == ["SOUL.md"]


def test_memory_manifest_changes_when_content_changes(tmp_path):
    f = tmp_path / "MEMORY.md"
    f.write_text("v1")
    before = agent_anchor.build_memory_manifest(str(tmp_path))
    f.write_text("v2 — silently edited history")
    after = agent_anchor.build_memory_manifest(str(tmp_path))
    assert before != after


# ── prompt cards ───────────────────────────────────────────────────

def test_card_is_canonical_and_deterministic():
    c1 = anchor_prompt.build_card("aa" * 32, "2026-07-10T00:00:00Z",
                                  parent_receipt="r123", score=0.83, label="v12")
    c2 = anchor_prompt.build_card("aa" * 32, "2026-07-10T00:00:00Z",
                                  parent_receipt="r123", score=0.83, label="v12")
    assert c1 == c2
    assert " " not in c1  # separators=(",", ":") — no incidental whitespace
    parsed = json.loads(c1)
    assert parsed["kind"] == "prompt-card"
    assert parsed["parent_receipt"] == "r123"


def test_card_omits_absent_optional_fields():
    card = json.loads(anchor_prompt.build_card("bb" * 32, "2026-07-10T00:00:00Z"))
    assert set(card) == {"version", "kind", "prompt_sha256", "created_utc"}


def test_card_hash_binds_lineage_claim():
    base = dict(prompt_sha256="cc" * 32, created_utc="2026-07-10T00:00:00Z")
    honest = anchor_prompt.build_card(parent_receipt="r1", score=0.5, **base)
    inflated = anchor_prompt.build_card(parent_receipt="r1", score=0.9, **base)
    h = lambda s: hashlib.sha256(s.encode()).hexdigest()
    assert h(honest) != h(inflated)  # score can't be revised post-anchor


def test_lineage_read_roundtrip(tmp_path):
    prompt = tmp_path / "SOUL.md"
    prompt.write_text("you are a helpful agent")
    card = anchor_prompt.build_card(anchor_prompt.sha256_file(str(prompt)),
                                    "2026-07-10T00:00:00Z", label="v1")
    rec = {"card": card,
           "card_sha256": hashlib.sha256(card.encode()).hexdigest(),
           "response": {"receipt_id": "rTEST"}}
    with open(anchor_prompt.lineage_path(str(prompt)), "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    lineage = anchor_prompt.read_lineage(str(prompt))
    assert len(lineage) == 1
    assert json.loads(lineage[0]["card"])["label"] == "v1"
