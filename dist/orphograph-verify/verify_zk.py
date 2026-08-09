#!/usr/bin/env python3
"""verify_zk.py — standalone ZK agent-provenance verifier (MIT, stdlib only).

Companion to verify.py in this bundle. Given an agent output (or its
SHA-256) and an Orphograph receipt.json carrying a `zk_provenance` proof,
this script verifies — with no Orphograph server, no network, no pip
installs — that:

  1. the output's SHA-256 equals the receipt's anchored hash_hex,
  2. the receipt's zero-knowledge proof is valid (Fiat-Shamir Schnorr
     proof of knowledge over the RFC 3526 2048-bit MODP group), and
  3. the proof is bound to this exact anchored hash + model_id.

What a PASS means (stated honestly — see the threat model in the repo's
docs/): the prover knew hidden inputs (prompt, seed) committed under the
named model_id, bound to this output hash, at anchor time. It does NOT
by itself demonstrate that the named program executed to produce the
output — that stronger statement is the SNARK step and is not claimed
here.

Usage:
    verify_zk.py --output FILE     --receipt receipt.json [--ots-dir DIR]
    verify_zk.py --output-hash HEX --receipt receipt.json [--ots-dir DIR]

Exit codes: 0 verified · 1 verification failed · 2 usage/input error.
When --ots-dir is given, the .ots files are checked with verify.py (same
bundle) semantics: header magic + embedded hash, locally. That is a
STRUCTURAL check, not a chain check — no network call is made. Full
chain verification requires the `ots` reference client.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# RFC 3526, 2048-bit MODP group 14 — same nothing-up-my-sleeve group the
# prover uses (zk-provenance/zk_provenance.py). Vendored here so the
# bundle stays dependency-free.
_P = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA6"
    "3B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F2411"
    "7C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08"
    "CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFF"
    "FFFFFFFF",
    16,
)
_G = 2
_Q = (_P - 1) // 2

OTS_HEADER_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"


def _h_bytes(*parts) -> bytes:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode())
    return h.digest()


def _reduce_scalar(*parts) -> int:
    return int.from_bytes(_h_bytes(*parts), "big") % _Q


def _modpow(base: int, exp: int) -> int:
    return pow(base % _P, exp % (_P - 1), _P)


_H = _modpow(_G, _reduce_scalar(b"orphograph-zk-salt-v1"))


def verify_schnorr(proof: dict) -> dict:
    """Pure verification of the schnorr-zk-pok-v1 proof dict. No I/O."""
    try:
        C = int(proof["commitment"])
        A = int(proof["A"])
        s1 = int(proof["s1"])
        s2 = int(proof["s2"])
        c = int(proof["challenge"])
        output_hash = proof["output_hash"]
        model_id = proof["model_id"]
    except (KeyError, ValueError, TypeError) as exc:
        return {"valid": False, "error": f"malformed proof: {exc}"}

    c_recomputed = _reduce_scalar(
        b"chal", str(_G).encode(), str(_H).encode(), str(C).encode(),
        str(A).encode(), output_hash.encode(), model_id.encode(),
    )
    challenge_ok = c_recomputed == c
    lhs = (_modpow(_G, s1) * _modpow(_H, s2)) % _P
    rhs = (A * _modpow(C, c)) % _P
    eq_ok = lhs == rhs
    return {
        "valid": bool(challenge_ok and eq_ok),
        "challenge_ok": challenge_ok,
        "equation_ok": eq_ok,
    }


def check_ots_dir(ots_dir: Path, expected_hash_hex: str) -> list[dict]:
    """Local structural check of each .ots: header magic + embedded hash.
    (This is a STRUCTURAL check and makes no network call.
    Full Bitcoin-chain verification requires the `ots` reference
    client; verify.py --ots delegates to it via otscheck.py.)"""
    expected = bytes.fromhex(expected_hash_hex)
    checks = []
    for ots in sorted(ots_dir.glob("*.ots")):
        data = ots.read_bytes()
        magic_ok = data.startswith(OTS_HEADER_MAGIC)
        offset = len(OTS_HEADER_MAGIC) + 2
        embedded = data[offset:offset + 32] if magic_ok else b""
        checks.append({
            "file": ots.name,
            "magic_ok": magic_ok,
            "hash_match": embedded == expected,
        })
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--output", type=Path, help="the agent output file")
    src.add_argument("--output-hash", help="SHA-256 hex of the output")
    ap.add_argument("--receipt", type=Path, required=True,
                    help="path to the Orphograph receipt.json")
    ap.add_argument("--ots-dir", type=Path,
                    help="directory holding the receipt's .ots files")
    args = ap.parse_args(argv)

    try:
        receipt = json.loads(args.receipt.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read receipt: {exc}")
        return 2

    anchored = receipt.get("hash_hex", "")
    if not (isinstance(anchored, str) and len(anchored) == 64):
        print("ERROR: receipt has no valid hash_hex")
        return 2

    # 1) Output binds to the anchored hash.
    if args.output is not None:
        try:
            h = hashlib.sha256()
            with open(args.output, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            output_hash = h.hexdigest()
        except OSError as exc:
            print(f"ERROR: cannot read output file: {exc}")
            return 2
    else:
        output_hash = args.output_hash.strip().lower()

    if output_hash != anchored:
        print(f"FAIL: output hash {output_hash[:16]}… != anchored hash "
              f"{anchored[:16]}… — this is not the anchored output")
        return 1
    print(f"[1] output hash matches anchored hash_hex: {anchored[:16]}… OK")

    # 2) ZK proof present?
    proof = receipt.get("zk_provenance")
    if not proof:
        print("NOTE: receipt carries no zk_provenance field — this is an "
              "existence-only receipt (still a valid Orphograph receipt; "
              "verify it with verify.py). Nothing further to check here.")
        return 1
    if proof.get("proof_type") != "schnorr-zk-pok-v1":
        print(f"FAIL: unknown proof_type {proof.get('proof_type')!r}")
        return 1

    # 3) Proof bound to THIS anchor?
    if proof.get("output_hash") != anchored:
        print("FAIL: proof.output_hash is not bound to the receipt's hash_hex")
        return 1
    print(f"[2] proof bound to anchor · model_id = {proof.get('model_id')!r}")

    # 4) The Schnorr verification itself.
    result = verify_schnorr(proof)
    if not result["valid"]:
        print(f"FAIL: ZK proof invalid: {result}")
        return 1
    print("[3] ZK proof VALID (challenge + Schnorr equation)")

    # 5) Optional Bitcoin-path structural check.
    if args.ots_dir:
        checks = check_ots_dir(args.ots_dir, anchored)
        ok = sum(1 for c in checks if c["magic_ok"] and c["hash_match"])
        print(f"[4] .ots structural check: {ok}/{len(checks)} carry this hash "
              f"(full chain: run the `ots` reference client)")
        if not checks:
            # An empty/misspelled --ots-dir yielded "0/0" and then printed
            # VERIFIED: the user explicitly asked for the .ots check and got
            # a pass without one ever running. Silence on a requested check
            # is a failure, not a skip.
            print("FAIL: --ots-dir was given but contains no .ots files — "
                  "the check you asked for did not run")
            return 1
        if ok == 0:
            print("FAIL: no .ots file matches the anchored hash")
            return 1

    print()
    print("VERIFIED: the prover knew hidden inputs (prompt, seed) committed "
          "under this model_id, bound to this output hash, at anchor time.")
    print("SCOPE: this does not, by itself, demonstrate the named program "
          "executed to produce the output (that stronger statement is the "
          "SNARK step and is not claimed by this proof type).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
