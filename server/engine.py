#!/usr/bin/env python3
"""engine.py — submit a SHA-256 hash to OpenTimestamps calendars and persist receipts.

Client computes the hash; this module never sees user content.
Stdlib only. Builds the .ots binary directly from calendar HTTP responses.

Public API:
    anchor_hash(hash_hex: str, client_label: str | None = None) -> dict
    verify_receipt(receipt_id: str) -> dict
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Data root: defaults to repo root in dev; /app/data in prod (mounted volume).
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
RECEIPTS_DIR = Path(os.environ.get("ORPHO_RECEIPTS_DIR", str(DATA_DIR / "receipts")))
LEDGER = Path(os.environ.get("ORPHO_LEDGER", str(DATA_DIR / "ledger.jsonl")))

OTS_HEADER_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
OTS_VERSION = b"\x01"
OTS_TAG_SHA256 = b"\x08"

CALENDARS = [
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://alice.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
    "https://btc.calendar.catallaxy.com",
]
HTTP_TIMEOUT_SEC = 15
USER_AGENT = "orphograph/0.1 (stdlib)"


def _calendar_short(url: str) -> str:
    return url.split("//", 1)[1].split(".", 1)[0]


def _submit(calendar_url: str, hash_bytes: bytes) -> tuple[bool, bytes | str]:
    if len(hash_bytes) != 32:
        return False, "hash must be exactly 32 bytes (SHA-256)"
    req = urllib.request.Request(
        calendar_url.rstrip("/") + "/digest",
        data=hash_bytes,
        method="POST",
        headers={
            "Accept": "application/vnd.opentimestamps.v1",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return True, resp.read()
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, ConnectionError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


def _build_ots(hash_bytes: bytes, calendar_body: bytes) -> bytes:
    return OTS_HEADER_MAGIC + OTS_VERSION + OTS_TAG_SHA256 + hash_bytes + calendar_body


def _append_ledger(record: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(LEDGER.parent, 0o700)
    except OSError:
        pass
    with LEDGER.open("a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
    try:
        os.chmod(LEDGER, 0o600)
    except OSError:
        pass


def _new_receipt_id() -> str:
    return secrets.token_urlsafe(12)


def _is_hex(s: str, length: int) -> bool:
    return isinstance(s, str) and len(s) == length and all(c in "0123456789abcdef" for c in s)


def anchor_hash(
    hash_hex: str,
    client_label: str | None = None,
    sha512_hex: str | None = None,
    source: str = "free",
    private: bool = False,
    owner_id: str | None = None,
    attestation: dict | None = None,
    metadata: dict | None = None,
    c2pa_manifest_hash: str | None = None,
) -> dict:
    """Anchor a client-supplied SHA-256 hex digest. Returns the receipt record.

    sha512_hex is an optional sibling witness: a SHA-512 of the same file,
    stored alongside the SHA-256 in the receipt. The OTS protocol only
    anchors SHA-256, but recording SHA-512 in the receipt means an attacker
    must collide both hashes to forge the file→receipt binding. Quantum
    hedge — Grover halves preimage resistance in bits, leaving SHA-256
    at ~2^128 and SHA-512 at ~2^256.
    """
    hash_hex = hash_hex.strip().lower()
    if not _is_hex(hash_hex, 64):
        raise ValueError("hash_hex must be 64 lowercase hex characters")
    if sha512_hex is not None:
        sha512_hex = sha512_hex.strip().lower()
        if not _is_hex(sha512_hex, 128):
            raise ValueError("sha512_hex must be 128 lowercase hex characters")
    # Optional C2PA manifest reference. The customer pre-computes the
    # SHA-256 of the C2PA manifest JSON (or of the embedded JUMBF block)
    # and supplies it here; the receipt records the relationship without
    # the office ever seeing the manifest itself.
    if c2pa_manifest_hash is not None:
        c2pa_manifest_hash = c2pa_manifest_hash.strip().lower()
        if not _is_hex(c2pa_manifest_hash, 64):
            raise ValueError("c2pa_manifest_hash must be 64 lowercase hex characters")
    hash_bytes = bytes.fromhex(hash_hex)

    receipt_id = _new_receipt_id()
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    receipt_dir = RECEIPTS_DIR / receipt_id
    receipt_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(RECEIPTS_DIR, 0o700)
        os.chmod(receipt_dir, 0o700)
    except OSError:
        pass

    # Submit to all calendars in parallel. Total wall time becomes
    # max(per-calendar latency) instead of sum(); one slow calendar no
    # longer blocks anchoring on the others. Ordering of successes/
    # failures is preserved by collecting via the input index.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results: list[tuple[bool, object]] = [(False, "not run")] * len(CALENDARS)
    with ThreadPoolExecutor(max_workers=len(CALENDARS)) as pool:
        future_to_idx = {
            pool.submit(_submit, cal, hash_bytes): i
            for i, cal in enumerate(CALENDARS)
        }
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:  # noqa: BLE001 — calendar errors are recorded, not raised
                results[idx] = (False, f"executor: {exc}")

    successes, failures = [], []
    for cal, (ok, body) in zip(CALENDARS, results):
        if ok:
            ots_bytes = _build_ots(hash_bytes, body)
            ots_path = receipt_dir / f"{_calendar_short(cal)}.ots"
            ots_path.write_bytes(ots_bytes)
            try:
                os.chmod(ots_path, 0o600)
            except OSError:
                pass
            successes.append({
                "calendar": cal,
                # Logical path within the data root; informational only — verifier
                # discovers .ots files via glob, not by reading this field.
                "ots_path": f"receipts/{receipt_id}/{_calendar_short(cal)}.ots",
                "ots_bytes": len(ots_bytes),
            })
        else:
            failures.append({"calendar": cal, "error": str(body)})

    record = {
        "receipt_id": receipt_id,
        "created_at": created_at,
        "hash_hex": hash_hex,
        "sha512_hex": sha512_hex,
        "client_label": client_label,
        # source: "free" | "pack:<claim_code_prefix>" | "sub:<email_hash>" — used
        # by the expiry worker to identify free-tier receipts eligible for pruning.
        "source": source,
        # Private flag: when True, /api/receipt/<id> requires session cookie
        # matching owner_id. owner_id is the HMAC-keyed email_id (not the
        # plaintext email) — so an attacker with disk access cannot
        # dictionary-attack receipts→emails.
        "private": bool(private),
        "owner_id": owner_id,
        # Optional attestation: a free-form claim the anchorer makes about the
        # file at anchor time. Examples: {"claim": "I took this photo on
        # 2026-05-15", "author": "Orphograph", "license": "CC-BY 4.0"}. The
        # claim is part of the receipt JSON, so its existence at anchor time
        # is itself anchored to Bitcoin — anyone disputing it later would
        # need to forge the receipt's full content, not just the hash.
        "attestation": _sanitize_attestation(attestation),
        # Optional C2PA assertion: the hash of a Coalition for Content
        # Provenance and Authenticity manifest the anchorer wishes the
        # receipt to reference. The manifest itself stays with the file;
        # only its fingerprint is recorded here. Coexistence-first design.
        "c2pa_manifest_hash": c2pa_manifest_hash,
        # Optional metadata: client-extracted EXIF / file metadata. Strong
        # corroborating evidence in disputes (camera serial, capture time,
        # GPS). Caller is responsible for redacting fields they don't want
        # public (GPS often is redacted client-side before anchoring).
        "metadata": _sanitize_metadata(metadata),
        "calendars_ok": len(successes),
        "calendars_total": len(CALENDARS),
        "successes": successes,
        "failures": failures,
    }
    receipt_file = receipt_dir / "receipt.json"
    receipt_file.write_text(json.dumps(record, indent=2))
    try:
        os.chmod(receipt_file, 0o600)
    except OSError:
        pass
    _append_ledger(record)
    return record


def _sanitize_attestation(attestation: dict | None) -> dict | None:
    """Limit attestation to a small set of expected fields with size caps.

    Reject anything that looks like an attempt to smuggle binary data or
    a giant blob into the receipt. The attestation is meant to be a brief
    authorship claim, not a generic data dump.
    """
    if not attestation or not isinstance(attestation, dict):
        return None
    allowed = ("claim", "author", "license", "url", "signed_at")
    out: dict = {}
    for k in allowed:
        v = attestation.get(k)
        if isinstance(v, str):
            stripped = v.strip()
            if stripped:
                out[k] = stripped[:500]
    return out or None


def _sanitize_metadata(metadata: dict | None) -> dict | None:
    """Allow a known subset of EXIF / file metadata fields with size caps.

    Drops anything outside the allowlist. This protects against a malicious
    client trying to write large or sensitive data into the public receipt.
    """
    if not metadata or not isinstance(metadata, dict):
        return None
    allowed = (
        "filename", "size_bytes", "mime_type",
        "exif_camera_make", "exif_camera_model", "exif_camera_serial",
        "exif_lens", "exif_capture_time", "exif_software",
        "exif_iso", "exif_aperture", "exif_shutter", "exif_focal_length",
        # Note: GPS deliberately omitted — clients should redact GPS before
        # passing metadata to the server; we don't accept it here.
        "image_width", "image_height", "image_format",
    )
    out: dict = {}
    for k in allowed:
        v = metadata.get(k)
        if isinstance(v, str):
            stripped = v.strip()
            if stripped:
                out[k] = stripped[:200]
        elif isinstance(v, (int, float)) and -1e15 < v < 1e15:
            out[k] = v
    return out or None


def verify_receipt(receipt_id: str) -> dict:
    """Verify a receipt: check .ots magic + recover hash from .ots, return status."""
    receipt_dir = RECEIPTS_DIR / receipt_id
    receipt_file = receipt_dir / "receipt.json"
    if not receipt_file.exists():
        return {"receipt_id": receipt_id, "found": False, "error": "receipt not found"}
    try:
        record = json.loads(receipt_file.read_text())
        recorded_hex = record["hash_hex"]
        if not isinstance(recorded_hex, str) or len(recorded_hex) != 64:
            raise ValueError("malformed hash in receipt")
        expected_hash = bytes.fromhex(recorded_hex)
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return {"receipt_id": receipt_id, "found": False, "error": "corrupt receipt"}

    ots_files = sorted(receipt_dir.glob("*.ots"))
    checks = []
    for ots in ots_files:
        data = ots.read_bytes()
        magic_ok = data.startswith(OTS_HEADER_MAGIC)
        offset = len(OTS_HEADER_MAGIC) + 2
        embedded = data[offset:offset + 32] if magic_ok else b""
        hash_match = embedded == expected_hash
        checks.append({
            "file": ots.name,
            "magic_ok": magic_ok,
            "hash_match": hash_match,
            "ok": magic_ok and hash_match,
        })
    out = {
        "receipt_id": receipt_id,
        "found": True,
        "created_at": record["created_at"],
        "hash_hex": record["hash_hex"],
        "sha512_hex": record.get("sha512_hex"),
        "client_label": record.get("client_label"),
        "private": bool(record.get("private", False)),
        "owner_id": record.get("owner_id"),
        "attestation": record.get("attestation"),
        "metadata": record.get("metadata"),
        "calendars_ok": sum(1 for c in checks if c["ok"]),
        "calendars_total": len(checks),
        "status": record.get("status", "pending"),
        "btc_pinned_at": record.get("btc_pinned_at"),
        "checks": checks,
    }
    # Folder anchors carry three extra fields so verifiers can fetch the
    # manifest in addition to the .ots files. Only surface them when set
    # so single-file receipts remain shape-stable.
    if record.get("kind"):
        out["kind"] = record["kind"]
    if record.get("leaf_count") is not None:
        out["leaf_count"] = record["leaf_count"]
    if record.get("merkle_algorithm"):
        out["merkle_algorithm"] = record["merkle_algorithm"]
    # Optional Ed25519 authorship signature. Only surface when a signature
    # was actually presented at anchor time — single-file receipts and folder
    # anchors with no signature remain shape-stable.
    if "signature_verified" in record:
        out["signature_verified"] = bool(record["signature_verified"])
    if record.get("signer_kid"):
        out["signer_kid"] = record["signer_kid"]
    return out


def verify_hash_against_receipt(receipt_id: str, hash_hex: str) -> dict:
    """Verify that a supplied hash matches the receipt's anchored hash."""
    result = verify_receipt(receipt_id)
    if not result.get("found"):
        return result
    result["supplied_hash"] = hash_hex.strip().lower()
    result["supplied_matches_receipt"] = result["supplied_hash"] == result["hash_hex"]
    return result
