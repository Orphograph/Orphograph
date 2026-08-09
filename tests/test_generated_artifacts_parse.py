#!/usr/bin/env python3
"""test_generated_artifacts_parse.py — every generated artifact must be VALID
in its own format, proven by parsing it, not by serving it.

Class history, one generator at a time:
  * qrcode_svg produced QR-shaped images that no scanner could decode
    (format bits reversed; found 2026-08-09 when the founder scanned one);
  * badge_svg produced badge-shaped documents that no XML parser accepts —
    `&middot;` is an HTML named entity, undefined in XML, so the embeddable
    badge was ill-formed on every third-party surface that parses it
    strictly (found the same day, by sweeping the sibling generators).

"Renders in my browser" is the weakest possible claim for a generated
artifact: HTML parsers are forgiving, image decoders are forgiving, and the
CDN serves bytes without opinions. These tests parse each artifact with a
STRICT consumer of its declared format.
"""
from __future__ import annotations

import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import badge_svg  # noqa: E402
import qrcode_svg  # noqa: E402

# XML defines exactly five named entities. Anything else is ill-formed.
UNDEFINED_ENTITY = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#)[A-Za-z]+;")


def _badge_variants():
    """Exercise both branches of badge_svg.render(receipt_dict).

    The real API (server/app.py:1316) is render(record, base_url=...): a
    receipt dict, where a `leaves` manifest selects the folder branch and its
    `&middot;`-bearing "dataset" subtitle.
    """
    single = {"receipt_id": "XwTULwlh76PcCst9",
              "created_at": "2026-05-12T17:36:24+00:00",
              "status": "pinned"}
    folder = dict(single, leaves=["a"] * 42)
    variants = [
        ({"branch": "single"}, badge_svg.render(single, base_url="https://orphograph.com")),
        ({"branch": "folder"}, badge_svg.render(folder, base_url="https://orphograph.com")),
    ]
    assert all(v for _, v in variants), "render returned empty output"
    return variants


class TestGeneratedArtifactsParse(unittest.TestCase):
    def test_badge_is_wellformed_xml(self):
        for kwargs, svg in _badge_variants():
            with self.subTest(**{k: v for k, v in kwargs.items() if k != "date"}):
                ET.fromstring(svg)  # raises on ill-formed XML

    def test_badge_has_no_undefined_named_entities(self):
        for kwargs, svg in _badge_variants():
            hits = UNDEFINED_ENTITY.findall(svg)
            self.assertEqual(hits, [],
                             f"HTML-only entities in badge output: {hits}")

    def test_qr_svg_is_wellformed_xml(self):
        ET.fromstring(qrcode_svg.make_svg("https://orphograph.com/r/XwTULwlh76PcCst9"))

    def test_shipped_qr_asset_is_wellformed(self):
        shipped = (Path(__file__).resolve().parent.parent / "web" / "qr-receipt.svg")
        ET.fromstring(shipped.read_text())


if __name__ == "__main__":
    unittest.main()
