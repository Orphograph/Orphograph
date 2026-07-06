#!/usr/bin/env python3
"""gen_og_dark.py — generate web/og-image-dark.png (1200×630) for the dark /v2 page.

Composition: ink canvas, parchment seal-medallion left, headline right —
"Prove it existed. / Forever." with the brand eyebrow and a mono footer.
Fonts come from the self-hosted woff2 files (decompressed via fontTools;
Fraunces variable is used at display weight, italic cut for "Forever.").

Usage:  python3 scripts/gen_og_dark.py [--out web/og-image-dark.png]
"""
from __future__ import annotations

import argparse
import io
import pathlib
import tempfile

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
FONTS = WEB / "fonts"

INK = (20, 17, 15)          # --ink
INK_LINE = (42, 37, 31)     # border line on ink
PARCHMENT = (236, 228, 210)  # --parchment
PARCHMENT_2 = (244, 238, 224)  # --parchment-2 (medallion fill)
BRASS = (184, 137, 59)      # --brass
BRASS_BRIGHT = (217, 169, 78)  # --brass-bright
MUTE_LIGHT = (168, 154, 130)   # --mute-light


def woff2_to_ttf(woff2_path: pathlib.Path, out_dir: pathlib.Path,
                 axes: dict[str, float] | None = None) -> pathlib.Path:
    """Decompress a woff2 into a TTF PIL can open; optionally pin variable axes."""
    out = out_dir / (woff2_path.stem + ".ttf")
    font = TTFont(str(woff2_path))
    font.flavor = None
    if axes and "fvar" in font:
        from fontTools.varLib import instancer
        font = instancer.instantiateVariableFont(font, axes)
    font.save(str(out))
    return out


def load_font(ttf: pathlib.Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ttf), size)


def draw_tracked(d: ImageDraw.ImageDraw, xy, text, font, fill, tracking=0.0):
    """Draw text with letterspacing (PIL has no native tracking)."""
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font) + tracking
    return x


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(WEB / "og-image-dark.png"))
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        display_axes = {"wght": 540, "opsz": 100}
        fraunces = woff2_to_ttf(FONTS / "Fraunces-300_900.woff2", tdp, display_axes)
        fraunces_i = woff2_to_ttf(FONTS / "Fraunces-300_900-i.woff2", tdp, display_axes)
        plex = woff2_to_ttf(FONTS / "IBMPlexMono-500.woff2", tdp)

        img = Image.new("RGB", (1200, 630), INK)
        d = ImageDraw.Draw(img)

        # hairline border, mirroring the page's --line-d on ink
        d.rectangle([12, 12, 1187, 617], outline=INK_LINE, width=1)

        # ── seal medallion, left ────────────────────────────────────────
        cx, cy, r = 235, 315, 168
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=PARCHMENT_2)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=INK_LINE, width=2)
        with Image.open(WEB / "seal.png") as seal:
            seal = seal.convert("RGBA")
            side = int(r * 2 * 0.78)
            seal = seal.resize((side, side), Image.LANCZOS)
            img.paste(seal, (cx - side // 2, cy - side // 2), seal)

        # ── text block, right ───────────────────────────────────────────
        tx = 470
        eyebrow = load_font(plex, 25)
        draw_tracked(d, (tx, 150), "BITCOIN-ANCHORED PROOF OF EXISTENCE",
                     eyebrow, BRASS_BRIGHT, tracking=3.5)

        # size the headline to the available column, never clip
        max_w = 1130 - tx
        size = 96
        while size > 40:
            h1 = load_font(fraunces, size)
            if d.textlength("Prove it existed.", font=h1) <= max_w:
                break
            size -= 2
        h1i = load_font(fraunces_i, size)
        d.text((tx - 4, 220), "Prove it existed.", font=h1, fill=PARCHMENT)
        d.text((tx - 4, 230 + size + 14), "Forever.", font=h1i, fill=BRASS_BRIGHT)

        d.line([tx, 490, 1130, 490], fill=(BRASS[0], BRASS[1], BRASS[2]), width=1)

        foot = load_font(plex, 23)
        end_x = draw_tracked(d, (tx, 515), "ORPHOGRAPH.COM", foot, PARCHMENT, tracking=3.0)
        tail = "SHA-256 · OpenTimestamps"
        tail_f = load_font(plex, 21)
        d.text((1130 - d.textlength(tail, font=tail_f), 517), tail, font=tail_f, fill=MUTE_LIGHT)
        assert end_x < 1130 - d.textlength(tail, font=tail_f) - 20, "footer collision"

        out = pathlib.Path(args.out)
        img.save(out, "PNG", optimize=True)
        print(f"wrote {out} ({out.stat().st_size // 1024}KB, 1200x630)")


if __name__ == "__main__":
    main()
