#!/usr/bin/env python3
"""verify_cli.py — standalone structural verifier for orphograph receipts.

WHAT THIS CHECKS (all offline, no network):
  * each .ots file carries a well-formed OpenTimestamps header;
  * the hash embedded in each .ots equals the receipt's claimed hash;
  * optionally, that re-hashing your file reproduces that hash.

WHAT THIS DOES NOT CHECK — read this before relying on it:
  * it does NOT contact Bitcoin and does NOT confirm the timestamp was
    ever included in a block. This script makes zero network calls. A
    well-formed .ots that no calendar ever attested will pass every
    check here.
  * therefore a PASS means "this bundle is internally consistent", not
    "this was proven to exist at a date". For the chain check run the
    OpenTimestamps reference client:
        pip install opentimestamps-client && ots verify <file>.ots
    which is the step that actually consults Bitcoin.

This script is self-contained — no imports from server/engine.py — so
it can be vendored as the public open-source verifier.

Usage:
    python3 verify_cli.py <receipt.json>
    python3 verify_cli.py <receipt.json> --file <original-file>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

OTS_HEADER_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha512_file(path: Path) -> str:
    h = hashlib.sha512()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(receipt_path: Path, file_path: Path | None) -> int:
    if not receipt_path.exists():
        print(f"receipt not found: {receipt_path}", file=sys.stderr)
        return 2
    record = json.loads(receipt_path.read_text())
    receipt_dir = receipt_path.parent
    expected_hex = record["hash_hex"]
    expected_bytes = bytes.fromhex(expected_hex)

    print(f"  receipt:        {record['receipt_id']}")
    print(f"  created at UTC: {record['created_at']}")
    print(f"  anchored hash:  {expected_hex}")
    if record.get("client_label"):
        print(f"  client label:   {record['client_label']}")

    if file_path is not None:
        if not file_path.exists():
            print(f"  file not found: {file_path}", file=sys.stderr)
            return 2
        recomputed = sha256_file(file_path)
        match = recomputed == expected_hex
        print(f"  your file:      {file_path.name}")
        print(f"  recomputed:     {recomputed}")
        print(f"  file matches:   {'YES' if match else 'NO'}")
        if not match:
            return 3
        sibling = record.get("sha512_hex")
        if sibling:
            recomputed_512 = sha512_file(file_path)
            sibling_match = recomputed_512 == sibling
            print(f"  sha-512 sibling: {sibling[:16]}…")
            print(f"  sha-512 match:   {'YES' if sibling_match else 'NO'}")
            if not sibling_match:
                print("  WARNING: SHA-256 matched but SHA-512 sibling did NOT.")
                print("  Either the receipt is post-collision tampered, or the wrong file.")
                return 3

    bad = 0
    ots_files = sorted(receipt_dir.glob("*.ots"))
    if not ots_files and record.get("successes"):
        ots_files = sorted((receipt_dir.parent.parent / s["ots_path"]).resolve()
                           for s in record["successes"])
    print(f"  ots receipts:   {len(ots_files)}")
    for ots in ots_files:
        data = ots.read_bytes() if ots.exists() else b""
        magic_ok = data.startswith(OTS_HEADER_MAGIC)
        offset = len(OTS_HEADER_MAGIC) + 2
        embedded = data[offset:offset + 32] if magic_ok else b""
        hash_match = embedded == expected_bytes
        status = "OK " if magic_ok and hash_match else "BAD"
        if not (magic_ok and hash_match):
            bad += 1
        print(f"    [{status}] {ots.name}  magic={'ok' if magic_ok else 'bad'}  hash_match={hash_match}")

    if bad:
        print(f"\n  {bad} receipt(s) failed validation")
        return 4
    print("\n  STRUCTURE OK — every .ots is well-formed and commits to this")
    print("  receipt's hash. This did NOT check Bitcoin: no network call was")
    print("  made, and a well-formed .ots that no calendar attested would also")
    print("  reach this line. To confirm the timestamp is actually in a block:")
    print("      pip install opentimestamps-client && ots verify <ots-file>")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Verify an orphograph receipt")
    p.add_argument("receipt", help="path to receipt.json")
    p.add_argument("--file", help="optional: original file to re-hash and compare")
    args = p.parse_args()
    return verify(Path(args.receipt).expanduser().resolve(),
                  Path(args.file).expanduser().resolve() if args.file else None)


if __name__ == "__main__":
    sys.exit(main())
