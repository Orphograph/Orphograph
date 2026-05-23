#!/usr/bin/env python3
"""dealpha_brand.py — replace the cream background of seal.png + lockup.png
with transparent alpha, so they blend with the page instead of reading as
a rectangle pasted on top.

Flood-fills from all 4 corners with a tolerance band around the corner
average color, then converts every flood-touched pixel to alpha=0.
Interior cream regions of the artwork (egg body, etc.) are NOT touched
because they're disconnected from the corners.

Usage:
  python3 scripts/dealpha_brand.py             # process seal.png + lockup.png
  python3 scripts/dealpha_brand.py --check     # just report current state
  python3 scripts/dealpha_brand.py --tolerance 20  # widen the bg color band
"""

from __future__ import annotations

import argparse
import pathlib
import statistics

from PIL import Image, ImageDraw

REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [REPO / "web" / "seal.png", REPO / "web" / "lockup.png"]
SENTINEL = (255, 0, 255)  # magenta — assumed absent from artwork


def _corner_avg(im: Image.Image) -> tuple[int, int, int]:
    w, h = im.size
    samples: list[tuple[int, int, int]] = []
    for cx, cy in [(8, 8), (w - 9, 8), (8, h - 9), (w - 9, h - 9)]:
        for dx in range(0, 8, 2):
            for dy in range(0, 8, 2):
                p = im.getpixel((cx + dx, cy + dy))
                samples.append((p[0], p[1], p[2]))
    rs = [s[0] for s in samples]
    gs = [s[1] for s in samples]
    bs = [s[2] for s in samples]
    return (round(statistics.mean(rs)), round(statistics.mean(gs)), round(statistics.mean(bs)))


def _has_alpha(im: Image.Image) -> bool:
    if im.mode != "RGBA":
        return False
    alphas = im.getchannel("A").getdata()
    return min(alphas) < 255


def _dealpha(path: pathlib.Path, tolerance: int, dry_run: bool) -> dict:
    src = Image.open(path).convert("RGB")
    w, h = src.size
    bg = _corner_avg(src)

    # Floodfill an RGB copy from each corner with the sentinel color.
    work = src.copy()
    for corner in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        ImageDraw.floodfill(work, corner, SENTINEL, thresh=tolerance)

    # Build RGBA: any pixel == SENTINEL → alpha 0; else original RGB at alpha 255.
    work_data = list(work.getdata())
    src_data = list(src.getdata())
    transparent_count = 0
    out_data: list[tuple[int, int, int, int]] = []
    for i, p in enumerate(work_data):
        if p == SENTINEL:
            out_data.append((0, 0, 0, 0))
            transparent_count += 1
        else:
            r, g, b = src_data[i]
            out_data.append((r, g, b, 255))

    out = Image.new("RGBA", (w, h))
    out.putdata(out_data)

    pct = round(100.0 * transparent_count / (w * h), 1)
    info = {
        "path": path.name,
        "size": f"{w}x{h}",
        "bg_color": f"#{bg[0]:02x}{bg[1]:02x}{bg[2]:02x}",
        "transparent_pct": pct,
        "transparent_px": transparent_count,
    }

    if dry_run:
        return info

    out.save(path, "PNG", optimize=True)
    info["new_size_kb"] = path.stat().st_size // 1024
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="report current alpha state without modifying")
    ap.add_argument("--tolerance", type=int, default=18,
                    help="color tolerance around corner avg (default 18 ~7%% wiggle)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would happen, no write")
    args = ap.parse_args()

    print(f"dealpha_brand: tolerance={args.tolerance} dry_run={args.dry_run}")
    print()

    for p in TARGETS:
        if not p.exists():
            print(f"  MISSING: {p}")
            continue
        im = Image.open(p)
        has_a = _has_alpha(im.convert("RGBA"))
        print(f"  {p.name:14s} mode={im.mode} has_transparency={has_a}")
        if args.check:
            continue
        info = _dealpha(p, args.tolerance, args.dry_run)
        action = "WOULD WRITE" if args.dry_run else "wrote"
        print(f"    {action} {info['size']} bg≈{info['bg_color']} "
              f"transparent={info['transparent_pct']}% ({info['transparent_px']:,} px)"
              + (f" file_size={info['new_size_kb']}KB" if 'new_size_kb' in info else ''))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
