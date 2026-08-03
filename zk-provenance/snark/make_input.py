#!/usr/bin/env python3
"""make_input.py — build circom witness input for program_v2 circuits, plus
the expected public outputs computed independently in Python.

The cross-check is the point: the circuit's stN/commitment public outputs
must equal what zk_provenance.PROGRAM_V2's transcript produces for the same
inputs. If they match, the circuit implements the normative spec.

Usage:
  python3 make_input.py --model gpt-class-v3 --prompt "hidden P" --seed s1 \
      [--rounds 8] [--r-hex <64 hex>] [--out input.json] [--expected expected.json]

Writes:
  input.json     — {st0, pDigest, sDigest, r} as 256-bit "0"/"1" arrays
                   (big-endian bit order, circomlib sha256 convention)
  expected.json  — {stN_hex, commitment_hex, output_hash_hex} computed in
                   Python; compare against snarkjs public.json via
                   check_public.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets

DOMAIN = b"orpho-prog-v2"


def bits_be(data: bytes) -> list[str]:
    out = []
    for byte in data:
        for i in range(8):
            out.append(str((byte >> (7 - i)) & 1))
    return out


def rounds_chain(st0: bytes, p_digest: bytes, s_digest: bytes, n: int) -> bytes:
    st = st0
    for i in range(1, n + 1):
        st = hashlib.sha256(st + p_digest + s_digest + i.to_bytes(4, "big")).digest()
    return st


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--seed", required=True)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--r-hex", help="64-hex commitment randomness (random if omitted)")
    ap.add_argument("--out", default="input.json")
    ap.add_argument("--expected", default="expected.json")
    args = ap.parse_args()

    p_digest = hashlib.sha256(args.prompt.encode()).digest()
    s_digest = hashlib.sha256(args.seed.encode()).digest()
    st0 = hashlib.sha256(DOMAIN + args.model.encode()).digest()
    r = bytes.fromhex(args.r_hex) if args.r_hex else secrets.token_bytes(32)

    st_n = rounds_chain(st0, p_digest, s_digest, args.rounds)
    commitment = hashlib.sha256(p_digest + s_digest + st0 + r).digest()

    with open(args.out, "w") as f:
        json.dump({
            "st0": bits_be(st0),
            "pDigest": bits_be(p_digest),
            "sDigest": bits_be(s_digest),
            "r": bits_be(r),
        }, f)

    with open(args.expected, "w") as f:
        json.dump({
            "rounds": args.rounds,
            "st0_hex": st0.hex(),
            "stN_hex": st_n.hex(),
            "commitment_hex": commitment.hex(),
            # Only meaningful for rounds == 8 (the production transform):
            "output_hash_hex": hashlib.sha256(
                ("out2:" + st_n.hex()).encode()).hexdigest(),
        }, f, indent=2)

    print(f"wrote {args.out} + {args.expected}")
    print(f"stN        = {st_n.hex()}")
    print(f"commitment = {commitment.hex()}")


if __name__ == "__main__":
    main()
