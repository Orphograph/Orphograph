#!/usr/bin/env python3
"""test_qrcode_svg_decodes.py — the QR encoder must produce codes that SCAN.

Found 2026-08-09, founder report ("making real qr codes"): every QR this
module had ever produced failed to decode. All data, ECC and placement were
byte-identical with a reference encoder — the sole defect was _place_format
writing the 15 format-information bits LSB-first along a path the spec reads
MSB-first, which made the mask/ECC level unrecoverable and therefore the
whole symbol unreadable. Two smaller bugs rode along: copy 2 of the format
info leaked bit 7 into the dark-module cell (`i < 8` for a 7-bit column), and
mask 5's predicate parsed as `x + (y == 0)`.

It survived because nothing ever SCANNED the output. The SVG looked exactly
like a QR code; the payment page rendered it; tests asserted the SVG parsed.
"Looks like a QR" and "is a QR" are different claims, and only the second
one matters. Ground truth for the pins below was established by decoding
with Apple's Vision framework (VNDetectBarcodesRequest) on 2026-08-09 —
three payloads including the BIP-21 shape, all round-tripping exactly.

CI cannot run Vision, so the pins are structural, chosen so that each of the
three fixed bugs breaks at least one of them:

  * a known-answer matrix digest (any encoder regression diverges it);
  * the spec's format-info property: copy 1 of the format bits, read
    MSB-first along the placement path, must equal _FORMAT_INFO_L[mask] —
    this is exactly the property the bug violated;
  * copy 2 integrity: the dark module is dark and column SIZE-8 of the
    top-right strip is written;
  * mask 5's predicate against hand-computed truth values.
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import qrcode_svg as q  # noqa: E402

# sha256 of the flattened HELLO-ORPHO matrix, decode-verified via Vision.
KAT_SHA256 = "11846df576eea7684a245f3a118368a1d5e2a5cec1000a81523566b4e6b97875"


def _format_cells_copy1(m):
    """Copy 1 of the format info, in spec path order (MSB first)."""
    cells = []
    for i in range(15):
        if i < 6:
            cells.append(m[8][i])
        elif i == 6:
            cells.append(m[8][7])
        elif i == 7:
            cells.append(m[8][8])
        elif i == 8:
            cells.append(m[7][8])
        else:
            cells.append(m[14 - i][8])
    return cells


class TestQrEncoder(unittest.TestCase):
    def test_known_answer_matrix(self):
        m = q.encode_matrix("HELLO-ORPHO")
        flat = "".join(str(b) for row in m for b in row)
        self.assertEqual(hashlib.sha256(flat.encode()).hexdigest(), KAT_SHA256,
                         "encoder output diverged from the scan-verified matrix")

    def test_format_info_copy1_is_msb_first(self):
        """The property whose violation made every symbol unreadable."""
        for data in ("HELLO-ORPHO", "https://orphograph.com/r/XwTULwlh76PcCst9"):
            m = q.encode_matrix(data)
            cells = _format_cells_copy1(m)
            value = 0
            for bit in cells:                       # path order = MSB first
                value = (value << 1) | bit
            self.assertIn(value, q._FORMAT_INFO_L,
                          f"format copy 1 reads {value:015b}, which is not a "
                          f"valid L-level format word — bit order regressed")

    def test_dark_module_and_copy2_column(self):
        m = q.encode_matrix("HELLO-ORPHO")
        self.assertEqual(m[4 * q._VERSION + 9][8], 1, "dark module must be dark")
        # With `i < 8`, the cell at (8, SIZE-8) was never written by copy 2;
        # after the fix it always is, and both copies agree on bit 7.
        cells = _format_cells_copy1(m)
        self.assertEqual(m[8][q._SIZE - 8], cells[7],
                         "copy 2 bit 7 must live at (8, SIZE-8) and match copy 1")

    def test_mask5_predicate(self):
        """(r*c)%2 + (r*c)%3 == 0 must be evaluated as a SUM equal to zero."""
        truth = {(0, 0): True, (1, 2): False, (2, 3): True, (1, 1): False,
                 (3, 4): True, (2, 2): False}
        for (r, c), want in truth.items():
            self.assertEqual(bool(q._mask_bit(5, r, c)), want,
                             f"mask 5 wrong at ({r},{c})")

    # test_shipped_asset_matches_encoder removed 2026-08-18 with
    # web/qr-receipt.svg. The encoder itself is still live -- it serves the
    # documented /api/btc-order/<order_id>/qr.svg route -- so every other
    # test in this file stays. There is no longer a shipped static asset to
    # drift from it.


if __name__ == "__main__":
    unittest.main()
