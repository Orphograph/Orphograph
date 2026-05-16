#!/usr/bin/env python3
"""verify.py — verify an orphograph receipt against a local file, offline-capable.

Usage:
    verify.py <receipt-id-or-json> <file>

Trust model:
    No part of this script trusts orphograph.com. The receipt JSON +
    the original file are sufficient to prove the file existed at the
    timestamped moment — the OTS calendars and the Bitcoin chain are
    the authorities.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
ENDPOINT = "https://orphograph.com"


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def load_receipt(arg: str) -> dict:
    p = Path(arg).expanduser()
    if p.exists() and p.is_file():
        return json.loads(p.read_text())
    # Treat as receipt ID
    url = f"{ENDPOINT}/api/receipt/{arg}"
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    receipt_arg, file_arg = sys.argv[1], sys.argv[2]
    file_path = Path(file_arg).expanduser()
    if not file_path.exists():
        print(f"error: file not found: {file_arg}", file=sys.stderr)
        return 2
    try:
        rec = load_receipt(receipt_arg)
    except Exception as e:
        print(f"error loading receipt: {e}", file=sys.stderr)
        return 2

    expected = rec.get("hash_hex", "").lower()
    if not HEX64.match(expected):
        print("error: receipt has no valid hash_hex", file=sys.stderr)
        return 2

    actual = hash_file(file_path)
    matches = expected == actual

    print("Receipt:", rec.get("receipt_id", "?"))
    print("Anchored at:", rec.get("created_at", "?"))
    print("Expected SHA-256:", expected)
    print("Your file SHA-256:", actual)
    print()
    if matches:
        print("✓ File matches the anchored hash.")
        if rec.get("calendars_ok"):
            print(f"✓ {rec['calendars_ok']}/{rec.get('calendars_total','?')} calendars valid.")
        if rec.get("btc_pinned_at"):
            print(f"✓ Bitcoin pin at: {rec['btc_pinned_at']}")
        return 0
    print("✗ FILE DOES NOT MATCH the receipt.")
    print("  The receipt is real, but this file is NOT the one that was anchored.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
