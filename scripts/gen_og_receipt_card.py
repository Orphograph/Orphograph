#!/usr/bin/env python3
"""gen_og_receipt_card.py — generate web/og-receipt-card.png (1200×630).

The shared unfurl card for every /r/<id> link: per-receipt DATA travels in the
templated og:title/og:description text (server-side, stdlib); this image is the
static branded verdict-tile behind it. Kept as a committed asset because the
Fly image is deliberately stdlib-only (no Pillow at serve time).

Usage: python3 scripts/gen_og_receipt_card.py
"""
from __future__ import annotations

import pathlib
import tempfile

from gen_og_dark import (  # reuse the brand composition helpers
    BRASS_BRIGHT, INK, INK_LINE, MUTE_LIGHT, PARCHMENT, PARCHMENT_2,
    draw_tracked, load_font, woff2_to_ttf, FONTS, WEB,
)
from PIL import Image, ImageDraw

VERDIGRIS = (90, 163, 145)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        fraunces = woff2_to_ttf(FONTS / "Fraunces-300_900.woff2", tdp, {"wght": 540, "opsz": 100})
        plex = woff2_to_ttf(FONTS / "IBMPlexMono-500.woff2", tdp)

        img = Image.new("RGB", (1200, 630), INK)
        d = ImageDraw.Draw(img)
        d.rectangle([12, 12, 1187, 617], outline=INK_LINE, width=1)

        cx, cy, r = 235, 315, 168
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=PARCHMENT_2)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=INK_LINE, width=2)
        with Image.open(WEB / "seal.png") as seal:
            seal = seal.convert("RGBA")
            side = int(r * 2 * 0.78)
            seal = seal.resize((side, side), Image.LANCZOS)
            img.paste(seal, (cx - side // 2, cy - side // 2), seal)

        tx = 470
        draw_tracked(d, (tx, 150), "RECEIPT · SEALED IN BITCOIN",
                     load_font(plex, 25), BRASS_BRIGHT, tracking=3.5)
        d.text((tx - 4, 220), "Existence sealed.", font=load_font(fraunces, 88), fill=PARCHMENT)
        d.text((tx, 350), "Verify it yourself — no account,", font=load_font(fraunces, 40), fill=MUTE_LIGHT)
        d.text((tx, 402), "no trust in us required.", font=load_font(fraunces, 40), fill=MUTE_LIGHT)
        d.line([tx, 490, 1130, 490], fill=VERDIGRIS, width=1)
        draw_tracked(d, (tx, 515), "ORPHOGRAPH.COM", load_font(plex, 23), PARCHMENT, tracking=3.0)

        out = WEB / "og-receipt-card.png"
        img.save(out, "PNG", optimize=True)
        print(f"wrote {out} ({out.stat().st_size // 1024}KB, 1200x630)")


if __name__ == "__main__":
    main()
