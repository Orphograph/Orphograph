"""test_verify_output_route.py — GET /api/verify_output/<receipt_id>?sha256=<hex>.

An agent that RECEIVED an output checks it against a receipt with one GET,
no MCP. Drives the real HTTP server (tests/_srv.py, calendars stubbed) and
rechecks every inclusion proof it is handed with server/merkle.py against
the anchored root, never against a value this file built itself.

Privacy contract under test: a private receipt answers 404, byte-for-byte
the same as an unknown id, to everyone but its owner; a public folder
receipt's leaf paths stay hidden from non-owners unless paths_public.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

import _srv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import merkle  # noqa: E402 — pure tree code; no state shared with the server.

OWNER_EMAIL = "owner@example.test"
OTHER_EMAIL = "other@example.test"
SCOPE = ("Shows this exact output existed by the anchor time. It does not "
         "show who produced it or that it is correct.")
OUTPUT = b"the agent's answer, byte for byte"
OUTPUT_SHA = hashlib.sha256(OUTPUT).hexdigest()
FILES = {"steps/01_plan.txt": b"plan", "steps/02_tool.json": b'{"tool":1}',
         "final/answer.txt": OUTPUT}


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _append(path: Path, *rows: dict) -> None:
    with path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")


def _cookie(sid: str) -> dict:
    return {"Cookie": f"orpho_sid={sid}"}


OWNER, OTHER = _cookie("sess-owner"), _cookie("sess-other")


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    """Two signed-in subscribers (private anchors need a subscription)."""
    data = tmp_path_factory.mktemp("verify_output")
    exp = time.time() + 86400
    _append(data / "auth_sessions.jsonl",
            {"event": "created", "session_hash": _sha("sess-owner"), "email": OWNER_EMAIL,
             "expires_unix": exp},
            {"event": "created", "session_hash": _sha("sess-other"), "email": OTHER_EMAIL,
             "expires_unix": exp})
    _append(data / "stripe_customer_emails.jsonl",
            {"ts": "2026-09-01T00:00:00+00:00", "stripe_customer": "cus_o", "email": OWNER_EMAIL},
            {"ts": "2026-09-01T00:00:00+00:00", "stripe_customer": "cus_x", "email": OTHER_EMAIL})
    _append(data / "subscriptions.jsonl", *[
        {"ts": "2026-09-01T00:00:00+00:00", "event_type": "customer.subscription.created",
         "stripe_customer": cus, "stripe_sub": sub, "email": email, "status": "active",
         "current_period_end": exp + 20 * 86400, "cancel_at_period_end": False}
        for cus, sub, email in (("cus_o", "sub_o", OWNER_EMAIL), ("cus_x", "sub_x", OTHER_EMAIL))])
    yield from _srv.server_processes(data, stub_calendars=True, stub_stripe=True,
                                     STRIPE_SECRET_KEY="")


def _manifest(tmp_path: Path) -> dict:
    root = tmp_path / "run"
    for rel, data in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return merkle.MerkleTree.from_folder(root).manifest()


def _anchor_folder(base: str, manifest: dict, headers: dict | None = None, **extra) -> str:
    code, body = _srv.post_json(base, "/api/anchor_folder", {**manifest, **extra}, headers)
    assert code == 200, body
    return body["receipt_id"]


def _anchor_single(base: str, hash_hex: str, headers: dict | None = None, **extra) -> str:
    code, body = _srv.anchor(base, {"hash_hex": hash_hex, **extra}, headers)
    assert code == 200, body
    return body["receipt_id"]


def _vo(base: str, rid: str, sha: str, headers: dict | None = None, method: str = "GET"):
    code, raw, h = _srv.request(base, f"/api/verify_output/{rid}?sha256={sha}", method,
                                headers=headers or {})
    return code, (json.loads(raw) if raw else None), h, raw


@pytest.fixture(scope="module")
def run_public(srv, tmp_path_factory):
    m = _manifest(tmp_path_factory.mktemp("run_public"))
    return _anchor_folder(srv, m, OWNER), m


# ── single anchor ──────────────────────────────────────────────────────

def test_single_anchor_match(srv):
    rid = _anchor_single(srv, OUTPUT_SHA)
    code, b, h, _ = _vo(srv, rid, OUTPUT_SHA.upper())   # any hex case; lowercased
    assert code == 200, b
    assert b["match"] is True and b["kind"] == "single"
    assert b["receipt_id"] == rid
    assert b["anchored_hash_sha256"] == OUTPUT_SHA
    assert b["output_sha256_hex"] == OUTPUT_SHA
    assert b["scope"] == SCOPE
    assert b["receipt_url"].endswith(f"/r/{rid}")
    assert b["calendars_ok"] > 0
    assert "btc_pinned_at_utc" in b and b["anchored_at_utc"]
    assert h.get("Access-Control-Allow-Origin") == "*"


def test_single_anchor_mismatch(srv):
    rid = _anchor_single(srv, "ab" * 32)
    code, b, _h, _ = _vo(srv, rid, "cd" * 32)
    assert code == 200, b
    assert b["match"] is False and b["kind"] == "single"
    assert b["anchored_hash_sha256"] == "ab" * 32
    assert "inclusion_proof" not in b


# ── folder / run anchor ────────────────────────────────────────────────

def _climb(leaf_hex: str, proof: list, root_hex: str) -> bool:
    """Recompute the root from a leaf hash with merkle.py's own node hash."""
    cur = bytes.fromhex(leaf_hex)
    for direction, sib in proof:
        s = bytes.fromhex(sib)
        cur = merkle._internal_hash(s, cur) if direction == "L" else merkle._internal_hash(cur, s)
    return cur.hex() == root_hex


def test_run_match_for_a_non_owner_hides_the_path_but_proves_inclusion(srv, run_public):
    rid, manifest = run_public
    code, b, _h, raw = _vo(srv, rid, OUTPUT_SHA)
    assert code == 200, b
    assert b["match"] is True and b["kind"] == "folder"
    assert b["anchored_hash_sha256"] == manifest["root_hex"]
    assert b["leaf_count"] == len(FILES)
    assert b["path_visible"] is False
    assert "path" not in b and "path" not in b["inclusion_proof"]
    assert b"final/answer.txt" not in raw, "a redacted path leaked"
    proof = b["inclusion_proof"]
    assert proof["root_hex"] == b["anchored_hash_sha256"]
    assert proof["file_sha256_hex"] == OUTPUT_SHA
    # The leaf binds the digest to its (withheld) path; we know the path here.
    assert proof["leaf_hex"] == merkle._leaf_hash("final/answer.txt", bytes.fromhex(OUTPUT_SHA)).hex()
    assert _climb(proof["leaf_hex"], proof["proof"], b["anchored_hash_sha256"])
    # Control: a tampered sibling no longer reaches the root.
    bad = [list(s) for s in proof["proof"]]
    bad[0][1] = "00" * 32
    assert not _climb(proof["leaf_hex"], bad, b["anchored_hash_sha256"])


@pytest.mark.parametrize("who", [{}, OWNER, OTHER])
def test_path_visibility_follows_verify_folder_for_the_same_viewer(srv, run_public, who):
    """A public run's paths are hidden exactly when /api/verify_folder hides
    them from this viewer (receipt_export.manifest_view's rule)."""
    rid, _m = run_public
    code, folder = _srv.get_json(srv, f"/api/verify_folder/{rid}", headers=who)
    assert code == 200, folder
    code, b, _h, _ = _vo(srv, rid, OUTPUT_SHA, who)
    assert code == 200, b
    assert b["path_visible"] is (not folder["manifest"].get("paths_redacted", False))


@pytest.fixture(scope="module")
def run_private(srv, tmp_path_factory):
    m = _manifest(tmp_path_factory.mktemp("run_private_owner"))
    return _anchor_folder(srv, m, OWNER, private=True), m


def test_run_match_for_the_owner_names_the_path_and_the_proof_verifies(srv, run_private):
    rid, _m = run_private
    code, b, _h, _ = _vo(srv, rid, OUTPUT_SHA, OWNER)
    assert code == 200, b
    assert b["match"] is True and b["path_visible"] is True
    assert b["path"] == "final/answer.txt" == b["inclusion_proof"]["path"]
    proof = [tuple(s) for s in b["inclusion_proof"]["proof"]]
    assert merkle.MerkleTree.verify_inclusion(
        bytes.fromhex(OUTPUT_SHA), b["path"], proof, bytes.fromhex(b["anchored_hash_sha256"]))
    assert not merkle.MerkleTree.verify_inclusion(
        bytes.fromhex("ee" * 32), b["path"], proof, bytes.fromhex(b["anchored_hash_sha256"]))
    # Same proof the existing inclusion-proof route serves for that path.
    code2, ip = _srv.get_json(srv, f"/api/inclusion_proof?receipt_id={rid}&path=final/answer.txt",
                              headers=OWNER)
    assert code2 == 200, ip
    assert ip["proof"] == b["inclusion_proof"]["proof"]
    assert ip["root_hex"] == b["inclusion_proof"]["root_hex"]


def test_paths_public_run_shows_the_path_to_anyone(srv, tmp_path_factory):
    m = _manifest(tmp_path_factory.mktemp("run_paths_public"))
    rid = _anchor_folder(srv, m, paths_public=True)
    code, b, _h, _ = _vo(srv, rid, OUTPUT_SHA)
    assert code == 200 and b["path_visible"] is True and b["path"] == "final/answer.txt", b


def test_digest_not_in_run(srv, run_public):
    rid, _m = run_public
    code, b, _h, _ = _vo(srv, rid, "ef" * 32)
    assert code == 200, b
    assert b["match"] is False and b["kind"] == "folder"
    assert "inclusion_proof" not in b and "path" not in b


# ── input validation ───────────────────────────────────────────────────

@pytest.mark.parametrize("rid", ["bad.id", "a%20b", "x" * 65, "a'b"])
def test_bad_receipt_id_is_400_naming_the_field(srv, rid):
    code, b, _h, _ = _vo(srv, rid, OUTPUT_SHA)
    assert code == 400, b
    assert "receipt_id" in b["error"]


@pytest.mark.parametrize("query", [
    "sha256=" + "a" * 63,                       # 63 chars
    "sha256=" + "g" * 64,                       # 64 chars, not hex
    "sha256=" + "a" * 65,
    "sha256=",
    "",
    f"sha256={OUTPUT_SHA}&sha256={OUTPUT_SHA}",  # ambiguous
    "sha256=%20" + "a" * 63,
])
def test_bad_sha256_is_400_naming_the_field(srv, run_public, query):
    rid, _m = run_public
    code, raw, _h = _srv.request(srv, f"/api/verify_output/{rid}?{query}")
    b = json.loads(raw)
    assert code == 400, b
    assert "sha256" in b["error"]


# ── privacy ────────────────────────────────────────────────────────────

def test_unknown_receipt_is_404_like_verify(srv):
    code, b, _h, _ = _vo(srv, "no-such-receipt", OUTPUT_SHA)
    assert code == 404
    vcode, vb = _srv.get_json(srv, "/api/verify/no-such-receipt")
    assert vcode == 404 and b == vb


@pytest.mark.parametrize("kind", ["single", "folder"])
def test_private_receipt_is_404_to_everyone_but_its_owner(srv, tmp_path_factory, kind):
    if kind == "single":
        rid = _anchor_single(srv, OUTPUT_SHA, OWNER, private=True)
    else:
        rid = _anchor_folder(srv, _manifest(tmp_path_factory.mktemp("run_private")), OWNER,
                             private=True)
    _c, unknown, unknown_h, _ = _vo(srv, "zzzzzzzzzzzzzzzz", OUTPUT_SHA)
    for who in ({}, OTHER):
        for sha in (OUTPUT_SHA, "ef" * 32):      # match or not: the same answer
            code, b, h, _ = _vo(srv, rid, sha, who)
            assert code == 404, (who, b)
            assert b == {**unknown, "receipt_id": rid}, b
            assert h.get("Content-Length") == unknown_h.get("Content-Length")
    # Control: the owner sees it.
    code, b, _h, _ = _vo(srv, rid, OUTPUT_SHA, OWNER)
    assert code == 200 and b["match"] is True and b["kind"] == kind, b


# ── HEAD parity ────────────────────────────────────────────────────────

def test_head_answers_like_get(srv, run_public):
    rid, _m = run_public
    cases = [(rid, OUTPUT_SHA), (rid, "ef" * 32), (rid, "a" * 63),
             ("bad.id", OUTPUT_SHA), ("no-such-receipt", OUTPUT_SHA)]
    for r, sha in cases:
        gcode, _gb, gh, graw = _vo(srv, r, sha)
        hcode, _hb, hh, hraw = _vo(srv, r, sha, method="HEAD")
        assert hcode == gcode, (r, sha, hcode, gcode)
        assert hraw == b"", "HEAD carried a body"
        assert gh.get("Content-Length") == str(len(graw))
        for name in ("Content-Type", "Content-Length", "Cache-Control",
                     "Access-Control-Allow-Origin", "Strict-Transport-Security",
                     "Content-Security-Policy", "X-Content-Type-Options"):
            assert hh.get(name) == gh.get(name), (r, sha, name)
