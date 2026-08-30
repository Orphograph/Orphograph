"""conftest.py — shared pytest fixtures for orphograph tests."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


# --- shared OpenTimestamps test bodies (one definition, four users) ---------
# sha256 then a Bitcoin attestation for block 949156 (varint a4 f7 39,
# payload length 3): the smallest calendar body the guard accepts.
# What a calendar returns from POST /digest: append(16-byte nonce) · sha256 ·
# pending attestation (URI "x"). engine.anchor_hash accepts nothing less.
PENDING_BODY = b"\xf0\x10" + b"\x01" * 16 + b"\x08" + b"\x00\x83\xdf\xe3\x0d\x2e\xf9\x0c\x8e" + b"\x02\x01x"

PINNED_BODY = b"\x08\x00\x05\x88\x96\x0d\x73\xd7\x19\x01\x03\xa4\xf7\x39"


def make_pending_ots(digest: bytes = b"\x11" * 32, ops: bytes = b"") -> bytes:
    """A well-formed pending .ots blob as engine.py writes it: header +
    version + sha256 tag + digest + optional op run + pending attestation
    (URI 'x'). `upgrade_worker._commitment_for_pending` parses it."""
    import upgrade_worker  # noqa: E402  (server/ is on sys.path above)
    pending = upgrade_worker.PENDING_ATTESTATION_MARKER + b"\x02\x01x"
    return (upgrade_worker.OTS_HEADER_MAGIC + upgrade_worker.OTS_VERSION
            + upgrade_worker.OTS_TAG_SHA256 + digest + ops + pending)
