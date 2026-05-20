#!/usr/bin/env python3
"""manifest_signature.py — optional Ed25519 author signature for folder manifests.

Bitcoin proves existence (the manifest's root was committed at time T).
A signature over the manifest proves authorship (a specific key claimed the
manifest). The two together are stronger than either alone.

The signature block is OPTIONAL. A manifest with no `signature` field MUST
verify and anchor exactly as before — this module is strictly additive.

Wire format added to the manifest (top-level, sibling of `root_hex`):

    "signature": {
        "kid":           "did:key:z6Mk...",   # did:key per W3C did-method-key
        "alg":           "EdDSA",
        "curve":         "Ed25519",
        "signature_b64": "<urlsafe base64, no padding, of 64 raw bytes>"
    }

The signature is computed over the canonical-JSON serialisation of the
manifest with the `signature` field REMOVED. Canonical JSON here means:
sort_keys=True, separators=(",", ":"), ensure_ascii=True. Receipt-side
fields the server appends post-signing (`receipt_id`, `kind`) are NOT part
of the manifest at the moment the signer hashes it; the signer signs the
manifest BEFORE it is anchored. The verifier therefore also strips
`receipt_id` and `kind` before recomputing the canonical bytes — see
:func:`canonical_manifest_bytes`.

did:key encoding for Ed25519 follows the W3C did-method-key spec:
    multibase("z" base58btc) || multicodec(0xed 0x01) || 32 raw pubkey bytes

The office uses the `cryptography` package's Ed25519 primitives when
available, and a vendored RFC 8032 pure-Python reference implementation
otherwise (see server/_ed25519.py). MIT licensed.
"""
from __future__ import annotations

import base64
import json
from typing import Tuple

# Try to use the system cryptography library; fall back to vendored stdlib-only
# RFC 8032 reference implementation if unavailable; fall back AGAIN to a
# disabled mode if neither is present. Disabled mode never silently accepts
# a signed manifest — it rejects signed manifests with a clear "signature
# verification unavailable" reason, so the trust contract cannot regress.
_HAVE_CRYPTOGRAPHY = False
_HAVE_REF = False
_ref = None  # type: ignore[assignment]

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PrivateFormat,
        PublicFormat,
        NoEncryption,
    )
    from cryptography.exceptions import InvalidSignature

    _HAVE_CRYPTOGRAPHY = True
except Exception:  # pragma: no cover - exercised only when cryptography missing
    try:
        from . import _ed25519 as _ref  # type: ignore
        _HAVE_REF = True
    except (ImportError, ValueError):
        try:
            import _ed25519 as _ref  # type: ignore
            _HAVE_REF = True
        except ImportError:
            # Neither backend available. The module still loads — signing and
            # verification will return graceful "unavailable" results when
            # called. Unsigned manifests anchor and verify normally.
            _ref = None  # type: ignore[assignment]


def signature_backend_available() -> bool:
    """Return True iff Ed25519 sign/verify is wired up in this build."""
    return _HAVE_CRYPTOGRAPHY or _HAVE_REF


# ---------------------------------------------------------------------- base58

# Bitcoin alphabet — same alphabet did:key uses for the "z" multibase prefix.
_B58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58encode(data: bytes) -> str:
    """Encode bytes as base58btc (no checksum)."""
    n = int.from_bytes(data, "big")
    out = bytearray()
    while n > 0:
        n, rem = divmod(n, 58)
        out.append(_B58_ALPHABET[rem])
    # Preserve leading zero bytes as leading '1's.
    pad = 0
    for b in data:
        if b == 0:
            pad += 1
        else:
            break
    return (bytes([_B58_ALPHABET[0]]) * pad + bytes(reversed(out))).decode("ascii")


def _b58decode(s: str) -> bytes:
    """Decode a base58btc string to bytes (no checksum)."""
    n = 0
    for ch in s:
        idx = _B58_ALPHABET.find(ch.encode("ascii"))
        if idx < 0:
            raise ValueError(f"invalid base58 character: {ch!r}")
        n = n * 58 + idx
    # Recover leading zeros.
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n > 0 else b""
    return b"\x00" * pad + body


# ------------------------------------------------------------------ urlsafe b64


def _b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ------------------------------------------------------------------ did:key

# Multicodec varint prefix for Ed25519 public key: 0xed 0x01.
_DIDKEY_ED25519_PREFIX = b"\xed\x01"


def derive_did_key(public_key_bytes: bytes) -> str:
    """Produce a did:key identifier for an Ed25519 public key (32 raw bytes).

    Format per W3C did-method-key:
        "did:key:" + "z" + base58btc( 0xed 0x01 || pubkey )
    """
    if not isinstance(public_key_bytes, (bytes, bytearray)) or len(public_key_bytes) != 32:
        raise ValueError("Ed25519 public key must be exactly 32 bytes")
    body = _DIDKEY_ED25519_PREFIX + bytes(public_key_bytes)
    return "did:key:z" + _b58encode(body)


def public_key_from_did_key(kid: str) -> bytes:
    """Recover the 32-byte Ed25519 public key from a did:key identifier.

    Raises ValueError if the kid is not a did:key form for Ed25519.
    """
    if not isinstance(kid, str) or not kid.startswith("did:key:z"):
        raise ValueError("kid must be a did:key:z... identifier")
    body = _b58decode(kid[len("did:key:z"):])
    if not body.startswith(_DIDKEY_ED25519_PREFIX):
        raise ValueError("did:key is not an Ed25519 key (wrong multicodec prefix)")
    pub = body[len(_DIDKEY_ED25519_PREFIX):]
    if len(pub) != 32:
        raise ValueError("decoded Ed25519 key is not 32 bytes")
    return pub


# -------------------------------------------------------- canonical-JSON bytes

# Fields the server appends AFTER signing — strip these before recomputing
# the canonical bytes so a signature made on the pre-anchor manifest still
# verifies after the receipt has been persisted.
_POST_ANCHOR_FIELDS = ("receipt_id", "kind", "signature")


def canonical_manifest_bytes(manifest: dict) -> bytes:
    """Return the canonical-JSON bytes the signature is computed over.

    Drops the `signature` field (and any post-anchor server-side fields), then
    serialises with sort_keys=True, separators=(",", ":"), ensure_ascii=True.
    """
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a dict")
    cleaned = {k: v for k, v in manifest.items() if k not in _POST_ANCHOR_FIELDS}
    return json.dumps(
        cleaned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


# --------------------------------------------------------------- sign / verify


def _sign_raw(message: bytes, private_key_bytes: bytes) -> Tuple[bytes, bytes]:
    """Return (signature_64_bytes, public_key_32_bytes) for a 32-byte seed."""
    if not signature_backend_available():
        raise RuntimeError(
            "Ed25519 signing not available in this build: install 'cryptography' "
            "or place server/_ed25519.py alongside this module."
        )
    if len(private_key_bytes) != 32:
        raise ValueError("Ed25519 private key (seed) must be exactly 32 bytes")
    if _HAVE_CRYPTOGRAPHY:
        sk = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
        pk = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        sig = sk.sign(message)
        return sig, pk
    pk = _ref.publickey(private_key_bytes)
    sig = _ref.signature(message, private_key_bytes, pk)
    return sig, pk


def _verify_raw(message: bytes, signature: bytes, public_key_bytes: bytes) -> bool:
    """Return True iff ``signature`` is a valid Ed25519 sig of ``message``.

    When no Ed25519 backend is available in this build, returns False — the
    safe default. Callers that need to distinguish "wrong signature" from
    "verification unavailable" should check :func:`signature_backend_available`
    first.
    """
    if not signature_backend_available():
        return False
    if len(public_key_bytes) != 32 or len(signature) != 64:
        return False
    if _HAVE_CRYPTOGRAPHY:
        try:
            Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(signature, message)
            return True
        except InvalidSignature:
            return False
        except Exception:
            return False
    try:
        return _ref.checkvalid(signature, message, public_key_bytes)
    except Exception:
        return False


def sign_manifest(manifest: dict, private_key_bytes: bytes) -> dict:
    """Return a new manifest dict with a `signature` block appended.

    Input manifest is NOT mutated. Any pre-existing `signature` field on the
    input is dropped before signing, then replaced with the fresh one — this
    keeps re-signing idempotent without producing nested signature blocks.
    """
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a dict")
    base = {k: v for k, v in manifest.items() if k != "signature"}
    msg = canonical_manifest_bytes(base)
    sig, pk = _sign_raw(msg, private_key_bytes)
    kid = derive_did_key(pk)
    out = dict(base)
    out["signature"] = {
        "kid": kid,
        "alg": "EdDSA",
        "curve": "Ed25519",
        "signature_b64": _b64u_encode(sig),
    }
    return out


def verify_manifest_signature(manifest: dict) -> Tuple[bool, str]:
    """Verify the optional signature block on a manifest.

    Returns (ok, reason). Reason is a short human-readable string; on success
    it identifies the algorithm and the signer's did:key.

    A manifest with NO `signature` field returns (False, "no signature
    present") — callers should branch on the presence of the field and only
    invoke this function when the field exists.
    """
    if not isinstance(manifest, dict):
        return False, "manifest must be a dict"
    sig_block = manifest.get("signature")
    if sig_block is None:
        return False, "no signature present"
    if not isinstance(sig_block, dict):
        return False, "signature must be an object"
    alg = sig_block.get("alg")
    curve = sig_block.get("curve")
    kid = sig_block.get("kid")
    sig_b64 = sig_block.get("signature_b64")
    if alg != "EdDSA" or curve != "Ed25519":
        return False, f"unsupported algorithm: alg={alg!r} curve={curve!r}"
    if not isinstance(kid, str) or not isinstance(sig_b64, str):
        return False, "signature kid/signature_b64 must be strings"
    try:
        pk = public_key_from_did_key(kid)
    except ValueError as e:
        return False, f"invalid kid: {e}"
    try:
        sig_bytes = _b64u_decode(sig_b64)
    except (ValueError, base64.binascii.Error):
        return False, "signature_b64 is not valid urlsafe-base64"
    if len(sig_bytes) != 64:
        return False, "signature must decode to 64 bytes"
    msg = canonical_manifest_bytes(manifest)
    if not _verify_raw(msg, sig_bytes, pk):
        return False, "signature does not verify against canonical manifest bytes"
    return True, f"EdDSA/Ed25519 signature verified for {kid}"


__all__ = [
    "canonical_manifest_bytes",
    "sign_manifest",
    "verify_manifest_signature",
    "derive_did_key",
    "public_key_from_did_key",
]
