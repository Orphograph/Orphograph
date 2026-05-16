#!/usr/bin/env python3
"""Generate PNG icons for the Orphograph browser extension.

Stdlib only — uses struct + zlib to build a minimal valid PNG.
Produces icon-16.png, icon-48.png, icon-128.png with a cream-on-dark "O".

Run from this directory:
    python3 generate_icons.py
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

CREAM = (0xfa, 0xf6, 0xef)
DARK = (0x1f, 0x1d, 0x1a)


def _u32(n: int) -> bytes:
    return struct.pack(">I", n)


def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return _u32(len(data)) + kind + data + _u32(crc)


def _png(width: int, pixels: bytes) -> bytes:
    """Build a PNG from raw RGB pixel data (rows of WIDTH*3 bytes)."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(
        ">IIBBBBB",
        width,
        width,
        8,  # bit depth
        2,  # color type 2 = RGB
        0,
        0,
        0,
    )
    # Add filter byte 0 (None) to each row
    row_stride = width * 3
    filtered = bytearray()
    for r in range(width):
        filtered.append(0)
        filtered.extend(pixels[r * row_stride:(r + 1) * row_stride])
    idat = zlib.compress(bytes(filtered), 9)
    return (
        sig
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )


def _render(width: int) -> bytes:
    """Draw a cream "O" on a dark rounded square."""
    # Background dark, with rounded corners
    r = width // 8  # corner radius
    cx = cy = (width - 1) / 2
    outer = (width * 0.36)
    inner = (width * 0.22)
    pixels = bytearray()
    for y in range(width):
        for x in range(width):
            # Rounded-square mask
            dx_corner = max(0, abs(x - cx) - (cx - r))
            dy_corner = max(0, abs(y - cy) - (cy - r))
            in_bg = (dx_corner * dx_corner + dy_corner * dy_corner) <= r * r
            if not in_bg:
                pixels.extend(CREAM)
                continue
            # Ring "O"
            dx = x - cx
            dy = y - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if inner <= dist <= outer:
                pixels.extend(CREAM)
            else:
                pixels.extend(DARK)
    return bytes(pixels)


def main() -> int:
    out_dir = Path(__file__).resolve().parent
    for size in (16, 48, 128):
        png = _png(size, _render(size))
        (out_dir / f"icon-{size}.png").write_bytes(png)
        print(f"wrote {out_dir / f'icon-{size}.png'} ({len(png)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
