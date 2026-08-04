"""test_verify_hw.py — pin the standalone hardware-attestation verifier
(dist/orphograph-verify/verify_hw.py).

Two layers, per the no-new-dependencies rule (python stdlib has no P-256
signing):

  1. EMBEDDED VECTOR — a precomputed p256-device-sig-v1 attestation (key
     generated + message signed once with the openssl CLI; only the PUBLIC
     artifacts are embedded). Runs everywhere, no tools needed. Pins the
     pure-Python ECDSA implementation and the whole accept/reject matrix.
  2. OPENSSL ROUNDTRIP — generates a FRESH P-256 keypair with the `openssl`
     CLI (a system tool, same class the repo already shells out to), signs
     the spec message, and drives verify_hw.py as a subprocess end to end.
     Skipped cleanly when openssl is absent.

Real Secure Enclave signing cannot run in CI — exercise it manually:
    python3 capture/orphograph_attest.py --self-test
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist" / "orphograph-verify"
if str(DIST_DIR) not in sys.path:
    sys.path.insert(0, str(DIST_DIR))

import verify_hw  # noqa: E402


# ─── Embedded static vector (openssl-generated once; public parts only) ─────
# msg = "orpho-hw-v1"||00||hash||00||signed_at||00||device_id||00||u64(counter)
VECTOR_HASH = "22dc653dfb0a14751702873d2238e78bff661f0003fec84fb3dc47744b9657e3"
VECTOR_ATT = {
    "attestation_type": "p256-device-sig-v1",
    "hash_hex": VECTOR_HASH,
    "device_id": "bb232d9b6fbe93f059428009003a146306b3aec00745fa0f570f46d7422e93e3",
    "device_pubkey": (
        "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAELnujO7kGjRv3XzEnT88Aq8jSmYsk"
        "6/Rns1Gwgcmd9clrPBW/sIEuWruYUFlmkzDCBDBJH5oNj7c7vNtbQhWhRg=="
    ),
    "signed_at": "2026-08-04T12:00:00+00:00",
    "key_created_at": "2026-08-01T00:00:00+00:00",
    "counter": 1,
    "counter_kind": "software",
    "signature": (
        "MEQCIClHUNb36+iNVm4/ZdRDZRXxMHbpazXRVX8NtmXxqyXXAiAD/P08LfE+fwZL"
        "4AypHisdIt5ncJTQXf27o+/TNPNQ1A=="
    ),
    "element": "apple-secure-enclave",
}


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    # Neutral cwd so accidental relative-path coupling would fail.
    return subprocess.run(
        [sys.executable, str(DIST_DIR / "verify_hw.py"), *args],
        check=False, capture_output=True, text=True,
        cwd=str(DIST_DIR.parent), timeout=120,
    )


# ─── 1a. Embedded vector: library-level accept path ─────────────────────────

def test_vector_verifies():
    result = verify_hw.verify_attestation(VECTOR_ATT, VECTOR_HASH)
    assert result["valid"], result
    assert result["device_id"] == VECTOR_ATT["device_id"]


def test_vector_device_id_is_sha256_of_pubkey():
    spki = base64.b64decode(VECTOR_ATT["device_pubkey"])
    assert hashlib.sha256(spki).hexdigest() == VECTOR_ATT["device_id"]


# ─── 1b. Embedded vector: reject matrix ─────────────────────────────────────

def _mutated(**overrides) -> dict:
    att = copy.deepcopy(VECTOR_ATT)
    att.update(overrides)
    return att


def test_tampered_signature_fails():
    sig = bytearray(base64.b64decode(VECTOR_ATT["signature"]))
    sig[-1] ^= 0x01
    att = _mutated(signature=base64.b64encode(bytes(sig)).decode())
    assert not verify_hw.verify_attestation(att, VECTOR_HASH)["valid"]


def test_wrong_anchored_hash_fails():
    # Attestation-swapper at read time: same attestation, different receipt.
    other = hashlib.sha256(b"a different file").hexdigest()
    result = verify_hw.verify_attestation(VECTOR_ATT, other)
    assert not result["valid"]
    assert "bound" in result["error"]


def test_tampered_signed_at_breaks_signature():
    att = _mutated(signed_at="2020-01-01T00:00:00+00:00")  # backdating attempt
    assert not verify_hw.verify_attestation(att, VECTOR_HASH)["valid"]


def test_tampered_counter_breaks_signature():
    att = _mutated(counter=2)
    assert not verify_hw.verify_attestation(att, VECTOR_HASH)["valid"]


def test_swapped_pubkey_fails_device_id_derivation():
    other_spki = base64.b64decode(VECTOR_ATT["device_pubkey"])[:-1] + b"\x00"
    att = _mutated(device_pubkey=base64.b64encode(other_spki).decode())
    result = verify_hw.verify_attestation(att, VECTOR_HASH)
    assert not result["valid"]


def test_asserted_device_id_cannot_disagree_with_pubkey():
    att = _mutated(device_id="f" * 64)
    result = verify_hw.verify_attestation(att, VECTOR_HASH)
    assert not result["valid"]
    assert "device_id" in result["error"]


def test_unknown_attestation_type_fails():
    att = _mutated(attestation_type="p256-device-sig-v99")
    assert not verify_hw.verify_attestation(att, VECTOR_HASH)["valid"]


def test_malformed_der_signature_fails():
    att = _mutated(signature=base64.b64encode(b"\x31\x06\x02\x01\x01\x02\x01\x01").decode())
    result = verify_hw.verify_attestation(att, VECTOR_HASH)
    assert not result["valid"]
    assert "DER" in result["error"]


def test_pubkey_point_not_on_curve_fails():
    spki = bytearray(base64.b64decode(VECTOR_ATT["device_pubkey"]))
    spki[-1] ^= 0x01  # perturb Y — leaves the curve
    att = _mutated(device_pubkey=base64.b64encode(bytes(spki)).decode())
    att["device_id"] = hashlib.sha256(bytes(spki)).hexdigest()  # keep derivation
    result = verify_hw.verify_attestation(att, VECTOR_HASH)
    assert not result["valid"]


# ─── 1c. Embedded vector: CLI end to end ────────────────────────────────────

def _write_receipt(tmp_path: Path, att: dict | None,
                   hash_hex: str = VECTOR_HASH) -> Path:
    receipt = {"receipt_id": "RCPT_TEST_HW", "hash_hex": hash_hex,
               "created_at": "2026-08-04T12:00:01+00:00"}
    if att is not None:
        receipt["hardware_attestation"] = att
    p = tmp_path / "receipt.json"
    p.write_text(json.dumps(receipt, indent=2))
    return p


def test_cli_pass_prints_honest_tofu_scope(tmp_path):
    receipt = _write_receipt(tmp_path, VECTOR_ATT)
    out = _run_cli("--output-hash", VECTOR_HASH, "--receipt", str(receipt))
    assert out.returncode == 0, out.stdout + out.stderr
    assert "VERIFIED" in out.stdout
    # The honesty line ships inside the tool (spike doc §4, verbatim scope).
    assert ("proves this device key signed this hash; first-use trust — "
            "not a chain to Apple's CA in v1") in out.stdout
    assert "device-key continuity under first-use pinning" in out.stdout
    assert "client-side claim in v1" in out.stdout
    # Provenance framing only — never authenticity/authorship claims.
    assert "authentic capture" not in out.stdout


def test_cli_fails_on_tampered_attestation(tmp_path):
    sig = bytearray(base64.b64decode(VECTOR_ATT["signature"]))
    sig[10] ^= 0xFF
    att = _mutated(signature=base64.b64encode(bytes(sig)).decode())
    receipt = _write_receipt(tmp_path, att)
    out = _run_cli("--output-hash", VECTOR_HASH, "--receipt", str(receipt))
    assert out.returncode == 1
    assert "FAIL" in out.stdout


def test_cli_wrong_file_hash_fails_before_attestation(tmp_path):
    receipt = _write_receipt(tmp_path, VECTOR_ATT)
    out = _run_cli("--output-hash", "0" * 64, "--receipt", str(receipt))
    assert out.returncode == 1
    assert "not the anchored file" in out.stdout


def test_cli_no_attestation_field_is_note_not_pass(tmp_path):
    receipt = _write_receipt(tmp_path, None)
    out = _run_cli("--output-hash", VECTOR_HASH, "--receipt", str(receipt))
    assert out.returncode == 1
    assert "no hardware_attestation" in out.stdout
    assert "still" not in out.stdout.lower() or "valid Orphograph receipt" in out.stdout


def test_cli_corrupt_receipt_is_usage_error(tmp_path):
    p = tmp_path / "receipt.json"
    p.write_text("{not json")
    out = _run_cli("--output-hash", VECTOR_HASH, "--receipt", str(p))
    assert out.returncode == 2


# ─── 2. openssl roundtrip with a FRESH synthetic P-256 keypair ──────────────

needs_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl CLI not available")


def _openssl_keypair(tmp_path: Path) -> tuple[Path, bytes]:
    key_pem = tmp_path / "key.pem"
    pub_der = tmp_path / "pub.der"
    subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey",
                    "-noout", "-out", str(key_pem)],
                   check=True, capture_output=True, timeout=60)
    subprocess.run(["openssl", "ec", "-in", str(key_pem), "-pubout",
                    "-outform", "DER", "-out", str(pub_der)],
                   check=True, capture_output=True, timeout=60)
    return key_pem, pub_der.read_bytes()


def _openssl_sign(tmp_path: Path, key_pem: Path, message: bytes) -> bytes:
    msg_file = tmp_path / "msg.bin"
    sig_file = tmp_path / "sig.der"
    msg_file.write_bytes(message)
    subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key_pem),
                    "-out", str(sig_file), str(msg_file)],
                   check=True, capture_output=True, timeout=60)
    return sig_file.read_bytes()


@needs_openssl
def test_fresh_keypair_full_cli_roundtrip(tmp_path):
    """capture-shaped attestation → receipt → verify_hw CLI, file mode."""
    anchored_file = tmp_path / "photo.jpg"
    anchored_file.write_bytes(b"fresh synthetic capture bytes")
    hash_hex = hashlib.sha256(anchored_file.read_bytes()).hexdigest()

    key_pem, spki = _openssl_keypair(tmp_path)
    assert len(spki) == 91  # openssl emits exactly the SPKI shape v1 requires
    device_id = hashlib.sha256(spki).hexdigest()
    signed_at = "2026-08-04T15:30:00+00:00"
    counter = 42
    message = verify_hw.build_message(hash_hex, signed_at, device_id, counter)
    signature = _openssl_sign(tmp_path, key_pem, message)

    att = {
        "attestation_type": "p256-device-sig-v1",
        "hash_hex": hash_hex,
        "device_id": device_id,
        "device_pubkey": base64.b64encode(spki).decode(),
        "signed_at": signed_at,
        "key_created_at": "2026-08-04T15:00:00+00:00",
        "counter": counter,
        "counter_kind": "software",
        "signature": base64.b64encode(signature).decode(),
        "element": "test-synthetic-p256",
    }
    receipt = _write_receipt(tmp_path, att, hash_hex=hash_hex)
    out = _run_cli("--output", str(anchored_file), "--receipt", str(receipt))
    assert out.returncode == 0, out.stdout + out.stderr
    assert "VERIFIED" in out.stdout

    # The same fresh attestation also survives the server-side sanitizer.
    server_dir = ROOT / "server"
    if str(server_dir) not in sys.path:
        sys.path.insert(0, str(server_dir))
    import engine
    assert engine._sanitize_hardware_attestation(att, hash_hex) is not None


@needs_openssl
def test_fresh_keypair_rejects_cross_receipt_swap(tmp_path):
    """A signature minted for hash A must not verify on receipt B."""
    key_pem, spki = _openssl_keypair(tmp_path)
    device_id = hashlib.sha256(spki).hexdigest()
    hash_a = hashlib.sha256(b"file A").hexdigest()
    hash_b = hashlib.sha256(b"file B").hexdigest()
    signed_at = "2026-08-04T15:30:00+00:00"
    message = verify_hw.build_message(hash_a, signed_at, device_id, 1)
    signature = _openssl_sign(tmp_path, key_pem, message)
    att = {
        "attestation_type": "p256-device-sig-v1",
        "hash_hex": hash_b,  # swapped binding
        "device_id": device_id,
        "device_pubkey": base64.b64encode(spki).decode(),
        "signed_at": signed_at,
        "key_created_at": "2026-08-04T15:00:00+00:00",
        "counter": 1,
        "counter_kind": "software",
        "signature": base64.b64encode(signature).decode(),
    }
    result = verify_hw.verify_attestation(att, hash_b)
    assert not result["valid"]  # message rebuilt with hash_b ⇒ sig breaks
