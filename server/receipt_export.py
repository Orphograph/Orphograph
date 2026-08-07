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


def export_zip(receipt_id: str) -> tuple[bytes | None, str | None]:
    """Export a receipt as a ZIP file containing receipt.json + 5 .ots proofs.

    Returns (zip_bytes, None) on success.
    Returns (None, "not_found") if the receipt directory or receipt.json
    is absent.
    Returns (None, "broken") if the receipt exists but we couldn't build
    the zip (disk error, malformed file, etc.) — caller should surface 500.
    """
    receipt_dir = RECEIPTS_DIR / receipt_id

    if not receipt_dir.is_dir():
        return None, NOT_FOUND
    receipt_json = receipt_dir / "receipt.json"
    if not receipt_json.exists():
        return None, NOT_FOUND

    buf = io.BytesIO()
    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(receipt_json, arcname="receipt.json")
            # Folder/lineage anchors: the manifest is part of the verifiable
            # bundle (offline lineage walking needs it — design §3). Absent
            # for single-file receipts; included only when present.
            manifest_json = receipt_dir / "manifest.json"
            if manifest_json.exists():
                zf.write(manifest_json, arcname="manifest.json")
            for ots_file in sorted(receipt_dir.glob("*.ots")):
                zf.write(ots_file, arcname=ots_file.name)
    except (OSError, zipfile.BadZipFile) as e:
        sys.stderr.write(f"[receipt_export] could not build zip for {receipt_id}: {e}\n")
        return None, BROKEN

    return buf.getvalue(), None


def export_readable_json(receipt_id: str) -> tuple[dict | None, str | None]:
    """Export receipt as a human-readable summary dict.

    Returns (summary_dict, None) on success.
    Returns (None, "not_found") if receipt absent.
    Returns (None, "broken") if receipt exists but is corrupt — caller
    should surface 500 + log so the founder sees the data-integrity event.
    """
    receipt_dir = RECEIPTS_DIR / receipt_id
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
        **data,
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
