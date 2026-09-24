#!/usr/bin/env python3
"""receipt_export.py — export receipts as ZIP bundles.

Public API:
    export_zip(receipt_id: str) -> bytes | None
        Returns ZIP file bytes containing receipt.json + 5 .ots files,
        or None if receipt not found or export failed.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
RECEIPTS_DIR = Path(os.environ.get("ORPHO_RECEIPTS_DIR", str(DATA_DIR / "receipts")))

# Sentinel return value distinguishing "receipt does not exist on disk"
# from "receipt exists but we could not read/serve it". Callers translate
# the latter into a 500 so the customer sees an actionable error instead
# of a misleading 404 — paying subscribers trying to download their vault
# deserve to know the difference.
NOT_FOUND = "not_found"
BROKEN = "broken"

# receipt.json holds proof data AND operational fields: `notify_email` (where
# upgrade notices go), `owner_id` (an HMAC of the owner's email), a `source`
# tag that can carry the same HMAC ("sub:<id>") or a key/code prefix, and the
# upgrade worker's bookkeeping. The JSON route answers from an ALLOW-list
# (engine.verify_receipt); the /summary and .zip exports spread the raw file,
# so a public receipt served the buyer's email address to anyone (found
# 2026-09-23: 23 public receipts in production carried one). Public exports
# are now an allow-list too: a field added to receipt.json later stays out of
# them until someone decides it is proof data.
EXPORT_FIELDS = frozenset({
    # identity and the anchored digests
    "receipt_id", "created_at", "hash_hex", "sha512_hex", "client_label",
    # what the anchor carried
    "attestation", "c2pa_manifest_hash", "metadata", "zk_provenance",
    "hardware_attestation", "lineage",
    # the calendar/Bitcoin evidence and its public status
    "calendars_ok", "calendars_total", "successes", "failures",
    "status", "btc_pinned_at", "pinned_count", "pinned_total",
    # folder receipts
    "kind", "leaf_count", "merkle_algorithm", "paths_public",
    "private",
})
# `source` is renewal CORE (renewal.CORE_ALWAYS): an offline renewal check
# re-hashes it, so a bundle whose receipt carries renewal records must ship
# it. Otherwise it stays out: "sub:<id>" links every receipt one subscriber
# made, "api:"/"pack:" leak a key or claim-code prefix.
SOURCE_FIELD = "source"
_NEVER_EXPORTED = frozenset({"notify_email"})


def public_receipt_view(data: dict, *, owner_view: bool, keep_source: bool = False) -> dict:
    """receipt.json as an export may show it.

    The owner's view of a PRIVATE receipt is the whole record minus
    `notify_email`. Everyone else gets EXPORT_FIELDS only, plus `source` when
    `keep_source` (the bundle carries renewal records that re-hash it)."""
    if owner_view and data.get("private"):
        return {k: v for k, v in data.items() if k not in _NEVER_EXPORTED}
    out = {k: data[k] for k in EXPORT_FIELDS if k in data}
    if keep_source and SOURCE_FIELD in data:
        out[SOURCE_FIELD] = data[SOURCE_FIELD]
    return out


def _has_renewal_records(receipt_dir: Path) -> bool:
    rd = receipt_dir / "renewal"
    return rd.is_dir() and any(rd.glob("*.json"))


# A folder manifest's own schema (merkle.MerkleTree.manifest() plus the two
# keys the anchor route adds and the lineage hint). /api/anchor_folder used to
# persist the whole request body as manifest.json, so request fields could sit
# in it; both the stored copy and every export are projected to these.
MANIFEST_FIELDS = frozenset({"algorithm", "version", "root_hex", "leaves",
                             "scope", "receipt_id", "kind", "parent", "signature"})
LEAF_FIELDS = ("path", "file_sha256_hex", "leaf_hex", "size_bytes")
# The reserved lineage leaf (engine.RESERVED_PARENT_PATH) is structure, not a
# customer's file: redacting it would break every offline lineage walk.
RESERVED_PARENT_PATH = ".orphograph/parent"

PATHS_REDACTION_REASON = (
    "Leaf paths are visible only to the receipt owner. Each "
    "file's SHA-256 digest and size remain public, so anyone "
    "holding a candidate file can confirm its membership; only "
    "the human-readable paths are withheld. Inclusion proofs "
    "remain available to anyone who already knows the path of "
    "the file they wish to prove. This projected manifest cannot reproduce a "
    "renewal commitment to the original manifest bytes."
)


def manifest_view(manifest: dict, *, redact_paths: bool) -> dict:
    """A folder manifest projected to its own schema; with `redact_paths`,
    every customer path withheld (index, leaf hash, file digest and size stay;
    the reserved lineage leaf keeps its path). The one implementation the
    anchor route, the folder verify route and the .zip all use."""
    out = {k: manifest[k] for k in MANIFEST_FIELDS if k in manifest and k != "leaves"}
    leaves = []
    for i, leaf in enumerate(manifest.get("leaves", []) or []):
        leaf = leaf if isinstance(leaf, dict) else {}
        view = {k: leaf.get(k) for k in LEAF_FIELDS if k in leaf}
        if redact_paths:
            path = leaf.get("path")
            keep = path == RESERVED_PARENT_PATH
            view = {"index": i, **{k: v for k, v in view.items() if keep or k != "path"}}
        leaves.append(view)
    out["leaves"] = leaves
    if redact_paths:
        out["paths_redacted"] = True
        out["paths_redaction_reason"] = PATHS_REDACTION_REASON
    if "signature" in manifest:
        # Signature bytes bind the complete pre-anchor manifest, including paths.
        # Never present a signature as usable over a changed projection.
        signed_keys = set(manifest) - {"signature", "receipt_id", "kind"}
        projected_keys = set(out) - {"signature", "receipt_id", "kind"}
        unchanged = signed_keys == projected_keys and all(
            manifest[k] == out[k] for k in signed_keys)
        sig = manifest["signature"]
        if unchanged and isinstance(sig, dict):
            out["signature"] = {k: sig[k] for k in
                                ("alg", "curve", "kid", "signature_b64") if k in sig}
        else:
            out.pop("signature", None)
            out["signature_unavailable_reason"] = "Manifest projection changed signed fields; obtain the original owner manifest to verify its signature."
    return out


def redact_manifest_paths(manifest: dict) -> dict:
    return manifest_view(manifest, redact_paths=True)


def export_zip(receipt_id: str, *, owner_view: bool = False) -> tuple[bytes | None, str | None]:
    """Export a receipt as a ZIP file containing receipt.json + 5 .ots proofs.

    Returns (zip_bytes, None) on success.
    Returns (None, "not_found") if the receipt directory or receipt.json
    is absent.
    Returns (None, "broken") if the receipt exists but we couldn't build
    the zip (disk error, malformed file, etc.) — caller should surface 500.
    """
    if not isinstance(receipt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", receipt_id):
        return None, NOT_FOUND
    # Check the resolved path as well as the identifier: a valid-looking
    # receipt directory must not be a symlink escaping the receipt store.
    base_path = os.path.realpath(RECEIPTS_DIR)
    fullpath = os.path.realpath(os.path.join(base_path, receipt_id))
    if not fullpath.startswith(base_path + os.sep):
        return None, NOT_FOUND
    receipt_dir = Path(fullpath)

    if not receipt_dir.is_dir():
        return None, NOT_FOUND
    receipt_json = receipt_dir / "receipt.json"
    if not receipt_json.exists():
        return None, NOT_FOUND

    try:
        data = json.loads(receipt_json.read_text())
    except (OSError, json.JSONDecodeError) as e:
        sys.stderr.write(f"[receipt_export] corrupt receipt {receipt_id}: {e}\n")
        return None, BROKEN
    buf = io.BytesIO()
    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            view = public_receipt_view(data, owner_view=owner_view,
                                       keep_source=_has_renewal_records(receipt_dir))
            zf.writestr("receipt.json", json.dumps(view, indent=2))
            # Folder/lineage anchors: the manifest is part of the verifiable
            # bundle (offline lineage walking needs it — design §3). Absent
            # for single-file receipts; included only when present. Paths are
            # owner-only unless the owner published them (`paths_public`),
            # the same rule the folder verify route applies.
            manifest_json = receipt_dir / "manifest.json"
            if manifest_json.exists():
                manifest = json.loads(manifest_json.read_text())
                redact = not (owner_view or data.get("paths_public"))
                projected = manifest_view(manifest, redact_paths=redact)
                # Preserve the original bytes when projection changes nothing:
                # renewal records may commit the raw manifest digest.
                payload = (manifest_json.read_bytes() if projected == manifest else
                           json.dumps(projected, indent=2).encode("utf-8"))
                zf.writestr("manifest.json", payload)
            for ots_file in sorted(receipt_dir.glob("*.ots")):
                zf.write(ots_file, arcname=ots_file.name)
            # Renewal records. Without these the bundle is NOT self-sufficient:
            # verify_renewal.py ships in the same download and treats a
            # missing batch block as a hard failure, so a customer who had
            # renewed would export a bundle that our own verifier could not
            # check the renewals from. The records are small JSON and each
            # carries its own inclusion proof.
            renewal_dir = receipt_dir / "renewal"
            if renewal_dir.is_dir():
                for rec in sorted(renewal_dir.glob("*.json")):
                    zf.write(rec, arcname=f"renewal/{rec.name}")
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as e:
        sys.stderr.write(f"[receipt_export] could not build zip for {receipt_id}: {e}\n")
        return None, BROKEN

    return buf.getvalue(), None


def export_readable_json(receipt_id: str, *, owner_view: bool = False) -> tuple[dict | None, str | None]:
    """Export receipt as a human-readable summary dict.

    Returns (summary_dict, None) on success.
    Returns (None, "not_found") if receipt absent.
    Returns (None, "broken") if receipt exists but is corrupt — caller
    should surface 500 + log so the founder sees the data-integrity event.
    """
    if not isinstance(receipt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", receipt_id):
        return None, NOT_FOUND
    # Check the resolved path as well as the identifier: a valid-looking
    # receipt directory must not be a symlink escaping the receipt store.
    base_path = os.path.realpath(RECEIPTS_DIR)
    fullpath = os.path.realpath(os.path.join(base_path, receipt_id))
    if not fullpath.startswith(base_path + os.sep):
        return None, NOT_FOUND
    receipt_dir = Path(fullpath)
    receipt_json = receipt_dir / "receipt.json"

    if not receipt_json.exists():
        return None, NOT_FOUND

    try:
        with receipt_json.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        sys.stderr.write(f"[receipt_export] corrupt receipt {receipt_id}: {e}\n")
        return None, BROKEN

    return {
        **public_receipt_view(data, owner_view=owner_view),  # no `source`: not a verifier input
        "what_this_proves": "This file hash existed on the specified date, anchored to the Bitcoin blockchain.",
        "what_this_does_not_prove": [
            "Does not prove you created the file",
            "Does not prove you own the copyright",
            "Does not prove the file is original or unique",
            "Is not court-admissible legal evidence",
            "Does not prevent others from copying the file",
        ],
        "how_to_verify": (
            "Two independent checks, in order. (1) STRUCTURE — the MIT-licensed "
            "verifier at https://github.com/Orphograph/Orphograph checks this "
            "receipt's internal consistency and the shape of its .ots files. It "
            "makes no network calls and does NOT consult Bitcoin. (2) CHAIN — "
            "run the OpenTimestamps client (`ots verify <file>.ots`) from "
            "https://github.com/opentimestamps/opentimestamps-client to confirm "
            "the commitment actually landed in a Bitcoin block. Only step 2 "
            "checks the chain."
        ),
    }, None
