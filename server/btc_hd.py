#!/usr/bin/env python3
"""btc_hd.py — derive fresh Bitcoin P2WPKH addresses from an extended public key.

Why this exists:
    Reusing one Bitcoin address for every customer is a privacy leak. Anyone
    can pull the address from any order page and link future payments to past
    payments via on-chain analysis. With an extended public key (xpub), we
    derive a fresh address per order. The xpub alone CANNOT spend funds; only
    the corresponding extended private key (which stays on the founder's
    hardware wallet) can. So the server gains privacy without gaining the
    ability to steal.

Spec compliance:
    BIP-32 — hierarchical deterministic wallets
    BIP-44 — m/44'/0'/0'/0/i path for legacy compat (we use m/0/i child of xpub)
    BIP-84 — P2WPKH (native segwit) addresses, hrp="bc"
    BIP-173 — bech32 encoding

Stdlib only. No `coincurve`, no `ecdsa`, no `secp256k1` package. Test vectors
from BIP-32 and BIP-173 verify correctness — run this file directly to validate.

Public API:
    parse_xpub(xpub: str) -> XPub
    derive_address(xpub: XPub | str, index: int) -> str
    is_valid_xpub(xpub: str) -> bool
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

# ─── secp256k1 curve parameters ─────────────────────────────────────────────
# Public, standard, well-known. Defined in SEC 2 §2.7.1.
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8


def _inv_mod_p(x: int) -> int:
    # Fermat's little theorem on a prime field: x^(p-2) mod p.
    return pow(x, P - 2, P)


def _point_add(p1: tuple[int, int] | None, p2: tuple[int, int] | None
              ) -> tuple[int, int] | None:
    """secp256k1 affine point addition. None represents the point at infinity."""
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None  # P + (-P) = O
        # Point doubling.
        s = (3 * x1 * x1) * _inv_mod_p(2 * y1) % P
    else:
        s = (y2 - y1) * _inv_mod_p((x2 - x1) % P) % P
    x3 = (s * s - x1 - x2) % P
    y3 = (s * (x1 - x3) - y1) % P
    return (x3, y3)


def _scalar_mult(k: int, point: tuple[int, int]) -> tuple[int, int] | None:
    """Double-and-add scalar multiplication. Constant-ish time is fine — only
    used on public data (the parent pubkey + child index, never a private key).
    """
    k = k % N
    if k == 0:
        return None
    result: tuple[int, int] | None = None
    addend: tuple[int, int] | None = point
    while k:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        k >>= 1
    return result


def _compress_pubkey(point: tuple[int, int]) -> bytes:
    x, y = point
    prefix = b"\x02" if (y & 1) == 0 else b"\x03"
    return prefix + x.to_bytes(32, "big")


def _decompress_pubkey(compressed: bytes) -> tuple[int, int]:
    if len(compressed) != 33 or compressed[0] not in (0x02, 0x03):
        raise ValueError("not a compressed pubkey")
    x = int.from_bytes(compressed[1:], "big")
    # y² = x³ + 7 mod p
    y_sq = (pow(x, 3, P) + 7) % P
    # Square root via Tonelli-Shanks shortcut (p ≡ 3 mod 4 for secp256k1).
    y = pow(y_sq, (P + 1) // 4, P)
    if (y & 1) != (compressed[0] & 1):
        y = P - y
    return (x, y)


# ─── Base58Check (decoding an xpub) ──────────────────────────────────────────
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        i = _B58.find(c)
        if i < 0:
            raise ValueError("invalid base58 character")
        n = n * 58 + i
    # Restore leading 0x00 bytes that base58 elides via leading '1'.
    leading_ones = len(s) - len(s.lstrip("1"))
    body = n.to_bytes((n.bit_length() + 7) // 8 or 1, "big")
    out = b"\x00" * leading_ones + body
    return out


def _b58check_decode(s: str) -> bytes:
    raw = _b58decode(s)
    payload, checksum = raw[:-4], raw[-4:]
    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if checksum != expected:
        raise ValueError("base58 checksum mismatch")
    return payload


# ─── Extended-public-key structure ───────────────────────────────────────────
@dataclass
class XPub:
    version: bytes        # 4 bytes — 0x0488B21E for mainnet xpub
    depth: int
    parent_fingerprint: bytes
    child_number: int
    chain_code: bytes     # 32 bytes
    public_key: bytes     # 33 bytes compressed


def parse_xpub(xpub: str) -> XPub:
    raw = _b58check_decode(xpub.strip())
    if len(raw) != 78:
        raise ValueError(f"xpub wrong length: {len(raw)}, expected 78")
    return XPub(
        version=raw[0:4],
        depth=raw[4],
        parent_fingerprint=raw[5:9],
        child_number=int.from_bytes(raw[9:13], "big"),
        chain_code=raw[13:45],
        public_key=raw[45:78],
    )


def is_valid_xpub(xpub: str) -> bool:
    try:
        x = parse_xpub(xpub)
        # Mainnet xpub version bytes — accept legacy xpub, ypub (P2SH-P2WPKH),
        # and zpub (native P2WPKH). All can derive bech32 addresses; we just
        # use the public key the same way regardless of the version prefix.
        return x.version in (
            bytes.fromhex("0488B21E"),  # xpub — legacy
            bytes.fromhex("049D7CB2"),  # ypub — P2SH-wrapped
            bytes.fromhex("04B24746"),  # zpub — native segwit
        )
    except (ValueError, IndexError):
        return False


# ─── BIP-32 child key derivation (non-hardened only — xpub can't do hardened) ─
def _ckd_pub(parent_pubkey: bytes, parent_chain_code: bytes, index: int
            ) -> tuple[bytes, bytes]:
    """CKDpub from BIP-32 §3. Returns (child_pubkey_compressed, child_chain_code)."""
    if index >= 0x80000000:
        raise ValueError("hardened derivation impossible from xpub")
    data = parent_pubkey + index.to_bytes(4, "big")
    I = hmac.new(parent_chain_code, data, hashlib.sha512).digest()
    IL, IR = I[:32], I[32:]
    il_int = int.from_bytes(IL, "big")
    if il_int >= N:
        raise ValueError(f"invalid child key at index {index} (IL ≥ N) — try next")
    parent_point = _decompress_pubkey(parent_pubkey)
    il_point = _scalar_mult(il_int, (GX, GY))
    child_point = _point_add(parent_point, il_point)
    if child_point is None:
        raise ValueError(f"invalid child key at index {index} (point at infinity)")
    return _compress_pubkey(child_point), IR


def derive_pubkey(xpub: XPub | str, path: list[int]) -> bytes:
    """Walk `path` from the xpub, returning the final compressed pubkey."""
    x = parse_xpub(xpub) if isinstance(xpub, str) else xpub
    pubkey = x.public_key
    chain_code = x.chain_code
    for idx in path:
        pubkey, chain_code = _ckd_pub(pubkey, chain_code, idx)
    return pubkey


# ─── Bech32 (BIP-173) ────────────────────────────────────────────────────────
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _bech32_polymod(values: list[int]) -> int:
    gen = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (b >> i) & 1 else 0
    return chk


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _bech32_create_checksum(hrp: str, data: list[int]) -> list[int]:
    values = _bech32_hrp_expand(hrp) + data
    polymod = _bech32_polymod(values + [0, 0, 0, 0, 0, 0]) ^ 1
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def _convertbits(data: bytes, frombits: int, tobits: int, pad: bool = True
                ) -> list[int]:
    acc = 0
    bits = 0
    ret: list[int] = []
    maxv = (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad and bits:
        ret.append((acc << (tobits - bits)) & maxv)
    return ret


def _bech32_encode(hrp: str, witver: int, witprog: bytes) -> str:
    data = [witver] + _convertbits(witprog, 8, 5)
    checksum = _bech32_create_checksum(hrp, data)
    combined = data + checksum
    return hrp + "1" + "".join(_BECH32_CHARSET[d] for d in combined)


def _hash160(data: bytes) -> bytes:
    sha = hashlib.sha256(data).digest()
    return hashlib.new("ripemd160", sha).digest()


def pubkey_to_p2wpkh(pubkey_compressed: bytes, hrp: str = "bc") -> str:
    """Compressed pubkey → bech32 P2WPKH address."""
    return _bech32_encode(hrp, 0, _hash160(pubkey_compressed))


# ─── High-level: address from xpub + index ───────────────────────────────────
def derive_address(xpub: XPub | str, index: int, change: int = 0,
                   hrp: str = "bc") -> str:
    """Derive a bech32 P2WPKH address from `xpub` at path m/<change>/<index>.

    change=0 = receive chain (BIP-44 convention)
    change=1 = internal/change chain (we don't use this for receive)
    """
    pubkey = derive_pubkey(xpub, [change, index])
    return pubkey_to_p2wpkh(pubkey, hrp)


# ─── Self-test (run this file directly to validate) ──────────────────────────
def _self_test() -> None:
    """Validate against BIP-32 / BIP-173 published test vectors."""
    print("btc_hd self-test:")

    # BIP-32 test vector 1, m chain code + pubkey, derive m/0 non-hardened.
    # Seed: 000102030405060708090a0b0c0d0e0f
    # Master xpub: xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8
    master_xpub = ("xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGh"
                   "ePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8")
    x = parse_xpub(master_xpub)
    assert x.depth == 0, f"depth mismatch: {x.depth}"
    assert x.version == bytes.fromhex("0488B21E"), "version mismatch"
    print(f"  ✓ parsed master xpub depth={x.depth}")

    # Derive m/0 (non-hardened) — BIP-32 vector 1's m/0 child xpub starts with
    # depth=1, child_number=0 and a specific pubkey.
    child_pubkey, child_cc = _ckd_pub(x.public_key, x.chain_code, 0)
    # The known pubkey for m/0 from the vector:
    expected = bytes.fromhex(
        "027c4b09ffb985c298afe7e5813266cbfcb7780b480ac294b0b43dc21f2be3d13c")
    assert child_pubkey == expected, f"m/0 pubkey wrong:\n  got      {child_pubkey.hex()}\n  expected {expected.hex()}"
    print(f"  ✓ m/0 child pubkey matches BIP-32 vector")

    # BIP-173 test vector: pubkey hash 751e76e8199196d454941c45d1b3a323f1433bd6
    # → bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4
    pkh = bytes.fromhex("751e76e8199196d454941c45d1b3a323f1433bd6")
    addr = _bech32_encode("bc", 0, pkh)
    expected_addr = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
    assert addr == expected_addr, f"bech32 wrong: got {addr}"
    print(f"  ✓ bech32 P2WPKH encoding matches BIP-173 vector")

    # End-to-end: derive an address from the BIP-32 master xpub at m/0/0.
    addr_e2e = derive_address(master_xpub, 0, change=0)
    print(f"  ✓ derived m/0/0 address from BIP-32 master xpub: {addr_e2e}")
    # Verify it starts with bc1q and is 42 chars (standard P2WPKH).
    assert addr_e2e.startswith("bc1q"), "not a P2WPKH address"
    assert len(addr_e2e) == 42, f"wrong length: {len(addr_e2e)}"

    print("\nAll self-tests pass.")


if __name__ == "__main__":
    _self_test()
