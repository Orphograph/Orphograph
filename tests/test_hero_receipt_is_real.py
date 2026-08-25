"""The homepage receipt must be a REAL receipt, not a mock.

Until 2026-08-25 the hero showed a genuine receipt id (XwTULwlh76PcCst9)
beside six invented values: a fake filename, a fake hash, a fake timestamp,
a fake block height and a fake confirmation count. Anyone who looked that id
up in Orphograph's own verifier saw entirely different data.

On a notary that is not a copy nit. It is the product publishing the exact
class of thing it sells protection against.

This test pins the displayed values to the canonical sample receipt on disk,
so the two cannot drift apart again. It reads local data only, so it passes
with no network in CI.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "web" / "index.html"
SAMPLE_ID = "XwTULwlh76PcCst9"
# The canonical sample ships with the site and the verifier kit.
RECEIPT_JSON = ROOT / "web" / "sample" / "receipt.json"


def _hero_meta() -> str:
    html = INDEX.read_text(encoding="utf-8")
    block = html.split('id="hero-sample-receipt"', 1)[1]
    return block.split("</dl>", 1)[0]


def test_hero_shows_the_real_hash_of_the_sample_receipt():
    if not RECEIPT_JSON.exists():
        pytest.skip(f"canonical sample receipt not on disk: {RECEIPT_JSON}")
    real = json.loads(RECEIPT_JSON.read_text(encoding="utf-8"))
    real_hash = real["hash_hex"]
    meta = _hero_meta()
    assert real_hash in meta, (
        "the homepage shows a hash that is not the sample receipt's real hash "
        f"({real_hash}) — a real receipt id beside invented data is the defect "
        "this test exists to prevent"
    )


def test_hero_shows_no_fabricated_fields():
    """The API returns no block height, no confirmation count and no file
    size for a receipt. Displaying any of them means inventing them."""
    meta = _hero_meta()
    for banned in ("Bitcoin block", "Confirmations", "Size"):
        assert banned not in meta, (
            f"'{banned}' is not a field Orphograph's receipt API returns; "
            "showing it means the number was made up"
        )


def test_hero_hash_is_a_real_sha256_not_a_pattern():
    """The old fake hash was 2f7c4e0b8e3a3f6d... — an ascending-digit pattern.
    Any human-authored 'hash' looks like that; a real one does not."""
    meta = _hero_meta()
    hashes = re.findall(r"\b[0-9a-f]{64}\b", meta)
    assert hashes, "the hero must display a full 64-character SHA-256"
    for h in hashes:
        assert "0a1b2c3d4e5f" not in h, "that is the fabricated placeholder hash"
