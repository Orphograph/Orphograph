from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import merkle
from merkle import MerkleTree


def _write(root: Path, rel: str, content: bytes) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def _make_folder(root: Path, n: int) -> list[str]:
    """Populate ``root`` with ``n`` files of distinct content. Returns rel paths."""
    paths: list[str] = []
    for i in range(n):
        rel = f"file_{i:03d}.bin"
        _write(root, rel, f"content-of-file-{i}".encode())
        paths.append(rel)
    return paths


# -------------------------------------------------------------- reproducibility


def test_reproducible_root_across_runs(tmp_path):
    _make_folder(tmp_path, 10)
    t1 = MerkleTree.from_folder(tmp_path)
    t2 = MerkleTree.from_folder(tmp_path)
    assert t1.root() == t2.root()
    assert t1.root_hex() == t2.root_hex()
    assert len(t1.root()) == 32


# -------------------------------------------------------------- inclusion proofs


def test_inclusion_proof_verifies_every_leaf(tmp_path):
    paths = _make_folder(tmp_path, 10)
    tree = MerkleTree.from_folder(tmp_path)
    root = tree.root()
    for rel in paths:
        file_hash = hashlib.sha256((tmp_path / rel).read_bytes()).digest()
        proof = tree.inclusion_proof(rel)
        assert MerkleTree.verify_inclusion(file_hash, rel, proof, root) is True


# -------------------------------------------------------------- bit-flip sensitivity


def test_bitflip_changes_root(tmp_path):
    _make_folder(tmp_path, 6)
    original_root = MerkleTree.from_folder(tmp_path).root()

    victim = tmp_path / "file_003.bin"
    data = bytearray(victim.read_bytes())
    data[0] ^= 0x01
    victim.write_bytes(bytes(data))

    new_root = MerkleTree.from_folder(tmp_path).root()
    assert new_root != original_root


# -------------------------------------------------------------- rename sensitivity


def test_rename_changes_root(tmp_path):
    _make_folder(tmp_path, 4)
    original_root = MerkleTree.from_folder(tmp_path).root()
    (tmp_path / "file_002.bin").rename(tmp_path / "renamed.bin")
    new_root = MerkleTree.from_folder(tmp_path).root()
    assert new_root != original_root


# -------------------------------------------------------------- odd-count round-trip


@pytest.mark.parametrize("n", [1, 3, 5, 7, 9])
def test_odd_file_counts_round_trip(tmp_path, n):
    paths = _make_folder(tmp_path, n)
    tree = MerkleTree.from_folder(tmp_path)
    root = tree.root()
    # Every leaf must produce a verifying proof under odd-count promotion.
    for rel in paths:
        file_hash = hashlib.sha256((tmp_path / rel).read_bytes()).digest()
        proof = tree.inclusion_proof(rel)
        assert MerkleTree.verify_inclusion(file_hash, rel, proof, root) is True


# -------------------------------------------------------------- manifest round-trip


def test_manifest_round_trip(tmp_path):
    _make_folder(tmp_path, 7)
    tree = MerkleTree.from_folder(tmp_path)
    m = tree.manifest()
    # Survives a JSON encode/decode cycle.
    m_again = json.loads(json.dumps(m))
    rebuilt = MerkleTree.from_manifest(m_again)
    assert rebuilt.root() == tree.root()
    assert rebuilt.root_hex() == tree.root_hex()
    assert rebuilt.manifest() == tree.manifest()


def test_manifest_has_expected_shape(tmp_path):
    _make_folder(tmp_path, 2)
    tree = MerkleTree.from_folder(tmp_path)
    m = tree.manifest()
    assert m["algorithm"] == "orphograph-merkle-v1-rfc6962"
    assert m["version"] == 1
    assert len(m["root_hex"]) == 64
    assert isinstance(m["leaves"], list) and len(m["leaves"]) == 2
    for leaf in m["leaves"]:
        assert set(leaf.keys()) == {"path", "file_sha256_hex", "leaf_hex", "size_bytes"}
        assert len(leaf["file_sha256_hex"]) == 64
        assert len(leaf["leaf_hex"]) == 64


# -------------------------------------------------------------- tamper detection


def test_verification_rejects_tampered_file_hash(tmp_path):
    paths = _make_folder(tmp_path, 5)
    tree = MerkleTree.from_folder(tmp_path)
    root = tree.root()
    rel = paths[2]
    proof = tree.inclusion_proof(rel)
    real_hash = hashlib.sha256((tmp_path / rel).read_bytes()).digest()
    forged = bytearray(real_hash)
    forged[0] ^= 0xFF
    assert MerkleTree.verify_inclusion(bytes(forged), rel, proof, root) is False


def test_verification_rejects_wrong_direction(tmp_path):
    paths = _make_folder(tmp_path, 8)
    tree = MerkleTree.from_folder(tmp_path)
    root = tree.root()
    # Pick a file whose proof has at least one step.
    rel = paths[0]
    proof = tree.inclusion_proof(rel)
    assert len(proof) >= 1
    flipped = [
        (("R" if d == "L" else "L"), sib) for (d, sib) in proof
    ]
    file_hash = hashlib.sha256((tmp_path / rel).read_bytes()).digest()
    assert MerkleTree.verify_inclusion(file_hash, rel, flipped, root) is False


# -------------------------------------------------------------- exclusion list


def test_default_exclude_filters_ds_store_and_git_head(tmp_path):
    _write(tmp_path, "evidence.txt", b"real content")
    _write(tmp_path, ".DS_Store", b"mac junk")
    _write(tmp_path, ".git/HEAD", b"ref: refs/heads/main\n")
    _write(tmp_path, "node_modules/pkg/index.js", b"console.log(1)")
    _write(tmp_path, "__pycache__/x.cpython-311.pyc", b"\x00\x00")
    _write(tmp_path, "scratch.tmp", b"temp")

    tree = MerkleTree.from_folder(tmp_path)
    paths = [leaf["path"] for leaf in tree.manifest()["leaves"]]
    assert paths == ["evidence.txt"]


def test_custom_exclude_overrides_defaults(tmp_path):
    _write(tmp_path, "keep.txt", b"a")
    _write(tmp_path, "drop.log", b"b")
    tree = MerkleTree.from_folder(tmp_path, exclude=["*.log"])
    paths = [leaf["path"] for leaf in tree.manifest()["leaves"]]
    assert paths == ["keep.txt"]


# -------------------------------------------------------------- single-file tree


def test_single_file_root_equals_leaf_hash(tmp_path):
    _write(tmp_path, "only.bin", b"the one file")
    tree = MerkleTree.from_folder(tmp_path)
    m = tree.manifest()
    assert len(m["leaves"]) == 1
    assert m["root_hex"] == m["leaves"][0]["leaf_hex"]
    # Inclusion proof for a single-leaf tree is empty and still verifies.
    proof = tree.inclusion_proof("only.bin")
    assert proof == []
    file_hash = hashlib.sha256(b"the one file").digest()
    assert MerkleTree.verify_inclusion(file_hash, "only.bin", proof, tree.root()) is True


# -------------------------------------------------------------- edge cases


def test_empty_folder_raises(tmp_path):
    with pytest.raises(ValueError, match="Empty folders"):
        MerkleTree.from_folder(tmp_path)


def test_not_a_directory_raises(tmp_path):
    f = tmp_path / "single.txt"
    f.write_bytes(b"x")
    with pytest.raises(ValueError):
        MerkleTree.from_folder(f)


def test_symlinks_are_skipped(tmp_path):
    _write(tmp_path, "real.txt", b"real")
    target = tmp_path / "target.txt"
    target.write_bytes(b"target")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    tree = MerkleTree.from_folder(tmp_path)
    paths = sorted(leaf["path"] for leaf in tree.manifest()["leaves"])
    # ``link.txt`` is a symlink and must be skipped; ``target.txt`` and
    # ``real.txt`` are real files and must be present.
    assert "link.txt" not in paths
    assert "real.txt" in paths
    assert "target.txt" in paths


def test_subdirectory_paths_use_forward_slash(tmp_path):
    _write(tmp_path, "sub/a.txt", b"a")
    _write(tmp_path, "sub/deeper/b.txt", b"b")
    tree = MerkleTree.from_folder(tmp_path)
    paths = sorted(leaf["path"] for leaf in tree.manifest()["leaves"])
    assert paths == ["sub/a.txt", "sub/deeper/b.txt"]
    for p in paths:
        assert "\\" not in p


def test_manifest_rejects_wrong_algorithm():
    with pytest.raises(ValueError):
        MerkleTree.from_manifest({
            "algorithm": "some-other-scheme",
            "version": 1,
            "root_hex": "0" * 64,
            "leaves": [],
        })


def test_manifest_rejects_root_mismatch(tmp_path):
    _make_folder(tmp_path, 3)
    tree = MerkleTree.from_folder(tmp_path)
    m = tree.manifest()
    # Corrupt the recorded root.
    m["root_hex"] = "0" * 64
    with pytest.raises(ValueError, match="root_hex"):
        MerkleTree.from_manifest(m)


def test_inclusion_proof_unknown_path_raises(tmp_path):
    _make_folder(tmp_path, 3)
    tree = MerkleTree.from_folder(tmp_path)
    with pytest.raises(ValueError):
        tree.inclusion_proof("no/such/file.bin")


def test_known_vectors_match_rfc6962_domain_separation():
    """Spot-check the leaf and internal hash domain separation by hand."""
    file_hash = hashlib.sha256(b"hello").digest()
    rel_path = "a.txt"
    expected_leaf = hashlib.sha256(
        b"\x00" + rel_path.encode("utf-8") + b"\x00" + file_hash
    ).digest()
    assert merkle._leaf_hash(rel_path, file_hash) == expected_leaf

    left = b"\x11" * 32
    right = b"\x22" * 32
    expected_internal = hashlib.sha256(b"\x01" + left + right).digest()
    assert merkle._internal_hash(left, right) == expected_internal
