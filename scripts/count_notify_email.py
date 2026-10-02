#!/usr/bin/env python3
"""count_notify_email.py - how many stored receipts still hold notify_email.

Prints four counts for a data directory and nothing else, so production can
be checked without anyone reading an address:

    public_with_notify_email N
    public_without_notify_email N
    private_with_notify_email N
    private_without_notify_email N

"Public" means `private` is falsy, the same test the upgrade worker uses when
it removes the address from a public receipt whose pin notice is settled.
Read-only. A receipt.json that cannot be read is left out of the four counts;
their number goes to stderr and the exit code is 1, so an incomplete count
never passes for a complete one.

Usage: python3 scripts/count_notify_email.py [DATA_DIR]
(DATA_DIR defaults to $ORPHO_DATA_DIR, then ./data; receipts are DATA_DIR/receipts.)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def count(receipts_dir: Path) -> tuple[dict[str, int], int]:
    counts = {"public_with_notify_email": 0, "public_without_notify_email": 0,
              "private_with_notify_email": 0, "private_without_notify_email": 0}
    unreadable = 0
    for child in sorted(receipts_dir.iterdir()):
        rfile = child / "receipt.json"
        if not child.is_dir() or not rfile.exists():
            continue
        try:
            rec = json.loads(rfile.read_text())
        except (OSError, ValueError):
            unreadable += 1
            continue
        if not isinstance(rec, dict):
            unreadable += 1
            continue
        posture = "private" if rec.get("private") else "public"
        held = "with" if "notify_email" in rec else "without"
        counts[f"{posture}_{held}_notify_email"] += 1
    return counts, unreadable


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        sys.stderr.write("usage: count_notify_email.py [DATA_DIR]\n")
        return 2
    data_dir = Path(argv[1] if len(argv) == 2 else os.environ.get("ORPHO_DATA_DIR", "data"))
    receipts_dir = data_dir / "receipts"
    if not receipts_dir.is_dir():
        sys.stderr.write(f"no receipts directory under {data_dir}\n")
        return 2
    counts, unreadable = count(receipts_dir)
    for name, n in counts.items():
        sys.stdout.write(f"{name} {n}\n")
    if unreadable:
        sys.stderr.write(f"unreadable {unreadable}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
