"""capture/test_orphograph_attest.py — offline tests for hardware attestation.

Covers everything EXCEPT real Secure Enclave signing (which requires a
physical Mac with an SE and cannot run in CI — exercise it manually with
`python3 capture/orphograph_attest.py --self-test`):

  1. attestation construction with a stubbed signer: field shape, derived
     device_id, domain-separated message bytes, software-counter increment,
     TOFU key_created_at stability across calls
  2. HONEST DEGRADATION — non-macOS / missing swiftc / signer failure /
     bad input all return None (never fake, never raise)
  3. `--attest` wiring in orphograph_capture.scan_once: attestation rides
     the anchor call when produced, the flow anchors WITHOUT attestation
     when it cannot be produced, and attest=False never touches the module
  4. wire shape: anchor_hash() adds `hardware_attestation` to the POST body
     only when one was supplied
  5. sidecar carries the field only when the server echoed it

All network + all Swift-helper subprocesses are stubbed.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

CAPTURE_DIR = Path(__file__).resolve().parent
if str(CAPTURE_DIR) not in sys.path:
    sys.path.insert(0, str(CAPTURE_DIR))

import orphograph_attest as oa  # noqa: E402
import orphograph_capture as oc  # noqa: E402


SAMPLE_HASH = hashlib.sha256(b"captured file bytes").hexdigest()


class FakeSigner:
    """Deterministic stand-in for SecureEnclaveSigner. Records every signed
    message; emits a structurally-DER signature (verification of a REAL
    signature is tests/test_verify_hw.py's job with a real P-256 key)."""

    def __init__(self, raw_point: bytes | None = None, fail_sign: bool = False):
        raw_point = raw_point or (b"\x04" + bytes(range(64)))
        self._spki = oa.spki_from_raw_point(raw_point)
        assert self._spki is not None
        self.fail_sign = fail_sign
        self.signed_messages: list[bytes] = []

    def pubkey_der(self) -> bytes:
        return self._spki

    def sign(self, message: bytes) -> bytes:
        if self.fail_sign:
            raise RuntimeError("simulated SE denial")
        self.signed_messages.append(message)
        return bytes([0x30, 0x0C, 0x02, 0x04, 9, 9, 9, 9, 0x02, 0x04, 8, 8, 8, 8])


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(oa, "STATE_DIR", state)
    monkeypatch.setattr(oa, "HW_STATE_FILE", state / "hw_attest_state.json")
    monkeypatch.setattr(oc, "STATE_DIR", state)
    monkeypatch.setattr(oc, "SEEN_DB", state / "seen.jsonl")
    monkeypatch.setattr(oc, "LOG_FILE", state / "capture.log")
    yield state


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def _blocked(*a, **k):  # pragma: no cover — only fires on a bug
        raise AssertionError("test attempted a real network call via urlopen")
    monkeypatch.setattr(oc.urllib.request, "urlopen", _blocked)


# ─── 1. Attestation construction (stubbed signer) ───────────────────────────

def test_make_attestation_shape_and_derived_device_id():
    signer = FakeSigner()
    att = oa.make_attestation(SAMPLE_HASH, signer=signer)
    assert att is not None
    assert att["attestation_type"] == oa.ATTESTATION_TYPE
    assert att["hash_hex"] == SAMPLE_HASH
    # device_id is DERIVED from the pubkey, never asserted independently.
    spki = base64.b64decode(att["device_pubkey"])
    assert att["device_id"] == hashlib.sha256(spki).hexdigest()
    assert spki.startswith(oa.P256_SPKI_PREFIX) and len(spki) == 91
    assert att["counter"] == 1
    assert att["counter_kind"] == "software"  # honest label: no SE HW counter
    assert att["element"] == "apple-secure-enclave"
    assert base64.b64decode(att["signature"])[0] == 0x30


def test_signed_message_is_domain_separated_spec_bytes():
    signer = FakeSigner()
    att = oa.make_attestation(SAMPLE_HASH, signer=signer)
    assert len(signer.signed_messages) == 1
    msg = signer.signed_messages[0]
    expected = oa.build_message(
        SAMPLE_HASH, att["signed_at"], att["device_id"], att["counter"])
    assert msg == expected
    assert msg.startswith(b"orpho-hw-v1\x00")
    assert msg.endswith(att["counter"].to_bytes(8, "big"))


def test_counter_increments_and_tofu_timestamp_stable():
    signer = FakeSigner()
    att1 = oa.make_attestation(SAMPLE_HASH, signer=signer)
    att2 = oa.make_attestation(SAMPLE_HASH, signer=signer)
    assert (att1["counter"], att2["counter"]) == (1, 2)
    # key_created_at is the TOFU pinning moment — set once per device key.
    assert att1["key_created_at"] == att2["key_created_at"]
    state = json.loads(oa.HW_STATE_FILE.read_text())
    assert state["counter"] == 2
    assert state["device_id"] == att1["device_id"]


def test_new_device_key_resets_tofu_state():
    att1 = oa.make_attestation(SAMPLE_HASH, signer=FakeSigner())
    other = FakeSigner(raw_point=b"\x04" + bytes(reversed(range(64))))
    att2 = oa.make_attestation(SAMPLE_HASH, signer=other)
    assert att1["device_id"] != att2["device_id"]
    assert att2["counter"] == 1  # fresh key ⇒ fresh pin ⇒ fresh counter


def test_attestation_passes_engine_sanitizer():
    """The capture-side payload must survive the server-side strict
    sanitizer verbatim — otherwise the field silently never persists."""
    server_dir = CAPTURE_DIR.parent / "server"
    if str(server_dir) not in sys.path:
        sys.path.insert(0, str(server_dir))
    import engine
    att = oa.make_attestation(SAMPLE_HASH, signer=FakeSigner())
    kept = engine._sanitize_hardware_attestation(att, SAMPLE_HASH)
    assert kept is not None
    assert kept["device_id"] == att["device_id"]


# ─── 2. Honest degradation: None, never fake, never raise ───────────────────

def test_non_macos_returns_none(monkeypatch):
    monkeypatch.setattr(oa.sys, "platform", "linux")
    assert oa.make_attestation(SAMPLE_HASH) is None


def test_missing_swiftc_returns_none(monkeypatch):
    monkeypatch.setattr(oa.sys, "platform", "darwin")
    monkeypatch.setattr(oa.shutil, "which", lambda _n: None)
    assert oa.make_attestation(SAMPLE_HASH) is None


def test_signer_denial_returns_none():
    logs: list[str] = []
    att = oa.make_attestation(SAMPLE_HASH, signer=FakeSigner(fail_sign=True),
                              log=logs.append)
    assert att is None
    assert any("without attestation" in m for m in logs)


def test_bad_hash_input_returns_none():
    assert oa.make_attestation("not-a-hash", signer=FakeSigner()) is None
    assert oa.make_attestation("", signer=FakeSigner()) is None
    assert oa.make_attestation(SAMPLE_HASH[:-4], signer=FakeSigner()) is None


def test_failed_attempt_does_not_burn_a_counter():
    good = FakeSigner()
    oa.make_attestation(SAMPLE_HASH, signer=good)
    oa.make_attestation(SAMPLE_HASH, signer=FakeSigner(fail_sign=True))
    att = oa.make_attestation(SAMPLE_HASH, signer=good)
    # counter state is only persisted on success: 1, (fail), 2
    assert att["counter"] == 2


# ─── 3. --attest wiring in the capture scan loop ────────────────────────────

FAKE_RECEIPT = {
    "receipt_id": "RCPT_HW_0001",
    "created_at": "2026-08-04T00:00:00+00:00",
    "calendars_ok": 5,
    "calendars_total": 5,
}


class _AnchorRecorder:
    """Records anchor calls; optionally echoes the attestation like the
    real server does after sanitizing."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, endpoint, hash_hex, sha512_hex, label, api_key,
                 hardware_attestation=None):
        self.calls.append({"hash_hex": hash_hex,
                           "hardware_attestation": hardware_attestation})
        receipt = dict(FAKE_RECEIPT)
        receipt["hash_hex"] = hash_hex
        receipt["sha512_hex"] = sha512_hex
        if hardware_attestation is not None:
            receipt["hardware_attestation"] = hardware_attestation
        return True, receipt


class _LegacyAnchorRecorder:
    """Pre-attestation 5-arg signature — proves attest=False and the degrade
    path never require the new parameter (additive compatibility)."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, endpoint, hash_hex, sha512_hex, label, api_key):
        self.calls.append({"hash_hex": hash_hex})
        receipt = dict(FAKE_RECEIPT)
        receipt["hash_hex"] = hash_hex
        return True, receipt


def _write_aged(folder: Path, name: str, data: bytes) -> Path:
    p = folder / name
    p.write_bytes(data)
    past = time.time() - 3600
    os.utime(p, (past, past))
    return p


def test_scan_with_attest_flag_sends_attestation(tmp_path, monkeypatch):
    watch = tmp_path / "watch"
    watch.mkdir()
    f = _write_aged(watch, "photo.jpg", b"pixels")
    recorder = _AnchorRecorder()
    monkeypatch.setattr(oc, "anchor_hash", recorder)
    monkeypatch.setattr(oc, "_make_hw_attestation",
                        lambda h: oa.make_attestation(h, signer=FakeSigner()))
    counts = oc.scan_once([watch], oc.DEFAULT_EXTENSIONS, False,
                          "https://example.invalid", "sk", 2, attest=True)
    assert counts["anchored"] == 1
    (call,) = recorder.calls
    assert call["hardware_attestation"] is not None
    assert call["hardware_attestation"]["hash_hex"] == call["hash_hex"]
    # Sidecar carries the field because the (fake) server echoed it.
    sidecar = json.loads((watch / "photo.jpg.orpho.json").read_text())
    assert sidecar["hardware_attestation"]["attestation_type"] == oa.ATTESTATION_TYPE


def test_scan_degrades_to_plain_anchor_when_attestation_unavailable(tmp_path, monkeypatch):
    """--attest on a machine with no SE: anchors WITHOUT attestation —
    never fake, never block."""
    watch = tmp_path / "watch"
    watch.mkdir()
    _write_aged(watch, "photo.jpg", b"pixels")
    recorder = _LegacyAnchorRecorder()  # would TypeError on a 6th argument
    monkeypatch.setattr(oc, "anchor_hash", recorder)
    monkeypatch.setattr(oc, "_make_hw_attestation", lambda _h: None)
    counts = oc.scan_once([watch], oc.DEFAULT_EXTENSIONS, False,
                          "https://example.invalid", "sk", 2, attest=True)
    assert counts["anchored"] == 1
    assert counts["failed"] == 0
    sidecar = json.loads((watch / "photo.jpg.orpho.json").read_text())
    assert "hardware_attestation" not in sidecar


def test_scan_without_attest_flag_never_touches_attest_module(tmp_path, monkeypatch):
    watch = tmp_path / "watch"
    watch.mkdir()
    _write_aged(watch, "photo.jpg", b"pixels")
    recorder = _LegacyAnchorRecorder()
    monkeypatch.setattr(oc, "anchor_hash", recorder)

    def _boom(_h):  # pragma: no cover — only fires on a bug
        raise AssertionError("attest module used without --attest")
    monkeypatch.setattr(oc, "_make_hw_attestation", _boom)
    counts = oc.scan_once([watch], oc.DEFAULT_EXTENSIONS, False,
                          "https://example.invalid", "sk", 2)
    assert counts["anchored"] == 1


def test_attest_module_crash_never_blocks_anchor(tmp_path, monkeypatch):
    watch = tmp_path / "watch"
    watch.mkdir()
    _write_aged(watch, "photo.jpg", b"pixels")
    recorder = _LegacyAnchorRecorder()
    monkeypatch.setattr(oc, "anchor_hash", recorder)
    monkeypatch.setattr(oa, "make_attestation",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    counts = oc.scan_once([watch], oc.DEFAULT_EXTENSIONS, False,
                          "https://example.invalid", "sk", 2, attest=True)
    assert counts["anchored"] == 1
    assert counts["failed"] == 0


# ─── 4. Wire shape of anchor_hash() itself ──────────────────────────────────

class _FakeHTTPResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture_post_body(monkeypatch) -> list[dict]:
    bodies: list[dict] = []

    def fake_urlopen(req, timeout=None):
        bodies.append(json.loads(req.data.decode()))
        return _FakeHTTPResponse(json.dumps(
            {"receipt_id": "RCPT_WIRE", "hash_hex": "x"}).encode())
    monkeypatch.setattr(oc.urllib.request, "urlopen", fake_urlopen)
    return bodies


def test_anchor_body_includes_attestation_only_when_supplied(monkeypatch):
    bodies = _capture_post_body(monkeypatch)
    att = oa.make_attestation(SAMPLE_HASH, signer=FakeSigner())
    oc.anchor_hash("https://example.invalid", SAMPLE_HASH, "b" * 128, "", "",
                   hardware_attestation=att)
    oc.anchor_hash("https://example.invalid", SAMPLE_HASH, "b" * 128, "", "")
    assert bodies[0]["hardware_attestation"]["device_id"] == att["device_id"]
    assert "hardware_attestation" not in bodies[1]  # old wire shape untouched
