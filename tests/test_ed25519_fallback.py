"""server/_ed25519.py: the Ed25519 code production actually runs.

The production image (python:3.11-slim) installs nothing, and the weekly job
runs under the macOS system Python, which has no `cryptography` either. In
both places manifest_signature.py falls back to server/_ed25519.py. That file
was referenced since 023fac6 but never committed, so on those machines every
signature check answered False. These tests hold it to the RFC 8032 vectors
and to `cryptography`, in both directions.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import _ed25519  # noqa: E402

# RFC 8032 section 7.1, TEST 1 to TEST 3: (seed, public key, message, signature).
RFC8032 = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
     "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


def test_rfc8032_vectors():
    for seed, pub, msg, sig in RFC8032:
        seed, pub, msg, sig = map(bytes.fromhex, (seed, pub, msg, sig))
        assert _ed25519.publickey(seed) == pub
        assert _ed25519.signature(msg, seed, pub) == sig
        assert _ed25519.checkvalid(sig, msg, pub)


def _cases(n: int = 12):
    # Deterministic, so a failure reproduces.
    for i in range(n):
        seed = hashlib.sha256(b"ed25519 cross-check %d" % i).digest()
        msg = hashlib.sha512(b"message %d" % i).digest()[: i * 5]
        yield seed, msg


def test_agrees_with_cryptography_both_ways():
    for seed, msg in _cases():
        sk = Ed25519PrivateKey.from_private_bytes(seed)
        pub = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        assert _ed25519.publickey(seed) == pub
        theirs = sk.sign(msg)
        ours = _ed25519.signature(msg, seed, pub)
        assert ours == theirs
        assert _ed25519.checkvalid(theirs, msg, pub)
        Ed25519PublicKey.from_public_bytes(pub).verify(ours, msg)  # raises if bad


def test_refuses_what_it_should():
    seed, pub, msg, sig = map(bytes.fromhex, RFC8032[1])
    flipped = bytes([sig[0] ^ 1]) + sig[1:]
    assert not _ed25519.checkvalid(flipped, msg, pub)
    assert not _ed25519.checkvalid(sig, msg + b"\x00", pub)
    assert not _ed25519.checkvalid(sig[:63], msg, pub)
    assert not _ed25519.checkvalid(sig, msg, pub[:31])
    other = _ed25519.publickey(hashlib.sha256(b"other").digest())
    assert not _ed25519.checkvalid(sig, msg, other)
    # The same signature written a second way (s + L) is refused, so one
    # statement has exactly one valid signature per key.
    s = int.from_bytes(sig[32:], "little") + _ed25519._L
    assert s < 2 ** 256
    assert not _ed25519.checkvalid(sig[:32] + s.to_bytes(32, "little"), msg, pub)


def test_the_server_falls_back_to_it_when_cryptography_is_missing():
    """What production runs: no `cryptography`, so manifest_signature must
    pick up _ed25519 and the Standing Record check must still work."""
    probe = (
        "import sys\n"
        "sys.modules['cryptography'] = None\n"
        f"sys.path.insert(0, {str(ROOT / 'server')!r})\n"
        "import manifest_signature as ms, standing_record as sr\n"
        "assert not ms._HAVE_CRYPTOGRAPHY and ms._HAVE_REF, 'fallback not taken'\n"
        "seed = bytes(range(32))\n"
        "sig = bytes.fromhex(sr.sign('weekly-x', 'ab' * 32, seed))\n"
        "key = sr.public_key(seed)\n"
        "assert sr.verifies('weekly-x', 'ab' * 32, sig, (key,))\n"
        "assert not sr.verifies('weekly-y', 'ab' * 32, sig, (key,))\n"
        "print('ok')\n"
    )
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip() == b"ok", r.stderr.decode()
