"""make_handover_usb.py — build a self-contained handover bundle on a USB drive.

The recipient (client, attorney, adjuster, auditor) plugs the USB into any
machine, opens the new subdirectory, reads the README, and runs the verifier
locally. No contact with this office is required to confirm the receipt is
authentic.

Strict additive-only invariant: nothing already on the USB is modified,
renamed, truncated, or deleted. Every write goes through the helpers in
``scripts/_usb_safety.py``.

Usage::

    python3 scripts/make_handover_usb.py \\
        --usb /Volumes/CLIENT_USB \\
        --receipt-id <rid> \\
        --include-files /path/to/original/folder \\
        [--label "Wedding 2026-05-15"] \\
        [--dry-run]

Exit codes:
    0  success
    1  argument or path error
    2  safety violation (UsbSafetyError)
    3  missing receipt
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the sibling _usb_safety module importable whether this script is run
# from the repo root or from the scripts/ directory.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _usb_safety import (  # noqa: E402  (path setup above)
    UsbSafetyError,
    assert_drive_writable,
    manifest_of_writes,
    reserve_new_path,
    safe_copy_file,
    safe_copy_tree,
    safe_mkdir,
    safe_write_text,
    stamp_dirname,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
RECEIPTS_DIR = REPO_ROOT / "data" / "receipts"
VERIFIER_DIR = REPO_ROOT / "dist" / "orphograph-verify"


# --------------------------------------------------------------------------- #
# README text — plain English, no exclamation marks, no first-person, no
# third-party company names, no dollar amounts.
# --------------------------------------------------------------------------- #


def _build_readme(receipt_id: str, label: str, root_hex: str, has_files: bool) -> str:
    files_para = (
        "A copy of the original folder has been placed under files/ for "
        "your records. The cryptographic hash in receipt/receipt.json was "
        "computed over those files at the moment they were anchored.\n\n"
        if has_files
        else ""
    )
    label_line = f"Label: {label}\n" if label else ""

    text = (
        "Orphograph handover bundle\n"
        "==========================\n"
        "\n"
        "What this folder is\n"
        "-------------------\n"
        "This folder was added to your USB drive by an Orphograph tool. "
        "It contains a tamper-evident receipt that proves a specific file "
        "or folder existed at a specific moment in time. The proof is "
        "anchored to the Bitcoin timestamp network, which can be verified "
        "by anyone, on any computer, without contacting the office that "
        "produced this folder.\n"
        "\n"
        f"Receipt ID: {receipt_id}\n"
        f"Root hash:  {root_hex}\n"
        f"{label_line}"
        "\n"
        "What is inside this folder\n"
        "--------------------------\n"
        "  receipt/      The receipt JSON and the OpenTimestamps proofs.\n"
        "  verifier/     A small Python program that checks the proofs.\n"
        + ("  files/        A copy of the original files that were anchored.\n" if has_files else "")
        + "  README.txt    This document.\n"
        "  WHAT_WAS_ADDED.json   A list of every file added to your USB.\n"
        "\n"
        f"{files_para}"
        "How to check the proof on your own machine\n"
        "------------------------------------------\n"
        "1. Open a terminal.\n"
        "2. Change directory into this folder on the USB drive.\n"
        "3. Run the following command (Python 3.9 or newer is required):\n"
        "\n"
        "       python3 verifier/verify.py receipt/receipt.json\n"
        "\n"
        "The verifier prints PASS or FAIL for each calendar and for the "
        "Bitcoin anchor. A PASS means the receipt is genuine and the "
        "anchor time can be trusted.\n"
        "\n"
        "If you do not have Python on your machine\n"
        "-----------------------------------------\n"
        "Python is free and ships with every recent version of macOS and "
        "Linux. On Windows, install it from the python.org website. After "
        "installation, repeat the verifier command above. The receipt and "
        "the verifier are plain files; they do not need an internet "
        "connection except to confirm the Bitcoin anchor against a public "
        "block explorer.\n"
        "\n"
        "Promise about your USB drive\n"
        "----------------------------\n"
        "The tool that produced this folder is additive only. Nothing "
        "that was already on your USB drive was renamed, modified, or "
        "removed. WHAT_WAS_ADDED.json lists every file added under this "
        "folder; any other file on the drive is exactly as you left it.\n"
        "\n"
        "Questions\n"
        "---------\n"
        "If a verifier step fails, keep the USB drive as is and request a "
        "review. Do not edit any file under receipt/; the proofs are "
        "byte-exact and depend on the original bytes being preserved.\n"
    )
    # Hard-rule guard: visible README must contain no exclamation marks
    # and no first-person plural.
    assert "!" not in text, "README must not contain exclamation marks"
    for token in (" we ", " we,", " we.", " our ", " our,", " our.", " us ", " us,", " us."):
        assert token not in text.lower(), f"README must not contain {token!r}"
    return text


# --------------------------------------------------------------------------- #
# Core build steps
# --------------------------------------------------------------------------- #


def _gather_receipt_files(receipt_dir: Path) -> list[Path]:
    """Return all files inside the receipt directory worth copying.

    That is: receipt.json, manifest.json (folder anchors), every *.ots file.
    Sub-directories are intentionally not recursed into; receipts are flat.
    """
    if not receipt_dir.is_dir():
        raise FileNotFoundError(receipt_dir)
    candidates: list[Path] = []
    for entry in sorted(receipt_dir.iterdir()):
        if not entry.is_file():
            continue
        name = entry.name.lower()
        if name == "receipt.json" or name == "manifest.json" or name.endswith(".ots"):
            candidates.append(entry)
    return candidates


def _load_root_hex(receipt_dir: Path) -> str:
    rj = receipt_dir / "receipt.json"
    if not rj.is_file():
        return ""
    try:
        data = json.loads(rj.read_text(encoding="utf-8"))
    except Exception:
        return ""
    for key in ("hash_hex", "root_hex", "merkle_root_hex", "root"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _dry_run_report(usb_root: Path, target_name: str, receipt_files: list[Path],
                    verifier_files: list[Path], include_files: Path | None) -> str:
    lines = [
        f"[dry-run] would create directory: {usb_root / target_name}",
        f"[dry-run] would add {len(receipt_files)} receipt file(s) under receipt/:",
    ]
    for f in receipt_files:
        lines.append(f"[dry-run]   {f.name}")
    lines.append(f"[dry-run] would add {len(verifier_files)} verifier file(s) under verifier/:")
    for f in verifier_files:
        lines.append(f"[dry-run]   {f.name}")
    if include_files is not None:
        lines.append(f"[dry-run] would copy directory tree into files/: {include_files}")
    lines.append("[dry-run] would write README.txt")
    lines.append("[dry-run] would write WHAT_WAS_ADDED.json")
    lines.append("[dry-run] no filesystem changes were made.")
    return "\n".join(lines)


def build_handover(
    usb: Path,
    receipt_id: str,
    include_files: Path | None,
    label: str,
    dry_run: bool,
    stdout=sys.stdout,
) -> int:
    # Step 1 — validate USB mount.
    try:
        usb_root = assert_drive_writable(usb)
    except UsbSafetyError as exc:
        print(f"safety error: {exc}", file=sys.stderr)
        return 2

    # Step 3-prep — confirm receipt exists before reserving anything on USB.
    receipt_dir = RECEIPTS_DIR / receipt_id
    if not receipt_dir.is_dir():
        print(f"missing receipt: {receipt_dir}", file=sys.stderr)
        return 3
    try:
        receipt_files = _gather_receipt_files(receipt_dir)
    except FileNotFoundError:
        print(f"missing receipt: {receipt_dir}", file=sys.stderr)
        return 3
    if not receipt_files:
        print(f"receipt directory is empty: {receipt_dir}", file=sys.stderr)
        return 3

    # Verifier files
    if not VERIFIER_DIR.is_dir():
        print(f"verifier directory missing: {VERIFIER_DIR}", file=sys.stderr)
        return 1
    verifier_src_files = [
        VERIFIER_DIR / "verify.py",
        VERIFIER_DIR / "merkle.py",
    ]
    for f in verifier_src_files:
        if not f.is_file():
            print(f"verifier file missing: {f}", file=sys.stderr)
            return 1

    # Include-files validation (before any USB write).
    include_dir: Path | None = None
    if include_files is not None:
        include_dir = Path(include_files).expanduser().resolve()
        if not include_dir.is_dir():
            print(f"--include-files must point at an existing directory: {include_dir}",
                  file=sys.stderr)
            return 1

    # Step 2 — compute target subdir name.
    target_name = stamp_dirname(receipt_id, "handover")

    if dry_run:
        print(_dry_run_report(usb_root, target_name, receipt_files,
                              verifier_src_files, include_dir), file=stdout)
        return 0

    # Step 3 — reserve target path.
    try:
        target = reserve_new_path(usb_root, target_name)
        safe_mkdir(target)

        # Step 5 — copy receipt files.
        receipt_out = target / "receipt"
        safe_mkdir(receipt_out)
        for f in receipt_files:
            safe_copy_file(f, receipt_out / f.name)

        # Step 6 — copy verifier files.
        verifier_out = target / "verifier"
        safe_mkdir(verifier_out)
        for f in verifier_src_files:
            safe_copy_file(f, verifier_out / f.name)

        # Step 7 — optional include-files tree.
        if include_dir is not None:
            safe_copy_tree(include_dir, target / "files")

        # Step 8 — README.
        root_hex = _load_root_hex(receipt_dir)
        readme = _build_readme(
            receipt_id=receipt_id,
            label=label,
            root_hex=root_hex,
            has_files=include_dir is not None,
        )
        safe_write_text(target / "README.txt", readme)

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
    file_count = sum(1 for p in target.rglob("*") if p.is_file())
    print(f"target:     {target}", file=stdout)
    print(f"receipt_id: {receipt_id}", file=stdout)
    print(f"root_hex:   {root_hex}", file=stdout)
    print(f"files_added: {file_count}", file=stdout)
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="make_handover_usb.py",
        description=(
            "Build a self-contained Orphograph handover bundle on a USB drive. "
            "The bundle includes the receipt, the OpenTimestamps proofs, the "
            "verifier program, and an optional copy of the original files. "
            "Strictly additive: nothing already on the drive is modified."
        ),
    )
    p.add_argument("--usb", required=True, type=Path,
                   help="path to the mounted USB drive (e.g. /Volumes/CLIENT_USB)")
    p.add_argument("--receipt-id", required=True,
                   help="receipt id under data/receipts/ to bundle")
    p.add_argument("--include-files", type=Path, default=None,
                   help="optional directory of original files to copy under files/")
    p.add_argument("--label", default="",
                   help="optional human label printed in the README (e.g. project name)")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be written; make no filesystem changes")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return build_handover(
            usb=args.usb,
            receipt_id=args.receipt_id,
            include_files=args.include_files,
            label=args.label,
            dry_run=args.dry_run,
        )
    except UsbSafetyError as exc:
        print(f"safety error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
