"""_usb_safety.py — additive-only primitives for every USB workflow.

The rule, written in code instead of in documentation:

    NOTHING that is already on the target USB is ever removed, truncated,
    renamed, or overwritten by an Orphograph script.

The helpers below are the ONLY way the USB-facing scripts in this folder
are allowed to touch a USB drive. Each helper refuses, with a clear error,
to do anything that could destroy data the user already had on the drive.

Defensive doctrine:
  * No `rm`, `unlink`, `rmtree`, `os.remove`, or `Path.unlink` in any USB
    script. The helpers do not provide a removal primitive; if you find
    yourself needing one, the design is wrong.
  * Every output path the script will write to MUST go through
    `reserve_new_path()` which fails when the path already exists.
  * Writes happen to a fresh subdirectory keyed by the receipt id and the
    UTC timestamp, so two invocations on the same USB cannot collide.
  * `assert_drive_writable()` verifies the target mount is a writable
    directory before any write attempt, and rejects roots ('/' or 'C:\\')
    so a typo on the mount path can never touch the host filesystem.

MIT-licensed. Stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
from pathlib import Path


class UsbSafetyError(RuntimeError):
    """Raised whenever a USB operation would violate the additive-only rule."""


# --------------------------------------------------------------------------- #
# Path safety
# --------------------------------------------------------------------------- #


_FORBIDDEN_ROOTS = {
    Path("/"),
    Path("/Users"),
    Path("/home"),
    Path("/Volumes"),  # the parent of all macOS USB mounts — refuse the parent
    Path("/mnt"),
    Path("/media"),
    Path("C:\\"),
}


def assert_drive_writable(mount: Path) -> Path:
    """Validate ``mount`` is a real, writable, non-root directory.

    Returns the resolved Path. Raises UsbSafetyError on any issue.
    """
    p = Path(mount).expanduser().resolve()
    if not p.exists():
        raise UsbSafetyError(f"USB mount does not exist: {p}")
    if not p.is_dir():
        raise UsbSafetyError(f"USB mount is not a directory: {p}")
    if p in _FORBIDDEN_ROOTS:
        raise UsbSafetyError(
            f"refusing to write to a system root: {p}. Pass the path to a "
            f"specific USB volume, e.g. /Volumes/MY_USB"
        )
    if not os.access(p, os.W_OK):
        raise UsbSafetyError(f"USB mount is not writable: {p}")
    return p


def reserve_new_path(parent: Path, name: str) -> Path:
    """Return ``parent / name``, failing if that path already exists.

    This is the ONLY primitive in this module that picks a target path.
    Every other helper takes the path it was given. If the path exists
    even as a broken symlink, this raises and the caller must choose a
    new name. The additive-only invariant flows from this: a path the
    script is going to write to is never one that already had data.
    """
    target = parent / name
    if target.exists() or target.is_symlink():
        raise UsbSafetyError(
            f"refusing to overwrite an existing path: {target}. "
            f"Pick a different subdirectory name."
        )
    return target


def stamp_dirname(receipt_id: str, kind: str) -> str:
    """A unique, sortable, human-readable directory name for one handover.

    Format: ``orphograph_<kind>_<receipt_id>_<utc-iso>``. The leading
    ``orphograph_`` prefix makes it obvious to the receiving party that
    the directory was added by this office's tooling and not by the
    sender personally.
    """
    safe_kind = "".join(c for c in kind if c.isalnum() or c in "-_")[:32]
    safe_rid = "".join(c for c in receipt_id if c.isalnum() or c in "-_")[:64]
    iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"orphograph_{safe_kind}_{safe_rid}_{iso}"


# --------------------------------------------------------------------------- #
# Write helpers — all additive, none destructive
# --------------------------------------------------------------------------- #


def safe_mkdir(target: Path) -> None:
    """Create a directory. Fails if it already exists.

    We deliberately do NOT use ``mkdir(exist_ok=True)`` — the additive-only
    invariant requires the directory to be fresh.
    """
    if target.exists():
        raise UsbSafetyError(f"directory already exists: {target}")
    target.mkdir(parents=True, exist_ok=False)


def safe_write_bytes(target: Path, data: bytes) -> None:
    """Write bytes to ``target``. Fails if the file already exists."""
    if target.exists() or target.is_symlink():
        raise UsbSafetyError(f"refusing to overwrite existing file: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write via a sibling temp file — but ONLY when the final
    # destination does not exist. We do not allow temp-file → existing-name
    # rename because that would still overwrite.
    tmp = target.with_name(target.name + ".part")
    if tmp.exists():
        raise UsbSafetyError(f"stale temp file exists, will not clobber: {tmp}")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, target)


def safe_write_text(target: Path, text: str) -> None:
    """UTF-8 text variant of safe_write_bytes."""
    safe_write_bytes(target, text.encode("utf-8"))


def safe_copy_file(src: Path, dst: Path) -> None:
    """Copy ``src`` to ``dst``. Fails if ``dst`` already exists."""
    if dst.exists() or dst.is_symlink():
        raise UsbSafetyError(f"refusing to overwrite existing file: {dst}")
    if not src.is_file():
        raise UsbSafetyError(f"source is not a file: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # shutil.copy2 preserves the source mtime — useful for evidentiary work
    # because the on-USB file then carries the same mtime as the original.
    shutil.copy2(src, dst)


def safe_copy_tree(src: Path, dst: Path) -> None:
    """Recursively copy ``src/`` to ``dst/``. Fails if ``dst`` exists.

    Symlinks inside ``src`` are NOT followed — they are copied as symlinks.
    This keeps the additive-only invariant: a symlink in the source pointing
    outside the source tree never causes us to read or write that target.
    """
    if dst.exists() or dst.is_symlink():
        raise UsbSafetyError(f"refusing to overwrite existing tree: {dst}")
    if not src.is_dir():
        raise UsbSafetyError(f"source is not a directory: {src}")
    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=False)


# --------------------------------------------------------------------------- #
# Audit — explicit additive snapshot of what we did
# --------------------------------------------------------------------------- #


def manifest_of_writes(root: Path) -> dict:
    """Return a manifest of every file inside ``root`` (relative path + size + mtime).

    Used at the end of each USB script to produce ``WHAT_WAS_ADDED.json``
    inside the new subdirectory, so the recipient can see exactly which
    files were dropped by this office and can confirm nothing else on the
    drive was touched.
    """
    entries = []
    root = root.resolve()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            stat = p.stat()
            entries.append({
                "path": str(p.relative_to(root)),
                "size_bytes": stat.st_size,
                "mtime_utc": _dt.datetime.fromtimestamp(
                    stat.st_mtime, tz=_dt.timezone.utc
                ).isoformat(timespec="seconds"),
            })
    return {
        "added_under": str(root),
        "added_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "additive_only": True,
        "policy": (
            "Files outside this directory were not read, modified, renamed, "
            "or deleted by the script that produced this manifest. The "
            "Orphograph USB helpers operate under an additive-only invariant; "
            "see scripts/_usb_safety.py for the implementation."
        ),
        "files": entries,
    }
