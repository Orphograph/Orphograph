"""test_hw_attestation_honest_scope.py

Pins what a hardware attestation does NOT prove (audit 2026-08-25, backlog
item A: "Hardware attestation (HSM/TPM binding) — what is signed, can it be
spoofed, is the attestation ever trusted without verification").

The audit found the implementation CLEAN. This module exists so it stays that
way, because the risk here is not a code bug — it is a future copy edit or a
"clarified" verifier message quietly upgrading a TOFU signature into a claim
of hardware provenance.

WHAT WAS FOUND
--------------
* WHAT IS SIGNED — a domain-separated message that binds the anchored hash:
      "orpho-hw-v1" || 0x00 || hash_hex || 0x00 || signed_at
                    || 0x00 || device_id || 0x00 || uint64_be(counter)
  device_id is DERIVED (sha256 of the pubkey DER) on both sides, so a forged
  device_id cannot disagree with the key that signed.
* CAN IT BE SPOOFED — YES, by design in v1. v1 is trust-on-first-use with no
  certificate chain to Apple, so ANY P-256 key verifies. The fixture below is
  a plain OpenSSL software key that never went near a Secure Enclave, and it
  carries element="Apple Secure Enclave" as an outright lie. It verifies.
  That is not a defect; it is the documented scope. The defect would be
  claiming otherwise.
* TRUSTED WITHOUT VERIFICATION — no. The server deliberately shape-validates
  only and never verifies the signature (engine._sanitize_hardware_attestation:
  "the server stays dependency-free and never becomes the trust root"). The
  offline verifier does the real ECDSA, and no path returns valid=True without
  calling verify_p256_sha256. No customer-facing page claims hardware backing.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VERIFY_HW = REPO_ROOT / "dist" / "orphograph-verify" / "verify_hw.py"
WEB = REPO_ROOT / "web"

# A SOFTWARE P-256 key generated with `openssl ecparam -name prime256v1`.
# No secure element was involved at any point.
SW_HASH_HEX = "ab80b4eb6604c6e591f8f826b8476c1adb94c883b7e3a459bf74c821ecf6c887"
SW_DEVICE_ID = "77fc682687a50d5d0835a268f816149ef82e8a4aae3a01a584be3e028e6d7c6e"
SW_SIGNED_AT = "2026-08-25T21:45:00Z"
SW_PUBKEY_B64 = (
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEJS2Su0HsaSlPccH666tDdgdlE0GIrpmZ"
    "64U5MIPKtVZBnMat7uPsOsFfpR6qbVdtdGU8AD5sQsYMs5YreL2M1g=="
)
SW_SIG_B64 = (
    "MEYCIQCXaSCWVevd0HvUAAqZanv20n1yX1pY2J4MYKh8HLmE1gIhAIrGY57OmCdnoOEN"
    "kkIE0oDyICFMRjlVeOmrn77jUdSt"
)


def _software_attestation() -> dict:
    return {
        "attestation_type": "p256-device-sig-v1",
        "hash_hex": SW_HASH_HEX,
        "device_id": SW_DEVICE_ID,
        "device_pubkey": SW_PUBKEY_B64,
        "signed_at": SW_SIGNED_AT,
        "key_created_at": "2026-08-25T21:44:00Z",
        "counter": 1,
        "counter_kind": "monotonic",
        "element": "Apple Secure Enclave",   # the lie the format cannot catch
        "signature": SW_SIG_B64,
    }


@pytest.fixture(scope="module")
def vhw():
    spec = importlib.util.spec_from_file_location("verify_hw", VERIFY_HW)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["verify_hw"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_fixture_is_internally_consistent(vhw):
    """NEGATIVE CONTROL for the fixture itself. If the constants ever drift,
    every assertion below would be testing nothing."""
    spki = base64.b64decode(SW_PUBKEY_B64)
    assert len(spki) == 91, len(spki)
    assert hashlib.sha256(spki).hexdigest() == SW_DEVICE_ID


def test_a_software_key_produces_a_VALID_attestation(vhw):
    """THE HONEST-SCOPE PIN. v1 is TOFU with no chain to Apple's CA, so a key
    that never touched a secure element verifies. If this ever starts FAILING,
    real hardware provenance was added — which is good, but every page and
    every message describing the scope must then be re-read and updated."""
    result = vhw.verify_attestation(_software_attestation(), SW_HASH_HEX)
    assert result["valid"] is True, result.get("error")
    assert result["device_id"] == SW_DEVICE_ID


def test_the_element_label_is_never_treated_as_evidence(vhw):
    """The `element` string is attacker-controlled. It must not influence the
    verdict — the same attestation verifies with any label at all."""
    for label in ("Apple Secure Enclave", "TPM 2.0", "", "definitely-real-hsm"):
        att = _software_attestation() | {"element": label}
        assert vhw.verify_attestation(att, SW_HASH_HEX)["valid"] is True


def test_the_signature_is_actually_checked(vhw):
    """Can-this-test-fail check. Flip one byte of the signature and one byte of
    the signed hash; both must be rejected, or `valid` is decoration."""
    sig = bytearray(base64.b64decode(SW_SIG_B64))
    sig[-1] ^= 0x01
    bad = _software_attestation() | {"signature": base64.b64encode(bytes(sig)).decode()}
    assert vhw.verify_attestation(bad, SW_HASH_HEX)["valid"] is False

    other_hash = "b" + SW_HASH_HEX[1:]
    swapped = _software_attestation() | {"hash_hex": other_hash}
    assert vhw.verify_attestation(swapped, other_hash)["valid"] is False


def test_verifier_states_the_scope_it_actually_proves(vhw):
    """The verifier's own docstring must keep disclaiming what a PASS means.
    This wording IS the product's honesty on a trust surface."""
    doc = VERIFY_HW.read_text(encoding="utf-8")
    for phrase in (
        "client-side claim in v1",
        "not a chain to Apple",
        "client-asserted label, uncertified in v1",
        "does NOT establish scene/content authenticity",
    ):
        assert phrase.lower() in doc.lower(), f"verifier no longer states: {phrase!r}"


def test_no_public_page_claims_hardware_backed_verification():
    """The customer surface must not imply the enclave claim is verified.
    Nothing does today; this keeps it that way."""
    import html as html_mod
    import re

    bad = []
    for page in sorted(WEB.rglob("*.html")):
        if "_mockups" in page.parts or page.stem == "index-legacy":
            continue
        raw = re.sub(r"<(script|style)\b.*?</\1>", " ", page.read_text(errors="replace"),
                     flags=re.S | re.I)
        flat = " ".join(html_mod.unescape(re.sub(r"<[^>]+>", " ", raw)).split()).lower()
        for m in re.finditer(
            r"(secure enclave|hardware[- ]backed|hsm|tpm)[^.]{0,60}"
            r"(verified|proven|guarantee|certif)", flat
        ):
            bad.append(f"{page.relative_to(REPO_ROOT).as_posix()}: …{m.group(0)}…")
    assert not bad, (
        "A page implies the secure-element claim is verified. In v1 it is a "
        "client-asserted label with no certificate chain:\n  " + "\n  ".join(bad)
    )
