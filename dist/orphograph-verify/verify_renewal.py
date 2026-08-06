#!/usr/bin/env python3
"""verify_renewal.py — check an Orphograph renewal record, fully offline.

A renewal record says: "at this time, this enumerated core of receipt R had
these SHA-256 / SHA-512 / SHA3-256 digests, chained to any prior renewal, and
that statement was folded into a Bitcoin-anchored batch root."

This tool re-derives every one of those claims from the files you hold. It
never contacts Orphograph.

What a PASS means, exactly:
  * the record's digests match the receipt you have, recomputed here;
  * the record chains correctly to the previous renewal record, if any;
  * the record's inclusion proof re-derives the batch root it claims.

What a PASS does NOT mean:
  * it does not prove the batch root reached Bitcoin — check the batch
    anchor's own .ots with verify.py for that;
  * renewal cannot repair a break that already happened. It preserves
    evidence created BEFORE a weakness became exploitable; it is not a
    retroactive fix;
  * the outer commitment is still SHA-256. The SHA-512/SHA3-256 digests are
    content, not transport. Do not read this as hash-agility.

Usage:
    python3 verify_renewal.py <receipt.json> [--renewal-dir DIR]
Exit: 0 = every performed check passed · 1 = a check failed · 2 = bad input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

KIND = "orphograph-renewal-v1"
LEAF_PREFIX = b"\x00"
INTERNAL_PREFIX = b"\x01"

CORE_ALWAYS = (
    "receipt_id", "created_at", "hash_hex", "sha512_hex", "client_label",
    "source", "private", "owner_id", "attestation", "c2pa_manifest_hash",
    "metadata", "calendars_ok", "calendars_total", "successes", "failures",
)
CORE_IF_PRESENT = ("zk_provenance", "hardware_attestation")


def canonical_bytes(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def receipt_core(record: dict) -> dict:
    core = {}
    for key in CORE_ALWAYS:
        if key not in record:
            raise ValueError(f"receipt missing required core field {key!r}")
        core[key] = record[key]
    for key in CORE_IF_PRESENT:
        if key in record:
            core[key] = record[key]
    return core


def record_digest(rr: dict) -> str:
    return hashlib.sha256(canonical_bytes(
        {k: v for k, v in rr.items() if k != "batch"})).hexdigest()


def _leaf_hash(path: str, digest: bytes) -> bytes:
    return hashlib.sha256(LEAF_PREFIX + path.encode("utf-8") + b"\x00" + digest).digest()


def _internal(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(INTERNAL_PREFIX + left + right).digest()


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify Orphograph renewal records")
    ap.add_argument("receipt", help="path to receipt.json")
    ap.add_argument("--renewal-dir", default=None,
                    help="defaults to <receipt dir>/renewal")
    args = ap.parse_args()

    rpath = Path(args.receipt).expanduser().resolve()
    try:
        record = json.loads(rpath.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"could not read receipt: {e}", file=sys.stderr)
        return 2
    rdir = Path(args.renewal_dir) if args.renewal_dir else rpath.parent / "renewal"
    if not rdir.is_dir():
        print(f"no renewal directory at {rdir} — this receipt has not been renewed.",
              file=sys.stderr)
        print("That is not a failure: renewal is additive and most receipts "
              "predate it.", file=sys.stderr)
        return 2

    files = sorted(rdir.glob("*.json"))
    if not files:
        print(f"no renewal records in {rdir}", file=sys.stderr)
        return 2

    try:
        core = receipt_core(record)
    except ValueError as e:
        print(f"FAIL  {e}")
        return 1
    raw = canonical_bytes(core)
    actual = {
        "core_sha256": hashlib.sha256(raw).hexdigest(),
        "core_sha512": hashlib.sha512(raw).hexdigest(),
        "core_sha3_256": hashlib.sha3_256(raw).hexdigest(),
    }
    print(f"  receipt:        {record.get('receipt_id')}")
    print(f"  renewals found: {len(files)}")

    ok = True
    prev_digest = None
    for f in files:
        try:
            rr = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError) as e:
            print(f"FAIL  {f.name}: unreadable ({e})")
            ok = False
            continue
        label = f"{f.name} (seq {rr.get('sequence')})"
        if rr.get("kind") != KIND:
            print(f"FAIL  {label}: unknown kind {rr.get('kind')!r}")
            ok = False
            continue

        tgt = rr.get("target") or {}
        if tgt.get("receipt_id") != record.get("receipt_id"):
            print(f"FAIL  {label}: record targets a different receipt")
            ok = False
            continue
        if tgt.get("anchored_digest_hex") != record.get("hash_hex"):
            print(f"FAIL  {label}: anchored digest does not match this receipt")
            ok = False

        for key, want in actual.items():
            got = tgt.get(key)
            if got != want:
                print(f"FAIL  {label}: {key} mismatch "
                      f"(record {str(got)[:16]}… vs receipt {want[:16]}…)")
                ok = False
        # Chain: the record must point at the digest of its predecessor. The
        # chain is the authority; `sequence` is only an ordering hint.
        if rr.get("prev_renewal_sha256") != prev_digest:
            print(f"FAIL  {label}: broken chain — prev_renewal_sha256 "
                  f"{str(rr.get('prev_renewal_sha256'))[:16]}… "
                  f"expected {str(prev_digest)[:16]}…")
            ok = False

        batch = rr.get("batch")
        if isinstance(batch, dict):
            running = _leaf_hash(batch.get("leaf_path", ""),
                                 bytes.fromhex(record_digest(rr)))
            good = True
            for step in batch.get("proof", []):
                if not (isinstance(step, list) and len(step) == 2):
                    good = False
                    break
                direction, sib_hex = step
                try:
                    sib = bytes.fromhex(sib_hex)
                except ValueError:
                    good = False
                    break
                if direction == "L":
                    running = _internal(sib, running)
                elif direction == "R":
                    running = _internal(running, sib)
                else:
                    good = False
                    break
            good = good and running.hex() == batch.get("root_hex")
            print(("  OK  " if good else "FAIL") +
                  f" {label}: inclusion proof → batch root "
                  f"{str(batch.get('root_hex'))[:16]}…")
            if not good:
                ok = False
        else:
            print(f"  OK  {label}: digests + chain (no batch block)")

        prev_digest = record_digest(rr)

    if not ok:
        print("\nFAIL — at least one renewal record did not verify")
        return 1
    print("\nPASS — the renewal chain matches this receipt and re-derives its "
          "batch root(s).")
    print("Scope: this does not itself prove the batch root reached Bitcoin "
          "(check the batch anchor's .ots with verify.py), and renewal cannot "
          "repair a break that already happened. The outer commitment is "
          "still SHA-256; the SHA-512/SHA3-256 digests are content, not "
          "transport.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
