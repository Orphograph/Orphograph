#!/usr/bin/env python3
"""receipt_pdf.py — stdlib PDF generator for receipt attachments.

Renders a single-page receipt PDF matching the Orphograph website's
institutional-notary aesthetic: cream paper, serif typography, the
notary seal embedded at the top-left, and a typed data table.

The PDF is hand-rolled (no fpdf2, no reportlab) so the project remains
stdlib-only. The image embedding path uses only zlib and struct from
the standard library — the seal PNG is decoded, alpha-composited over
the cream background to produce flat RGB pixel data, then re-encoded
as a FlateDecode'd Image XObject.

Public API (unchanged):
    render_receipt_pdf(receipt: dict, site_url: str) -> bytes
"""
from __future__ import annotations

import os
import struct
import zlib

# ---------------------------------------------------------------------------
# Page geometry and palette
# ---------------------------------------------------------------------------

PAGE_WIDTH_PT = 612    # 8.5 in at 72pt — US Letter
PAGE_HEIGHT_PT = 792   # 11 in

# Cream background (#fdfaf3) expressed as PDF rg fractions.
CREAM_R, CREAM_G, CREAM_B = 0xfd / 255.0, 0xfa / 255.0, 0xf3 / 255.0

# Muted secondary text (#837e75).
MUTED_R, MUTED_G, MUTED_B = 0x83 / 255.0, 0x7e / 255.0, 0x75 / 255.0

# Header / body dark.
DARK_R, DARK_G, DARK_B = 0.1, 0.1, 0.1

# Accent green (#4a9a73) — used for the calendars-ok value when complete.
ACCENT_R, ACCENT_G, ACCENT_B = 0x4a / 255.0, 0x9a / 255.0, 0x73 / 255.0

SEAL_PATH_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "web",
    "seal.png",
)


# ---------------------------------------------------------------------------
# String escaping for PDF (string) literals
# ---------------------------------------------------------------------------

def _escape(s: str) -> str:
    """Escape characters that PDF treats as syntax inside a (string) literal."""
    return (
        s.replace("\\", "\\\\")
         .replace("(", "\\(")
         .replace(")", "\\)")
         .replace("\n", " ")
         .replace("\r", " ")
    )


# ---------------------------------------------------------------------------
# PNG decoding (stdlib only)
# ---------------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _paeth(a: int, b: int, c: int) -> int:
    """PNG Paeth predictor."""
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _decode_png_rgba_or_rgb(data: bytes):
    """Decode an 8-bit RGB or RGBA PNG. Returns (width, height, channels, raw_bytes).

    Raises ValueError on anything we don't accept (non-PNG signature,
    bit-depth != 8, color-type not 2/6, interlaced, malformed).
    """
    if data[:8] != _PNG_SIG:
        raise ValueError("not a PNG (signature mismatch)")

    pos = 8
    width = height = bit_depth = color_type = interlace = -1
    idat_parts: list[bytes] = []
    n = len(data)

    while pos < n:
        if pos + 8 > n:
            raise ValueError("truncated PNG chunk header")
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk_data = data[pos + 8:pos + 8 + length]
        if len(chunk_data) != length:
            raise ValueError("truncated PNG chunk body")
        pos += 8 + length + 4  # skip CRC

        if ctype == b"IHDR":
            (width, height, bit_depth, color_type, _cm, _fm, interlace) = struct.unpack(
                ">IIBBBBB", chunk_data
            )
        elif ctype == b"IDAT":
            idat_parts.append(chunk_data)
        elif ctype == b"IEND":
            break

    if width <= 0 or height <= 0:
        raise ValueError("PNG missing IHDR")
    if bit_depth != 8:
        raise ValueError(f"unsupported PNG bit depth: {bit_depth}")
    if color_type not in (2, 6):
        raise ValueError(f"unsupported PNG color type: {color_type}")
    if interlace != 0:
        raise ValueError("interlaced PNG not supported")
    if not idat_parts:
        raise ValueError("PNG missing IDAT")

    channels = 3 if color_type == 2 else 4
    stride = width * channels

    raw = zlib.decompress(b"".join(idat_parts))
    expected = (stride + 1) * height
    if len(raw) != expected:
        raise ValueError(
            f"PNG IDAT decompressed size mismatch: got {len(raw)} expected {expected}"
        )

    # Undo PNG scanline filters.
    out = bytearray(stride * height)
    prev_line = bytes(stride)
    src = 0
    dst = 0
    for _ in range(height):
        filt = raw[src]
        src += 1
        line = bytearray(raw[src:src + stride])
        src += stride

        if filt == 0:
            pass
        elif filt == 1:  # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filt == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev_line[i]) & 0xFF
        elif filt == 3:  # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = prev_line[i]
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
        elif filt == 4:  # Paeth
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = prev_line[i]
                up_left = prev_line[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(left, up, up_left)) & 0xFF
        else:
            raise ValueError(f"unknown PNG filter type: {filt}")

        out[dst:dst + stride] = line
        prev_line = bytes(line)
        dst += stride

    return width, height, channels, bytes(out)


def _composite_over_cream(width: int, height: int, channels: int, pixels: bytes) -> bytes:
    """Flatten RGB or RGBA pixel data to RGB over the cream background."""
    if channels == 3:
        return pixels

    cream_r = int(round(CREAM_R * 255))
    cream_g = int(round(CREAM_G * 255))
    cream_b = int(round(CREAM_B * 255))

    n = width * height
    out = bytearray(n * 3)
    src = 0
    dst = 0
    for _ in range(n):
        r = pixels[src]
        g = pixels[src + 1]
        b = pixels[src + 2]
        a = pixels[src + 3]
        src += 4
        if a == 255:
            out[dst] = r
            out[dst + 1] = g
            out[dst + 2] = b
        elif a == 0:
            out[dst] = cream_r
            out[dst + 1] = cream_g
            out[dst + 2] = cream_b
        else:
            # Standard "over" compositing: out = src*a + bg*(1-a).
            inv = 255 - a
            out[dst]     = (r * a + cream_r * inv + 127) // 255
            out[dst + 1] = (g * a + cream_g * inv + 127) // 255
            out[dst + 2] = (b * a + cream_b * inv + 127) // 255
        dst += 3
    return bytes(out)


def _try_load_seal(path: str):
    """Return (width, height, flate_rgb_bytes) for the embedded seal, or None."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        w, h, ch, raw = _decode_png_rgba_or_rgb(data)
        flat_rgb = _composite_over_cream(w, h, ch, raw)
        compressed = zlib.compress(flat_rgb, 6)
        return w, h, compressed
    except Exception:
        # Any failure (missing file, malformed PNG, unsupported variant) falls
        # back to the vector placeholder seal drawn directly in the content
        # stream — better than crashing the receipt pipeline.
        return None


# ---------------------------------------------------------------------------
# Content stream
# ---------------------------------------------------------------------------

def _content_stream(receipt: dict, site_url: str, seal_available: bool) -> bytes:
    rid = _escape(str(receipt.get("receipt_id", "")))
    hash_hex = _escape(str(receipt.get("hash_hex", "")))
    created_at = _escape(str(receipt.get("created_at", "")))
    cal_ok = int(receipt.get("calendars_ok", 0))
    cal_total = int(receipt.get("calendars_total", 0))
    pinned_at = _escape(str(receipt.get("btc_pinned_at", "") or "(pending)"))
    site = _escape(site_url.rstrip("/"))

    lines: list[str] = []
    add = lines.append

    # ---- Page background — cream fill across the whole MediaBox.
    add("q")
    add(f"{CREAM_R:.3f} {CREAM_G:.3f} {CREAM_B:.3f} rg")
    add("0 0 612 792 re f")
    add("Q")

    # ---- Header band: seal (aspect-preserved, taller than wide) at the
    # left, wordmark + subline beside it. Both elements sit just above the
    # horizontal rule at y=700. Seal source is web/seal.png at 170x285
    # (aspect 0.596 — taller than wide). Forcing it into a square box
    # made the cord-coil look elliptical; we preserve the native ratio.
    RULE_Y = 700.0                              # horizontal rule baseline
    GAP_ABOVE_RULE = 4.0                        # both elements end this many pts above the rule
    seal_x = 72.0
    seal_h = 78.0                               # taller logo, "long not stretched"
    seal_aspect_w_over_h = 170.0 / 285.0        # native PNG aspect
    seal_w = seal_h * seal_aspect_w_over_h      # ~46.5pt — slim portrait
    seal_y_bottom = RULE_Y + GAP_ABOVE_RULE     # 704 — sits just above the rule
    seal_y_top = seal_y_bottom + seal_h         # 782

    if seal_available:
        # PDF image space is 1×1; the cm matrix scales + translates into place.
        # The first two non-zero entries are width and height in pts.
        add("q")
        add(f"{seal_w:.3f} 0 0 {seal_h:.3f} {seal_x:.3f} {seal_y_bottom:.3f} cm")
        add("/Im1 Do")
        add("Q")
    else:
        # Vector fallback: vertical oval (matches the native portrait aspect)
        # with a serif "O" inside, drawn so the bottom kisses the rule.
        cx = seal_x + seal_w / 2.0
        cy = seal_y_bottom + seal_h / 2.0
        rx = seal_w / 2.0 - 2.0
        ry = seal_h / 2.0 - 2.0
        kx = 0.5522847498 * rx
        ky = 0.5522847498 * ry
        add("q")
        add("0.1 0.1 0.1 RG 1.2 w")
        add(f"{cx - rx:.3f} {cy:.3f} m")
        add(f"{cx - rx:.3f} {cy + ky:.3f} {cx - kx:.3f} {cy + ry:.3f} {cx:.3f} {cy + ry:.3f} c")
        add(f"{cx + kx:.3f} {cy + ry:.3f} {cx + rx:.3f} {cy + ky:.3f} {cx + rx:.3f} {cy:.3f} c")
        add(f"{cx + rx:.3f} {cy - ky:.3f} {cx + kx:.3f} {cy - ry:.3f} {cx:.3f} {cy - ry:.3f} c")
        add(f"{cx - kx:.3f} {cy - ry:.3f} {cx - rx:.3f} {cy - ky:.3f} {cx - rx:.3f} {cy:.3f} c")
        add("S")
        add("Q")
        add("BT")
        add("/F1 28 Tf")
        add(f"{DARK_R} {DARK_G} {DARK_B} rg")
        add(f"{cx - 9:.3f} {cy - 9:.3f} Td (O) Tj")
        add("ET")

    # Wordmark "Orphograph" — serif, 26pt. Baseline sits just above the rule
    # with enough room for descenders. The "p"/"g" descend ~6pt at 26pt;
    # placing the baseline at 716 keeps descenders clear of the rule at 700.
    word_x = seal_x + seal_w + 18.0
    wordmark_baseline = 736.0
    add("BT")
    add("/F1 26 Tf")
    add(f"{DARK_R} {DARK_G} {DARK_B} rg")
    add(f"{word_x:.3f} {wordmark_baseline:.3f} Td")
    add("(Orphograph) Tj")
    add("ET")

    # Subline "EMPIRICAL NOTARY" — letterspaced, muted. Baseline ~14pt
    # below the wordmark's baseline, still sitting just above the rule.
    subline_baseline = 714.0
    add("BT")
    add("/F1 9 Tf")
    add(f"{MUTED_R:.3f} {MUTED_G:.3f} {MUTED_B:.3f} rg")
    add("3 Tc")
    add(f"{word_x:.3f} {subline_baseline:.3f} Td")
    add("(EMPIRICAL NOTARY) Tj")
    add("ET")
    add("BT 0 Tc ET")

    # Thin rule under the entire header band — both seal-bottom and the
    # subline-bottom are GAP_ABOVE_RULE points above this line.
    add(f"{MUTED_R:.3f} {MUTED_G:.3f} {MUTED_B:.3f} RG")
    add("0.4 w")
    add(f"72 {RULE_Y:.3f} m 540 {RULE_Y:.3f} l S")

    # ---- Title.
    add("BT")
    add("/F1 18 Tf")
    add(f"{DARK_R} {DARK_G} {DARK_B} rg")
    add(f"72 670 Td (Receipt {rid}) Tj")
    add("ET")

    # Subtitle in muted color.
    add("BT")
    add("/F1 11 Tf")
    add(f"{MUTED_R:.3f} {MUTED_G:.3f} {MUTED_B:.3f} rg")
    add("72 652 Td (Issued by Orphograph. Verifies against the Bitcoin chain.) Tj")
    add("ET")

    # ---- Data table.
    rows = [
        ("Receipt", rid, False),
        ("SHA-256", hash_hex, False),
        ("Registered", created_at + "  UTC", False),
        ("Calendars", f"{cal_ok} of {cal_total} attesting", cal_total > 0 and cal_ok == cal_total),
        ("Bitcoin commitment", pinned_at, False),
    ]
    y = 608
    for label, value, accent in rows:
        # Muted 9pt label.
        add("BT")
        add("/F1 9 Tf")
        add(f"{MUTED_R:.3f} {MUTED_G:.3f} {MUTED_B:.3f} rg")
        add(f"72 {y} Td ({_escape(label)}) Tj")
        add("ET")
        # 11pt value — accent green when complete (calendars row only).
        v = value if len(value) <= 70 else value[:67] + "..."
        add("BT")
        add("/F1 11 Tf")
        if accent:
            add(f"{ACCENT_R:.3f} {ACCENT_G:.3f} {ACCENT_B:.3f} rg")
        else:
            add(f"{DARK_R} {DARK_G} {DARK_B} rg")
        add(f"72 {y - 14} Td ({_escape(v)}) Tj")
        add("ET")
        y -= 42

    # ---- Footer rule + verification URL.
    add(f"{MUTED_R:.3f} {MUTED_G:.3f} {MUTED_B:.3f} RG 0.4 w")
    add("72 130 m 540 130 l S")
    add("BT")
    add("/F1 9 Tf")
    add(f"{MUTED_R:.3f} {MUTED_G:.3f} {MUTED_B:.3f} rg")
    add("72 112 Td (Full receipt and live verification:) Tj")
    add("ET")
    add("BT")
    add("/F1 10 Tf")
    add(f"{DARK_R} {DARK_G} {DARK_B} rg")
    add(f"72 96 Td ({site}/r/{rid}) Tj")
    add("ET")
    add("BT")
    add("/F1 8 Tf")
    add(f"{MUTED_R:.3f} {MUTED_G:.3f} {MUTED_B:.3f} rg")
    add("72 72 Td (This document is informational. The receipt verifies against the Bitcoin chain using any OpenTimestamps client.) Tj")
    add("ET")

    return "\n".join(lines).encode("utf-8")


# ---------------------------------------------------------------------------
# PDF assembly
# ---------------------------------------------------------------------------

def render_receipt_pdf(receipt: dict, site_url: str = "https://orphograph.com") -> bytes:
    """Return a single-page PDF document for the given receipt record.

    Signature preserved — call sites in the mailer pipeline are unchanged.
    """
    seal = _try_load_seal(SEAL_PATH_DEFAULT)
    seal_available = seal is not None

    content = _content_stream(receipt, site_url, seal_available)

    # Object plan:
    #   1 — Catalog
    #   2 — Pages
    #   3 — Page
    #   4 — Content stream
    #   5 — Font (Times-Roman, serif, one of the 14 base PDF fonts)
    #   6 — Image XObject (only when the seal loaded successfully)
    offsets: list[int] = []
    out = bytearray()

    def write(b: bytes) -> None:
        out.extend(b)

    def remember_offset() -> None:
        offsets.append(len(out))

    write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    # Object 1 — Catalog
    remember_offset()
    write(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    # Object 2 — Pages
    remember_offset()
    write(b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n")

    # Object 3 — Page (resources reference the font and, if present, the seal image)
    remember_offset()
    if seal_available:
        resources = (
            b"/Resources << "
            b"/Font << /F1 5 0 R >> "
            b"/XObject << /Im1 6 0 R >> "
            b">>"
        )
    else:
        resources = b"/Resources << /Font << /F1 5 0 R >> >>"
    page = (
        b"3 0 obj\n"
        b"<< /Type /Page /Parent 2 0 R "
        b"/MediaBox [0 0 612 792] "
        + resources + b" "
        b"/Contents 4 0 R >>\n"
        b"endobj\n"
    )
    write(page)

    # Object 4 — Content stream
    remember_offset()
    write(f"4 0 obj\n<< /Length {len(content)} >>\nstream\n".encode("ascii"))
    write(content)
    write(b"\nendstream\nendobj\n")

    # Object 5 — Font (Times-Roman; closest stdlib-accessible match to the
    # site's Georgia wordmark, no embedding required).
    remember_offset()
    write(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman >>\nendobj\n")

    # Object 6 — Seal Image XObject (FlateDecode'd raw RGB pixel data).
    if seal_available:
        seal_w, seal_h, seal_zlib = seal  # type: ignore[misc]
        remember_offset()
        write(
            (
                f"6 0 obj\n"
                f"<< /Type /XObject /Subtype /Image "
                f"/Width {seal_w} /Height {seal_h} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 "
                f"/Filter /FlateDecode /Length {len(seal_zlib)} >>\n"
                f"stream\n"
            ).encode("ascii")
        )
        write(seal_zlib)
        write(b"\nendstream\nendobj\n")

    # Cross-reference table.
    xref_offset = len(out)
    n_objects = 1 + len(offsets)  # +1 for the free entry at index 0
    write(f"xref\n0 {n_objects}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        write(f"{off:010d} 00000 n \n".encode("ascii"))

    # Trailer.
    write(f"trailer\n<< /Size {n_objects} /Root 1 0 R >>\nstartxref\n".encode("ascii"))
    write(f"{xref_offset}\n".encode("ascii"))
    write(b"%%EOF\n")

    return bytes(out)
