"""_ots_bodies.py — the shared OpenTimestamps test bodies, importable from any
process (pytest or the stub server launcher) without pulling in conftest."""
from __future__ import annotations

import sys
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

import ots_timestamp as _ots  # noqa: E402

# What a calendar returns from POST /digest: append(16-byte nonce) · sha256 ·
# pending attestation (URI "x"). engine.anchor_hash accepts nothing less.
PENDING_BODY = (b"\xf0\x10" + b"\x01" * 16 + b"\x08"
                + b"\x00" + _ots.PENDING_ATTESTATION_TAG + b"\x02\x01x")

# sha256 then a Bitcoin attestation for block 949156 (varint a4 f7 39,
# payload length 3): the smallest calendar body the guard accepts.
PINNED_BODY = b"\x08\x00" + _ots.BITCOIN_ATTESTATION_TAG + b"\x03\xa4\xf7\x39"
