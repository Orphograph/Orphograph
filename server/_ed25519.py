#!/usr/bin/env python3
"""_ed25519.py — Ed25519 (RFC 8032) in the standard library only.

server/manifest_signature.py uses the `cryptography` package when it can be
imported and this module otherwise. Two places that matter cannot import it:
the production image (python:3.11-slim, nothing installed) and the Python
that launchd runs the weekly job with (/usr/bin/python3). Until this file
existed, signing raised there and every verification answered False.

The arithmetic follows the reference code in RFC 8032 section 6: points are
extended homogeneous coordinates (X, Y, Z, T) on the twisted Edwards curve
-x^2 + y^2 = 1 + d x^2 y^2 over GF(2^255 - 19).

Not constant time. That is acceptable for the two uses it has: verifying,
which handles public values only, and signing in the office's own weekly job
on the office's own machine, where nobody else can time it. Do not use it to
sign on a machine that answers requests from strangers.

tests/test_ed25519_fallback.py checks it against the RFC's test vectors and
against `cryptography`, both ways round.
"""
from __future__ import annotations

import hashlib

_P = 2 ** 255 - 19
# Order of the base point's group.
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _add(a: tuple, b: tuple) -> tuple:
    f1 = (a[1] - a[0]) * (b[1] - b[0]) % _P
    f2 = (a[1] + a[0]) * (b[1] + b[0]) % _P
    f3 = 2 * a[3] * b[3] * _D % _P
    f4 = 2 * a[2] * b[2] % _P
    e, f, g, h = f2 - f1, f4 - f3, f4 + f3, f2 + f1
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, point: tuple) -> tuple:
    out = (0, 1, 1, 0)  # the neutral element
    while s > 0:
        if s & 1:
            out = _add(out, point)
        point = _add(point, point)
        s >>= 1
    return out


def _same_point(a: tuple, b: tuple) -> bool:
    # x1/z1 == x2/z2 and y1/z1 == y2/z2, without dividing.
    return ((a[0] * b[2] - b[0] * a[2]) % _P == 0
            and (a[1] * b[2] - b[1] * a[2]) % _P == 0)


def _recover_x(y: int, sign: int):
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_BY = 4 * _inv(5) % _P
_BX = _recover_x(_BY, 0)
_B = (_BX, _BY, 1, _BX * _BY % _P)


def _compress(point: tuple) -> bytes:
    zinv = _inv(point[2])
    x = point[0] * zinv % _P
    y = point[1] * zinv % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(raw: bytes):
    if len(raw) != 32:
        return None
    y = int.from_bytes(raw, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _expand(seed: bytes) -> tuple:
    if len(seed) != 32:
        raise ValueError("Ed25519 private key (seed) must be exactly 32 bytes")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def _hash_mod_l(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _L


def publickey(seed: bytes) -> bytes:
    """The 32-byte public key of a 32-byte seed."""
    a, _prefix = _expand(bytes(seed))
    return _compress(_mul(a, _B))


def signature(message: bytes, seed: bytes, public_key: bytes) -> bytes:
    """The 64-byte signature of `message`. The same seed and message always
    give the same signature; nothing random is drawn."""
    a, prefix = _expand(bytes(seed))
    public_key = bytes(public_key)
    r = _hash_mod_l(prefix + bytes(message))
    r_bytes = _compress(_mul(r, _B))
    h = _hash_mod_l(r_bytes + public_key + bytes(message))
    s = (r + h * a) % _L
    return r_bytes + s.to_bytes(32, "little")


def checkvalid(sig: bytes, message: bytes, public_key: bytes) -> bool:
    """True only when `sig` is this key's signature of `message`."""
    sig, public_key = bytes(sig), bytes(public_key)
    if len(sig) != 64 or len(public_key) != 32:
        return False
    a_point = _decompress(public_key)
    r_point = _decompress(sig[:32])
    if a_point is None or r_point is None:
        return False
    s = int.from_bytes(sig[32:], "little")
    # A second encoding of the same signature (s + L) is refused, so one
    # statement has one signature.
    if s >= _L:
        return False
    h = _hash_mod_l(sig[:32] + public_key + bytes(message))
    return _same_point(_mul(s, _B), _add(r_point, _mul(h, a_point)))


__all__ = ["publickey", "signature", "checkvalid"]
