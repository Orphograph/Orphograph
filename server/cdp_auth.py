"""cdp_auth.py — Bearer JWTs for the Coinbase CDP x402 facilitator, stdlib only.

The CDP facilitator (the mainnet x402 facilitator that also screens every
payer and recipient against OFAC lists) authenticates each request with a
short-lived JWT signed by the project's CDP API key. CDP's recommended key
type is Ed25519 (EdDSA). The server is stdlib-only by design, so the
signature is computed here, following RFC 8032 section 5.1.6, and checked in
tests/test_x402_mainnet_switch.py against the RFC's own test vectors (fetched from
rfc-editor.org) and against OpenSSL's independent Ed25519 verifier.

Wire format, matched to the reference SDK (coinbase/cdp-sdk,
python/cdp/auth/utils/jwt.py, read 2026-11-04):

    header  {"alg": "EdDSA", "kid": <key id>, "typ": "JWT", "nonce": <16 digits>}
    claims  {"sub": <key id>, "iss": "cdp", "nbf": now, "exp": now + 120,
             "uris": ["POST api.cdp.coinbase.com/platform/v2/x402/verify"]}

The secret is base64 of 64 bytes: the 32-byte seed, then the 32-byte public
key. Only the seed is used. The secret is never logged, never put in an
exception message, and never leaves this process except as a signature.

Side channels: Python big-integer arithmetic is not constant-time. The key
authenticates this server to CDP and moves no funds (payments are signed by
the payer, not by us), every JWT is bound to one URI and expires after 120 s,
and an attacker cannot choose what is signed. Signing time is observable only
through the facilitator round-trip, which dwarfs it.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import json
import os
import secrets
import time
from urllib.parse import urlparse

# ── Ed25519 (RFC 8032, section 5.1) ─────────────────────────────────────────
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)  # sqrt(-1)


def _recover_x(y: int, sign: int) -> int:
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _I % _P
    if (x * x - x2) % _P:
        raise ValueError("not a curve point")
    if x & 1 != sign:
        x = _P - x
    return x


_BY = 4 * pow(5, _P - 2, _P) % _P
_B = (_recover_x(_BY, 0), _BY, 1, _recover_x(_BY, 0) * _BY % _P)  # extended coords


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _encode(p) -> bytes:
    zinv = pow(p[2], _P - 2, _P)
    x, y = p[0] * zinv % _P, p[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _expand(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("Ed25519 seed must be 32 bytes")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    a, _ = _expand(seed)
    return _encode(_mul(a, _B))


def sign(seed: bytes, msg: bytes) -> bytes:
    """Ed25519 signature of msg (RFC 8032 5.1.6). Deterministic: no RNG."""
    a, prefix = _expand(seed)
    pub = _encode(_mul(a, _B))
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % _L
    big_r = _encode(_mul(r, _B))
    k = int.from_bytes(hashlib.sha512(big_r + pub + msg).digest(), "little") % _L
    s = (r + k * a) % _L
    return big_r + int.to_bytes(s, 32, "little")


# ── CDP JWT ──────────────────────────────────────────────────────────────────
CDP_HOST = "api.cdp.coinbase.com"
CDP_X402_BASE = f"https://{CDP_HOST}/platform/v2/x402"
JWT_TTL_SEC = 120


class CdpKeyError(ValueError):
    """The configured CDP secret is not a usable Ed25519 key. The message
    never contains the secret."""


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def key_id() -> str:
    return os.environ.get("ORPHO_X402_CDP_KEY_ID", "").strip()


@functools.lru_cache(maxsize=4)
def _seed_from_secret(secret: str) -> bytes:
    """Decoded and self-checked once per distinct secret value. Without the
    cache every configured() call re-derived the public key (3.5 ms of pure-
    Python curve math, measured 2026-11-04), up to three times per anchor
    request, which any caller could trigger for free. A rotated secret is a
    new cache key, so it is checked again. Failures are not cached (they
    raise), so a fixed secret is accepted on the next call."""
    try:
        raw = base64.b64decode(secret.strip(), validate=True)
    except (ValueError, TypeError):
        raise CdpKeyError("ORPHO_X402_CDP_KEY_SECRET is not base64") from None
    if len(raw) != 64:
        raise CdpKeyError(
            f"ORPHO_X402_CDP_KEY_SECRET decodes to {len(raw)} bytes; an Ed25519 "
            "CDP key is 64 (create the key with the Ed25519 algorithm)")
    seed, pub = raw[:32], raw[32:]
    if public_key(seed) != pub:
        raise CdpKeyError("ORPHO_X402_CDP_KEY_SECRET: public half does not match its seed")
    return seed


def credentials_present() -> bool:
    return bool(key_id() and os.environ.get("ORPHO_X402_CDP_KEY_SECRET", "").strip())


def credentials_problem() -> str | None:
    """None when the CDP key is present and well-formed, else why not."""
    if not credentials_present():
        return "CDP facilitator selected but ORPHO_X402_CDP_KEY_ID / ORPHO_X402_CDP_KEY_SECRET are not both set"
    try:
        _seed_from_secret(os.environ["ORPHO_X402_CDP_KEY_SECRET"])
    except CdpKeyError as e:
        return str(e)
    return None


def is_cdp_url(url: str) -> bool:
    return urlparse(url).hostname == CDP_HOST


def bearer_for(method: str, url: str, *, now: int | None = None) -> str:
    """The Authorization header value for one request to `url`."""
    seed = _seed_from_secret(os.environ.get("ORPHO_X402_CDP_KEY_SECRET", ""))
    kid = key_id()
    if not kid:
        raise CdpKeyError("ORPHO_X402_CDP_KEY_ID is not set")
    parsed = urlparse(url)
    now = int(time.time()) if now is None else now
    header = {"alg": "EdDSA", "kid": kid, "typ": "JWT",
              "nonce": "".join(secrets.choice("0123456789") for _ in range(16))}
    claims = {"sub": kid, "iss": "cdp", "nbf": now, "exp": now + JWT_TTL_SEC,
              "uris": [f"{method.upper()} {parsed.netloc}{parsed.path}"]}
    signing_input = (_b64url(json.dumps(header, separators=(",", ":")).encode()) + "." +
                     _b64url(json.dumps(claims, separators=(",", ":")).encode()))
    sig = sign(seed, signing_input.encode())
    return "Bearer " + signing_input + "." + _b64url(sig)
