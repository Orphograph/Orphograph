#!/usr/bin/env python3
"""test_x402_mainnet_switch.py — what has to hold before x402 moves to Base mainnet.

Added 2026-11-04 with the mainnet preparation:

  * cdp_auth signs Ed25519 exactly as RFC 8032 says (the RFC's own section
    7.1 vectors, vendored in tests/vectors with the RFC's sha256), and OpenSSL,
    an implementation that shares no code with ours, accepts our JWT
    signatures and rejects a tampered one;
  * the JWT carries the header and claims the reference CDP SDK sends;
  * a half-switched configuration (mainnet network with testnet USDC, the
    testnet-only facilitator, or a CDP facilitator without a usable key)
    is NOT armed;
  * the Bearer token goes to the CDP host and to no other host;
  * the 402 challenge, read off the wire, carries discovery metadata whose
    example body the server itself accepts.

No test here reaches the network: facilitator calls are captured by a
replaced urlopen that fails the test if it is ever asked for a real socket.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

import _srv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import cdp_auth  # noqa: E402
import x402  # noqa: E402

VECTORS = json.loads((ROOT / "tests" / "vectors" / "rfc8032_ed25519.json").read_text())
CDP_VERIFY = cdp_auth.CDP_X402_BASE + "/verify"
SEED = hashlib.sha256(b"orphograph test key, never a real one").digest()


def _secret(seed: bytes = SEED) -> str:
    return base64.b64encode(seed + cdp_auth.public_key(seed)).decode()


@pytest.fixture()
def env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("ORPHO_X402_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("ORPHO_X402_PAY_TO_ADDRESS", "0x000000000000000000000000000000000000BEEF")
    return monkeypatch


def _b64url_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ── Ed25519 ──────────────────────────────────────────────────────────────────

def test_rfc8032_vectors_all_pass():
    assert len(VECTORS["vectors"]) == 5
    for v in VECTORS["vectors"]:
        sk = bytes.fromhex(v["secret_key"])
        assert cdp_auth.public_key(sk).hex() == v["public_key"], v["name"]
        assert cdp_auth.sign(sk, bytes.fromhex(v["message"])).hex() == v["signature"], v["name"]


def test_vector_check_can_fail():
    """Control: the comparison above is not vacuous. A different key over the
    same message must not reproduce the RFC signature."""
    v = VECTORS["vectors"][1]
    other = bytes.fromhex(VECTORS["vectors"][2]["secret_key"])
    assert cdp_auth.sign(other, bytes.fromhex(v["message"])).hex() != v["signature"]


def _openssl_verify(tmp_path: Path, pub: bytes, msg: bytes, sig: bytes) -> bool:
    spki = bytes.fromhex("302a300506032b6570032100") + pub
    pem = ("-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(spki).decode()
           + "\n-----END PUBLIC KEY-----\n")
    (tmp_path / "pub.pem").write_text(pem)
    (tmp_path / "msg").write_bytes(msg)
    (tmp_path / "sig").write_bytes(sig)
    r = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(tmp_path / "pub.pem"),
                        "-rawin", "-in", str(tmp_path / "msg"), "-sigfile", str(tmp_path / "sig")],
                       capture_output=True, text=True)
    return r.returncode == 0


def test_openssl_independently_accepts_our_jwt_and_rejects_a_tampered_one(env, tmp_path):
    assert shutil.which("openssl"), "openssl is the independent verifier; it must be present"
    env.setenv("ORPHO_X402_CDP_KEY_ID", "test-key-id")
    env.setenv("ORPHO_X402_CDP_KEY_SECRET", _secret())
    token = cdp_auth.bearer_for("POST", CDP_VERIFY).removeprefix("Bearer ")
    h, c, s = token.split(".")
    pub = cdp_auth.public_key(SEED)
    assert _openssl_verify(tmp_path, pub, f"{h}.{c}".encode(), _b64url_dec(s))
    forged = base64.urlsafe_b64encode(json.dumps({"sub": "someone-else"}).encode()).rstrip(b"=").decode()
    assert not _openssl_verify(tmp_path, pub, f"{h}.{forged}".encode(), _b64url_dec(s))


def test_jwt_matches_the_reference_sdk_shape(env):
    env.setenv("ORPHO_X402_CDP_KEY_ID", "test-key-id")
    env.setenv("ORPHO_X402_CDP_KEY_SECRET", _secret())
    token = cdp_auth.bearer_for("post", CDP_VERIFY, now=1_800_000_000)
    assert token.startswith("Bearer ")
    h, c, _ = token.removeprefix("Bearer ").split(".")
    header, claims = json.loads(_b64url_dec(h)), json.loads(_b64url_dec(c))
    assert {k: header[k] for k in ("alg", "kid", "typ")} == {"alg": "EdDSA", "kid": "test-key-id", "typ": "JWT"}
    assert len(header["nonce"]) == 16 and header["nonce"].isdigit()
    assert claims == {"sub": "test-key-id", "iss": "cdp", "nbf": 1_800_000_000,
                      "exp": 1_800_000_120,
                      "uris": ["POST api.cdp.coinbase.com/platform/v2/x402/verify"]}


@pytest.mark.parametrize("secret,why", [
    ("not base64 !!", "not base64"),
    (base64.b64encode(b"x" * 32).decode(), "32 bytes"),
    (base64.b64encode(SEED + b"\0" * 32).decode(), "does not match"),
])
def test_unusable_secrets_are_refused_without_echoing_them(env, secret, why):
    env.setenv("ORPHO_X402_CDP_KEY_ID", "k")
    env.setenv("ORPHO_X402_CDP_KEY_SECRET", secret)
    with pytest.raises(cdp_auth.CdpKeyError) as e:
        cdp_auth.bearer_for("POST", CDP_VERIFY)
    assert why in str(e.value) and secret not in str(e.value)


# ── fail-closed configuration ───────────────────────────────────────────────

def test_testnet_default_is_armed_and_unchanged(env):
    assert x402.config_problems() == [] and x402.configured()
    assert x402.network() == x402.NETWORK_BASE_SEPOLIA and x402.asset() == x402.USDC_BASE_SEPOLIA
    assert x402.is_testnet()


def test_switching_only_the_network_does_not_arm(env):
    env.setenv("ORPHO_X402_NETWORK", x402.NETWORK_BASE_MAINNET)
    assert x402.asset() == x402.USDC_BASE_MAINNET          # default follows the network
    assert x402.asset_domain() == ("USD Coin", "2")
    assert any("testnet-only" in p for p in x402.config_problems())
    assert not x402.configured()


def test_mainnet_with_testnet_usdc_does_not_arm(env):
    env.setenv("ORPHO_X402_NETWORK", x402.NETWORK_BASE_MAINNET)
    env.setenv("ORPHO_X402_FACILITATOR_URL", cdp_auth.CDP_X402_BASE)
    env.setenv("ORPHO_X402_CDP_KEY_ID", "k")
    env.setenv("ORPHO_X402_CDP_KEY_SECRET", _secret())
    env.setenv("ORPHO_X402_ASSET", x402.USDC_BASE_SEPOLIA)
    assert any("other network" in p for p in x402.config_problems())
    assert not x402.configured()


def test_cdp_without_a_usable_key_does_not_arm(env):
    env.setenv("ORPHO_X402_NETWORK", x402.NETWORK_BASE_MAINNET)
    env.setenv("ORPHO_X402_FACILITATOR_URL", cdp_auth.CDP_X402_BASE)
    assert not x402.configured()
    env.setenv("ORPHO_X402_CDP_KEY_ID", "k")
    env.setenv("ORPHO_X402_CDP_KEY_SECRET", base64.b64encode(b"short").decode())
    assert not x402.configured()


def test_unknown_network_and_plain_http_do_not_arm(env):
    env.setenv("ORPHO_X402_NETWORK", "eip155:1")
    assert not x402.configured()
    env.setenv("ORPHO_X402_NETWORK", x402.NETWORK_BASE_SEPOLIA)
    env.setenv("ORPHO_X402_FACILITATOR_URL", "http://x402.org/facilitator")
    assert not x402.configured()


def test_complete_mainnet_configuration_arms(env):
    env.setenv("ORPHO_X402_NETWORK", x402.NETWORK_BASE_MAINNET)
    env.setenv("ORPHO_X402_FACILITATOR_URL", cdp_auth.CDP_X402_BASE)
    env.setenv("ORPHO_X402_CDP_KEY_ID", "k")
    env.setenv("ORPHO_X402_CDP_KEY_SECRET", _secret())
    assert x402.config_problems() == [] and x402.configured() and not x402.is_testnet()
    req = x402.build_payment_requirements("https://orphograph.com/api/anchor")
    assert (req["network"], req["asset"]) == (x402.NETWORK_BASE_MAINNET, x402.USDC_BASE_MAINNET)
    assert (req["extra"]["name"], req["extra"]["version"]) == ("USD Coin", "2")


# ── who receives the Bearer token ───────────────────────────────────────────

def _capture(monkeypatch):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"isValid": false, "invalidReason": "captured"}'

    def fake_urlopen(req, timeout=None):
        seen["url"], seen["headers"] = req.full_url, dict(req.header_items())
        return _Resp()

    # x402.urllib.request IS urllib.request: this one replacement is also the
    # egress guard: no facilitator call in this file can open a real socket.
    assert x402.urllib.request is urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def test_bearer_goes_to_cdp(env):
    seen = _capture(env)
    env.setenv("ORPHO_X402_FACILITATOR_URL", cdp_auth.CDP_X402_BASE)
    env.setenv("ORPHO_X402_CDP_KEY_ID", "k")
    env.setenv("ORPHO_X402_CDP_KEY_SECRET", _secret())
    x402.verify({"x402Version": 2}, {})
    assert seen["url"] == CDP_VERIFY
    assert seen["headers"].get("Authorization", "").startswith("Bearer ")


def test_bearer_never_goes_to_another_host(env):
    seen = _capture(env)
    env.setenv("ORPHO_X402_CDP_KEY_ID", "k")
    env.setenv("ORPHO_X402_CDP_KEY_SECRET", _secret())
    for url in ("https://x402.org/facilitator", "https://api.cdp.coinbase.com.evil.example/x402"):
        env.setenv("ORPHO_X402_FACILITATOR_URL", url)
        x402.verify({"x402Version": 2}, {})
        assert "Authorization" not in seen["headers"], url


def test_broken_cdp_key_is_unavailable_not_a_leak(env):
    _capture(env)
    env.setenv("ORPHO_X402_FACILITATOR_URL", cdp_auth.CDP_X402_BASE)
    env.setenv("ORPHO_X402_CDP_KEY_ID", "k")
    bad = base64.b64encode(b"y" * 40).decode()
    env.setenv("ORPHO_X402_CDP_KEY_SECRET", bad)
    with pytest.raises(x402.FacilitatorUnavailable) as e:
        x402.verify({"x402Version": 2}, {})
    assert bad not in str(e.value)


# ── discovery metadata, off the wire ────────────────────────────────────────

X402_ENV = {"ORPHO_X402_BACKEND": "mock", "ORPHO_X402_ALLOW_MOCK": "1",
            "ORPHO_X402_PAY_TO_ADDRESS": "0x000000000000000000000000000000000000BEEF",
            "RATE_LIMIT_PER_DAY": "1"}


@pytest.fixture()
def server(tmp_path):
    yield from _srv.server_processes(tmp_path, stub_calendars=True, **X402_ENV)


def test_challenge_carries_discovery_metadata_the_server_accepts(server):
    h = {"Content-Type": "application/json"}
    _srv.request(server, "/api/anchor", "POST", json.dumps({"hash_hex": "c1" * 32}).encode(), h)
    status, raw, headers = _srv.request(server, "/api/anchor", "POST",
                                        json.dumps({"hash_hex": "c2" * 32}).encode(), h)
    assert status == 402, raw
    challenge = json.loads(base64.b64decode(headers.get(x402.PAYMENT_REQUIRED_HEADER)))
    baz = challenge["extensions"]["bazaar"]
    assert baz["info"]["input"]["method"] == "POST" and baz["info"]["input"]["bodyType"] == "json"
    assert set(baz["schema"]["properties"]["input"]["required"]) == {"type", "method", "bodyType", "body"}
    assert "test network for now" in json.loads(raw)["hint"]       # testnet wording stays on testnet
    # The advertised example body is one the server really accepts.
    # Input is validated before any accounting, so an invalid body would be
    # a 400 here even past the free tier.
    code, rec = _srv.anchor(server, dict(baz["info"]["input"]["body"]))
    assert code in (200, 402, 429), (code, rec)


def test_arming_check_does_no_curve_math_per_request(env):
    """configured() runs on every anchor request. Re-deriving the CDP public
    key each time cost 3.5 ms of CPU any caller could trigger (2026-11-04);
    the decoded key is cached per secret value. A rotated secret is still
    checked: a bad replacement disarms on the very next call."""
    env.setenv("ORPHO_X402_NETWORK", x402.NETWORK_BASE_MAINNET)
    env.setenv("ORPHO_X402_FACILITATOR_URL", cdp_auth.CDP_X402_BASE)
    env.setenv("ORPHO_X402_CDP_KEY_ID", "k")
    env.setenv("ORPHO_X402_CDP_KEY_SECRET", _secret(hashlib.sha256(b"cache probe").digest()))
    calls = {"n": 0}
    real_mul = cdp_auth._mul

    def counting_mul(s, p):
        calls["n"] += 1
        return real_mul(s, p)

    env.setattr(cdp_auth, "_mul", counting_mul)
    for _ in range(50):
        assert x402.configured()
    assert calls["n"] <= 1, calls
    env.setenv("ORPHO_X402_CDP_KEY_SECRET", base64.b64encode(b"z" * 64).decode())
    assert not x402.configured()
