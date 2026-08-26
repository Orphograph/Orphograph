"""test_edit_lineage_wire_path.py

Draft-two-commits-to-draft-one must survive the REAL request path
(audit 2026-08-25, backlog item C: "Edit lineage DAG — prove parent->child
survives the wire").

Result: CLEAN. The homepage claim — "A revision can now be anchored so that it
commits to the prior draft's receipt, and the receipt page shows the chain —
draft two provably committed to draft one" — is TRUE, and is now proven
through POST /api/anchor_folder rather than asserted.

WHY THIS FILE EXISTS. tests/test_edit_lineage.py is thorough at the engine
level, but its `_anchor_link` helper calls ENGINE.anchor_hash() and writes
manifest.json by hand, "the way the folder path does". Nothing in it ever
touches an HTTP handler. That is the exact shape of the defect this repo has
already shipped once: /api/anchor silently dropped zk_proof for as long as the
field existed while every engine-level test stayed green.

A NOTE ON WHAT IS *NOT* A DEFECT, checked during the audit. engine.anchor_hash
accepts parent_root / parent_receipt_id kwargs on the bare single-hash path,
and /api/anchor never reads them from the payload. That is NOT a dropped field:
those kwargs are documented in the engine as RECORDED-ONLY hints, they are
absent from /docs/api, no SDK or MCP client sends them, and no public claim
depends on them. Binding lineage goes through the folder-manifest path, which
is what the homepage describes and what this file pins. Reporting that as
"lineage dropped on the wire" would have been a fabricated finding.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import _srv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "server"))

import engine as ENGINE          # noqa: E402
import merkle as MERKLE          # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    yield from _srv.server_processes(tmp_path_factory.mktemp("lineage_wire"))


def _post(base: str, path: str, body: dict):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def _receipt(base: str, rid: str) -> dict:
    with urllib.request.urlopen(f"{base}/api/receipt/{rid}", timeout=30) as r:
        return json.loads(r.read())


def _reserved_leaf(parent_root: str) -> dict:
    """The reserved `.orphograph/parent` leaf that COMMITS the parent root
    inside the child's anchored 32 bytes."""
    return {
        "path": ENGINE.RESERVED_PARENT_PATH,
        "file_sha256_hex": parent_root,
        "leaf_hex": hashlib.sha256(
            b"\x00" + ENGINE.RESERVED_PARENT_PATH.encode("utf-8")
            + b"\x00" + bytes.fromhex(parent_root)).hexdigest(),
        "size_bytes": 0,
    }


def _manifest(leaves: list[dict], parent: dict | None = None) -> dict:
    leaves = sorted(leaves, key=lambda leaf: leaf["path"].encode("utf-8"))
    levels = MERKLE._build_levels([bytes.fromhex(leaf["leaf_hex"]) for leaf in leaves])
    m = {"algorithm": MERKLE.ALGORITHM, "version": MERKLE.VERSION,
         "root_hex": levels[-1][0].hex(), "leaves": leaves}
    if parent is not None:
        m["parent"] = parent
    return m


def _folder(name: str, body: str) -> Path:
    d = Path(tempfile.mkdtemp()) / name
    d.mkdir()
    (d / "draft.md").write_text(body)
    return d


@pytest.fixture(scope="module")
def chain(server):
    """Anchor D1, then D2 committing to D1 — both through the HTTP endpoint."""
    m1 = MERKLE.MerkleTree.from_folder(_folder("d1", "draft v1\n")).manifest()
    st1, r1 = _post(server, "/api/anchor_folder", {"manifest": m1, "client_label": "draft one"})
    assert st1 == 200 and r1, st1
    rid1, root1 = r1["receipt_id"], m1["root_hex"]

    leaves = list(MERKLE.MerkleTree.from_folder(_folder("d2", "draft v2 revised\n")).manifest()["leaves"])
    leaves.append(_reserved_leaf(root1))
    m2 = _manifest(leaves, parent={"receipt_id": rid1, "root_hex": root1})
    st2, r2 = _post(server, "/api/anchor_folder", {"manifest": m2, "client_label": "draft two"})
    assert st2 == 200 and r2, st2
    return {"rid1": rid1, "root1": root1, "rid2": r2["receipt_id"], "d2_leaves": leaves}


def test_lineage_survives_to_the_receipt_endpoint(server, chain):
    """THE PIN. This is the hop the engine-level suite never exercises."""
    rec = _receipt(server, chain["rid2"])
    lineage = rec.get("lineage")
    assert lineage, "lineage did not survive to GET /api/receipt/<id>"
    assert lineage["parent_receipt_id"] == chain["rid1"]
    assert lineage["parent_root"] == chain["root1"]
    assert lineage["committed"] is True, (
        "lineage present but not COMMITTED — the homepage says 'provably "
        "committed', which requires the reserved leaf inside the anchored root"
    )


def test_the_parent_is_resolvable_not_just_named(server, chain):
    """A receipt id that points nowhere would still satisfy the assertions
    above. The server must report that it actually found the parent."""
    rec = _receipt(server, chain["rid2"])
    assert rec["lineage"].get("parent_receipt_found") is True
    parent = _receipt(server, chain["rid1"])
    assert parent["hash_hex"] == chain["root1"]


def test_a_contradictory_parent_is_refused(server, chain):
    """CAN-THIS-TEST-FAIL control. If the endpoint accepted any parent block,
    the assertions above would pass for a server that never checks anything.
    A parent receipt that EXISTS but whose anchored hash disagrees with the
    committed parent root must be refused before calendars are spent."""
    leaves = [l for l in chain["d2_leaves"] if l["path"] != ENGINE.RESERVED_PARENT_PATH]
    leaves.append(_reserved_leaf("b" * 64))
    bad = _manifest(leaves, parent={"receipt_id": chain["rid1"], "root_hex": "b" * 64})
    status, _ = _post(server, "/api/anchor_folder", {"manifest": bad})
    assert status == 400, f"a contradictory parent was ACCEPTED with {status}"


def test_a_plain_folder_still_anchors_without_lineage(server):
    """No over-blocking: a manifest with no parent block anchors as before and
    simply carries no lineage."""
    m = MERKLE.MerkleTree.from_folder(_folder("plain", "no lineage here\n")).manifest()
    status, r = _post(server, "/api/anchor_folder", {"manifest": m})
    assert status == 200, status
    assert not _receipt(server, r["receipt_id"]).get("lineage")
