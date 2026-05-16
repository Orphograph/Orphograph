#!/usr/bin/env python3
"""dispute_bundle.py — Build a portable, fully-verifiable proof bundle.

For use in disputes or for long-term archival. Produces a single .tar.gz
containing:

  - The original file (or a copy you provide)
  - The Orphograph receipt JSON
  - The 5 .ots Bitcoin proof files
  - The standalone open-source verifier (stdlib Python, no deps)
  - VERIFY.md — step-by-step verification instructions
  - sha256sum.txt — checksums for every file in the bundle

Anyone receiving the bundle can verify the proof offline against Bitcoin's
public ledger without trusting Orphograph or its domain.

Usage:
    python3 dispute_bundle.py <file> <receipt_dir> [-o <output.tar.gz>]

Example:
    # If you saved your receipt to ~/Downloads/r_abc123/, do:
    python3 dispute_bundle.py photo.jpg ~/Downloads/r_abc123/

Output: <basename>_dispute_bundle.tar.gz
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path


VERIFY_MD = """# Verifying this Orphograph dispute bundle

This bundle contains a Bitcoin-anchored timestamp proof. You can verify it
offline using only Python standard library — no Orphograph server required.

## What's in this bundle

- `<filename>` — the original file
- `receipt.json` — Orphograph receipt metadata (hashes, calendars, attestation)
- `*.ots` — 5 OpenTimestamps binary proof files
- `verify_cli.py` — standalone verifier, stdlib Python only
- `sha256sum.txt` — checksums for every file in the bundle
- `VERIFY.md` — this file

## Run verification (3 steps)

```bash
# 1. Verify the bundle hasn't been tampered with
sha256sum -c sha256sum.txt

# 2. Verify the file's hash matches the receipt
python3 -c "
import hashlib, json, sys
data = open('<filename>', 'rb').read()
sha256 = hashlib.sha256(data).hexdigest()
sha512 = hashlib.sha512(data).hexdigest()
rec = json.load(open('receipt.json'))
print('SHA-256 file:    ', sha256)
print('SHA-256 receipt: ', rec['hash_hex'])
print('Match:', sha256 == rec['hash_hex'])
if rec.get('sha512_hex'):
    print('SHA-512 match:', sha512 == rec['sha512_hex'])
"

# 3. Verify the .ots proofs against Bitcoin's chain
python3 verify_cli.py receipt.json
```

## What this proves

The file's SHA-256 hash was anchored to the Bitcoin blockchain on the date
recorded in `receipt.json` (field `created_at`). The Merkle path in each
`.ots` file ties the hash to a specific Bitcoin transaction in a specific
block.

## What this does NOT prove

- It does NOT prove who created the file.
- It does NOT prove ownership.
- It does NOT replace legal evidence (consult a digital evidence specialist).

It proves the file existed in the form anchored at the time anchored. The
rest of the evidence chain (RAW camera files, social media posts, email
records, witnesses) must come from elsewhere.

## Attestation

If the receipt has an `attestation` field, it is a free-form claim the
anchorer attached to the receipt at anchor time. The claim itself is part
of the anchored data — its existence at the receipt date is provable. Its
truth value is a separate question for the parties involved.
"""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", help="path to the original file")
    ap.add_argument("receipt_dir", help="directory containing receipt.json + .ots files")
    ap.add_argument("-o", "--output", help="output .tar.gz path")
    args = ap.parse_args()

    src_file = Path(args.file).resolve()
    src_dir = Path(args.receipt_dir).resolve()
    if not src_file.is_file():
        print(f"error: file not found: {src_file}", file=sys.stderr)
        return 1
    if not src_dir.is_dir():
        print(f"error: receipt dir not found: {src_dir}", file=sys.stderr)
        return 1

    receipt_json = src_dir / "receipt.json"
    if not receipt_json.exists():
        print(f"error: receipt.json not found in {src_dir}", file=sys.stderr)
        return 1
    rec = json.loads(receipt_json.read_text())

    # Sanity: file hash must match receipt
    actual = sha256_of(src_file)
    expected = rec.get("hash_hex", "")
    if actual != expected:
        print(f"WARNING: file SHA-256 ({actual}) does NOT match receipt hash ({expected}).")
        print("This bundle would fail verification. Continuing anyway.")

    # Determine output path
    out = args.output
    if not out:
        bundle_name = f"{src_file.stem}_dispute_bundle.tar.gz"
        out = str(Path.cwd() / bundle_name)
    out_path = Path(out).resolve()

    # Locate standalone verifier (try several common locations)
    here = Path(__file__).resolve().parent.parent
    verifier_candidates = [
        here / "server" / "verify_cli.py",
        here / "dist" / "orphograph-verify" / "verify_cli.py",
    ]
    verifier = next((p for p in verifier_candidates if p.is_file()), None)
    if not verifier:
        print("error: could not locate verify_cli.py; download from https://orphograph.com/verify/",
              file=sys.stderr)
        return 1

    # Build bundle in a temp dir, then tar.gz it
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # Copy original file
        shutil.copy2(src_file, td_path / src_file.name)
        # Copy receipt.json + .ots files
        shutil.copy2(receipt_json, td_path / "receipt.json")
        for ots in sorted(src_dir.glob("*.ots")):
            shutil.copy2(ots, td_path / ots.name)
        # Copy verifier
        shutil.copy2(verifier, td_path / "verify_cli.py")
        # Write VERIFY.md (substituting filename)
        verify_md = VERIFY_MD.replace("<filename>", src_file.name)
        (td_path / "VERIFY.md").write_text(verify_md)
        # Build sha256sum.txt
        sums = []
        for f in sorted(td_path.iterdir()):
            if f.is_file():
                sums.append(f"{sha256_of(f)}  {f.name}")
        (td_path / "sha256sum.txt").write_text("\n".join(sums) + "\n")
        # Tar.gz
        with tarfile.open(out_path, "w:gz") as tf:
            for f in sorted(td_path.iterdir()):
                if f.is_file():
                    tf.add(f, arcname=f"{src_file.stem}_dispute_bundle/{f.name}")

    print(f"✓ Bundle created: {out_path}")
    print(f"  Size: {out_path.stat().st_size:,} bytes")
    print(f"  SHA-256: {sha256_of(out_path)}")
    print()
    print(f"Share this bundle. Anyone can verify it with:")
    print(f"  tar xzf {out_path.name} && cd {src_file.stem}_dispute_bundle")
    print(f"  python3 verify_cli.py receipt.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
