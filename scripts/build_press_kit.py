#!/usr/bin/env python3
"""build_press_kit.py — assemble the Orphograph press kit ZIP.

Bundles brand assets and the brand guide into a single downloadable archive
served at /press-kit/orphograph-press-kit.zip. Pure stdlib (zipfile). All
paths inside the archive are relative to the archive root.

Idempotent: overwrites the existing ZIP if present, then validates it.
"""
from __future__ import annotations

import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PRESS_KIT_DIR = WEB / "press-kit"
ZIP_PATH = PRESS_KIT_DIR / "orphograph-press-kit.zip"

# (source path relative to WEB, archive name)
ASSETS: list[tuple[str, str]] = [
    ("seal.png",                                "seal.png"),
    ("seal-display.png",                        "seal-display.png"),
    ("lockup.png",                              "lockup.png"),
    ("favicon.ico",                             "favicon.ico"),
    ("favicon-16.png",                          "favicon-16.png"),
    ("favicon-32.png",                          "favicon-32.png"),
    ("apple-touch-icon-180.png",                "apple-touch-icon-180.png"),
    ("og-image.png",                            "og-image.png"),
    ("press-kit/orphograph-brand-guide.txt",    "orphograph-brand-guide.txt"),
    ("press-kit/orphograph-brand-guide.html",   "orphograph-brand-guide.html"),
]

README_TEXT = """ORPHOGRAPH — PRESS KIT
Established 2026. An empirical notary.

This archive contains the institutional brand assets and the brand guide,
provided for editorial use by journalists, reviewers, and academic writers.

------------------------------------------------------------
CONTENTS
------------------------------------------------------------

  seal.png                       Institutional seal, 1254 by 1254 px,
                                 transparent background.
  seal-display.png               Display variant of the seal, 600 by 600
                                 px, transparent background.
  lockup.png                     Seal plus wordmark, 1736 by 906 px,
                                 transparent background.
  favicon.ico                    Multi-resolution favicon for legacy
                                 browser tabs.
  favicon-16.png                 16 by 16 px favicon.
  favicon-32.png                 32 by 32 px favicon.
  apple-touch-icon-180.png       180 by 180 px touch icon.
  og-image.png                   Open Graph social card.
  orphograph-brand-guide.txt     Plain-text brand guide.
  orphograph-brand-guide.html    Printable brand guide (open in a browser
                                 and Save as PDF for a print-ready file).

------------------------------------------------------------
BRAND PALETTE
------------------------------------------------------------

  Cream          #f7f1e3   surface, paper
  Ink            #14110d   primary type
  Confirm green  #3a6a4c   attestation, the receipt mark

A classical book-weight serif (EB Garamond is the reference) is used for
display and body type. Any equivalent serif at regular or medium weight
is an acceptable substitute. No third-party brand-font reference is used.

------------------------------------------------------------
A LIVE SAMPLE RECEIPT
------------------------------------------------------------

A real, publicly addressable receipt issued by the office:

  https://orphograph.com/r/7KViBg91CR8D4mTr

The receipt is verifiable against the public Bitcoin blockchain at any
time, using the MIT-licensed offline verifier kit at
https://orphograph.com/dist/orphograph-verify.zip.

------------------------------------------------------------
LICENCE
------------------------------------------------------------

Protocol source code is published under the MIT licence and is available
on the public source tree. The brand — name, seal, wordmark, lockup,
voice, written copy, and the visual system described in the brand guide
— is reserved. The brand is not part of the MIT-licensed code release.

------------------------------------------------------------
CONTACT
------------------------------------------------------------

Press correspondence:  hello@orphograph.com
Security disclosure:   security@orphograph.com
Press kit web page:    https://orphograph.com/press-kit.html

The office responds to press inquiries within a small number of business
days.
"""


def build() -> Path:
    """Build the press-kit ZIP. Returns the path to the written archive."""
    PRESS_KIT_DIR.mkdir(parents=True, exist_ok=True)

    # Validate every source file exists before opening the archive.
    missing: list[str] = []
    for src_rel, _arc in ASSETS:
        if not (WEB / src_rel).is_file():
            missing.append(src_rel)
    if missing:
        raise FileNotFoundError(
            "Missing source assets for press kit: " + ", ".join(missing)
        )

    # Overwrite any prior archive (idempotent).
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    with zipfile.ZipFile(ZIP_PATH, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src_rel, arc_name in ASSETS:
            # arcname is always relative (never absolute) — strip any leading slash.
            zf.write(WEB / src_rel, arcname=arc_name.lstrip("/"))
        zf.writestr("README.txt", README_TEXT)

    return ZIP_PATH


def validate(zip_path: Path) -> dict:
    """Open the archive, check integrity, and return a summary dict."""
    with zipfile.ZipFile(zip_path, mode="r") as zf:
        bad = zf.testzip()
        if bad is not None:
            raise RuntimeError(f"ZIP integrity check failed on: {bad}")
        names = zf.namelist()
        info = [(zi.filename, zi.file_size, zi.compress_size) for zi in zf.infolist()]
    size = zip_path.stat().st_size
    return {
        "path": str(zip_path),
        "size_bytes": size,
        "file_count": len(names),
        "files": info,
    }


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0:
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n:,} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def main() -> int:
    try:
        path = build()
        summary = validate(path)
    except Exception as exc:  # noqa: BLE001 — top-level reporting
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"Built: {summary['path']}")
    print(f"Size:  {_human(summary['size_bytes'])}  ({summary['size_bytes']:,} bytes)")
    print(f"Files: {summary['file_count']}")
    print(f"Date:  {date.today().isoformat()}")
    print("")
    print("Contents:")
    for name, raw, comp in summary["files"]:
        print(f"  {name:<32}  {_human(raw):>10}  (stored {_human(comp)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
