#!/usr/bin/env python3
"""usb_air_gap_hash.py — offline-machine half of the air-gapped anchoring workflow.

This script runs on a machine that holds the customer's sensitive files and
that MUST remain disconnected from the internet for the duration of the run.
Its job is to produce a Merkle manifest (paths + 32-byte SHA-256 digests
only — never file contents) onto a USB drive, so the customer can carry the
manifest to an online machine and submit it for anchoring there.

Hard invariants enforced in this file:

  * No network usage of any kind. This module deliberately does NOT import
    urllib, http, socket, or requests. A greppable invariant guards it.
  * USB writes go ONLY through scripts/_usb_safety primitives. Nothing
    already on the USB is removed, renamed, truncated, or overwritten.
  * The customer's file contents stay on the offline machine. Only paths
    and digests cross to the USB.

Stdlib only. MIT-licensed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the repo's server/ importable for the MerkleTree primitive. The merkle
# module itself depends only on stdlib (hashlib, json, pathlib).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SERVER_DIR = _REPO_ROOT / "server"
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from _usb_safety import (  # noqa: E402
    UsbSafetyError,
    assert_drive_writable,
    manifest_of_writes,
    reserve_new_path,
    safe_mkdir,
    safe_write_bytes,
    safe_write_text,
    stamp_dirname,
)
from merkle import MerkleTree  # noqa: E402


EXIT_OK = 0
EXIT_ARG_OR_FS = 1
EXIT_USB_SAFETY = 2
EXIT_EMPTY_OR_UNREADABLE = 3


PENDING_README = """\
Air-gapped manifest ready for online submission.

What is on this USB now:
  * manifest.json     — paths and 32-byte SHA-256 digests for each file in the
                        source folder. File contents are NOT included.
  * PENDING_README.txt — this note.
  * WHAT_WAS_ADDED.json — additive inventory of every file added by the
                          offline script.

What happened on the offline machine:
  * Each file in the source folder was read once and hashed locally.
  * No network connection was opened. The offline script imports no network
    module.
  * The source folder was not modified. Nothing already on this USB was
    modified.

Next step, performed on a machine with internet:
  1. Plug this USB into the online machine.
  2. Run scripts/usb_offline_anchor_submit.py with --manifest-subdir set to
     this directory name.
  3. The online script will POST the manifest to orphograph.com and write the
     receipt into a NEW sibling directory on this USB. This directory will be
     left exactly as it is.

After step 3, carry the USB back to the offline machine. The receipt and a
local verifier will be in the new sibling directory. Verification can be
performed offline against the original folder.

Root hash for this manifest is recorded inside manifest.json under the
"root_hex" key.
"""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="usb_air_gap_hash.py",
        description=(
            "Offline-machine half of the air-gapped anchoring workflow. "
            "Hashes a folder and writes only paths and digests to a USB. "
            "No network access is performed or required."
        ),
    )
    p.add_argument(
        "--usb",
        required=True,
        type=Path,
        help="Path to the writable USB volume (e.g. /Volumes/AIR_GAP_USB).",
    )
    p.add_argument(
        "--folder",
        required=True,
        type=Path,
        help="Path to the source folder on the offline machine.",
    )
    p.add_argument(
        "--label",
        default="",
        help="Optional short label stored alongside the manifest.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan the writes but do not touch the USB.",
    )
    return p.parse_args(argv)


def _build_manifest(folder: Path, label: str) -> dict:
    tree = MerkleTree.from_folder(folder)
    manifest = tree.manifest()
    # Add an envelope around the bare merkle manifest so the online submitter
    # has full context. The merkle fields (algorithm, version, root_hex,
    # leaves) remain untouched.
    enveloped = {
        "source_folder_name": folder.name,
        "label": label or "",
        "private": True,
        "merkle": manifest,
    }
    return enveloped


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else EXIT_ARG_OR_FS

    folder = args.folder.expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        print(f"error: source folder is not a directory: {folder}", file=sys.stderr)
        return EXIT_EMPTY_OR_UNREADABLE

    # Validate the USB mount before reading the source folder.
    try:
        usb_root = assert_drive_writable(args.usb)
    except UsbSafetyError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USB_SAFETY

    # Build the manifest. MerkleTree.from_folder raises ValueError on empty
    # folders or non-directories.
    try:
        manifest = _build_manifest(folder, args.label)
    except ValueError as e:
        print(f"error: source folder unusable: {e}", file=sys.stderr)
        return EXIT_EMPTY_OR_UNREADABLE
    except OSError as e:
        print(f"error: could not read source folder: {e}", file=sys.stderr)
        return EXIT_EMPTY_OR_UNREADABLE

    root_hex = manifest["merkle"]["root_hex"]
    leaf_count = len(manifest["merkle"]["leaves"])

    # Reserve a fresh subdir on the USB. The receipt id is unknown at this
    # stage, so the stamp uses the merkle root as a stable identifier.
    subdir_name = stamp_dirname(root_hex[:16], "offline_manifest")
    try:
        target = reserve_new_path(usb_root, subdir_name)
    except UsbSafetyError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USB_SAFETY

    if args.dry_run:
        print("dry-run plan:")
        print(f"  source folder        : {folder}")
        print(f"  usb mount            : {usb_root}")
        print(f"  new subdir on usb    : {target}")
        print(f"  files in manifest    : {leaf_count}")
        print(f"  root_hex             : {root_hex}")
        print("  no network was used.")
        return EXIT_OK

    try:
        safe_mkdir(target)
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
        safe_write_bytes(target / "manifest.json", manifest_bytes)
        safe_write_text(target / "PENDING_README.txt", PENDING_README)
        added = manifest_of_writes(target)
        safe_write_bytes(
            target / "WHAT_WAS_ADDED.json",
            json.dumps(added, indent=2).encode("utf-8"),
        )
    except UsbSafetyError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USB_SAFETY
    except OSError as e:
        print(f"error: filesystem error during write: {e}", file=sys.stderr)
        return EXIT_ARG_OR_FS

    print("offline manifest written.")
    print(f"  target               : {target}")
    print(f"  root_hex             : {root_hex}")
    print(f"  leaves               : {leaf_count}")
    print("  no network was used.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
