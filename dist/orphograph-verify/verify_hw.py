#!/usr/bin/env python3
"""verify_hw.py — standalone hardware-attestation verifier (MIT, stdlib only).

Companion to verify.py / verify_zk.py in this bundle. Given a file (or its
SHA-256) and an Orphograph receipt.json carrying a `hardware_attestation`
field, this script verifies — with no Orphograph server, no network, no
pip installs — that:

  1. the file's SHA-256 equals the receipt's anchored hash_hex,
  2. the attestation is bound to that exact hash (attestation.hash_hex),
  3. device_id equals SHA-256 of the embedded device public key (derived,
     not asserted),
  4. the ECDSA P-256 signature over the domain-separated message
     ("orpho-hw-v1" framing) verifies against the embedded public key.

What a PASS means (stated honestly — docs/HARDWARE_ATTESTATION_SPIKE.md):
this device key signed this hash; first-use trust — not a chain to
Apple's CA in v1. It is device-key continuity under first-use pinning;
the key's residence in a secure element is a client-side claim in v1.
It does NOT establish scene/content authenticity, authorship, that the
device was uncompromised, or true wall-clock time (`signed_at` and
`key_created_at` are claimed clock readings; the only load-bearing time
bound remains the OTS→Bitcoin path — verify that with verify.py).

The ECDSA verification below is pure-Python affine EC arithmetic — slow,
fine for one signature, and it keeps the bundle's no-dependency rule.

Usage:
    verify_hw.py --output FILE     --receipt receipt.json [--ots-dir DIR]
    verify_hw.py --output-hash HEX --receipt receipt.json [--ots-dir DIR]

Exit codes: 0 verified · 1 verification failed · 2 usage/input error.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import sys
from pathlib import Path

# ─── NIST P-256 (secp256r1) domain parameters ───────────────────────────────
_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_A = _P - 3
_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
_GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5

# Fixed 26-byte SubjectPublicKeyInfo prefix for an uncompressed P-256 point.
_SPKI_PREFIX = bytes.fromhex(
    "3059301306072a8648ce3d020106082a8648ce3d030107034200"
)

OTS_HEADER_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
DOMAIN_TAG = b"orpho-hw-v1"

_TOFU_SCOPE = (
    "SCOPE (honest, v1): proves this device key signed this hash; first-use "
    "trust — not a chain to Apple's CA in v1. This is device-key continuity "
    "under first-use pinning; the key's residence in a secure element is a "
    "client-side claim in v1. It does not establish scene/content "
    "authenticity, authorship, or true wall-clock time — the load-bearing "
    "time bound remains the OTS→Bitcoin path (verify.py)."
)


# ─── Affine EC arithmetic (None = point at infinity) ────────────────────────
def _inv_mod(x: int, m: int) -> int:
    return pow(x, m - 2, m)


def _is_on_curve(pt: tuple[int, int] | None) -> bool:
    if pt is None:
        return True
    x, y = pt
    return (y * y - (x * x * x + _A * x + _B)) % _P == 0


def _pt_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % _P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + _A) * _inv_mod(2 * y1, _P) % _P
    else:
        lam = (y2 - y1) * _inv_mod((x2 - x1) % _P, _P) % _P
    x3 = (lam * lam - x1 - x2) % _P
    y3 = (lam * (x1 - x3) - y1) % _P
    return (x3, y3)


def _pt_mul(k: int, pt):
    result = None
    addend = pt
    while k:
        if k & 1:
            result = _pt_add(result, addend)
        addend = _pt_add(addend, addend)
        k >>= 1
    return result


# ─── Parsers ────────────────────────────────────────────────────────────────
def parse_spki_pubkey(spki_der: bytes) -> tuple[int, int] | None:
    """Extract the uncompressed P-256 point from SubjectPublicKeyInfo DER."""
    if len(spki_der) != 91 or not spki_der.startswith(_SPKI_PREFIX):
        return None
    raw = spki_der[len(_SPKI_PREFIX):]
    if len(raw) != 65 or raw[0] != 0x04:
        return None
    x = int.from_bytes(raw[1:33], "big")
    y = int.from_bytes(raw[33:65], "big")
    pt = (x, y)
    if x >= _P or y >= _P or not _is_on_curve(pt):
        return None
    return pt


def parse_der_signature(sig_der: bytes) -> tuple[int, int] | None:
    """Minimal strict DER parse: SEQUENCE { INTEGER r, INTEGER s }."""
    try:
        if len(sig_der) < 8 or sig_der[0] != 0x30 or sig_der[1] != len(sig_der) - 2:
            return None
        idx = 2
        ints = []
        for _ in range(2):
            if sig_der[idx] != 0x02:
                return None
            length = sig_der[idx + 1]
            start = idx + 2
            end = start + length
            if length == 0 or end > len(sig_der):
                return None
            ints.append(int.from_bytes(sig_der[start:end], "big"))
            idx = end
        if idx != len(sig_der):
            return None
        return ints[0], ints[1]
    except IndexError:
        return None


def build_message(hash_hex: str, signed_at: str, device_id: str, counter: int) -> bytes:
    """The domain-separated signed message (spike doc §3.2, fixed order)."""
    return (DOMAIN_TAG + b"\x00" + hash_hex.encode("ascii")
            + b"\x00" + signed_at.encode("ascii")
            + b"\x00" + device_id.encode("ascii")
            + b"\x00" + counter.to_bytes(8, "big"))


def verify_p256_sha256(pubkey_pt: tuple[int, int], message: bytes,
                       r: int, s: int) -> bool:
    """Textbook ECDSA-SHA256 verification over P-256."""
    if not (1 <= r < _N and 1 <= s < _N):
        return False
    e = int.from_bytes(hashlib.sha256(message).digest(), "big") % _N
    w = _inv_mod(s, _N)
    u1 = (e * w) % _N
    u2 = (r * w) % _N
    pt = _pt_add(_pt_mul(u1, (_GX, _GY)), _pt_mul(u2, pubkey_pt))
    if pt is None:
        return False
    return pt[0] % _N == r


def verify_attestation(att: dict, anchored_hash_hex: str) -> dict:
    """Pure verification of a p256-device-sig-v1 attestation dict. No I/O."""
    if not isinstance(att, dict):
        return {"valid": False, "error": "attestation is not an object"}
    if att.get("attestation_type") != "p256-device-sig-v1":
        return {"valid": False,
                "error": f"unknown attestation_type {att.get('attestation_type')!r}"}
    # Bound to THIS anchored hash (belt-and-suspenders re-check of the
    # write-time rule — the attestation-swapper defense).
    if not isinstance(att.get("hash_hex"), str) \
            or att["hash_hex"].strip().lower() != anchored_hash_hex:
        return {"valid": False,
                "error": "attestation.hash_hex is not bound to the anchored hash"}
    try:
        pubkey_der = base64.b64decode(att.get("device_pubkey", ""), validate=True)
        sig_der = base64.b64decode(att.get("signature", ""), validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        return {"valid": False, "error": f"bad base64: {exc}"}
    pubkey_pt = parse_spki_pubkey(pubkey_der)
    if pubkey_pt is None:
        return {"valid": False,
                "error": "device_pubkey is not a valid P-256 SubjectPublicKeyInfo"}
    # device_id is DERIVED: must equal SHA-256 of the pubkey DER.
    derived_id = hashlib.sha256(pubkey_der).hexdigest()
    if att.get("device_id") != derived_id:
        return {"valid": False,
                "error": "device_id does not equal SHA-256 of device_pubkey"}
    signed_at = att.get("signed_at")
    counter = att.get("counter")
    if not isinstance(signed_at, str) or isinstance(counter, bool) \
            or not isinstance(counter, int) or not (0 <= counter < 2 ** 64):
        return {"valid": False, "error": "malformed signed_at / counter"}
    parsed = parse_der_signature(sig_der)
    if parsed is None:
        return {"valid": False, "error": "signature is not valid DER"}
    r, s = parsed
    message = build_message(anchored_hash_hex, signed_at, derived_id, counter)
    sig_ok = verify_p256_sha256(pubkey_pt, message, r, s)
    return {
        "valid": sig_ok,
        "device_id": derived_id,
        "signed_at": signed_at,
        "key_created_at": att.get("key_created_at"),
        "counter": counter,
        "counter_kind": att.get("counter_kind"),
        "element": att.get("element"),
        "error": None if sig_ok else "ECDSA signature does not verify",
    }


def check_ots_dir(ots_dir: Path, expected_hash_hex: str) -> list[dict]:
    """Local structural check of each .ots: header magic + embedded hash.
    (Full Bitcoin-chain verification: run the `ots` reference client —
    same delegation model as verify.py in this bundle.)"""
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
    src.add_argument("--output", type=Path, help="the anchored file")
    src.add_argument("--output-hash", help="SHA-256 hex of the anchored file")
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
    anchored = anchored.strip().lower()

    # 1) File binds to the anchored hash.
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
              f"{anchored[:16]}… — this is not the anchored file")
        return 1
    print(f"[1] file hash matches anchored hash_hex: {anchored[:16]}… OK")

    # 2) Attestation present?
    att = receipt.get("hardware_attestation")
    if not att:
        print("NOTE: receipt carries no hardware_attestation field — this is "
              "a valid Orphograph receipt without device binding (verify it "
              "with verify.py). Nothing further to check here.")
        return 1

    # 3) The signature verification itself (pure-Python P-256).
    result = verify_attestation(att, anchored)
    if not result["valid"]:
        print(f"FAIL: hardware attestation invalid: {result.get('error')}")
        return 1
    print(f"[2] device_id derived from embedded pubkey: {result['device_id'][:16]}… OK")
    print(f"[3] ECDSA P-256 signature over "
          f"(hash, signed_at={result['signed_at']}, device_id, "
          f"counter={result['counter']}/{result.get('counter_kind')}) VALID")
    if result.get("key_created_at"):
        print(f"    key first-use (TOFU pin, claimed): {result['key_created_at']}")
    if result.get("element"):
        print(f"    element (client-asserted label, uncertified in v1): "
              f"{result['element']}")

    # 4) Optional Bitcoin-path structural check.
    if args.ots_dir:
        checks = check_ots_dir(args.ots_dir, anchored)
        ok = sum(1 for c in checks if c["magic_ok"] and c["hash_match"])
        print(f"[4] .ots structural check: {ok}/{len(checks)} carry this hash "
              f"(full chain: run the `ots` reference client)")
        if checks and ok == 0:
            print("FAIL: no .ots file matches the anchored hash")
            return 1

    print()
    print("VERIFIED: a device-held key signed this exact hash at capture time.")
    print(_TOFU_SCOPE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
