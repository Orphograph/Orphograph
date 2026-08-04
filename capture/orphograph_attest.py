#!/usr/bin/env python3
"""orphograph_attest.py — macOS Secure Enclave hardware attestation (v1).

Implements the v1 recommendation of docs/HARDWARE_ATTESTATION_SPIKE.md:
Apple Secure Enclave first, TOFU-honest, emitting the element-agnostic
`p256-device-sig-v1` payload for the receipt's `hardware_attestation` field.

Honest scope (binding, per the spike doc):
    A valid attestation means "a hardware-resident key signed this hash at
    capture time" under first-use (TOFU) pinning. It NEVER implies scene or
    content authenticity, authorship, that the device was uncompromised, or
    that `signed_at` is true wall-clock time (the only load-bearing time
    bound remains the OTS→Bitcoin path). Tamper-EVIDENT, not tamper-proof.

Mechanism (spike doc §6, VERIFY-BEFORE-BUILD #2 path):
    A tiny Swift helper CLI wrapping the Security framework is embedded in
    this file, compiled on demand with `swiftc` (a system tool on any Mac
    with the Xcode Command Line Tools), and driven via subprocess. The
    private key is created with `kSecAttrTokenIDSecureEnclave` and never
    leaves the enclave; the helper only ever exports the PUBLIC key and
    DER ECDSA signatures. Stdlib + system tools only — no pip deps.

Honest degradation (never fake, never block):
    On non-macOS machines, machines without `swiftc`, without a Secure
    Enclave, or when key access is denied, `make_attestation()` returns
    None and the capture flow anchors WITHOUT attestation.

Signed message (spike doc §3.2, domain-separated, deterministic):
    msg = "orpho-hw-v1" || 0x00 || hash_hex || 0x00 || signed_at
          || 0x00 || device_id || 0x00 || uint64_be(counter)

Manual Secure Enclave exercise (real hardware; cannot run in CI):
    python3 capture/orphograph_attest.py --self-test
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ATTESTATION_TYPE = "p256-device-sig-v1"
ELEMENT = "apple-secure-enclave"
DOMAIN_TAG = b"orpho-hw-v1"

# Same state dir as the capture daemon (overridable for tests).
STATE_DIR = Path(os.environ.get(
    "ORPHO_CAPTURE_STATE",
    str(Path.home() / "Library" / "Application Support" / "Orphograph")))
# Persisted TOFU state: device_id, key_created_at (the first-use pinning
# moment), and the software monotonic counter. The PRIVATE key itself lives
# in the Secure Enclave / keychain, never in this file.
HW_STATE_FILE = STATE_DIR / "hw_attest_state.json"
HELPER_BASENAME = "orpho-se"
SWIFT_TIMEOUT_SEC = 120

# Fixed 26-byte SubjectPublicKeyInfo prefix for an uncompressed P-256 point.
# SecKeyCopyExternalRepresentation returns the raw X9.63 point (04||X||Y);
# prepending this constant yields standard SPKI DER.
P256_SPKI_PREFIX = bytes.fromhex(
    "3059301306072a8648ce3d020106082a8648ce3d030107034200"
)

# ─── Embedded Swift helper (Security framework, Secure Enclave) ─────────────
# `orpho-se pubkey` — find-or-create the SE P-256 key, print raw X9.63
#                     public point, base64. Prints "CREATED" or "EXISTING"
#                     on the first line so Python can record the TOFU moment.
# `orpho-se sign <msg-hex>` — ECDSA-SHA256 over the raw message bytes
#                     (.ecdsaSignatureMessageX962SHA256), print DER sig b64.
SWIFT_HELPER_SOURCE = r'''
import Foundation
import Security

let keyTag = "com.orphograph.hw-attest-v1".data(using: .utf8)!

func die(_ msg: String) -> Never {
    FileHandle.standardError.write(("orpho-se: " + msg + "\n").data(using: .utf8)!)
    exit(1)
}

func findKey() -> SecKey? {
    let query: [String: Any] = [
        kSecClass as String: kSecClassKey,
        kSecAttrApplicationTag as String: keyTag,
        kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
        kSecReturnRef as String: true,
    ]
    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    guard status == errSecSuccess else { return nil }
    return (item as! SecKey)
}

func createKey() -> SecKey {
    var err: Unmanaged<CFError>?
    // .privateKeyUsage only — no biometric gate, so unattended (launchd)
    // signing works. A biometric-gated variant is a product decision, not v1.
    guard let access = SecAccessControlCreateWithFlags(
        kCFAllocatorDefault,
        kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        [.privateKeyUsage],
        &err
    ) else { die("SecAccessControlCreateWithFlags failed") }
    let attrs: [String: Any] = [
        kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
        kSecAttrKeySizeInBits as String: 256,
        kSecAttrTokenID as String: kSecAttrTokenIDSecureEnclave,
        kSecPrivateKeyAttrs as String: [
            kSecAttrIsPermanent as String: true,
            kSecAttrApplicationTag as String: keyTag,
            kSecAttrAccessControl as String: access,
        ],
    ]
    guard let key = SecKeyCreateRandomKey(attrs as CFDictionary, &err) else {
        let detail = err?.takeRetainedValue().localizedDescription ?? "unknown"
        die("SecKeyCreateRandomKey failed (no Secure Enclave?): " + detail)
    }
    return key
}

func publicPointB64(_ key: SecKey) -> String {
    guard let pub = SecKeyCopyPublicKey(key) else { die("SecKeyCopyPublicKey failed") }
    var err: Unmanaged<CFError>?
    guard let data = SecKeyCopyExternalRepresentation(pub, &err) as Data? else {
        die("SecKeyCopyExternalRepresentation failed")
    }
    return data.base64EncodedString()
}

let args = CommandLine.arguments
guard args.count >= 2 else { die("usage: orpho-se pubkey | orpho-se sign <msg-hex>") }

switch args[1] {
case "pubkey":
    if let key = findKey() {
        print("EXISTING")
        print(publicPointB64(key))
    } else {
        let key = createKey()
        print("CREATED")
        print(publicPointB64(key))
    }
case "sign":
    guard args.count == 3 else { die("sign requires <msg-hex>") }
    let hex = args[2]
    guard hex.count % 2 == 0 else { die("odd-length hex") }
    var msg = Data(capacity: hex.count / 2)
    var idx = hex.startIndex
    while idx < hex.endIndex {
        let next = hex.index(idx, offsetBy: 2)
        guard let b = UInt8(hex[idx..<next], radix: 16) else { die("bad hex") }
        msg.append(b)
        idx = next
    }
    guard let key = findKey() else { die("no device key — run pubkey first") }
    var err: Unmanaged<CFError>?
    guard let sig = SecKeyCreateSignature(
        key, .ecdsaSignatureMessageX962SHA256, msg as CFData, &err
    ) as Data? else {
        let detail = err?.takeRetainedValue().localizedDescription ?? "unknown"
        die("SecKeyCreateSignature failed: " + detail)
    }
    print(sig.base64EncodedString())
default:
    die("unknown command: " + args[1])
}
'''


def build_message(hash_hex: str, signed_at: str, device_id: str, counter: int) -> bytes:
    """The domain-separated signed message (spike doc §3.2, fixed order)."""
    return (DOMAIN_TAG + b"\x00" + hash_hex.encode("ascii")
            + b"\x00" + signed_at.encode("ascii")
            + b"\x00" + device_id.encode("ascii")
            + b"\x00" + counter.to_bytes(8, "big"))


def spki_from_raw_point(raw_point: bytes) -> bytes | None:
    """Wrap a raw X9.63 uncompressed P-256 point in SubjectPublicKeyInfo DER."""
    if len(raw_point) != 65 or raw_point[0] != 0x04:
        return None
    return P256_SPKI_PREFIX + raw_point


# ─── State (TOFU timestamp + software counter) ──────────────────────────────
def _load_state() -> dict:
    try:
        return json.loads(HW_STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        HW_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        HW_STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError:
        pass


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─── Secure Enclave signer (default; injectable for tests) ──────────────────
class SecureEnclaveSigner:
    """Drives the embedded Swift helper. Every failure raises RuntimeError;
    make_attestation() converts that into an honest None."""

    def __init__(self) -> None:
        self._pubkey_der: bytes | None = None
        self.key_was_created = False

    def _helper_path(self) -> Path:
        src_hash = hashlib.sha256(SWIFT_HELPER_SOURCE.encode()).hexdigest()[:16]
        return STATE_DIR / f"{HELPER_BASENAME}-{src_hash}"

    def _ensure_helper(self) -> Path:
        if sys.platform != "darwin":
            raise RuntimeError("Secure Enclave signing requires macOS")
        swiftc = shutil.which("swiftc")
        if not swiftc:
            raise RuntimeError("swiftc not found (Xcode Command Line Tools required)")
        helper = self._helper_path()
        if helper.exists():
            return helper
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        src = helper.with_suffix(".swift")
        src.write_text(SWIFT_HELPER_SOURCE)
        proc = subprocess.run(
            [swiftc, "-O", "-o", str(helper), str(src)],
            capture_output=True, text=True, timeout=SWIFT_TIMEOUT_SEC,
        )
        if proc.returncode != 0 or not helper.exists():
            raise RuntimeError(f"swiftc failed: {proc.stderr.strip()[:500]}")
        return helper

    def _run(self, *args: str) -> str:
        helper = self._ensure_helper()
        proc = subprocess.run(
            [str(helper), *args],
            capture_output=True, text=True, timeout=SWIFT_TIMEOUT_SEC,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"orpho-se {args[0]} failed: {proc.stderr.strip()[:500]}")
        return proc.stdout.strip()

    def pubkey_der(self) -> bytes:
        """SubjectPublicKeyInfo DER of the device public key (find-or-create)."""
        if self._pubkey_der is not None:
            return self._pubkey_der
        out = self._run("pubkey").splitlines()
        if len(out) != 2 or out[0] not in ("CREATED", "EXISTING"):
            raise RuntimeError("unexpected orpho-se pubkey output")
        self.key_was_created = out[0] == "CREATED"
        try:
            raw_point = base64.b64decode(out[1], validate=True)
        except Exception as exc:
            raise RuntimeError(f"bad pubkey base64: {exc}") from exc
        spki = spki_from_raw_point(raw_point)
        if spki is None:
            raise RuntimeError("device public key is not an uncompressed P-256 point")
        self._pubkey_der = spki
        return spki

    def sign(self, message: bytes) -> bytes:
        """DER ECDSA-SHA256 signature over the raw message bytes."""
        sig_b64 = self._run("sign", message.hex())
        try:
            return base64.b64decode(sig_b64, validate=True)
        except Exception as exc:
            raise RuntimeError(f"bad signature base64: {exc}") from exc


# ─── Attestation builder ────────────────────────────────────────────────────
def make_attestation(hash_hex: str, signer=None, log=None) -> dict | None:
    """Build a `hardware_attestation` payload for an anchored hash.

    Returns the payload dict, or None on ANY failure (no macOS, no swiftc,
    no Secure Enclave, denied access, helper error). None means the caller
    anchors WITHOUT attestation — degradation is honest: we never fabricate
    an attestation and never block the anchor.
    """
    _log = log or (lambda _m: None)
    hash_hex = (hash_hex or "").strip().lower()
    if len(hash_hex) != 64 or any(c not in "0123456789abcdef" for c in hash_hex):
        _log("hw-attest: refusing to attest a non-SHA-256 hash")
        return None
    try:
        if signer is None:
            signer = SecureEnclaveSigner()
        pubkey_der = signer.pubkey_der()
        device_id = hashlib.sha256(pubkey_der).hexdigest()

        state = _load_state()
        if state.get("device_id") != device_id:
            # First use of this key on this machine — the TOFU pinning moment.
            state = {"device_id": device_id, "key_created_at": _utcnow(), "counter": 0}
        counter = int(state.get("counter", 0)) + 1
        state["counter"] = counter
        key_created_at = state["key_created_at"]

        signed_at = _utcnow()
        message = build_message(hash_hex, signed_at, device_id, counter)
        signature = signer.sign(message)
        if not signature or signature[0] != 0x30:
            _log("hw-attest: signer returned a non-DER signature; skipping")
            return None
        _save_state(state)
        return {
            "attestation_type": ATTESTATION_TYPE,
            "hash_hex": hash_hex,
            "device_id": device_id,
            "device_pubkey": base64.b64encode(pubkey_der).decode("ascii"),
            "signed_at": signed_at,
            "key_created_at": key_created_at,
            "counter": counter,
            "counter_kind": "software",  # honest label: SE has no user-visible HW counter
            "signature": base64.b64encode(signature).decode("ascii"),
            "element": ELEMENT,
        }
    except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        _log(f"hw-attest unavailable ({exc}); anchoring without attestation")
        return None


# ─── Manual self-test (real Secure Enclave; cannot run in CI) ───────────────
def _self_test() -> int:
    """Round trip on real hardware: SE keygen → sign → offline verify."""
    sample_hash = hashlib.sha256(b"orphograph hw-attest self-test").hexdigest()
    att = make_attestation(sample_hash, log=lambda m: print(m, file=sys.stderr))
    if att is None:
        print("SELF-TEST: no attestation produced (no Secure Enclave / swiftc "
              "/ macOS, or access denied). This is the honest degrade path — "
              "a real capture would anchor WITHOUT attestation.")
        return 1
    print(json.dumps(att, indent=2))
    # Verify offline with the shipped verifier, if present.
    verify_hw = (Path(__file__).resolve().parent.parent
                 / "dist" / "orphograph-verify" / "verify_hw.py")
    if not verify_hw.exists():
        print("NOTE: dist/orphograph-verify/verify_hw.py not found; "
              "structural output only.")
        return 0
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        receipt = Path(td) / "receipt.json"
        receipt.write_text(json.dumps({
            "receipt_id": "SELFTEST",
            "hash_hex": sample_hash,
            "hardware_attestation": att,
        }))
        proc = subprocess.run(
            [sys.executable, str(verify_hw),
             "--output-hash", sample_hash, "--receipt", str(receipt)],
            capture_output=True, text=True, timeout=60,
        )
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        print(f"SELF-TEST: verify_hw exit={proc.returncode} "
              f"({'PASS' if proc.returncode == 0 else 'FAIL'})")
        return proc.returncode


def main() -> int:
    p = argparse.ArgumentParser(description="Orphograph Secure Enclave attestation helper")
    p.add_argument("--self-test", action="store_true",
                   help="SE keygen + sign + offline verify round trip (macOS only)")
    p.add_argument("--attest-hash", metavar="SHA256_HEX",
                   help="emit a hardware_attestation JSON for the given hash")
    args = p.parse_args()
    if args.self_test:
        return _self_test()
    if args.attest_hash:
        att = make_attestation(args.attest_hash,
                               log=lambda m: print(m, file=sys.stderr))
        if att is None:
            return 1
        print(json.dumps(att, indent=2))
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
