"""Wedge 01 — the manifest records the scoping decision, not just the bytes.

Built 2026-07-26. Two problems, one schema change:

  * DEFECT 2 — the manifest never persisted the exclude patterns, so a folder
    anchored with a custom list could only be verified by a caller who
    independently remembered that list, months later. Correctness depended on
    human memory.
  * WEDGE 01 — a hash set with no scoping record cannot answer the first
    question a hostile reader asks: what did you leave out?

THE BINDING CONSTRAINT: 214 receipts exist in production. `scope` is additive
metadata and VERSION is deliberately NOT bumped, so every one of them must keep
verifying byte-for-byte. The root derives from the leaves alone; scope is
metadata ABOUT a tree, never an input to it. That invariant is what these tests
defend hardest.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.merkle import (  # noqa: E402
    ALGORITHM, DEFAULT_EXCLUDE, VERSION, MerkleTree, build_scope, scope_hex,
)


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "keep.txt").write_text("kept")
    (tmp_path / "sub" / "deep.txt").write_text("nested")
    (tmp_path / "noise.log").write_text("excluded")
    return tmp_path


# ───────────────────────── the invariant ─────────────────────────
def test_scope_does_not_change_the_root(folder):
    """The whole safety case for 214 live receipts."""
    plain = MerkleTree.from_folder(folder, exclude=["*.log"])
    annotated = MerkleTree.from_folder(
        folder, exclude=["*.log"],
        captured_by="an inspector", instruction="basement slab, pre-pour",
        omitted_note="device noise excluded", captured_at="2026-07-26T00:00:00Z",
    )
    assert plain.root_hex() == annotated.root_hex()


def test_version_is_not_bumped():
    """Bumping VERSION would fail from_manifest for every existing receipt."""
    assert VERSION == 1
    assert ALGORITHM == "orphograph-merkle-v1-rfc6962"


def test_pre_scope_manifest_still_verifies(folder):
    """A manifest issued before scope existed must round-trip unchanged."""
    m = MerkleTree.from_folder(folder).manifest()
    legacy = {k: v for k, v in m.items() if k != "scope"}
    rebuilt = MerkleTree.from_manifest(legacy)
    assert rebuilt.root_hex() == m["root_hex"]
    assert rebuilt.scope() is None
    assert rebuilt.exclude_patterns() is None


# ───────────────────────── defect 2 ─────────────────────────
def test_effective_excludes_are_recorded(folder):
    tree = MerkleTree.from_folder(folder, exclude=["*.log", "tmp/*"])
    assert tree.exclude_patterns() == ["*.log", "tmp/*"]
    assert tree.scope()["exclude_source"] == "custom"


def test_default_excludes_are_recorded_too(folder):
    """Defaults must be captured as literal patterns, not left implicit —
    the deny-list can change between capture and verification."""
    tree = MerkleTree.from_folder(folder)
    assert tree.exclude_patterns() == list(DEFAULT_EXCLUDE)
    assert tree.scope()["exclude_source"] == "default"


def test_verifier_can_reproduce_the_root_from_the_manifest_alone(folder):
    """The point of Defect 2's fix: no caller memory required."""
    original = MerkleTree.from_folder(folder, exclude=["*.log"])
    manifest = original.manifest()

    # A verifier who knows NOTHING except the manifest.
    recovered = manifest["scope"]["exclude"]
    reproduced = MerkleTree.from_folder(folder, exclude=recovered)
    assert reproduced.root_hex() == manifest["root_hex"]


def test_forgetting_the_excludes_is_what_used_to_break(folder):
    """Documents the failure the recorded patterns now prevent."""
    anchored = MerkleTree.from_folder(folder, exclude=["*.log"])
    guessed = MerkleTree.from_folder(folder)          # wrong list
    assert guessed.root_hex() != anchored.root_hex()


# ───────────────────────── wedge 01 ─────────────────────────
def test_intake_fields_round_trip(folder):
    tree = MerkleTree.from_folder(
        folder, captured_by="F. Rivera", instruction="pre-pour inspection",
        omitted_note="*.log excluded: device noise",
    )
    got = MerkleTree.from_manifest(tree.manifest()).scope()
    assert got["captured_by"] == "F. Rivera"
    assert got["instruction"] == "pre-pour inspection"
    assert got["omitted_note"] == "*.log excluded: device noise"


def test_empty_intake_fields_are_omitted_not_stored_blank(folder):
    scope = MerkleTree.from_folder(folder).scope()
    assert "captured_by" not in scope
    assert "instruction" not in scope


def test_scope_hex_detects_an_edited_scope(folder):
    m = MerkleTree.from_folder(folder, exclude=["*.log"],
                               omitted_note="device noise excluded").manifest()
    tampered = json.loads(json.dumps(m))
    tampered["scope"]["omitted_note"] = "nothing was omitted"
    with pytest.raises(ValueError, match="scope_hex"):
        MerkleTree.from_manifest(tampered)


def test_scope_hex_detects_an_edited_exclude_list(folder):
    m = MerkleTree.from_folder(folder, exclude=["*.log"]).manifest()
    tampered = json.loads(json.dumps(m))
    tampered["scope"]["exclude"] = []
    with pytest.raises(ValueError, match="scope_hex"):
        MerkleTree.from_manifest(tampered)


def test_scope_without_a_hash_is_accepted(folder):
    """Forward compatibility: a hand-written scope block need not be stamped."""
    m = MerkleTree.from_folder(folder).manifest()
    m["scope"].pop("scope_hex")
    assert MerkleTree.from_manifest(m).scope()["exclude"]


def test_non_object_scope_is_rejected(folder):
    m = MerkleTree.from_folder(folder).manifest()
    m["scope"] = "not an object"
    with pytest.raises(ValueError, match="scope must be an object"):
        MerkleTree.from_manifest(m)


def test_scope_hex_is_order_independent():
    """Key order in transit must not change the checksum."""
    a = build_scope(exclude=["*.log"], captured_by="x", instruction="y")
    reordered = {k: a[k] for k in reversed(list(a))}
    assert scope_hex(reordered) == a["scope_hex"]


def test_scope_hex_excludes_itself():
    s = build_scope(exclude=["*.log"])
    assert scope_hex(s) == s["scope_hex"]        # stable when re-stamped
    s2 = dict(s, scope_hex="deadbeef")
    assert scope_hex(s2) == s["scope_hex"]       # its own value is not an input


def test_scope_hex_is_not_claimed_to_be_tamper_proof():
    """The docstring must keep stating the limit: scope is NOT in the anchor.

    scope_hex catches careless edits inside the manifest. It cannot stop a
    determined party, because the anchored value is still root_hex alone. If
    someone deletes this note, the claim silently inflates.
    """
    src = (ROOT / "server" / "merkle.py").read_text()
    assert "tamper-evident against a determined party" in src, (
        "the scope_hex limitation note was removed or reworded; if the limit "
        "is no longer stated, the claim has silently inflated"
    )
    assert "not claimed here" in src
