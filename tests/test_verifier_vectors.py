"""test_verifier_vectors.py — conformance harness for the verifier test vectors.

Replays every vector in tests/vectors/verifier_vectors.json against the
CANONICAL implementation (server/engine.py + server/merkle.py). The engine is
canon: if this test fails, either the engine's behaviour changed (a breaking
change for every independent verifier — regenerate the vectors deliberately
and bump the format tag) or the vector file was edited by hand (don't).

The same JSON file is the conformance target for the independent verifiers
(sdk-python, sdk-node, verifier-js). See docs/VERIFIER_SPEC.md for the
normative algorithm and the mapping of vector kinds to verifier operations.

Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_verifier_vectors.py -q
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

import engine
import merkle

VECTORS_PATH = Path(__file__).resolve().parent / "vectors" / "verifier_vectors.json"
SUITE = json.loads(VECTORS_PATH.read_text())

assert SUITE["format"] == "orphograph-verifier-vectors-v1"

RECEIPT_VECTORS = [v for v in SUITE["vectors"] if v["kind"] == "receipt"]
MERKLE_VECTORS = [v for v in SUITE["vectors"] if v["kind"] == "merkle_inclusion"]


def test_vector_suite_is_complete():
    assert SUITE["vector_count"] == len(SUITE["vectors"])
    assert len(SUITE["vectors"]) == len(RECEIPT_VECTORS) + len(MERKLE_VECTORS)
    assert len(SUITE["vectors"]) >= 10
    ids = [v["id"] for v in SUITE["vectors"]]
    assert len(ids) == len(set(ids)), "duplicate vector ids"


def test_ots_magic_matches_engine():
    assert SUITE["ots_header_magic_hex"] == engine.OTS_HEADER_MAGIC.hex()


@pytest.fixture()
def isolated_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "RECEIPTS_DIR", tmp_path / "receipts")
    yield tmp_path / "receipts"


def _materialise(vector, receipts_dir: Path) -> None:
    """Write the vector's receipt fixture (receipt.json + .ots files) to disk."""
    d = receipts_dir / vector["receipt_id"]
    d.mkdir(parents=True, exist_ok=True)
    if vector["receipt_json"] is not None:
        (d / "receipt.json").write_text(vector["receipt_json"])
    for name, hexdata in vector["ots_files"].items():
        (d / name).write_bytes(bytes.fromhex(hexdata))


@pytest.mark.parametrize("vector", RECEIPT_VECTORS, ids=lambda v: v["id"])
def test_receipt_vector(vector, isolated_receipts):
    _materialise(vector, isolated_receipts)
    if vector["operation"] == "verify_hash_against_receipt":
        result = engine.verify_hash_against_receipt(
            vector["receipt_id"], vector["supplied_hash"]
        )
    else:
        result = engine.verify_receipt(vector["receipt_id"])
    for key, expected in vector["expect"].items():
        assert key in result, f"{vector['id']}: engine result missing key {key!r}"
        assert result[key] == expected, (
            f"{vector['id']}: engine drift on {key!r}: "
            f"expected {expected!r}, got {result[key]!r}"
        )


@pytest.mark.parametrize("vector", MERKLE_VECTORS, ids=lambda v: v["id"])
def test_merkle_inclusion_vector(vector):
    file_bytes = base64.b64decode(vector["file_b64"])
    file_hash = hashlib.sha256(file_bytes).digest()
    assert file_hash.hex() == vector["file_sha256_hex"], "vector self-consistency"
    try:
        root = bytes.fromhex(vector["root_hex"])
    except ValueError:
        root = vector["root_hex"].encode()
    proof = [tuple(step) for step in vector["proof"]]
    included = merkle.MerkleTree.verify_inclusion(
        file_hash, vector["rel_path"], proof, root
    )
    assert included == vector["expect"]["included"], (
        f"{vector['id']}: expected included={vector['expect']['included']}, "
        f"got {included}"
    )


def test_merkle_manifest_roundtrip():
    """The manifest embedded in the merkle vectors must reconstruct to the
    same root under the canonical implementation."""
    manifest = MERKLE_VECTORS[0]["manifest"]
    tree = merkle.MerkleTree.from_manifest(manifest)
    assert tree.root_hex() == manifest["root_hex"]
