#!/usr/bin/env python3
"""generate.py — reproducibly generate the published Orphograph test vectors.

Every expected value in the emitted JSON is produced by EXECUTING the
canonical implementation (server/engine.py and server/merkle.py) — never
written by hand. Regenerating with an unchanged engine yields byte-identical
output; tests/test_published_vectors.py pins that property. If the engine's
behaviour changes deliberately, regenerate in the same commit and treat the
diff as a breaking change for every independent verifier.

Usage:
    python3 docs/test-vectors/generate.py             # write next to this file
    python3 docs/test-vectors/generate.py --out DIR   # write elsewhere

Outputs:
    single-file.json   single-file digest/receipt vectors, incl. negatives
    folder.json        folder Merkle-tree vectors, incl. negatives

Stdlib only. No network. Deterministic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(REPO_ROOT / "server"))

import engine  # noqa: E402
import merkle  # noqa: E402

FORMAT = "orphograph-published-vectors-v1"

# Fixed constants so output is deterministic. created_at is informational in
# a receipt (the evidentiary time bound is the .ots attestation); a fixed
# value keeps regeneration byte-identical.
CREATED_AT = "2026-07-21T00:00:00+00:00"

# The engine's verification checks read the .ots header magic, the version
# and tag bytes, and the 32-byte digest at offset 33. The calendar body
# beyond offset 65 is opaque to the binding checks, so the vectors use a
# clearly-labelled synthetic body instead of a real calendar response.
SYNTHETIC_CALENDAR_BODY = b"synthetic-calendar-body-not-a-real-proof"


def _build_ots_hex(hash_hex: str) -> str:
    """Build a minimal engine-shaped .ots blob for a digest, as hex."""
    return engine._build_ots(bytes.fromhex(hash_hex), SYNTHETIC_CALENDAR_BODY).hex()


def _materialise_receipt(receipts_dir: Path, receipt_id: str,
                         receipt_json: str, ots_files: dict[str, str]) -> None:
    rd = receipts_dir / receipt_id
    if rd.exists():
        shutil.rmtree(rd)
    rd.mkdir(parents=True)
    (rd / "receipt.json").write_text(receipt_json)
    for name, blob_hex in ots_files.items():
        (rd / name).write_bytes(bytes.fromhex(blob_hex))


def _verify_subset(result: dict) -> dict:
    """The engine-output subset the vectors pin (stable across cosmetic keys)."""
    out = {}
    for k in ("found", "error", "hash_hex", "sha512_hex", "calendars_ok",
              "calendars_total", "status", "supplied_hash",
              "supplied_matches_receipt"):
        if k in result:
            out[k] = result[k]
    if "checks" in result:
        out["checks"] = [
            {"file": c["file"], "magic_ok": c["magic_ok"],
             "hash_match": c["hash_match"], "ok": c["ok"]}
            for c in result["checks"]
        ]
    return out


def build_single_file_vectors(receipts_dir: Path) -> dict:
    engine.RECEIPTS_DIR = receipts_dir  # engine reads this module global

    vectors = []

    def minimal_receipt(receipt_id: str, hash_hex: str,
                        sha512_hex: str | None = None) -> str:
        """The minimal receipt.json shape a verifier must accept: hash_hex is
        the only field verification requires; created_at and status are the
        conventional companions surfaced by the engine."""
        rec: dict = {
            "receipt_id": receipt_id,
            "created_at": CREATED_AT,
            "hash_hex": hash_hex,
        }
        if sha512_hex is not None:
            rec["sha512_hex"] = sha512_hex
        rec["status"] = "pending"
        return json.dumps(rec, indent=2)

    def run(receipt_id: str, receipt_json: str | None,
            ots_files: dict[str, str], operation: str,
            supplied_hash: str | None = None) -> dict:
        if receipt_json is not None:
            _materialise_receipt(receipts_dir, receipt_id, receipt_json, ots_files)
        if operation == "verify_receipt":
            result = engine.verify_receipt(receipt_id)
        elif operation == "verify_hash_against_receipt":
            result = engine.verify_hash_against_receipt(receipt_id, supplied_hash)
        else:
            raise ValueError(operation)
        return _verify_subset(result)

    # ---- positive vectors --------------------------------------------------
    # sf01: the empty byte string. sha256(b"") is a fixed point of the
    # standard; the canonical lowercase digest is anchored and matches.
    content = b""
    h256 = hashlib.sha256(content).hexdigest()
    h512 = hashlib.sha512(content).hexdigest()
    rid = "vec-sf01-empty"
    rj = minimal_receipt(rid, h256, h512)
    ots = {"a.ots": _build_ots_hex(h256)}
    vectors.append({
        "id": "sf01_empty_file_valid",
        "kind": "single_file",
        "description": "Empty byte string; canonical lowercase digest; one "
                       "well-formed .ots; supplied hash matches. The supplied "
                       "side is stripped+lowercased before comparison, so an "
                       "UPPERCASE *supplied* hash still matches this "
                       "lowercase-stored receipt.",
        "content_hex": content.hex(),
        "expected_sha256_hex": h256,
        "expected_sha512_hex": h512,
        "receipt_id": rid,
        "receipt_json": rj,
        "ots_files": ots,
        "operations": [
            {"operation": "verify_receipt",
             "expect": run(rid, rj, ots, "verify_receipt")},
            {"operation": "verify_hash_against_receipt",
             "supplied_hash": h256,
             "expect": run(rid, None, {}, "verify_hash_against_receipt",
                           supplied_hash=h256)},
            {"operation": "verify_hash_against_receipt",
             "supplied_hash": "  " + h256.upper() + "  ",
             "expect": run(rid, None, {}, "verify_hash_against_receipt",
                           supplied_hash="  " + h256.upper() + "  ")},
        ],
    })

    # sf02: b"abc" — the FIPS 180-4 example message, so the digest can be
    # cross-checked against the standard itself.
    content = b"abc"
    h256 = hashlib.sha256(content).hexdigest()
    h512 = hashlib.sha512(content).hexdigest()
    rid = "vec-sf02-abc"
    rj = minimal_receipt(rid, h256, h512)
    ots = {"a.ots": _build_ots_hex(h256), "b.ots": _build_ots_hex(h256)}
    vectors.append({
        "id": "sf02_abc_valid",
        "kind": "single_file",
        "description": "The three bytes 'abc' (the FIPS 180-4 example "
                       "message). Two well-formed .ots files; supplied hash "
                       "matches.",
        "content_utf8": "abc",
        "content_hex": content.hex(),
        "expected_sha256_hex": h256,
        "expected_sha512_hex": h512,
        "receipt_id": rid,
        "receipt_json": rj,
        "ots_files": ots,
        "operations": [
            {"operation": "verify_receipt",
             "expect": run(rid, rj, ots, "verify_receipt")},
            {"operation": "verify_hash_against_receipt",
             "supplied_hash": h256,
             "expect": run(rid, None, {}, "verify_hash_against_receipt",
                           supplied_hash=h256)},
        ],
    })

    # ---- negative vectors --------------------------------------------------
    # sf03: stored hash UPPERCASED. Strict canonical comparison: only the
    # SUPPLIED side is lowercased; the stored side is compared verbatim, so
    # no supplied digest can ever match an uppercase-stored receipt. The
    # service's anchoring path writes lowercase only, so an uppercase stored
    # hash is by definition an out-of-band-edited receipt — a verifier that
    # lowercases BOTH sides (the historical verifier-js drift, audit D1)
    # would wrongly accept it. This vector encodes the CORRECT behaviour:
    # strict verification MUST fail.
    content = b"abc"
    h256 = hashlib.sha256(content).hexdigest()
    rid = "vec-sf03-upper"
    rj = minimal_receipt(rid, h256.upper())
    ots = {"a.ots": _build_ots_hex(h256)}
    vectors.append({
        "id": "sf03_negative_stored_hash_uppercase",
        "kind": "single_file_negative",
        "description": "Same receipt as sf02 but with hash_hex UPPERCASED in "
                       "the stored receipt JSON. The .ots byte comparison "
                       "still passes (hex decoding is case-insensitive), but "
                       "the strict string comparison against the stored hash "
                       "MUST fail for every supplied digest — lowercasing the "
                       "stored side to force a match is a verifier bug "
                       "(historical verifier-js drift D1).",
        "content_utf8": "abc",
        "content_hex": content.hex(),
        "expected_sha256_hex": h256,
        "receipt_id": rid,
        "receipt_json": rj,
        "ots_files": ots,
        "operations": [
            {"operation": "verify_receipt",
             "expect": run(rid, rj, ots, "verify_receipt")},
            {"operation": "verify_hash_against_receipt",
             "supplied_hash": h256,
             "expect": run(rid, None, {}, "verify_hash_against_receipt",
                           supplied_hash=h256)},
        ],
    })
    assert vectors[-1]["operations"][1]["expect"]["supplied_matches_receipt"] is False

    # sf04: one byte of content flipped ('abc' -> 'abd'). The recomputed
    # digest differs, so the file-to-receipt binding fails.
    content = b"abc"
    flipped = b"abd"
    h256 = hashlib.sha256(content).hexdigest()
    h256_flipped = hashlib.sha256(flipped).hexdigest()
    rid = "vec-sf04-flip"
    rj = minimal_receipt(rid, h256)
    ots = {"a.ots": _build_ots_hex(h256)}
    vectors.append({
        "id": "sf04_negative_one_byte_flipped_content",
        "kind": "single_file_negative",
        "description": "Receipt anchors sha256('abc'); the candidate file is "
                       "'abd' (last byte flipped). The recomputed digest "
                       "differs, so verification MUST fail.",
        "content_utf8": "abc",
        "flipped_content_utf8": "abd",
        "flipped_content_hex": flipped.hex(),
        "expected_sha256_hex": h256,
        "flipped_sha256_hex": h256_flipped,
        "receipt_id": rid,
        "receipt_json": rj,
        "ots_files": ots,
        "operations": [
            {"operation": "verify_hash_against_receipt",
             "supplied_hash": h256_flipped,
             "expect": run(rid, rj, ots, "verify_hash_against_receipt",
                           supplied_hash=h256_flipped)},
        ],
    })
    assert vectors[-1]["operations"][0]["expect"]["supplied_matches_receipt"] is False

    # sf05: truncated stored hash (63 chars). Not a mismatch — a corrupt
    # receipt: found=false, error="corrupt receipt".
    content = b"abc"
    h256 = hashlib.sha256(content).hexdigest()
    rid = "vec-sf05-trunc"
    rj = minimal_receipt(rid, h256[:63])
    ots = {"a.ots": _build_ots_hex(h256)}
    vectors.append({
        "id": "sf05_negative_truncated_stored_hash",
        "kind": "single_file_negative",
        "description": "Stored hash_hex truncated to 63 characters. A "
                       "wrong-length stored hash renders the receipt CORRUPT "
                       "(found: false), not merely mismatched.",
        "content_utf8": "abc",
        "content_hex": content.hex(),
        "expected_sha256_hex": h256,
        "truncated_hash_hex": h256[:63],
        "receipt_id": rid,
        "receipt_json": rj,
        "ots_files": ots,
        "operations": [
            {"operation": "verify_receipt",
             "expect": run(rid, rj, ots, "verify_receipt")},
        ],
    })
    assert vectors[-1]["operations"][0]["expect"] == {
        "found": False, "error": "corrupt receipt"}

    return {
        "format": FORMAT,
        "suite": "single-file",
        "generated_by": "docs/test-vectors/generate.py",
        "canon": "server/engine.py (verify_receipt, verify_hash_against_receipt)",
        "spec": "docs/VERIFIER_SPEC.md",
        "created_at_constant": CREATED_AT,
        "ots_header_magic_hex": engine.OTS_HEADER_MAGIC.hex(),
        "ots_note": "ots_files are hex-encoded blobs in the engine's layout: "
                    "31-byte magic, version 0x01, tag 0x08 (SHA-256), the "
                    "32-byte digest at offset 33, then a SYNTHETIC body — the "
                    "body is opaque to the binding checks these vectors pin "
                    "and is NOT a real calendar proof.",
        "vector_count": len(vectors),
        "vectors": vectors,
    }


def build_folder_vectors() -> dict:
    # Three files, one in a subdirectory, one binary — exercising POSIX-path
    # ordering, an internal node, and RFC 6962 odd-node promotion (three
    # leaves -> level 1 holds [internal(l0,l1), promoted l2]).
    files = {
        "alpha.txt": b"the first file\n",
        "beta/gamma.txt": b"the second file\n",
        "delta.bin": bytes([0x00, 0x01, 0x02, 0xFF]),
    }

    tmp = Path(tempfile.mkdtemp(prefix="orpho-vectors-"))
    try:
        for rel, content in files.items():
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)

        tree = merkle.MerkleTree.from_folder(tmp, exclude=[])
        manifest = tree.manifest()
        root_hex = tree.root_hex()

        # Round-trip check: the manifest alone must rebuild to the same root.
        assert merkle.MerkleTree.from_manifest(manifest).root_hex() == root_hex

        leaves = manifest["leaves"]
        assert [m["path"] for m in leaves] == sorted(
            files, key=lambda s: s.encode("utf-8"))

        def proof_for(path: str) -> list[list[str]]:
            return [[d, s] for d, s in tree.inclusion_proof(path)]

        def included(content: bytes, rel_path: str, proof, root: str) -> bool:
            return merkle.MerkleTree.verify_inclusion(
                hashlib.sha256(content).digest(), rel_path,
                [tuple(step) for step in proof], bytes.fromhex(root))

        vectors = []

        # f01: inclusion proof for the middle leaf — two steps, one sibling
        # on each side (L then R).
        path = "beta/gamma.txt"
        proof = proof_for(path)
        ok = included(files[path], path, proof, root_hex)
        assert ok is True
        vectors.append({
            "id": "f01_inclusion_middle_leaf",
            "kind": "merkle_inclusion",
            "description": "Inclusion proof for beta/gamma.txt (leaf index "
                           "1 of 3): sibling L at the leaf level, sibling R "
                           "(the promoted third leaf) at level 1.",
            "rel_path": path,
            "content_hex": files[path].hex(),
            "file_sha256_hex": hashlib.sha256(files[path]).hexdigest(),
            "proof": proof,
            "root_hex": root_hex,
            "expect": {"included": ok},
        })

        # f02: inclusion proof for the promoted (lone-last) leaf — the
        # promotion level contributes NO step, so the proof is one step.
        path = "delta.bin"
        proof = proof_for(path)
        ok = included(files[path], path, proof, root_hex)
        assert ok is True and len(proof) == 1
        vectors.append({
            "id": "f02_inclusion_promoted_leaf",
            "kind": "merkle_inclusion",
            "description": "Inclusion proof for delta.bin (leaf index 2 of "
                           "3, the RFC 6962 promoted node). The promoted "
                           "level contributes no proof step, so the proof is "
                           "a single L step — shorter than ceil(log2(3)).",
            "rel_path": path,
            "content_hex": files[path].hex(),
            "file_sha256_hex": hashlib.sha256(files[path]).hexdigest(),
            "proof": proof,
            "root_hex": root_hex,
            "expect": {"included": ok},
        })

        # f03 negative: one byte of leaf content flipped — the recomputed
        # leaf differs, the walk reconstructs a different root.
        path = "beta/gamma.txt"
        tampered = bytearray(files[path])
        tampered[0] ^= 0x01
        tampered = bytes(tampered)
        proof = proof_for(path)
        ok = included(tampered, path, proof, root_hex)
        assert ok is False
        vectors.append({
            "id": "f03_negative_tampered_leaf_content",
            "kind": "merkle_inclusion_negative",
            "description": "Same path and proof as f01, but the first byte "
                           "of the file content is flipped. verify_inclusion "
                           "MUST return false.",
            "rel_path": path,
            "content_hex": tampered.hex(),
            "original_content_hex": files[path].hex(),
            "file_sha256_hex": hashlib.sha256(tampered).hexdigest(),
            "proof": proof,
            "root_hex": root_hex,
            "expect": {"included": ok},
        })

        # f04 negative: correct bytes under the WRONG path — the path is
        # bound into the leaf, so renaming breaks inclusion.
        path = "beta/gamma.txt"
        proof = proof_for(path)
        ok = included(files[path], "beta/renamed.txt", proof, root_hex)
        assert ok is False
        vectors.append({
            "id": "f04_negative_renamed_path",
            "kind": "merkle_inclusion_negative",
            "description": "Byte-identical content presented under the path "
                           "beta/renamed.txt with f01's proof. The relative "
                           "path is bound into the leaf "
                           "(SHA-256(0x00 || path || 0x00 || file_sha256)), "
                           "so verify_inclusion MUST return false.",
            "rel_path": "beta/renamed.txt",
            "committed_path": path,
            "content_hex": files[path].hex(),
            "file_sha256_hex": hashlib.sha256(files[path]).hexdigest(),
            "proof": proof,
            "root_hex": root_hex,
            "expect": {"included": ok},
        })

        return {
            "format": FORMAT,
            "suite": "folder",
            "generated_by": "docs/test-vectors/generate.py",
            "canon": "server/merkle.py (MerkleTree.from_folder, "
                     "inclusion_proof, verify_inclusion)",
            "spec": "docs/VERIFIER_SPEC.md §4",
            "algorithm": merkle.ALGORITHM,
            "leaf_rule": "SHA-256(0x00 || utf8(rel_path) || 0x00 || file_sha256)",
            "internal_rule": "SHA-256(0x01 || left || right)",
            "odd_level_rule": "lone last node PROMOTED unchanged (RFC 6962; "
                              "never duplicated)",
            "ordering_rule": "leaves sorted by UTF-8 byte order of the POSIX "
                             "relative path",
            "folder_files": {rel: content.hex() for rel, content in files.items()},
            "manifest": manifest,
            "root_hex": root_hex,
            "vector_count": len(vectors),
            "vectors": vectors,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the published Orphograph test vectors")
    ap.add_argument("--out", default=str(HERE), help="output directory (default: alongside this script)")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    receipts_tmp = Path(tempfile.mkdtemp(prefix="orpho-vector-receipts-"))
    try:
        single = build_single_file_vectors(receipts_tmp / "receipts")
    finally:
        shutil.rmtree(receipts_tmp, ignore_errors=True)
    folder = build_folder_vectors()

    for name, payload in (("single-file.json", single), ("folder.json", folder)):
        (out_dir / name).write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {out_dir / name} ({payload['vector_count']} vectors)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
