#!/usr/bin/env python3
"""engine.py — submit a SHA-256 hash to OpenTimestamps calendars and persist receipts.

Client computes the hash; this module never sees user content.
Stdlib only. Builds the .ots binary directly from calendar HTTP responses.

Public API:
    anchor_hash(hash_hex: str, client_label: str | None = None) -> dict
    verify_receipt(receipt_id: str) -> dict
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
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


# ── edit-lineage (docs/DESIGN_EDIT_LINEAGE.md) ────────────────────────────
# Reserved manifest path whose file_sha256_hex carries the PARENT receipt's
# anchored root. The leaf rides the existing orphograph-merkle-v1-rfc6962
# manifest unchanged (no merkle.py change, no algorithm-tag bump — design Q1
# working assumption), so the parent root is committed inside the anchored
# 32 bytes. The `.orphograph/` path prefix is documented as reserved.
RESERVED_PARENT_PATH = ".orphograph/parent"


def _is_receipt_id(s: object) -> bool:
    """True when s matches the receipt-id alphabet (alnum + _ -, 1..64 chars).

    Same rule the MCP applies before hitting /api/verify — the id is used to
    build filesystem paths under RECEIPTS_DIR, so the alphabet check doubles
    as a traversal guard.
    """
    return (
        isinstance(s, str)
        and 0 < len(s) <= 64
        and all(c.isalnum() or c in ("_", "-") for c in s)
    )


def _lineage_leaf_hex(parent_root_hex: str) -> str:
    """Recompute the reserved parent leaf exactly per merkle._leaf_hash.

    SHA-256(0x00 || ".orphograph/parent" || 0x00 || parent_root). Kept local
    (stdlib hashlib) so this module stays import-light; byte-identical to
    server/merkle._leaf_hash(RESERVED_PARENT_PATH, parent_root_bytes).
    """
    return hashlib.sha256(
        b"\x00" + RESERVED_PARENT_PATH.encode("utf-8") + b"\x00" + bytes.fromhex(parent_root_hex)
    ).hexdigest()


def derive_lineage_from_manifest(manifest: dict, verify_tree: bool = True) -> dict | None:
    """Derive the receipt's `lineage` mirror block from a folder manifest.

    Returns None when the manifest carries no lineage elements (the common
    case — plain folder anchors are untouched). Otherwise returns
    ``{"parent_receipt_id", "parent_root", "committed": True}`` — the block
    §2.3 of docs/DESIGN_EDIT_LINEAGE.md specifies — after recomputing the
    commitment from the reserved leaf itself. Hints are never trusted:
    ``committed`` is True only because the reserved leaf's ``leaf_hex`` is
    re-derived here from ``(RESERVED_PARENT_PATH, file_sha256_hex)`` and,
    with ``verify_tree=True``, the whole manifest is re-folded to its root
    via merkle.MerkleTree.from_manifest.

    Raises ValueError on every malformed-lineage shape (server maps these to
    400 per design §2.1):
      * reserved leaf present without a top-level ``parent`` block, or the
        converse;
      * ``parent.root_hex`` differing from the reserved leaf's
        ``file_sha256_hex``;
      * more than one reserved leaf;
      * reserved path with ``size_bytes != 0`` (design Q2 working
        assumption: a real user file may not shadow the reserved path);
      * non-canonical (not 64 lowercase hex) parent root;
      * a reserved ``leaf_hex`` that does not derive from its
        ``file_sha256_hex``;
      * an invalid ``parent.receipt_id``.
    """
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a dict")
    leaves = manifest.get("leaves")
    if not isinstance(leaves, list):
        raise ValueError("manifest leaves must be a list")
    reserved = [
        leaf for leaf in leaves
        if isinstance(leaf, dict) and leaf.get("path") == RESERVED_PARENT_PATH
    ]
    parent_block = manifest.get("parent")
    if not reserved:
        if parent_block is not None:
            raise ValueError(
                "manifest has a top-level parent block but no reserved "
                f"{RESERVED_PARENT_PATH!r} leaf"
            )
        return None
    if len(reserved) > 1:
        raise ValueError(f"manifest has more than one reserved {RESERVED_PARENT_PATH!r} leaf")
    leaf = reserved[0]
    # Q2 working assumption: reject a reserved-path leaf that claims bytes on
    # disk — the reserved leaf is synthetic and must carry size_bytes == 0.
    if leaf.get("size_bytes") != 0:
        raise ValueError(
            f"reserved {RESERVED_PARENT_PATH!r} leaf must have size_bytes 0 "
            "(the path is reserved for the parent-root commitment; rename any "
            "real file under .orphograph/)"
        )
    parent_root = leaf.get("file_sha256_hex")
    if not _is_hex(parent_root, 64):
        raise ValueError(
            "reserved parent leaf's file_sha256_hex must be 64 lowercase hex "
            "characters (the parent receipt's anchored root, canonical form)"
        )
    # Recompute the commitment — the hint block is never the authority.
    if _lineage_leaf_hex(parent_root) != leaf.get("leaf_hex"):
        raise ValueError(
            "reserved parent leaf_hex does not derive from its file_sha256_hex"
        )
    if not isinstance(parent_block, dict):
        raise ValueError(
            f"a manifest with a reserved {RESERVED_PARENT_PATH!r} leaf must "
            "carry a top-level parent block {receipt_id, root_hex}"
        )
    if parent_block.get("root_hex") != parent_root:
        raise ValueError(
            "parent.root_hex does not match the reserved leaf's file_sha256_hex"
        )
    parent_receipt_id = parent_block.get("receipt_id")
    if not _is_receipt_id(parent_receipt_id):
        raise ValueError("parent.receipt_id is not a valid receipt id")
    if verify_tree:
        # Confirm the reserved leaf is actually folded into root_hex — the
        # same recomputation the server runs at anchor time. Import is lazy
        # so engine keeps zero import-time coupling to merkle.
        try:
            import merkle as _merkle
        except ImportError:  # pragma: no cover — package-style import fallback
            from server import merkle as _merkle  # type: ignore[no-redef]
        _merkle.MerkleTree.from_manifest(manifest)  # raises ValueError on any mismatch
    return {
        "parent_receipt_id": parent_receipt_id,
        "parent_root": parent_root,
        "committed": True,
    }


def attach_lineage(receipt_id: str, manifest: dict) -> dict | None:
    """Mirror a manifest's committed lineage onto an existing receipt.

    Additive companion to the folder-anchor path: after a lineage manifest is
    anchored (its root is the receipt's hash_hex) and persisted, this derives
    the `lineage` block from the reserved leaf — recomputed, never trusted
    from hints — and rewrites ``receipt.json`` with it, the same post-anchor
    rewrite pattern the folder path already uses for kind/leaf_count.

    Returns ``{parent_receipt_id, parent_root, committed, parent_receipt_found}``
    (the design §2.4 response delta), or None for a non-lineage manifest.
    Raises ValueError when the manifest does not bind to this receipt, when
    the lineage elements are malformed, or when a locally known parent
    receipt's anchored hash contradicts the committed parent root.
    """
    if not _is_receipt_id(receipt_id):
        raise ValueError("invalid receipt id")
    lineage = derive_lineage_from_manifest(manifest)
    if lineage is None:
        return None
    receipt_file = RECEIPTS_DIR / receipt_id / "receipt.json"
    if not receipt_file.exists():
        raise ValueError("receipt not found")
    record = json.loads(receipt_file.read_text())
    if record.get("hash_hex") != manifest.get("root_hex"):
        raise ValueError("manifest root_hex does not match the receipt's anchored hash")
    # Design §2.1 rule 2: when the parent receipt exists locally its anchored
    # hash MUST equal the committed parent root; a missing parent is fine —
    # the commitment is self-contained (anchored elsewhere or pruned).
    parent_receipt_found = False
    parent_file = RECEIPTS_DIR / lineage["parent_receipt_id"] / "receipt.json"
    if parent_file.exists():
        try:
            parent_record = json.loads(parent_file.read_text())
        except (OSError, json.JSONDecodeError):
            parent_record = {}
        if parent_record.get("hash_hex") != lineage["parent_root"]:
            raise ValueError(
                "parent receipt exists locally but its anchored hash does not "
                "match the committed parent root"
            )
        parent_receipt_found = True
    record["lineage"] = lineage
    receipt_file.write_text(json.dumps(record, indent=2))
    try:
        os.chmod(receipt_file, 0o600)
    except OSError:
        pass
    return {**lineage, "parent_receipt_found": parent_receipt_found}


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
    zk_proof: dict | None = None,
    hardware_attestation: dict | None = None,
    parent_root: str | None = None,
    parent_receipt_id: str | None = None,
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
    # Optional edit-lineage hints (docs/DESIGN_EDIT_LINEAGE.md §2.2). On this
    # bare single-hash path they are RECORDED ONLY — never committed inside
    # the anchored 32 bytes. Binding lineage goes through the folder-manifest
    # path (reserved leaf + attach_lineage), which is the only place
    # `committed` becomes true. Canonical form is required, not repaired.
    if parent_root is not None:
        parent_root = parent_root.strip()
        if not _is_hex(parent_root, 64):
            raise ValueError("parent_root must be 64 lowercase hex characters")
    if parent_receipt_id is not None:
        parent_receipt_id = parent_receipt_id.strip()
        if not _is_receipt_id(parent_receipt_id):
            raise ValueError("parent_receipt_id is not a valid receipt id")
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
    # Optional zero-knowledge provenance proof (machine proof, distinct from
    # the human `attestation` claim above). Sanitized to a strict shape; only
    # written when present so existing receipts remain shape-stable.
    zk_sanitized = _sanitize_zk_provenance(zk_proof, hash_hex)
    if zk_sanitized is not None:
        record["zk_provenance"] = zk_sanitized
    # Optional hardware attestation (docs/HARDWARE_ATTESTATION_SPIKE.md §3):
    # a device-resident-key signature over the anchored hash. Machine artifact
    # like zk_provenance, NOT a human claim — so it gets its own strict
    # sanitizer and is only written when present (shape-stability rule).
    # Honest scope: it says "a hardware-resident key signed this hash at
    # capture time" — never scene/content authenticity or authorship.
    hw_sanitized = _sanitize_hardware_attestation(hardware_attestation, hash_hex)
    if hw_sanitized is not None:
        record["hardware_attestation"] = hw_sanitized
    # Edit-lineage hints: written only when supplied so existing receipts
    # remain shape-stable. `committed` is False here by construction — a
    # bare hash anchor cannot commit a parent inside the anchored bytes;
    # offline verifiers report such links as recorded_only, never committed.
    if parent_root is not None or parent_receipt_id is not None:
        record["lineage"] = {
            "parent_receipt_id": parent_receipt_id,
            "parent_root": parent_root,
            "committed": False,
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


def _sanitize_zk_provenance(proof: dict | None, hash_hex: str) -> dict | None:
    """Strictly validate a zero-knowledge provenance proof for the receipt.

    Unlike `attestation` (a brief human authorship claim), this field carries
    a machine-verifiable proof whose numeric fields are large decimal strings
    (a 2048-bit group element is ~617 digits) — so it gets its own sanitizer
    with shape validation instead of the attestation allowlist's short caps.

    Rejects the whole proof (returns None) on ANY violation rather than
    persisting a partial proof that could never re-verify.
    """
    if not proof or not isinstance(proof, dict):
        return None
    if proof.get("proof_type") == "snark-exec-v1":
        return _sanitize_snark_exec_v1(proof, hash_hex)
    if proof.get("proof_type") not in ("schnorr-zk-pok-v1",):
        return None
    # The proof must be bound to the hash being anchored — a proof for a
    # different output riding on this receipt is meaningless at best.
    output_hash = proof.get("output_hash")
    if not isinstance(output_hash, str) or output_hash.strip().lower() != hash_hex:
        return None
    out = {"proof_type": proof["proof_type"], "output_hash": hash_hex}
    model_id = proof.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    out["model_id"] = model_id.strip()[:200]
    # Decimal group elements / scalars: digits only, bounded well above the
    # 2048-bit maximum (~617 digits) but low enough to stop blob-smuggling.
    for k in ("commitment", "A", "s1", "s2", "challenge"):
        v = proof.get(k)
        if not isinstance(v, str):
            return None
        v = v.strip()
        if not v or len(v) > 700 or not v.isdigit():
            return None
        out[k] = v
    return out


def _snark_bits_to_hex(bits: list) -> str | None:
    """MSB-first bit list ('0'/'1' strings) → hex, or None if malformed.

    Mirrors zk-provenance/snark/check_public.py exactly — the two must never
    diverge or receipts and the circuit toolchain would disagree on identity.
    """
    v = 0
    for b in bits:
        if b not in ("0", "1"):
            return None
        v = (v << 1) | int(b)
    return v.to_bytes(len(bits) // 8, "big").hex()


def _sanitize_snark_exec_v1(proof: dict, hash_hex: str) -> dict | None:
    """snark-exec-v1: a groth16 proof that PROGRAM_V2 (the 8-round SHA-256
    chain) produced the anchored output.

    The server performs every check that is PURE HASHING — these are real
    bindings, not decoration:
      1. output_hash == the hash being anchored;
      2. output_hash == SHA-256("out2:" + hex(stN)) recomputed from the
         proof's own public signals (signals [0:256], MSB-first) — a proof
         whose circuit output does not hash to the anchored value is
         rejected outright;
      3. st0 (signals [512:768]) == SHA-256("orpho-prog-v2" || model_id) —
         the claimed model binds to the circuit's public input.
    The groth16 pairing check itself is NOT run here (no snarkjs on the
    server); verifiers run it client-side against vk_sha256's key. A proof
    that passes 1-3 but fails the pairing check is caught there — see the
    forgery test in tests/test_snark_receipt.py. Whole-record rejection on
    any violation, like every provenance sanitizer in this file.
    """
    output_hash = proof.get("output_hash")
    if not isinstance(output_hash, str) or output_hash.strip().lower() != hash_hex:
        return None
    model_id = proof.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    model_id = model_id.strip()[:200]
    if proof.get("program") != "orpho-prog-v2/8":
        return None
    if proof.get("protocol") != "groth16" or proof.get("curve") != "bn128":
        return None
    vk_sha256 = proof.get("vk_sha256")
    if not isinstance(vk_sha256, str):
        return None
    vk_sha256 = vk_sha256.strip().lower()
    if len(vk_sha256) != 64 or any(c not in "0123456789abcdef" for c in vk_sha256):
        return None

    signals = proof.get("public_signals")
    if not isinstance(signals, list) or len(signals) != 768:
        return None
    st_n = _snark_bits_to_hex(signals[0:256])
    commitment = _snark_bits_to_hex(signals[256:512])
    st0 = _snark_bits_to_hex(signals[512:768])
    if st_n is None or commitment is None or st0 is None:
        return None
    # Binding 2: the anchored hash must be the hash of the circuit's output.
    recomputed = hashlib.sha256(("out2:" + st_n).encode()).hexdigest()
    if recomputed != hash_hex:
        return None
    # Binding 3: st0 is the program's domain-separated model commitment.
    expected_st0 = hashlib.sha256(b"orpho-prog-v2" + model_id.encode()).hexdigest()
    if st0 != expected_st0:
        return None

    # groth16 proof shape: bn128 field elements are < 2^254 (≤ 77 decimal
    # digits); cap at 80. pi_a/pi_c: 3 elements; pi_b: 3 pairs.
    def _dec(v) -> bool:
        return isinstance(v, str) and 0 < len(v) <= 80 and v.isdigit()

    p = proof.get("proof")
    if not isinstance(p, dict):
        return None
    pi_a, pi_b, pi_c = p.get("pi_a"), p.get("pi_b"), p.get("pi_c")
    if not (isinstance(pi_a, list) and len(pi_a) == 3 and all(_dec(v) for v in pi_a)):
        return None
    if not (isinstance(pi_c, list) and len(pi_c) == 3 and all(_dec(v) for v in pi_c)):
        return None
    if not (isinstance(pi_b, list) and len(pi_b) == 3
            and all(isinstance(row, list) and len(row) == 2
                    and all(_dec(v) for v in row) for row in pi_b)):
        return None

    return {
        "proof_type": "snark-exec-v1",
        "output_hash": hash_hex,
        "model_id": model_id,
        "program": "orpho-prog-v2/8",
        "protocol": "groth16",
        "curve": "bn128",
        "vk_sha256": vk_sha256,
        "public_signals": signals,
        "proof": {"pi_a": pi_a, "pi_b": pi_b, "pi_c": pi_c},
        # Derived identities — stored so verifiers and humans read them
        # without re-deriving; the sanitizer proved them consistent above.
        "stN_hex": st_n,
        "commitment_hex": commitment,
        "st0_hex": st0,
    }
# ─── Hardware attestation (docs/HARDWARE_ATTESTATION_SPIKE.md §3.2) ──────────
# v1 attestation types. "p256-device-sig-v1" is the element-agnostic P-256
# shape the spike doc specifies; SE / ATECC / TPM all emit it.
HW_ATTESTATION_TYPES = ("p256-device-sig-v1",)
HW_COUNTER_KINDS = ("software", "hardware")
# Fixed 26-byte SubjectPublicKeyInfo prefix for an uncompressed P-256 point
# (ecPublicKey OID + prime256v1 OID + BIT STRING header). A v1 device_pubkey
# MUST be exactly this prefix + 0x04 || X || Y (91 bytes total).
HW_P256_SPKI_PREFIX = bytes.fromhex(
    "3059301306072a8648ce3d020106082a8648ce3d030107034200"
)
# ISO-8601 UTC-ish timestamp: claimed client clock readings, format-checked
# only — they are corroborating hints, never load-bearing time (H3: the only
# load-bearing time bound remains the OTS→Bitcoin path).
_HW_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?$"
)


def _hw_b64_decode(value: str, max_chars: int) -> bytes | None:
    """Strict base64 decode with a length cap. None on any violation."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > max_chars:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


def _sanitize_hardware_attestation(att: dict | None, hash_hex: str) -> dict | None:
    """Strictly validate a hardware-attestation payload for the receipt.

    Mirrors `_sanitize_zk_provenance`: this is a machine-verifiable
    cryptographic artifact (a device-resident-key ECDSA signature over the
    anchored hash), not a human claim, so it gets its own shape validator
    instead of the `attestation` allowlist's short caps.

    Rejects the WHOLE record (returns None) on ANY violation — never
    persists a partial attestation that could not re-verify offline.

    Honest scope (binding): a valid record means "a device-held key signed
    this hash at capture time" under first-use (TOFU) pinning. It does NOT
    mean the content is authentic capture, who made it, or that the device
    was uncompromised. The signature is checked offline by verifiers
    (dist/orphograph-verify/verify_hw.py), not here — the server stays
    dependency-free and never becomes the trust root.
    """
    if not att or not isinstance(att, dict):
        return None
    if att.get("attestation_type") not in HW_ATTESTATION_TYPES:
        return None
    # Bind to the hash being anchored — an attestation for a different hash
    # riding on this receipt is the attestation-swapper adversary.
    att_hash = att.get("hash_hex")
    if not isinstance(att_hash, str) or att_hash.strip().lower() != hash_hex:
        return None
    # Device public key: base64 DER SubjectPublicKeyInfo, uncompressed P-256.
    pubkey_b64 = att.get("device_pubkey")
    pubkey_der = _hw_b64_decode(pubkey_b64, 200) if isinstance(pubkey_b64, str) else None
    if pubkey_der is None or len(pubkey_der) != 91:
        return None
    if not pubkey_der.startswith(HW_P256_SPKI_PREFIX):
        return None
    # device_id is DERIVED (SHA-256 of the pubkey DER), never merely asserted
    # — so it cannot disagree with device_pubkey.
    device_id = att.get("device_id")
    if not isinstance(device_id, str):
        return None
    device_id = device_id.strip().lower()
    if device_id != hashlib.sha256(pubkey_der).hexdigest():
        return None
    # Claimed timestamps: signed_at (signing moment, inside the signed
    # message) and key_created_at (the TOFU pinning moment — when the device
    # key was first created). Format-checked, corroborating only.
    signed_at = att.get("signed_at")
    if not isinstance(signed_at, str) or len(signed_at) > 40 \
            or not _HW_TS_RE.match(signed_at.strip()):
        return None
    key_created_at = att.get("key_created_at")
    if not isinstance(key_created_at, str) or len(key_created_at) > 40 \
            or not _HW_TS_RE.match(key_created_at.strip()):
        return None
    # Counter: ordering hint. bool is an int subclass — reject explicitly.
    counter = att.get("counter")
    if isinstance(counter, bool) or not isinstance(counter, int) \
            or not (0 <= counter < 2 ** 64):
        return None
    if att.get("counter_kind") not in HW_COUNTER_KINDS:
        return None
    # Signature: base64 DER ECDSA-SHA256 (SEQUENCE of two INTEGERs).
    sig_b64 = att.get("signature")
    sig_der = _hw_b64_decode(sig_b64, 200) if isinstance(sig_b64, str) else None
    if sig_der is None or not (8 <= len(sig_der) <= 72) or sig_der[0] != 0x30:
        return None
    out = {
        "attestation_type": att["attestation_type"],
        "hash_hex": hash_hex,
        "device_id": device_id,
        "device_pubkey": pubkey_b64.strip(),
        "signed_at": signed_at.strip(),
        "key_created_at": key_created_at.strip(),
        "counter": counter,
        "counter_kind": att["counter_kind"],
        "signature": sig_b64.strip(),
    }
    # element: a client-asserted label (like model_id in the ZK layer — no
    # cryptographic tie to the actual silicon in v1). Optional.
    element = att.get("element")
    if element is not None:
        if not isinstance(element, str):
            return None
        element = element.strip()
        if not element or len(element) > 60:
            return None
        out["element"] = element
    # cert_chain: OPTIONAL, expected ABSENT in v1 (TOFU). Present only for
    # elements that ship manufacturer certs; capped hard.
    cert_chain = att.get("cert_chain")
    if cert_chain is not None:
        if not isinstance(cert_chain, list) or not (1 <= len(cert_chain) <= 4):
            return None
        chain_out = []
        for cert in cert_chain:
            if not isinstance(cert, str):
                return None
            if _hw_b64_decode(cert, 4000) is None:
                return None
            chain_out.append(cert.strip())
        out["cert_chain"] = chain_out
    return out


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
    # ZK provenance proof: surfaced only when present (same shape-stability
    # rule as the folder fields below).
    if record.get("zk_provenance"):
        out["zk_provenance"] = record["zk_provenance"]
    # Hardware attestation: surfaced only when present (same shape-stability
    # rule). Scope: "a device-held key signed this hash at capture time"
    # under TOFU pinning — offline signature check is verify_hw.py's job.
    if record.get("hardware_attestation"):
        out["hardware_attestation"] = record["hardware_attestation"]
    # Edit-lineage mirror: surfaced only when present (same shape-stability
    # rule). The block is an index, not the proof — the committed authority
    # is the reserved leaf inside the manifest + the .ots files. The
    # parent_receipt_found flag is recomputed live on every verify (never
    # stored) so a parent pruned after anchoring is reported honestly and
    # the UI links /r/<parent> only when it actually resolves here.
    if record.get("lineage"):
        lineage_out = dict(record["lineage"])
        parent_id = lineage_out.get("parent_receipt_id")
        if _is_receipt_id(parent_id):
            lineage_out["parent_receipt_found"] = (
                RECEIPTS_DIR / parent_id / "receipt.json"
            ).exists()
        out["lineage"] = lineage_out
    if record.get("kind"):
        out["kind"] = record["kind"]
    if record.get("leaf_count") is not None:
        out["leaf_count"] = record["leaf_count"]
    if record.get("merkle_algorithm"):
        out["merkle_algorithm"] = record["merkle_algorithm"]
    # Opt-in: a folder receipt whose owner has chosen to publish file paths
    # (e.g. a shareable provenance certificate) skips the default path
    # redaction in /api/verify_folder. Absent/false → paths stay owner-only.
    if record.get("paths_public"):
        out["paths_public"] = True
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
