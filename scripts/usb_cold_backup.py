"""usb_cold_backup.py — cold-storage backup of receipts to a USB drive.

Each run creates a NEW timestamped subdirectory on the USB drive containing
a full copy of the local receipts directory and the verifier program.
Existing files on the drive — including older backups produced by this
script — are never touched.

Usage::

    python3 scripts/usb_cold_backup.py \\
        --usb /Volumes/BACKUP_USB \\
        [--receipts-dir <path>] \\
        [--dry-run]

Exit codes:
    0  success (including the no-receipts-found case)
    1  argument or path error
    2  safety violation (UsbSafetyError)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _usb_safety import (  # noqa: E402
    UsbSafetyError,
    assert_drive_writable,
    manifest_of_writes,
    reserve_new_path,
    safe_copy_tree,
    safe_mkdir,
    safe_write_text,
    stamp_dirname,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RECEIPTS_DIR = REPO_ROOT / "data" / "receipts"
VERIFIER_DIR = REPO_ROOT / "dist" / "orphograph-verify"


# --------------------------------------------------------------------------- #
# README — plain English, no exclamation marks, no first-person plural,
# no third-party company names, no dollar amounts.
# --------------------------------------------------------------------------- #


def _build_readme(receipt_count: int, total_bytes: int) -> str:
    text = (
        "Orphograph cold-storage backup\n"
        "==============================\n"
        "\n"
        "What this folder is\n"
        "-------------------\n"
        "This folder is a cold-storage backup of every Orphograph receipt "
        "produced by the originating office, plus the verifier program "
        "needed to confirm any one of them on any machine.\n"
        "\n"
        f"Receipts backed up: {receipt_count}\n"
        f"Total bytes:        {total_bytes}\n"
        "\n"
        "What is inside this folder\n"
        "--------------------------\n"
        "  receipts/   One subdirectory per receipt, each containing a\n"
        "              receipt.json file and the OpenTimestamps proofs.\n"
        "  verifier/   The Python program that checks any receipt.\n"
        "  BACKUP_README.txt    This document.\n"
        "  WHAT_WAS_ADDED.json  A list of every file added to your USB.\n"
        "\n"
        "How to verify any receipt in the backup\n"
        "---------------------------------------\n"
        "1. Open a terminal.\n"
        "2. Change directory into this folder on the USB drive.\n"
        "3. Run the following command (Python 3.9 or newer is required),\n"
        "   replacing RECEIPT_ID with the id of the receipt to check:\n"
        "\n"
        "       python3 verifier/verify.py receipts/RECEIPT_ID/receipt.json\n"
        "\n"
        "The verifier prints PASS or FAIL for each calendar and for the "
        "Bitcoin anchor. A PASS means the receipt is genuine.\n"
        "\n"
        "Additive-only invariant\n"
        "-----------------------\n"
        "The tool that produced this folder is additive only. Nothing "
        "that was already on your USB drive was renamed, modified, or "
        "removed. Older backup folders written by previous runs of this "
        "tool are also untouched. WHAT_WAS_ADDED.json lists every file "
        "added under this folder, and only those files were created.\n"
        "\n"
        "If a receipt fails to verify\n"
        "----------------------------\n"
        "Keep the USB drive as is and request a review. Do not edit any "
        "file under receipts/; the proofs are byte-exact.\n"
    )
    assert "!" not in text, "README must not contain exclamation marks"
    for token in (" we ", " we,", " we.", " our ", " our,", " our.", " us ", " us,", " us."):
        assert token not in text.lower(), f"README must not contain {token!r}"
    return text


# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #


def _receipts_dir_is_empty(receipts_dir: Path) -> bool:
    if not receipts_dir.is_dir():
        return True
    for entry in receipts_dir.iterdir():
        if entry.name.startswith("__"):
            # ignore __pycache__ and similar
            continue
        if entry.is_dir():
            # only count it if it actually contains files
            for sub in entry.iterdir():
                if sub.is_file():
                    return False
        elif entry.is_file():
            return False
    return True


def _tree_byte_size(root: Path) -> int:
    total = 0
    for p in root.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _count_receipts(receipts_dir: Path) -> int:
    count = 0
    for entry in receipts_dir.iterdir():
        if entry.is_dir() and not entry.name.startswith("__"):
            if any(sub.is_file() for sub in entry.iterdir()):
                count += 1
    return count


def run_backup(
    usb: Path,
    receipts_dir: Path | None,
    dry_run: bool,
    stdout=sys.stdout,
) -> int:
    # Step 1 — validate USB.
    try:
        usb_root = assert_drive_writable(usb)
    except UsbSafetyError as exc:
        print(f"safety error: {exc}", file=sys.stderr)
        return 2

    # Step 4 — resolve receipts dir.
    src_receipts = (Path(receipts_dir).expanduser().resolve()
                    if receipts_dir is not None
                    else DEFAULT_RECEIPTS_DIR)

    # Step 5 — empty source = nothing to do.
    if _receipts_dir_is_empty(src_receipts):
        print("nothing to back up (receipts directory missing or empty).", file=stdout)
        return 0

    receipt_count = _count_receipts(src_receipts)
    total_bytes_src = _tree_byte_size(src_receipts)

    if not VERIFIER_DIR.is_dir():
        print(f"verifier directory missing: {VERIFIER_DIR}", file=sys.stderr)
        return 1

    # Step 2 — target subdir name.
    target_name = stamp_dirname("repo", "backup")

    if dry_run:
        print(f"[dry-run] would create directory: {usb_root / target_name}", file=stdout)
        print(f"[dry-run] would copy receipts tree from: {src_receipts}", file=stdout)
        print(f"[dry-run] would copy verifier tree from: {VERIFIER_DIR}", file=stdout)
        print(f"[dry-run] receipts to back up: {receipt_count}", file=stdout)
        print(f"[dry-run] approximate bytes: {total_bytes_src}", file=stdout)
        print("[dry-run] no filesystem changes were made.", file=stdout)
        return 0

    # Step 3 — reserve + create.
    try:
        target = reserve_new_path(usb_root, target_name)
        safe_mkdir(target)

        # Step 6 — copy receipts tree.
        safe_copy_tree(src_receipts, target / "receipts")

        # Step 7 — copy verifier.
        safe_copy_tree(VERIFIER_DIR, target / "verifier")

        # Step 8 — README.
        backed_up_bytes = _tree_byte_size(target / "receipts")
        readme = _build_readme(receipt_count=receipt_count, total_bytes=backed_up_bytes)
        safe_write_text(target / "BACKUP_README.txt", readme)

        # Step 9 — manifest of writes.
        manifest = manifest_of_writes(target)
        safe_write_text(
            target / "WHAT_WAS_ADDED.json",
            json.dumps(manifest, indent=2, sort_keys=True),
        )
    except UsbSafetyError as exc:
        print(f"safety error: {exc}", file=sys.stderr)
        return 2

    # Step 10 — summary.
    final_bytes = _tree_byte_size(target)
    print(f"target:           {target}", file=stdout)
    print(f"receipts_backed_up: {receipt_count}", file=stdout)
    print(f"total_bytes:      {final_bytes}", file=stdout)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="usb_cold_backup.py",
        description=(
            "Copy the local receipts directory to a USB drive as cold-storage "
            "backup. Each run creates a new timestamped subdirectory; previous "
            "backups and any other files already on the drive are never "
            "modified or removed."
        ),
    )
    p.add_argument("--usb", required=True, type=Path,
                   help="path to the mounted USB drive (e.g. /Volumes/BACKUP_USB)")
    p.add_argument("--receipts-dir", type=Path, default=None,
                   help="override the source receipts directory "
                        "(default: <repo>/data/receipts/)")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be written; make no filesystem changes")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return run_backup(
            usb=args.usb,
            receipts_dir=args.receipts_dir,
            dry_run=args.dry_run,
        )
    except UsbSafetyError as exc:
        print(f"safety error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
