"""test_sanitization.py — allowlist enforcement for the receipt sanitization
security boundary.

The attestation and metadata blocks are the only client-supplied free-form
fields that get written into a Bitcoin-anchored receipt. Once anchored, the
receipt content is committed to the chain and effectively immutable. A
malicious client must not be able to:

  - Smuggle unknown keys past the allowlist (e.g. JS payload, large blobs)
  - Exceed the per-field size caps (DoS via storage growth + ledger bloat)
  - Inject non-string/non-numeric values (lists, dicts, None) that downstream
    consumers might mishandle
  - Bypass the GPS-redaction rule (GPS is deliberately omitted from the
    metadata allowlist — clients must strip GPS before anchoring)
  - Cause anchor_hash() to crash on adversarial input shapes

These tests pin the security contract of the allowlist. If a new field is
added to the allowlist, add a test below.
"""
from __future__ import annotations

import json

import pytest

import engine
from conftest import PENDING_BODY  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ROOT", tmp_path)
    monkeypatch.setattr(engine, "RECEIPTS_DIR", tmp_path / "receipts")
    monkeypatch.setattr(engine, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(engine, "_submit", lambda _u, h: (True, PENDING_BODY))
    yield


HASH_HEX = "a" * 64


# -----------------------------------------------------------------------------
# _sanitize_attestation
# -----------------------------------------------------------------------------

def test_attestation_returns_none_for_falsy_inputs():
    assert engine._sanitize_attestation(None) is None
    assert engine._sanitize_attestation({}) is None
    assert engine._sanitize_attestation([]) is None
    assert engine._sanitize_attestation("not a dict") is None
    assert engine._sanitize_attestation(0) is None


def test_attestation_keeps_only_allowlisted_keys():
    raw = {
        "claim": "I took this photo",
        "author": "Orphograph",
        "license": "CC-BY 4.0",
        "url": "https://example.com",
        "signed_at": "2026-05-15T00:00:00Z",
        # The fields below MUST be dropped:
        "evil_payload": "<script>alert(1)</script>",
        "secret_key": "sk_live_abcdef",
        "__proto__": "polluted",
        "nested": {"inside": "object"},
        "list_field": ["a", "b"],
        "huge_blob": "x" * 10_000,
    }
    out = engine._sanitize_attestation(raw)
    assert out is not None
    assert set(out.keys()) == {"claim", "author", "license", "url", "signed_at"}
    assert "evil_payload" not in out
    assert "__proto__" not in out
    assert "nested" not in out
    assert "huge_blob" not in out


def test_attestation_enforces_500_char_size_cap():
    raw = {"claim": "A" * 5000}
    out = engine._sanitize_attestation(raw)
    assert out is not None
    # Cap is 500 chars per field; longer values must be truncated, not rejected.
    assert len(out["claim"]) == 500
    assert out["claim"] == "A" * 500


def test_attestation_drops_non_string_allowlisted_values():
    # Even keys on the allowlist must be strings — caller cannot smuggle
    # arbitrary types under a known key.
    raw = {
        "claim": 12345,             # int — drop
        "author": ["F", "R"],      # list — drop
        "license": {"k": "v"},     # dict — drop
        "url": None,                # None — drop
        "signed_at": True,          # bool — drop (bool isinstance int but not str)
    }
    out = engine._sanitize_attestation(raw)
    assert out is None  # everything dropped → empty dict → None


def test_attestation_strips_whitespace_and_drops_empty_strings():
    raw = {
        "claim": "   ",            # whitespace-only → drop
        "author": "  Orphograph  ", # strip
        "license": "",              # empty → drop
    }
    out = engine._sanitize_attestation(raw)
    assert out == {"author": "Orphograph"}


def test_attestation_persists_in_anchored_receipt_after_sanitization():
    """Adversarial attestation reaches the engine; only allowed fields survive."""
    rec = engine.anchor_hash(
        HASH_HEX,
        attestation={
            "claim": "valid",
            "evil": "drop me",
            "license": "x" * 1000,  # truncated to 500
        },
    )
    assert rec["attestation"] == {"claim": "valid", "license": "x" * 500}
    # And the on-disk receipt must reflect the same — this is what's anchored.
    rfile = engine.RECEIPTS_DIR / rec["receipt_id"] / "receipt.json"
    on_disk = json.loads(rfile.read_text())
    assert on_disk["attestation"] == {"claim": "valid", "license": "x" * 500}


# -----------------------------------------------------------------------------
# _sanitize_metadata
# -----------------------------------------------------------------------------

def test_metadata_returns_none_for_falsy_inputs():
    assert engine._sanitize_metadata(None) is None
    assert engine._sanitize_metadata({}) is None
    assert engine._sanitize_metadata("not a dict") is None
    assert engine._sanitize_metadata([1, 2, 3]) is None


def test_metadata_rejects_gps_fields_by_design():
    """GPS is deliberately NOT on the allowlist — clients must redact GPS
    client-side. Anchoring GPS into a public receipt would leak the
    photographer's home address.
    """
    raw = {
        "filename": "photo.jpg",
        "gps_latitude": 18.4655,
        "gps_longitude": -66.1057,
        "exif_gps_latitude": 18.4655,
        "GPSLatitude": 18.4655,
        "gps": {"lat": 18.4655, "lon": -66.1057},
    }
    out = engine._sanitize_metadata(raw)
    assert out == {"filename": "photo.jpg"}
    # Verify no GPS-shaped key leaked through:
    for k in out:
        assert "gps" not in k.lower()


def test_metadata_keeps_all_allowlisted_exif_fields():
    raw = {
        "filename": "shoot.cr3",
        "size_bytes": 24_500_000,
        "mime_type": "image/x-canon-cr3",
        "exif_camera_make": "Canon",
        "exif_camera_model": "EOS R5",
        "exif_camera_serial": "SN123456",
        "exif_lens": "RF 24-70mm F2.8",
        "exif_capture_time": "2026-05-15T14:32:00Z",
        "exif_software": "Lightroom",
        "exif_iso": 100,
        "exif_aperture": 2.8,
        "exif_shutter": "1/200",
        "exif_focal_length": 50,
        "image_width": 8192,
        "image_height": 5464,
        "image_format": "CR3",
    }
    out = engine._sanitize_metadata(raw)
    assert out is not None
    assert set(out.keys()) == set(raw.keys())
    assert out["exif_iso"] == 100
    assert out["exif_aperture"] == 2.8


def test_metadata_enforces_200_char_string_cap():
    raw = {"filename": "F" * 5000}
    out = engine._sanitize_metadata(raw)
    assert out is not None
    assert len(out["filename"]) == 200


def test_metadata_rejects_unbounded_numerics():
    # The numeric cap is -1e15..1e15 — protects against absurd values like
    # NaN/inf or attacker-supplied huge ints being JSON-serialized into
    # the on-chain-anchored receipt.
    raw = {
        "exif_iso": 1e20,           # too large — drop
        "exif_aperture": -1e16,     # too small — drop
        "image_width": float("inf"),  # not finite — drop (inf > 1e15)
        "exif_focal_length": 50,    # valid — keep
    }
    out = engine._sanitize_metadata(raw)
    assert out == {"exif_focal_length": 50}


def test_metadata_drops_nan():
    raw = {"exif_iso": float("nan"), "filename": "ok.jpg"}
    out = engine._sanitize_metadata(raw)
    # NaN compares False to any bound, so it gets dropped.
    assert "exif_iso" not in out
    assert out["filename"] == "ok.jpg"


def test_metadata_drops_list_and_dict_values_under_known_keys():
    raw = {
        "filename": ["a.jpg", "b.jpg"],     # list — drop
        "exif_lens": {"name": "RF"},         # dict — drop
        "size_bytes": "not a number",       # str under numeric key — allowed
                                              # via str branch (capped to 200)
    }
    out = engine._sanitize_metadata(raw)
    # size_bytes can legitimately be either a str or int per the sanitizer's
    # type branches; either way, list/dict must be dropped.
    assert "filename" not in out
    assert "exif_lens" not in out


def test_anchor_hash_survives_hostile_metadata_payload():
    """anchor_hash() must not raise on adversarial metadata/attestation."""
    hostile_metadata = {
        # Allowlisted but oversized:
        "filename": "x" * 10_000,
        # Unknown fields:
        "../../etc/passwd": "read me",
        "\x00\x01\x02": "binary key",
        "huge_int": 10**50,
        # Wrong types under known keys:
        "exif_camera_make": {"nested": "object"},
        # GPS attempts:
        "gps_latitude": 18.4655,
    }
    hostile_attestation = {
        "claim": "x" * 100_000,
        "__proto__": "polluted",
        "constructor": {"prototype": "x"},
    }
    rec = engine.anchor_hash(
        HASH_HEX,
        attestation=hostile_attestation,
        metadata=hostile_metadata,
    )
    # Only allowlisted survivors with caps applied:
    assert rec["metadata"] == {"filename": "x" * 200}
    assert rec["attestation"] == {"claim": "x" * 500}
    # And the receipt is JSON-serializable (no crash):
    rfile = engine.RECEIPTS_DIR / rec["receipt_id"] / "receipt.json"
    parsed = json.loads(rfile.read_text())
    assert parsed["attestation"]["claim"] == "x" * 500
    assert "gps_latitude" not in parsed["metadata"]


def test_anchor_with_none_attestation_and_metadata_stores_none():
    """No attestation/metadata supplied → fields are None on the receipt
    (not missing, not {}) — keeps receipt schema stable for verifiers.
    """
    rec = engine.anchor_hash(HASH_HEX)
    assert rec["attestation"] is None
    assert rec["metadata"] is None
