#!/usr/bin/env python3
"""qrcode_svg.py — Minimal stdlib QR code generator that outputs SVG.

Implements just enough of ISO/IEC 18004 (QR Code 2005) to encode a BIP-21
URI in byte mode at Version 4, error-correction level L (33x33 modules,
capacity 78 bytes — comfortably fits any reasonable bitcoin: URI). No
third-party dependencies. No PIL. No npm. Output is a self-contained
SVG string that renders even with JavaScript disabled.

References used (all public-domain spec):
    - ISO/IEC 18004:2015 (QR Code 2005 specification)
    - https://en.wikipedia.org/wiki/QR_code
    - https://www.thonky.com/qr-code-tutorial/  (excellent step-by-step)

Public API:
    make_svg(data: str, *, scale: int = 8, quiet: int = 4) -> str
        Returns a string containing the full SVG document for `data`.

    encode_matrix(data: str) -> list[list[int]]
        Returns the raw 33x33 module matrix (1 = dark, 0 = light).

Privacy note: the function does exactly one thing — turn the input string
into a QR-code SVG. No network calls, no logging, no persistence. The
caller is responsible for ensuring the input string contains only
public/non-sensitive data (e.g. a BIP-21 URI with only address + amount).
"""
from __future__ import annotations

# ── Constants for Version 4, ECC Level L ───────────────────────────────
# Version 4 = 33x33 modules.  Level L = 7% error correction.
# Total codewords = 100, data codewords = 80, ECC codewords = 20,
# in a single ECC block (no group splitting at this size/level).
_VERSION = 4
_SIZE = 17 + 4 * _VERSION  # 33
_DATA_CODEWORDS = 80
_ECC_CODEWORDS = 20
_TOTAL_CODEWORDS = _DATA_CODEWORDS + _ECC_CODEWORDS  # 100
_DATA_CAPACITY_BYTES = 78  # 80 data codewords − 2 header bytes (mode + length)

# Format-information bit strings for each (ECC level, mask) pair, already
# XOR-masked with 0x5412 per the spec.  Index by mask pattern (0..7).
# ECC Level L bits = 0b01.
_FORMAT_INFO_L = [
    0x77C4, 0x72F3, 0x7DAA, 0x789D, 0x662F, 0x6318, 0x6C41, 0x6976,
]

# Alignment-pattern centre coordinates for Version 4: rows/cols 6 and 26.
_ALIGNMENT_CENTRES = [6, 26]

# Galois-Field (GF(256)) log/antilog tables for Reed-Solomon arithmetic.
# Primitive polynomial is 0x11D (x^8 + x^4 + x^3 + x^2 + 1, per QR spec).
_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_x = 1
for _i in range(255):
    _GF_EXP[_i] = _x
    _GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator_poly(nsym: int) -> list[int]:
    """Build the Reed-Solomon generator polynomial of degree `nsym`."""
    g = [1]
    for i in range(nsym):
        # Multiply g(x) by (x − α^i)
        new = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            new[j] ^= c
            new[j + 1] ^= _gf_mul(c, _GF_EXP[i])
        g = new
    return g


def _rs_encode(data: list[int], nsym: int) -> list[int]:
    """Return the `nsym` Reed-Solomon ECC bytes for `data`."""
    gen = _rs_generator_poly(nsym)
    # Polynomial long division: msg(x) * x^nsym  mod  gen(x).
    buf = list(data) + [0] * nsym
    for i in range(len(data)):
        coef = buf[i]
        if coef == 0:
            continue
        for j in range(len(gen)):
            buf[i + j] ^= _gf_mul(gen[j], coef)
    return buf[len(data):]


# ── Bit-stream builder ─────────────────────────────────────────────────

class _BitBuffer:
    """Append-only MSB-first bit buffer used to assemble the data stream."""

    __slots__ = ("bits",)

    def __init__(self) -> None:
        self.bits: list[int] = []

    def put(self, value: int, nbits: int) -> None:
        for i in range(nbits - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def to_bytes(self) -> list[int]:
        out: list[int] = []
        for i in range(0, len(self.bits), 8):
            chunk = self.bits[i:i + 8]
            byte = 0
            for b in chunk:
                byte = (byte << 1) | b
            byte <<= (8 - len(chunk))  # pad final partial byte with zero bits
            out.append(byte)
        return out


def _encode_data(data: str) -> list[int]:
    """Encode `data` as byte-mode QR data codewords for Version 4-L."""
    raw = data.encode("utf-8")
    if len(raw) > _DATA_CAPACITY_BYTES:
        raise ValueError(
            f"data too long for QR Version 4-L: {len(raw)} bytes "
            f"(max {_DATA_CAPACITY_BYTES})"
        )
    buf = _BitBuffer()
    # Byte mode indicator (4 bits = 0b0100).
    buf.put(0b0100, 4)
    # Character count indicator: 8 bits for byte mode at Version 1-9.
    buf.put(len(raw), 8)
    for b in raw:
        buf.put(b, 8)
    # Terminator: up to four 0 bits, but no further than capacity.
    capacity_bits = _DATA_CODEWORDS * 8
    term = min(4, capacity_bits - len(buf.bits))
    buf.put(0, term)
    # Pad to a byte boundary.
    while len(buf.bits) % 8 != 0:
        buf.bits.append(0)
    codewords = buf.to_bytes()
    # Fill remaining codewords with the alternating pad pattern.
    pad_a, pad_b = 0xEC, 0x11
    i = 0
    while len(codewords) < _DATA_CODEWORDS:
        codewords.append(pad_a if i % 2 == 0 else pad_b)
        i += 1
    return codewords


# ── Module-matrix layout ───────────────────────────────────────────────

def _new_matrix() -> tuple[list[list[int]], list[list[bool]]]:
    """Return (modules, reserved). 0 = light, 1 = dark; reserved = function pattern."""
    modules = [[0] * _SIZE for _ in range(_SIZE)]
    reserved = [[False] * _SIZE for _ in range(_SIZE)]
    return modules, reserved


def _place_finder(modules: list[list[int]], reserved: list[list[bool]], r0: int, c0: int) -> None:
    """Draw a 7x7 finder pattern at (r0, c0)."""
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            r, c = r0 + dr, c0 + dc
            if not (0 <= r < _SIZE and 0 <= c < _SIZE):
                continue
            if 0 <= dr <= 6 and 0 <= dc <= 6:
                # Inside the 7x7 finder square.
                is_dark = (
                    dr in (0, 6) or dc in (0, 6)
                    or (2 <= dr <= 4 and 2 <= dc <= 4)
                )
                modules[r][c] = 1 if is_dark else 0
            else:
                modules[r][c] = 0  # surrounding separator
            reserved[r][c] = True


def _place_alignment(modules: list[list[int]], reserved: list[list[bool]]) -> None:
    """Draw the 5x5 alignment patterns. Skip those that overlap a finder."""
    centres = _ALIGNMENT_CENTRES
    for r in centres:
        for c in centres:
            # Skip the three positions that would collide with finder patterns.
            if (r, c) in {(6, 6), (6, 26), (26, 6)}:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    rr, cc = r + dr, c + dc
                    is_dark = max(abs(dr), abs(dc)) != 1
                    modules[rr][cc] = 1 if is_dark else 0
                    reserved[rr][cc] = True


def _place_timing(modules: list[list[int]], reserved: list[list[bool]]) -> None:
    """Draw the horizontal and vertical timing patterns on row/col 6."""
    for i in range(8, _SIZE - 8):
        bit = 1 if i % 2 == 0 else 0
        if not reserved[6][i]:
            modules[6][i] = bit
            reserved[6][i] = True
        if not reserved[i][6]:
            modules[i][6] = bit
            reserved[i][6] = True


def _reserve_format_areas(reserved: list[list[bool]]) -> None:
    """Mark the format-information cells so the data-placement step skips them."""
    # The dark module at (4*V + 9, 8) is always dark.
    for i in range(9):
        reserved[8][i] = True
        reserved[i][8] = True
    for i in range(8):
        reserved[8][_SIZE - 1 - i] = True
        reserved[_SIZE - 1 - i][8] = True


def _place_data(
    modules: list[list[int]],
    reserved: list[list[bool]],
    bits: list[int],
) -> None:
    """Snake the data bits up/down through unreserved cells, right-to-left."""
    idx = 0
    col = _SIZE - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1  # skip the vertical timing column
        col_pair = (col, col - 1)
        rng = range(_SIZE - 1, -1, -1) if upward else range(0, _SIZE)
        for row in rng:
            for c in col_pair:
                if reserved[row][c]:
                    continue
                bit = bits[idx] if idx < len(bits) else 0
                modules[row][c] = bit
                idx += 1
        col -= 2
        upward = not upward


# ── Mask patterns + scoring ────────────────────────────────────────────

def _mask_bit(mask: int, r: int, c: int) -> int:
    if mask == 0:
        return (r + c) % 2 == 0
    if mask == 1:
        return r % 2 == 0
    if mask == 2:
        return c % 3 == 0
    if mask == 3:
        return (r + c) % 3 == 0
    if mask == 4:
        return ((r // 2) + (c // 3)) % 2 == 0
    if mask == 5:
        return ((r * c) % 2 + (r * c) % 3) == 0
    if mask == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


def _apply_mask(modules: list[list[int]], reserved: list[list[bool]], mask: int) -> None:
    for r in range(_SIZE):
        for c in range(_SIZE):
            if reserved[r][c]:
                continue
            if _mask_bit(mask, r, c):
                modules[r][c] ^= 1


def _mask_penalty(modules: list[list[int]]) -> int:
    """Compute the four ISO/IEC 18004 §8.8.2 penalty scores. Lower = better."""
    n = _SIZE
    score = 0
    # Rule 1: five-or-more same-color modules in a row/column.
    for r in range(n):
        run_color = -1
        run_len = 0
        for c in range(n):
            if modules[r][c] == run_color:
                run_len += 1
            else:
                if run_len >= 5:
                    score += 3 + (run_len - 5)
                run_color = modules[r][c]
                run_len = 1
        if run_len >= 5:
            score += 3 + (run_len - 5)
    for c in range(n):
        run_color = -1
        run_len = 0
        for r in range(n):
            if modules[r][c] == run_color:
                run_len += 1
            else:
                if run_len >= 5:
                    score += 3 + (run_len - 5)
                run_color = modules[r][c]
                run_len = 1
        if run_len >= 5:
            score += 3 + (run_len - 5)
    # Rule 2: 2x2 same-color blocks.
    for r in range(n - 1):
        for c in range(n - 1):
            v = modules[r][c]
            if (
                modules[r][c + 1] == v
                and modules[r + 1][c] == v
                and modules[r + 1][c + 1] == v
            ):
                score += 3
    # Rule 3: finder-like patterns (10111010000 / 00001011101) in rows/cols.
    p1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    p2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for r in range(n):
        for c in range(n - 10):
            window = modules[r][c:c + 11]
            if window == p1 or window == p2:
                score += 40
    for c in range(n):
        col = [modules[r][c] for r in range(n)]
        for r in range(n - 10):
            window = col[r:r + 11]
            if window == p1 or window == p2:
                score += 40
    # Rule 4: dark-module proportion deviation from 50 %.
    dark = sum(sum(row) for row in modules)
    pct = (dark * 100) // (n * n)
    score += 10 * (abs(pct - 50) // 5)
    return score


def _place_format(modules: list[list[int]], mask: int) -> None:
    """Write the format-information bits (already masked with 0x5412)."""
    bits = _FORMAT_INFO_L[mask]
    # Positional index i runs 0..14 along the spec's placement path, and the
    # cell at path position i holds bit (14 - i) — MSB first. This was
    # `(bits >> i) & 1` (LSB first), which reversed all 15 bits in both
    # copies; the format info was unreadable, so no scanner could recover
    # the mask, and every QR this module ever produced failed to decode
    # (found 2026-08-08, proven by matrix-diff against a reference encoder:
    # data, ECC and placement were byte-identical — only these strips wrong).
    for i in range(15):
        bit = (bits >> (14 - i)) & 1
        # First copy (around top-left finder).
        if i < 6:
            modules[8][i] = bit
        elif i == 6:
            modules[8][7] = bit
        elif i == 7:
            modules[8][8] = bit
        elif i == 8:
            modules[7][8] = bit
        else:
            modules[14 - i][8] = bit
        # Second copy (split between top-right and bottom-left finders).
        if i < 7:
            modules[_SIZE - 1 - i][8] = bit
        else:
            modules[8][_SIZE - 15 + i] = bit
    # The "dark module" — always dark, lives at (4*V + 9, 8) = (25, 8).
    modules[4 * _VERSION + 9][8] = 1


# ── Public API ─────────────────────────────────────────────────────────

def encode_matrix(data: str) -> list[list[int]]:
    """Encode `data` and return the 33x33 module matrix (1 = dark, 0 = light)."""
    # 1. Encode the data into 80 data codewords.
    data_codewords = _encode_data(data)
    # 2. Compute 20 Reed-Solomon ECC codewords.
    ecc_codewords = _rs_encode(data_codewords, _ECC_CODEWORDS)
    # 3. With a single block at Version 4-L there is no interleaving —
    #    the final codeword sequence is just data || ecc.
    all_codewords = data_codewords + ecc_codewords
    assert len(all_codewords) == _TOTAL_CODEWORDS
    # 4. Convert codewords to a flat MSB-first bit list, plus the 7
    #    remainder bits required by the spec (Version 4 → 7 zero bits).
    bits: list[int] = []
    for byte in all_codewords:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    bits.extend([0] * 7)
    # 5. Build the module matrix: function patterns first, then data.
    modules, reserved = _new_matrix()
    _place_finder(modules, reserved, 0, 0)
    _place_finder(modules, reserved, 0, _SIZE - 7)
    _place_finder(modules, reserved, _SIZE - 7, 0)
    _place_alignment(modules, reserved)
    _place_timing(modules, reserved)
    _reserve_format_areas(reserved)
    _place_data(modules, reserved, bits)
    # 6. Try all 8 masks, pick the lowest-penalty one. Then place format bits.
    best_mask = 0
    best_score = None
    best_matrix = None
    for mask in range(8):
        trial = [row[:] for row in modules]
        _apply_mask(trial, reserved, mask)
        _place_format(trial, mask)
        score = _mask_penalty(trial)
        if best_score is None or score < best_score:
            best_score = score
            best_mask = mask
            best_matrix = trial
    assert best_matrix is not None
    return best_matrix


def make_svg(data: str, *, scale: int = 8, quiet: int = 4,
             label: str = "Bitcoin payment QR code") -> str:
    """Build a self-contained SVG document for `data`.

    Args:
        data: the payload string (e.g. a BIP-21 URI). UTF-8 is fine.
        scale: pixels per module. 8 → ~296 px square at Version 4 + 4-module quiet zone.
        quiet: width of the white quiet zone, in modules. 4 is the spec minimum.
        label: the SVG's aria-label. The default keeps the historical BTC
            wording so existing callers and the shipped homepage asset stay
            byte-identical; non-payment callers (receipt QRs) pass their own.
            Trusted call sites only — the label is interpolated into markup.

    Returns:
        An SVG document as a UTF-8 string. Safe to embed via innerHTML
        (no user-supplied attributes), but call sites should still avoid
        passing untrusted strings — the encoder only handles the BIP-21
        URI shape.
    """
    matrix = encode_matrix(data)
    n = _SIZE
    total = (n + 2 * quiet) * scale
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total} {total}" '
        f'width="{total}" height="{total}" '
        f'shape-rendering="crispEdges" '
        f'role="img" aria-label="{label}">'
    )
    # White background covers the quiet zone too.
    parts.append(f'<rect width="{total}" height="{total}" fill="#ffffff"/>')
    # Emit one <rect> per dark module. ~330 rects worst case — small.
    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                x = (c + quiet) * scale
                y = (r + quiet) * scale
                parts.append(
                    f'<rect x="{x}" y="{y}" width="{scale}" height="{scale}" fill="#1f1d1a"/>'
                )
    parts.append("</svg>")
    return "".join(parts)


if __name__ == "__main__":
    # Quick self-check: encode a representative BIP-21 URI and print
    # the SVG to stdout. Exit non-zero if anything blows up.
    import sys
    sample = "bitcoin:bc1qexampleaddressxxxxxxxxxxxxxxxxxxxxxx?amount=0.00012345"
    svg = make_svg(sample)
    sys.stdout.write(svg)
    sys.stdout.write("\n")
