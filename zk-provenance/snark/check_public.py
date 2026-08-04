#!/usr/bin/env python3
"""check_public.py — compare snarkjs public.json against expected.json.

snarkjs public signal order for program_v2 circuits:
  [ stN[0..255], commitment[0..255], st0[0..255] ]
(outputs first, then declared-public inputs), all as "0"/"1" strings.

Exit 0 = circuit outputs match the Python transcript · 1 = mismatch.
"""
from __future__ import annotations

import json
import sys


def bits_to_hex(bits: list[str]) -> str:
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v.to_bytes(len(bits) // 8, "big").hex()


def main() -> int:
    pub_path = sys.argv[1] if len(sys.argv) > 1 else "public.json"
    exp_path = sys.argv[2] if len(sys.argv) > 2 else "expected.json"
    pub = json.load(open(pub_path))
    exp = json.load(open(exp_path))
    if len(pub) != 768:
        print(f"FAIL: expected 768 public signals, got {len(pub)}")
        return 1
    st_n = bits_to_hex(pub[0:256])
    commitment = bits_to_hex(pub[256:512])
    st0 = bits_to_hex(pub[512:768])
    ok = True
    for name, got, want in (
        ("stN", st_n, exp["stN_hex"]),
        ("commitment", commitment, exp["commitment_hex"]),
        ("st0", st0, exp["st0_hex"]),
    ):
        line = "OK " if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"{line} {name}: circuit={got[:24]}… python={want[:24]}…")
    print("MATCH: circuit implements the Python transcript" if ok
          else "MISMATCH: circuit does NOT match the normative transform")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
