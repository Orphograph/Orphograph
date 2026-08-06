#!/usr/bin/env python3
"""renewal.py — batch timestamp renewal (docs/DESIGN_RENEWAL_PATH.md Phase 1).

RFC 4998 says a timestamp preserves evidentiary value across cryptographic
obsolescence only if it is RENEWED before the underlying algorithm weakens.
This module implements the cheapest honest version of that: one RFC-6962 tree
over per-receipt renewal records, ONE anchor for the whole corpus, and a
per-receipt inclusion proof written beside each receipt.

What Phase 1 does and does not do — say both, always:
  * DOES record, in a Bitcoin-anchored artifact, a second- and third-algorithm
    digest (SHA-512, SHA3-256) of an enumerated receipt core, chained to any
    prior renewal, at a stated time.
  * DOES NOT make the system hash-agile: the batch's OUTER commitment is still
    SHA-256, because Bitcoin and OpenTimestamps are SHA-256. The algorithm
    diversity lives in the record's CONTENT, not the transport.
  * DOES NOT repair a break that has already happened, and cannot defend a
    SHA-256 second-preimage break. Only the collision/chosen-prefix case is
    addressable this way.

Hard invariants (each has a regression test):
  1. An issued receipt is NEVER modified — no field added, no `.ots` rewritten.
  2. Renewal artifacts live ONLY under ``receipts/<rid>/renewal/``. A renewal
     `.ots` in the receipt root would be swept up by the non-recursive
     ``*.ots`` globs elsewhere and make receipts that verify today start
     failing.
  3. The record digest EXCLUDES the ``batch`` block — otherwise the inclusion
     proof would have to commit to itself.

Stdlib only, matching the rest of the server.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import merkle

KIND = "orphograph-renewal-v1"
RENEWAL_DIRNAME = "renewal"

# ── receipt_core: an ALLOW-LIST, never the raw receipt.json bytes ───────────
# receipt.json is MUTABLE. upgrade_worker.py rewrites status/btc_pinned_at/
# pinned_count/... as attestations complete, and attach_lineage writes
# `lineage` after the fact. A byte-hash of the file would be voided silently
# by a background job. An allow-list is also forward-safe: a new anchor-time
# field added later is simply not covered by v1 rather than splitting the
# corpus into two incompatible digest regimes.
#
# The present/absent classification is part of the SPEC, not an implementation
# detail: a strict canonical serializer emits different bytes for
# {"sha512_hex": null} and {}, so two verifiers would silently disagree on the
# very common receipt-with-no-ZK-block.
# NOTE (2026-08-05, found by the Stage 3e mutation-vs-commitment sweep):
# `private` and `owner_id` were in this list and MUST NOT BE. They are
# access-control state, not evidentiary anchor-time facts, and
# POST /api/me/receipt/<id>/privacy rewrites both on an issued receipt
# (server/app.py). A customer toggling privacy after a renewal cycle would
# have permanently voided every prior renewal record — on-chain,
# unrepairable, HTTP 200, detected only when a stranger ran the verifier.
# Removed before any renewal record existed in production, so no corpus
# split. The rule this enforces: a commitment may only cover fields no
# endpoint can rewrite.
CORE_ALWAYS = (
    "receipt_id", "created_at", "hash_hex", "sha512_hex", "client_label",
    "source", "attestation", "c2pa_manifest_hash",
    "metadata", "calendars_ok", "calendars_total", "successes", "failures",
)
CORE_IF_PRESENT = ("zk_provenance", "hardware_attestation")


class RenewalError(ValueError):
    """Raised for a malformed receipt or a broken renewal chain."""


def _is_safe_receipt_id(s: object) -> bool:
    """Receipt-id alphabet — the same rule engine._is_receipt_id applies.

    Kept local so this module stays import-light, but the check is NOT
    optional: every path this module builds is rooted at a receipt id, so
    the alphabet check doubles as the traversal guard. Rejects "..", "/",
    absolute paths, NUL and everything else outside [A-Za-z0-9_-].
    """
    return (isinstance(s, str) and 0 < len(s) <= 64
            and all(c.isalnum() or c in ("_", "-") for c in s))


def canonical_bytes(obj: dict) -> bytes:
    """Canonical JSON: UTF-8, sorted keys, no insignificant whitespace.

    ``allow_nan=False`` so a NaN/Infinity can never produce bytes that json
    parsers elsewhere would reject — a canonical form that cannot round-trip
    is not canonical.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def receipt_core(record: dict) -> dict:
    """Extract the enumerated, anchor-time core of a receipt.

    A key missing from CORE_ALWAYS is a MALFORMED receipt — we fail rather
    than substitute a default, because a default would let two different
    on-disk receipts produce the same core digest.
    """
    if not isinstance(record, dict):
        raise RenewalError("receipt must be a JSON object")
    core: dict = {}
    for key in CORE_ALWAYS:
        if key not in record:
            raise RenewalError(
                f"receipt is missing required core field {key!r} — refusing to "
                "substitute a default (that would make two different receipts "
                "hash alike)"
            )
        core[key] = record[key]
    for key in CORE_IF_PRESENT:
        if key in record:
            core[key] = record[key]
    return core


def core_digests(record: dict) -> dict:
    """The three digests of the canonical core. SHA3-256 is construction
    diversity, not just output-length diversity: a break in the SHA-2
    family would not automatically carry to Keccak."""
    raw = canonical_bytes(receipt_core(record))
    return {
        "core_sha256": hashlib.sha256(raw).hexdigest(),
        "core_sha512": hashlib.sha512(raw).hexdigest(),
        "core_sha3_256": hashlib.sha3_256(raw).hexdigest(),
    }


def build_record(record: dict, sequence: int, renewed_at: str,
                 prev_renewal_sha256: str | None = None,
                 manifest_sha256: str | None = None) -> dict:
    """Build a renewal record for one receipt (without its batch block)."""
    if sequence < 1:
        raise RenewalError("sequence starts at 1")
    rid = record.get("receipt_id")
    anchored = record.get("hash_hex")
    if not isinstance(rid, str) or not rid:
        raise RenewalError("receipt has no receipt_id")
    if not isinstance(anchored, str) or len(anchored) != 64:
        raise RenewalError("receipt has no valid hash_hex")
    return {
        "kind": KIND,
        "sequence": sequence,
        "renewed_at": renewed_at,
        "target": {
            "receipt_id": rid,
            # The anchored digest is immutable by definition — it is the value
            # the .ots files commit to. `.ots` BYTES are deliberately not
            # committed to: upgrade_worker rewrites them in place as pending
            # attestations complete, so committing to them would guarantee
            # the renewal record breaks.
            "anchored_digest_hex": anchored,
            "manifest_sha256": manifest_sha256,
            **core_digests(record),
        },
        "prev_renewal_sha256": prev_renewal_sha256,
    }


def record_digest(rr: dict) -> str:
    """SHA-256 over the record EXCLUDING its batch block.

    The batch block carries the inclusion proof into the tree whose leaf IS
    this digest; including it would make the record commit to itself. This is
    also the value `prev_renewal_sha256` points at, so the chain is stable
    whether or not a record was batched.
    """
    return hashlib.sha256(canonical_bytes(
        {k: v for k, v in rr.items() if k != "batch"})).hexdigest()


def leaf_path(rid: str, sequence: int) -> str:
    return f"{RENEWAL_DIRNAME}/{rid}/{sequence:03d}"


def build_batch(records: list[dict]) -> tuple[dict, dict]:
    """Fold per-receipt records into ONE RFC-6962 tree.

    Returns (manifest, proofs_by_receipt_id). The tree is built from DECLARED
    leaves, so it verifies via merkle.MerkleTree.from_manifest — nothing is
    walked on disk, which keeps the `from_folder` reserved-path limitation
    (design §2.2) entirely out of scope here.
    """
    if not records:
        raise RenewalError("cannot build a batch with no records")
    leaves = []
    for rr in records:
        rid = rr["target"]["receipt_id"]
        path = leaf_path(rid, rr["sequence"])
        digest_hex = record_digest(rr)
        leaves.append({
            "path": path,
            "file_sha256_hex": digest_hex,
            "leaf_hex": merkle._leaf_hash(path, bytes.fromhex(digest_hex)).hex(),
            "size_bytes": 0,
        })
    leaves.sort(key=lambda leaf: leaf["path"].encode("utf-8"))
    levels = merkle._build_levels([bytes.fromhex(l["leaf_hex"]) for l in leaves])
    manifest = {
        "algorithm": merkle.ALGORITHM,
        "version": merkle.VERSION,
        "root_hex": levels[-1][0].hex(),
        "leaves": leaves,
    }
    tree = merkle.MerkleTree.from_manifest(manifest)  # re-folds; raises on drift
    proofs = {}
    for rr in records:
        rid = rr["target"]["receipt_id"]
        proofs[rid] = {
            "root_hex": manifest["root_hex"],
            "algorithm": merkle.ALGORITHM,
            "leaf_path": leaf_path(rid, rr["sequence"]),
            "proof": [list(step) for step in
                      tree.inclusion_proof(leaf_path(rid, rr["sequence"]))],
        }
    return manifest, proofs


def verify_inclusion(rr: dict) -> bool:
    """Re-derive the batch root from this record alone."""
    batch = rr.get("batch")
    if not isinstance(batch, dict):
        return False
    running = merkle._leaf_hash(batch["leaf_path"],
                               bytes.fromhex(record_digest(rr)))
    for direction, sibling_hex in batch.get("proof", []):
        sibling = bytes.fromhex(sibling_hex)
        if direction == "L":
            running = merkle._internal_hash(sibling, running)
        elif direction == "R":
            running = merkle._internal_hash(running, sibling)
        else:
            return False
    return running.hex() == batch.get("root_hex")


def renewal_dir(receipts_dir: Path, rid: str) -> Path:
    return receipts_dir / rid / RENEWAL_DIRNAME


def next_sequence(receipts_dir: Path, rid: str) -> tuple[int, str | None]:
    """(next sequence, digest of the latest record) — walks the chain on disk."""
    d = renewal_dir(receipts_dir, rid)
    if not d.is_dir():
        return 1, None
    existing = sorted(p for p in d.glob("*.json"))
    if not existing:
        return 1, None
    latest = json.loads(existing[-1].read_text())
    seq = latest.get("sequence", len(existing))
    # A corrupt or hostile record must not drive the next filename: seq 0
    # would re-emit 001.json over a genuine record, and a non-int would
    # raise a bare ValueError that the caller's except-RenewalError misses.
    if not isinstance(seq, int) or isinstance(seq, bool) or not 1 <= seq < 10**6:
        raise RenewalError(
            f"renewal record {existing[-1].name} has an invalid sequence "
            f"{seq!r} — refusing to derive the next filename from it")
    return seq + 1, record_digest(latest)


def renew_corpus(receipts_dir: Path, anchor_fn, renewed_at: str | None = None,
                 receipt_ids: list[str] | None = None,
                 dry_run: bool = False) -> dict:
    """Renew every receipt in one batch, at the cost of ONE anchor.

    `anchor_fn(hash_hex) -> record` is injected (normally engine.anchor_hash)
    so this module never imports engine and stays testable offline.

    Writes ONLY under receipts/<rid>/renewal/. Never touches receipt.json,
    never writes an .ots into the receipt root.
    """
    receipts_dir = Path(receipts_dir)
    renewed_at = renewed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    ids = receipt_ids if receipt_ids is not None else sorted(
        p.name for p in receipts_dir.iterdir()
        if p.is_dir() and (p / "receipt.json").exists())

    records, skipped = [], []
    for rid in ids:
        rpath = receipts_dir / rid / "receipt.json"
        try:
            record = json.loads(rpath.read_text())
        except (OSError, json.JSONDecodeError) as e:
            skipped.append({"receipt_id": rid, "reason": f"unreadable: {e}"})
            continue
        try:
            # TRUST BOUNDARY: read by directory name, and REFUSE to write by
            # the JSON-declared id. A receipt.json carrying
            # "receipt_id": "../../escaped/x" would otherwise create
            # directories and write outside receipts/ entirely (verified).
            # The directory name is authoritative; the JSON must agree.
            rid_json = record.get("receipt_id")
            if not _is_safe_receipt_id(rid) or rid_json != rid:
                raise RenewalError(
                    f"receipt_id in receipt.json ({rid_json!r}) does not match "
                    f"its directory ({rid!r}), or the id is not a safe path "
                    f"component — refusing to write")
            seq, prev = next_sequence(receipts_dir, rid)
            records.append(build_record(record, seq, renewed_at, prev))
        except RenewalError as e:
            # A malformed receipt is skipped WITH A REASON, never silently
            # dropped and never renewed with substituted defaults.
            skipped.append({"receipt_id": rid, "reason": str(e)})

    if not records:
        return {"renewed": 0, "skipped": skipped, "root_hex": None,
                "receipt_id": None}

    manifest, proofs = build_batch(records)
    if dry_run:
        return {"renewed": 0, "skipped": skipped,
                "root_hex": manifest["root_hex"], "receipt_id": None,
                "would_renew": len(records)}

    anchor = anchor_fn(manifest["root_hex"])
    batch_rid = anchor["receipt_id"]

    written = 0
    for rr in records:
        rid = rr["target"]["receipt_id"]
        rr["batch"] = {**proofs[rid], "anchor_receipt_id": batch_rid}
        d = renewal_dir(receipts_dir, rid)
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"{rr['sequence']:03d}.json"
        # "x" mode: an existing record is evidence; refuse to clobber it
        # rather than silently replacing an anchored artifact.
        with out.open("x") as fh:
            json.dump(rr, fh, indent=2, sort_keys=True)
        try:
            os.chmod(out, 0o600)
        except OSError:
            pass
        written += 1

    batch_dir = receipts_dir / batch_rid
    if batch_dir.is_dir():
        (batch_dir / "renewal_batch.json").write_text(
            json.dumps(manifest, indent=2))

    return {"renewed": written, "skipped": skipped,
            "root_hex": manifest["root_hex"], "receipt_id": batch_rid,
            "renewed_at": renewed_at}


def _main() -> int:
    """CLI: renew the corpus in one batch.

    Default is --dry-run: this writes files and consumes a calendar
    submission, so the destructive form must be typed deliberately.
    """
    import argparse
    parser = argparse.ArgumentParser(
        description="Batch-renew Orphograph receipts (one anchor per cycle)")
    parser.add_argument("--data-dir", default=os.environ.get("ORPHO_DATA_DIR", "."),
                        help="data root containing receipts/")
    parser.add_argument("--commit", action="store_true",
                        help="actually anchor and write (default is dry-run)")
    parser.add_argument("--receipt", action="append", default=None,
                        help="limit to specific receipt id(s); repeatable")
    args = parser.parse_args()

    import sys as _sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import engine  # noqa: PLC0415 — resolved after ORPHO_DATA_DIR is known

    receipts_dir = Path(args.data_dir) / "receipts"
    if not receipts_dir.is_dir():
        print(f"no receipts directory at {receipts_dir}", file=_sys.stderr)
        return 2
    out = renew_corpus(receipts_dir, engine.anchor_hash,
                       receipt_ids=args.receipt, dry_run=not args.commit)
    print(json.dumps(out, indent=2))
    if out["skipped"]:
        print(f"\n{len(out['skipped'])} receipt(s) skipped — see 'skipped' above.",
              file=_sys.stderr)
    if not args.commit:
        print("\nDRY RUN — nothing written, nothing anchored. "
              "Re-run with --commit to renew.", file=_sys.stderr)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
