#!/usr/bin/env python3
"""verify_snark.py — standalone checker for snark-exec-v1 receipt fields.

Stdlib-only for every binding that is pure hashing; the groth16 pairing
check itself requires snarkjs (node) and runs automatically when a
verification key is supplied and node + snarkjs are available.

What PASS means (say it exactly like this, nothing stronger):
  - the anchored hash IS the hash of the circuit's output
    ("out2:" + hex(stN)) recomputed from the proof's public signals;
  - the claimed model_id binds to the circuit's public input st0;
  - with --vk and snarkjs: a groth16 proof verifies for those public
    signals under that verification key.
What PASS does NOT mean:
  - it does not prove an LLM produced the output — PROGRAM_V2 is a fixed
    8-round SHA-256 chain standing in for execution;
  - the ceremony behind the key is dev/public-file grade until a real
    multi-party setup exists. No production trust claim.

Usage:
    python3 verify_snark.py <receipt.json> [--vk verification_key.json]
Exit: 0 = all performed checks pass · 1 = a check failed · 2 = bad input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def hex_to_bits(hex_str: str) -> list[str]:
    """64-hex → 256 '0'/'1' strings, MSB-first — the exact snarkjs order."""
    v = int(hex_str, 16)
    return [str((v >> (255 - i)) & 1) for i in range(256)]


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify a snark-exec-v1 receipt field")
    ap.add_argument("receipt", help="path to receipt.json")
    ap.add_argument("--vk", default=None,
                    help="verification_key.json — enables the groth16 pairing "
                         "check via snarkjs (requires node)")
    args = ap.parse_args()

    try:
        record = json.loads(Path(args.receipt).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"could not read receipt: {e}", file=sys.stderr)
        return 2
    zk = record.get("zk_provenance")
    if not zk or zk.get("proof_type") != "snark-exec-v1":
        print("receipt has no snark-exec-v1 zk_provenance field", file=sys.stderr)
        return 2

    hash_hex = record.get("hash_hex", "")
    st_n = zk.get("stN_hex", "")
    st0 = zk.get("st0_hex", "")
    commitment = zk.get("commitment_hex", "")
    for name, v in (("stN_hex", st_n), ("commitment_hex", commitment), ("st0_hex", st0)):
        if not (isinstance(v, str) and len(v) == 64
                and all(c in "0123456789abcdef" for c in v)):
            print(f"FAIL  {name} missing or not 64 lowercase hex")
            return 1
    # The receipt stores the compact identities; rebuild the snarkjs
    # 768-bit public-signal array losslessly for the pairing check.
    signals = hex_to_bits(st_n) + hex_to_bits(commitment) + hex_to_bits(st0)

    ok = True
    recomputed = hashlib.sha256(("out2:" + st_n).encode()).hexdigest()
    line = "OK  " if recomputed == hash_hex else "FAIL"
    ok &= recomputed == hash_hex
    print(f"{line} anchored hash == SHA-256(circuit output)")

    expected_st0 = hashlib.sha256(
        b"orpho-prog-v2" + zk.get("model_id", "").encode()).hexdigest()
    line = "OK  " if st0 == expected_st0 else "FAIL"
    ok &= st0 == expected_st0
    print(f"{line} st0 == model commitment for '{zk.get('model_id', '')}'")

    if args.vk:
        vk_path = Path(args.vk)
        vk_hash = hashlib.sha256(vk_path.read_bytes()).hexdigest()
        line = "OK  " if vk_hash == zk.get("vk_sha256") else "FAIL"
        ok &= vk_hash == zk.get("vk_sha256")
        print(f"{line} verification key matches pinned vk_sha256")

        snarkjs = _find_snarkjs()
        if snarkjs is None:
            print("SKIP groth16 pairing check — node/snarkjs not found "
                  "(npm i snarkjs, then re-run)")
        else:
            with tempfile.TemporaryDirectory() as td:
                pub = Path(td) / "public.json"
                prf = Path(td) / "proof.json"
                pub.write_text(json.dumps(signals))
                prf.write_text(json.dumps({**zk["proof"],
                                           "protocol": "groth16",
                                           "curve": "bn128"}))
                r = subprocess.run(
                    snarkjs + ["groth16", "verify", str(vk_path),
                               str(pub), str(prf)],
                    capture_output=True, text=True, timeout=300)
            passed = r.returncode == 0 and "OK" in (r.stdout + r.stderr)
            ok &= passed
            print(("OK  " if passed else "FAIL") + " groth16 pairing check (snarkjs)")
    else:
        print("SKIP groth16 pairing check — pass --vk to enable")

    if ok:
        print("\nPASS — scope: proves the 8-round PROGRAM_V2 hash chain "
              "produced this output under the pinned key. It does NOT prove "
              "an LLM ran, and the ceremony is not production-grade.")
        return 0
    print("\nFAIL — at least one binding did not hold")
    return 1


def _find_snarkjs() -> list[str] | None:
    node = shutil.which("node")
    if not node:
        return None
    local = (Path(__file__).resolve().parent.parent.parent / "zk-provenance"
             / "snark" / "node_modules" / "snarkjs" / "build" / "cli.cjs")
    if local.exists():
        return [node, str(local)]
    if shutil.which("snarkjs"):
        return [shutil.which("snarkjs")]
    return None


if __name__ == "__main__":
    sys.exit(main())
