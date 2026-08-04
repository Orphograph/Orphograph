#!/usr/bin/env python3
"""verify_usb_bundle.py — offline roundtrip verifier for an on-drive .orphograph sidecar.

Point it at a drive root (or any directory carrying a `.orphograph/` sidecar
written by orphograph_usb.py). For every record the index marks `anchored` it:

  1. locates the file at the recorded relpath and the on-drive proof bundle
     `.orphograph/receipts/<receipt_id>/` (receipt.json + the .ots proofs),
  2. cross-checks the bundle receipt's hash against the index row,
  3. requires at least one .ots proof in the bundle (verify_cli.py returns
     "all receipts valid" on an EMPTY bundle, so the emptiness check lives here),
  4. runs server/verify_cli.py's verify() (imported, not copied): OTS header
     magic, embedded-hash match, and a local re-hash of the file (SHA-256 plus
     the SHA-512 sibling when the receipt carries one).

OFFLINE, honestly stated: every check above is structural/local — verify_cli.py
opens no sockets, and neither does this script. What offline verification does
NOT do is confirm the Bitcoin block inclusion: upgrading a calendar-pending
.ots to a full Bitcoin merkle proof needs the network
(`pip install opentimestamps-client && ots upgrade <file>.ots`). A PASS here
proves the bytes on the drive match the anchored hashes and the proofs are
well-formed; run `ots upgrade`/`ots verify` when online for the chain step.

Records with status pending/failed are reported but don't fail the run — they
were never anchored; re-run the recorder to retry them.

Exit codes: 0 = every anchored record verified · 1 = any failure · 2 = usage
(bad root / no sidecar).

Usage:
    python3 capture/verify_usb_bundle.py /Volumes/MYUSB
    python3 capture/verify_usb_bundle.py path/to/dir-with-.orphograph
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Sibling module (sidecar layout + index parsing) and the vendored verifier.
_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parent / "server"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from orphograph_usb import ORPHO_DIR, load_index  # noqa: E402
import verify_cli  # noqa: E402


def verify_record(root: Path, record: dict) -> tuple[bool, str]:
    """Verify one anchored index record against the drive. (ok, detail)."""
    rid = record.get("receipt_id") or ""
    relpath = record.get("relpath") or ""
    sha256 = record.get("sha256") or ""
    if not rid:
        return False, "index record has no receipt_id"
    target = root / relpath
    if not target.is_file():
        return False, f"file missing on drive: {relpath}"
    bundle_dir = root / ORPHO_DIR / "receipts" / rid
    receipt_json = bundle_dir / "receipt.json"
    if not receipt_json.is_file():
        return False, (f"no offline proof bundle at {ORPHO_DIR}/receipts/{rid}/ "
                       f"(recorder run with --no-proofs, or the fetch failed — "
                       f"re-run the recorder online to pull it)")
    try:
        receipt = json.loads(receipt_json.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return False, f"unreadable bundle receipt.json: {e}"
    if receipt.get("hash_hex") != sha256:
        return False, "bundle receipt hash_hex does not match the index sha256"
    if not sorted(bundle_dir.glob("*.ots")):
        return False, ".ots proofs missing from the bundle (0 found)"
    try:
        rc = verify_cli.verify(receipt_json, target)
    except Exception as e:  # defensive: malformed receipt fields
        return False, f"verify_cli raised {type(e).__name__}: {e}"
    if rc != 0:
        return False, f"verify_cli exit {rc} (3=file/hash mismatch, 4=bad .ots)"
    return True, "ok"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Offline roundtrip verifier for an orphograph USB sidecar "
                    "(.orphograph/ on the drive).",
        epilog="Fully offline: structural .ots + re-hash checks only. The one "
               "network step (upgrading to a full Bitcoin merkle proof) is out "
               "of scope — use `ots upgrade` when online.")
    p.add_argument("root", help="drive root (e.g. /Volumes/MYUSB) or any "
                                "directory containing a .orphograph/ sidecar")
    args = p.parse_args(argv)

    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    if not (root / ORPHO_DIR / "index.jsonl").is_file():
        print(f"error: no {ORPHO_DIR}/index.jsonl under {root} — not an "
              f"orphograph-recorded drive?", file=sys.stderr)
        return 2

    index = load_index(root)  # content-keyed, last-write-wins per sha256
    anchored = [r for r in index.values() if r.get("status") == "anchored"]
    unanchored = len(index) - len(anchored)

    failed = 0
    for record in anchored:
        ok, detail = verify_record(root, record)
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] {record.get('relpath', '?')} "
              f"(receipt {record.get('receipt_id', '?')})"
              + ("" if ok else f" — {detail}"))
        failed += 0 if ok else 1

    print(f"\n{len(anchored)} anchored record(s): "
          f"{len(anchored) - failed} verified, {failed} failed"
          + (f"; {unanchored} pending/failed record(s) skipped "
             f"(never anchored — re-run the recorder)" if unanchored else ""))
    if not anchored:
        print("note: nothing anchored on this drive yet")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
