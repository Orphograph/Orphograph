#!/usr/bin/env python3
"""ots_timestamp.py — structural walker for serialized OpenTimestamps timestamps.

Stdlib only. Shared by engine.py (the bytes a calendar returns from
POST /digest at anchor time) and upgrade_worker.py (the bytes GET
/timestamp/<commitment> returns once the commitment is in a block). Both
paths write those bytes into the customer's proof, so both must refuse
anything that is not exactly one well-formed timestamp.

What this establishes: the bytes parse under the OpenTimestamps grammar
(opentimestamps-core Timestamp.serialize), respect the reference client's
size caps so `ots` can read what we wrote, and carry the attestation kind
the caller requires. What it does NOT establish: that the ops applied to the
commitment reach a Bitcoin block header — that needs a node and belongs to
`ots verify` / verify_cli.py.
"""
from __future__ import annotations

OTS_HEADER_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
OTS_VERSION = b"\x01"
OTS_TAG_SHA256 = b"\x08"
PROOF_PREFIX_LEN = len(OTS_HEADER_MAGIC) + len(OTS_VERSION) + len(OTS_TAG_SHA256) + 32

# Attestation tags (8 bytes each, following the 0x00 attestation byte).
BITCOIN_ATTESTATION_TAG = b"\x05\x88\x96\x0d\x73\xd7\x19\x01"
PENDING_ATTESTATION_TAG = b"\x83\xdf\xe3\x0d\x2e\xf9\x0c\x8e"

# Reference client limits (python-opentimestamps core/op.py, core/notary.py).
# A body we accept must be one `ots` can deserialize.
MAX_OP_MSG_LENGTH = 4096
MAX_ATTESTATION_PAYLOAD = 8192

_OPS_UNARY = frozenset({0x02, 0x03, 0x08, 0x67, 0xf2, 0xf3})  # sha1 ripemd160 sha256 keccak256 reverse hexlify
_OPS_VARBYTES = frozenset({0xf0, 0xf1})                       # append prepend
_FORK = 0xff
_ATTESTATION = 0x00
_MAX_FORK_DEPTH = 64


def read_varint(b: bytes, i: int) -> tuple[int, int]:
    """LEB128 unsigned varint at b[i]. Returns (value, next_index)."""
    value, shift = 0, 0
    while True:
        if i >= len(b):
            raise ValueError("truncated varint")
        byte = b[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def _check_attestation_payload(tag: bytes, payload: bytes) -> None:
    if tag == BITCOIN_ATTESTATION_TAG:
        # Payload is exactly one varint: the block height.
        if not payload:
            raise ValueError("Bitcoin attestation has an empty payload")
        _height, end = read_varint(payload, 0)
        if end != len(payload):
            raise ValueError("Bitcoin attestation payload is not a single block height")
    elif tag == PENDING_ATTESTATION_TAG:
        # Payload is varbytes(uri).
        ln, end = read_varint(payload, 0)
        if end + ln != len(payload):
            raise ValueError("pending attestation payload is not a single URI")
    # Unknown tags are legal and skipped by length, as the reference client does.


def parse_timestamp(b: bytes, i: int, depth: int = 0) -> tuple[int, dict]:
    """Walk one serialized Timestamp starting at b[i].

    Returns (next_index, {"bitcoin": n, "pending": m}). Raises ValueError on
    anything outside the grammar or the reference size caps. Ops are walked
    iteratively (a real proof is a long linear chain); only forks recurse,
    with a depth cap so a hostile body cannot become a RecursionError.
    """
    if depth > _MAX_FORK_DEPTH:
        raise ValueError("fork nesting too deep")
    counts = {"bitcoin": 0, "pending": 0}
    while True:
        if i >= len(b):
            raise ValueError("truncated timestamp")
        tag = b[i]
        i += 1
        if tag == _FORK:
            i, sub = parse_timestamp(b, i, depth + 1)
            counts["bitcoin"] += sub["bitcoin"]
            counts["pending"] += sub["pending"]
            continue
        if tag == _ATTESTATION:
            if i + 8 > len(b):
                raise ValueError("truncated attestation tag")
            att_tag = b[i:i + 8]
            i += 8
            ln, i = read_varint(b, i)
            if ln > MAX_ATTESTATION_PAYLOAD:
                raise ValueError(f"attestation payload {ln} > {MAX_ATTESTATION_PAYLOAD}")
            if i + ln > len(b):
                raise ValueError("truncated attestation payload")
            _check_attestation_payload(att_tag, b[i:i + ln])
            i += ln
            if att_tag == BITCOIN_ATTESTATION_TAG:
                counts["bitcoin"] += 1
            elif att_tag == PENDING_ATTESTATION_TAG:
                counts["pending"] += 1
            return i, counts
        if tag in _OPS_VARBYTES:
            ln, i = read_varint(b, i)
            if ln == 0 or ln > MAX_OP_MSG_LENGTH:
                raise ValueError(f"varbytes op length {ln} outside 1..{MAX_OP_MSG_LENGTH}")
            if i + ln > len(b):
                raise ValueError("truncated varbytes op")
            i += ln
        elif tag not in _OPS_UNARY:
            raise ValueError(f"unknown op 0x{tag:02x}")
        # An op is followed by exactly one Timestamp: loop, don't recurse.


def timestamp_verdict(body: bytes, *, require_bitcoin: bool) -> tuple[bool, str]:
    """Decide whether `body` may be written into a proof.

    Exactly one timestamp with nothing left over; with `require_bitcoin`
    it must also carry at least one Bitcoin attestation (an upgrade),
    otherwise at least one attestation of any kind (a fresh submit).
    """
    if not body:
        return False, "calendar body empty"
    try:
        end, counts = parse_timestamp(body, 0)
    except (ValueError, IndexError, RecursionError) as e:
        return False, f"calendar body is not a well-formed OpenTimestamps timestamp ({e})"
    if end != len(body):
        return False, f"calendar body has {len(body) - end} trailing bytes after the timestamp"
    if require_bitcoin and counts["bitcoin"] == 0:
        return False, "calendar body carries no Bitcoin attestation"
    return True, "ok"


def proof_verdict(blob: bytes, *, require_bitcoin: bool) -> tuple[bool, str]:
    """Same verdict for a whole stored `<calendar>.ots` file (engine header +
    version + sha256 tag + 32-byte digest + timestamp)."""
    if not blob.startswith(OTS_HEADER_MAGIC + OTS_VERSION + OTS_TAG_SHA256):
        return False, "stored proof does not start with the OpenTimestamps sha256 header"
    if len(blob) < PROOF_PREFIX_LEN:
        return False, "stored proof shorter than header + digest"
    ok, why = timestamp_verdict(blob[PROOF_PREFIX_LEN:], require_bitcoin=require_bitcoin)
    return ok, why.replace("calendar body", "stored proof")
