#!/usr/bin/env python3
"""
Build the Show HN Open Graph share image.

Composes a 1200x630 cream canvas with the Orphograph seal, wordmark, hairline
rule, and tagline per outbox/launch_hn/SHARE_IMAGE_SPEC.md.

Pure stdlib + Pillow. No font installs. RGB output, < 200 KB target.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# -- Paths ---------------------------------------------------------------------
REPO = Path("/Users/franciscoefrain.rodriguezrivera/orphograph")
SEAL_SRC = REPO / "web" / "seal.png"
OUT_KIT = REPO / "outbox" / "launch_hn" / "og-share.png"
OUT_WEB = REPO / "web" / "share" / "og-show-hn.png"

# -- Canvas + colors -----------------------------------------------------------
W, H = 1200, 630
BG = (247, 241, 227)   # #f7f1e3 paper / cream
INK = (20, 17, 13)     # #14110d ink

# -- Font cascade --------------------------------------------------------------
# (regular_paths, italic_paths) — first hit wins
FONT_CASCADE = [
    ("EB Garamond", [
        "/Library/Fonts/EBGaramond-Regular.ttf",
        "/Library/Fonts/EBGaramond12-Regular.ttf",
        os.path.expanduser("~/Library/Fonts/EBGaramond-Regular.ttf"),
        os.path.expanduser("~/Library/Fonts/EBGaramond12-Regular.ttf"),
    ], [
        "/Library/Fonts/EBGaramond-Italic.ttf",
        "/Library/Fonts/EBGaramond12-Italic.ttf",
        os.path.expanduser("~/Library/Fonts/EBGaramond-Italic.ttf"),
        os.path.expanduser("~/Library/Fonts/EBGaramond12-Italic.ttf"),
    ]),
    ("Georgia", [
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/Library/Fonts/Georgia.ttf",
    ], [
        "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
        "/Library/Fonts/Georgia Italic.ttf",
    ]),
    ("Times New Roman", [
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/Library/Fonts/Times New Roman.ttf",
    ], [
        "/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf",
        "/Library/Fonts/Times New Roman Italic.ttf",
    ]),
]


def pick_font_family() -> tuple[str, str | None, str | None]:
    """Return (family_name, regular_path, italic_path). Either path may be None."""
    for family, regs, itals in FONT_CASCADE:
        reg = next((p for p in regs if os.path.isfile(p)), None)
        ital = next((p for p in itals if os.path.isfile(p)), None)
        if reg:
            return family, reg, ital
    return ("PIL default", None, None)


def load_font(path: str | None, size: int) -> ImageFont.ImageFont:
    if path:
        return ImageFont.truetype(path, size)
    # Last-resort fallback (bitmap; will look degraded).
    return ImageFont.load_default()


# -- Compose -------------------------------------------------------------------

def build() -> tuple[str, Path, Path]:
    family, reg_path, ital_path = pick_font_family()
    print(f"[font] family: {family}")
    print(f"[font] regular: {reg_path or '(PIL default)'}")
    print(f"[font] italic : {ital_path or '(falling back to regular)'}")

    canvas = Image.new("RGB", (W, H), BG)

    # --- Seal: 280 px tall, top edge y=90, centered horizontally ---
    seal = Image.open(SEAL_SRC).convert("RGBA")
    seal_h = 280
    seal_w = int(seal.width * (seal_h / seal.height))
    seal = seal.resize((seal_w, seal_h), Image.LANCZOS)
    canvas.paste(seal, ((W - seal_w) // 2, 90), seal)

    draw = ImageDraw.Draw(canvas, "RGBA")

    # --- Wordmark: ORPHOGRAPH @ 56px, tracked 0.32em, top y=410 ---
    word_font = load_font(reg_path, 56)
    word = "ORPHOGRAPH"
    track_px = int(0.32 * 56)  # ~18 px between glyphs
    glyph_widths = [draw.textlength(g, font=word_font) for g in word]
    total_w = sum(glyph_widths) + track_px * (len(word) - 1)
    # Optically balance the right-side gap from tracking by shifting half-track left
    x = (W - total_w) // 2 + track_px // 2
    y_word = 410
    for g, gw in zip(word, glyph_widths):
        draw.text((x, y_word), g, fill=INK, font=word_font)
        x += gw + track_px

    # --- Hairline rule: 120x1 px, ink @ opacity 0.45, y=490 ---
    rule_w, rule_h = 120, 1
    rx = (W - rule_w) // 2
    ry = 490
    draw.rectangle(
        [rx, ry, rx + rule_w - 1, ry + rule_h - 1],
        fill=(INK[0], INK[1], INK[2], int(255 * 0.45)),
    )

    # --- Tagline: italic @ 22px, opacity 0.78, y=520 ---
    # Falls back to regular font if no italic available.
    tag_font = load_font(ital_path or reg_path, 22)
    tag = "Bitcoin-anchored receipts · orphograph.com"
    tw = draw.textlength(tag, font=tag_font)
    draw.text(
        ((W - tw) // 2, 520),
        tag,
        fill=(INK[0], INK[1], INK[2], int(255 * 0.78)),
        font=tag_font,
    )

    # Ensure parent dirs and write.
    OUT_KIT.parent.mkdir(parents=True, exist_ok=True)
    OUT_WEB.parent.mkdir(parents=True, exist_ok=True)

    # Final flatten to RGB just in case (canvas is RGB but be defensive).
    flat = canvas if canvas.mode == "RGB" else canvas.convert("RGB")
    flat.save(OUT_KIT, "PNG", optimize=True)
    flat.save(OUT_WEB, "PNG", optimize=True)

    return family, OUT_KIT, OUT_WEB


def validate(path: Path) -> None:
    im = Image.open(path)
    assert im.size == (W, H), f"{path}: size {im.size} != ({W},{H})"
    assert im.mode in ("RGB", "RGBA"), f"{path}: mode {im.mode} not RGB/RGBA"
    size = path.stat().st_size
    assert size < 200_000, f"{path}: {size} bytes >= 200000"
    print(f"[ok] {path} -> {im.size} {im.mode} {size} bytes")


def main() -> int:
    family, out_kit, out_web = build()
    validate(out_kit)
    validate(out_web)
    print(f"[done] font family used: {family}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
