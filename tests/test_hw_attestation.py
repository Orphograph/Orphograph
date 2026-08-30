"""test_hw_attestation.py — allowlist/shape enforcement for the
`hardware_attestation` receipt field (docs/HARDWARE_ATTESTATION_SPIKE.md §3).

The field is a machine-verifiable cryptographic artifact (a device-held
key's ECDSA signature over the anchored hash), sanitized by
`engine._sanitize_hardware_attestation` mirroring the `zk_provenance`
pattern: strict shape validation, hash binding, and WHOLE-record rejection
on ANY violation — never a partial attestation that could not re-verify.

These tests pin:
  1. the accept path (canonicalized output, derived device_id, optional
     element / cert_chain, unknown keys dropped),
  2. the reject matrix (every field's format check ⇒ whole-record None),
  3. shape stability: the receipt record and verify_receipt() carry the
     field ONLY when a valid attestation was presented at anchor time.

Honest-scope note (framing pinned in test names, not just prose): a kept
record means "a hardware-resident key signed this hash at capture time" —
never scene/content authenticity or authorship.
"""
from __future__ import annotations

import base64
import hashlib
import json

import pytest

import engine
from conftest import PENDING_BODY  # noqa: E402


HASH_HEX = hashlib.sha256(b"hw-attested file bytes").hexdigest()

# Synthetic, structurally valid P-256 SPKI: fixed 26-byte prefix + 0x04 + 64
# point bytes. The sanitizer checks shape + derivation, not curve membership
# — signature validity is the offline verifier's job (verify_hw.py).
PUBKEY_DER = engine.HW_P256_SPKI_PREFIX + b"\x04" + bytes(range(64))
PUBKEY_B64 = base64.b64encode(PUBKEY_DER).decode()
DEVICE_ID = hashlib.sha256(PUBKEY_DER).hexdigest()
# DER-shaped ECDSA signature blob: SEQUENCE tag, within 8..72 bytes.
SIG_DER = bytes([0x30, 0x0C, 0x02, 0x04, 1, 2, 3, 4, 0x02, 0x04, 5, 6, 7, 8])
SIG_B64 = base64.b64encode(SIG_DER).decode()


def valid_att(**overrides) -> dict:
    att = {
        "attestation_type": "p256-device-sig-v1",
        "hash_hex": HASH_HEX,
        "device_id": DEVICE_ID,
        "device_pubkey": PUBKEY_B64,
        "signed_at": "2026-08-04T12:00:00+00:00",
        "key_created_at": "2026-08-01T00:00:00+00:00",
        "counter": 7,
        "counter_kind": "software",
        "signature": SIG_B64,
        "element": "apple-secure-enclave",
    }
    att.update(overrides)
    return att


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ROOT", tmp_path)
    monkeypatch.setattr(engine, "RECEIPTS_DIR", tmp_path / "receipts")
    monkeypatch.setattr(engine, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(engine, "_submit", lambda _u, h: (True, PENDING_BODY))
    yield


# ─── Accept path ────────────────────────────────────────────────────────────

def test_valid_attestation_kept_and_canonicalized():
    out = engine._sanitize_hardware_attestation(valid_att(), HASH_HEX)
    assert out is not None
    assert out["attestation_type"] == "p256-device-sig-v1"
    assert out["hash_hex"] == HASH_HEX
    assert out["device_id"] == DEVICE_ID
    assert out["device_pubkey"] == PUBKEY_B64
    assert out["counter"] == 7
    assert out["counter_kind"] == "software"
    assert out["element"] == "apple-secure-enclave"
    assert "cert_chain" not in out  # absent in v1 (TOFU) unless supplied


def test_hash_case_and_whitespace_normalized():
    att = valid_att(hash_hex="  " + HASH_HEX.upper() + " ",
                    device_id=DEVICE_ID.upper())
    out = engine._sanitize_hardware_attestation(att, HASH_HEX)
    assert out is not None
    assert out["hash_hex"] == HASH_HEX
    assert out["device_id"] == DEVICE_ID


def test_unknown_keys_dropped():
    att = valid_att(evil="<script>", scene_authentic=True, author="someone")
    out = engine._sanitize_hardware_attestation(att, HASH_HEX)
    assert out is not None
    # Never persist claims the layer does not make — no authorship or
    # content-authenticity fields survive, only the device-signature shape.
    assert "evil" not in out
    assert "scene_authentic" not in out
    assert "author" not in out
    assert set(out) <= {
        "attestation_type", "hash_hex", "device_id", "device_pubkey",
        "signed_at", "key_created_at", "counter", "counter_kind",
        "signature", "element", "cert_chain",
    }


def test_element_optional():
    att = valid_att()
    del att["element"]
    out = engine._sanitize_hardware_attestation(att, HASH_HEX)
    assert out is not None
    assert "element" not in out


def test_cert_chain_accepted_when_valid():
    chain = [base64.b64encode(b"cert-one").decode(),
             base64.b64encode(b"cert-two").decode()]
    out = engine._sanitize_hardware_attestation(
        valid_att(cert_chain=chain), HASH_HEX)
    assert out is not None
    assert out["cert_chain"] == chain


def test_hardware_counter_kind_accepted():
    out = engine._sanitize_hardware_attestation(
        valid_att(counter_kind="hardware"), HASH_HEX)
    assert out is not None
    assert out["counter_kind"] == "hardware"


# ─── Reject matrix: ANY violation rejects the WHOLE record ──────────────────

@pytest.mark.parametrize("att", [
    None,
    {},
    "not-a-dict",
    ["p256-device-sig-v1"],
    42,
])
def test_non_dict_or_empty_rejected(att):
    assert engine._sanitize_hardware_attestation(att, HASH_HEX) is None


@pytest.mark.parametrize("mutation", [
    {"attestation_type": "apple-se-v0"},          # unknown type
    {"attestation_type": None},                    # missing type
    {"hash_hex": "0" * 64},                        # attestation-swapper: wrong hash
    {"hash_hex": None},                            # missing hash
    {"hash_hex": HASH_HEX[:-2]},                   # truncated hash
    {"device_pubkey": "!!!not-base64!!!"},         # bad base64
    {"device_pubkey": base64.b64encode(PUBKEY_DER[:-1]).decode()},   # 90 bytes
    {"device_pubkey": base64.b64encode(b"\x00" * 91).decode()},      # wrong prefix
    {"device_pubkey": "A" * 300},                  # over cap
    {"device_id": "f" * 64},                       # asserted id != derived id
    {"device_id": 123},                            # non-string id
    {"device_id": DEVICE_ID[:-1]},                 # short id
    {"signed_at": "yesterday"},                    # not ISO-8601
    {"signed_at": "2026-08-04T12:00:00+00:00" + "0" * 30},  # over cap
    {"signed_at": None},
    {"key_created_at": "not-a-time"},              # TOFU timestamp format
    {"key_created_at": None},                      # TOFU timestamp required
    {"counter": True},                             # bool is not a counter
    {"counter": -1},
    {"counter": 2 ** 64},
    {"counter": "7"},
    {"counter": None},
    {"counter_kind": "quantum"},                   # outside allowlist
    {"counter_kind": None},
    {"signature": "!!!not-base64!!!"},
    {"signature": base64.b64encode(b"\x31" + b"\x00" * 20).decode()},  # not DER SEQ
    {"signature": base64.b64encode(b"\x30\x01").decode()},             # too short
    {"signature": base64.b64encode(b"\x30" + b"\x00" * 100).decode()},  # too long
    {"signature": None},
    {"element": ""},                               # empty label
    {"element": "x" * 61},                         # over cap
    {"element": 5},                                # non-string
    {"cert_chain": "cert"},                        # not a list
    {"cert_chain": []},                            # empty list
    {"cert_chain": [base64.b64encode(b"c").decode()] * 5},  # >4 entries
    {"cert_chain": ["not base64 at all !!!"]},
    {"cert_chain": ["A" * 4400]},                  # entry over cap
    {"cert_chain": [42]},                          # non-string entry
])
def test_field_violation_rejects_whole_record(mutation):
    att = valid_att(**mutation)
    assert engine._sanitize_hardware_attestation(att, HASH_HEX) is None


def test_sanitizer_never_crashes_on_adversarial_shapes():
    horrors = [
        valid_att(device_pubkey={"a": 1}),
        valid_att(signature=[1, 2, 3]),
        valid_att(counter=3.5),
        valid_att(cert_chain=[{"pem": "x"}]),
        {"attestation_type": b"p256-device-sig-v1"},
    ]
    for att in horrors:
        assert engine._sanitize_hardware_attestation(att, HASH_HEX) is None


# ─── anchor_hash / verify_receipt integration: only-when-set ────────────────

def test_anchor_with_valid_attestation_persists_field():
    rec = engine.anchor_hash(HASH_HEX, hardware_attestation=valid_att())
    assert rec["hardware_attestation"]["device_id"] == DEVICE_ID
    on_disk = json.loads(
        (engine.RECEIPTS_DIR / rec["receipt_id"] / "receipt.json").read_text())
    assert on_disk["hardware_attestation"]["hash_hex"] == HASH_HEX
    result = engine.verify_receipt(rec["receipt_id"])
    assert result["hardware_attestation"]["device_id"] == DEVICE_ID


def test_anchor_without_attestation_stays_shape_stable():
    rec = engine.anchor_hash(HASH_HEX)
    assert "hardware_attestation" not in rec
    on_disk = json.loads(
        (engine.RECEIPTS_DIR / rec["receipt_id"] / "receipt.json").read_text())
    assert "hardware_attestation" not in on_disk
    result = engine.verify_receipt(rec["receipt_id"])
    assert "hardware_attestation" not in result


def test_anchor_with_invalid_attestation_drops_field_but_anchors():
    # Whole-record rejection must not block the anchor itself: the receipt
    # exists, just without the field (never a partial attestation).
    bad = valid_att(device_id="f" * 64)
    rec = engine.anchor_hash(HASH_HEX, hardware_attestation=bad)
    assert "hardware_attestation" not in rec
    assert rec["calendars_ok"] == len(engine.CALENDARS)
    result = engine.verify_receipt(rec["receipt_id"])
    assert result["found"] is True
    assert "hardware_attestation" not in result


def test_anchor_with_swapped_hash_attestation_rejected():
    # The attestation-swapper adversary: a valid-looking attestation bound
    # to a DIFFERENT hash must never ride on this receipt.
    other_hash = hashlib.sha256(b"some other file").hexdigest()
    att = valid_att(hash_hex=other_hash)
    rec = engine.anchor_hash(HASH_HEX, hardware_attestation=att)
    assert "hardware_attestation" not in rec
