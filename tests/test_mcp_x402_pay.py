"""MCP 0.1.6: opt-in x402 payment in mcp/orphograph_mcp.py.

Signing is checked three independent ways, because the server's mock
facilitator does not check signatures:
  * the EIP-712 specification's own example (key keccak("cow"), digest
    0xbe609aee…, v=28, r=0x4355c47d…, s=0x07299936…);
  * the `cryptography` library's ECDSA verifier on random keys;
  * the USDC contracts' DOMAIN_SEPARATOR and TRANSFER_WITH_AUTHORIZATION_TYPEHASH,
    read from Base mainnet and Base Sepolia on 2026-11-05 and pinned below.

The payment path runs end to end against the real server (tests/_srv.py)
with the mock facilitator, past a free tier of one anchor per day.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

import _srv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import x402 as server_x402  # noqa: E402

PAY_TO = "0x000000000000000000000000000000000000BEEF"
SERVER_ENV = {
    "ORPHO_X402_BACKEND": "mock",
    "ORPHO_X402_ALLOW_MOCK": "1",
    "ORPHO_X402_PAY_TO_ADDRESS": PAY_TO,
    "ORPHO_X402_PRICE_CENTS": "5",
    "RATE_LIMIT_PER_DAY": "1",
}
# A test-only key; never funded. keccak256("orphograph mcp x402 test key").
TEST_KEY_HEX = None  # filled below from the module's own keccak


def _load(monkeypatch=None, **env):
    if monkeypatch is not None:
        for k in ("ORPHO_X402_PAYER_KEY_FILE", "ORPHO_X402_DAILY_CAP_CENTS",
                  "ORPHO_X402_MAX_CENTS_PER_CALL", "ORPHO_X402_ALLOW_TESTNET",
                  "ORPHO_X402_SPEND_FILE", "ORPHO_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    sys.dont_write_bytecode, before = True, sys.dont_write_bytecode
    spec = importlib.util.spec_from_file_location("mcp_x402_under_test", ROOT / "mcp/orphograph_mcp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.dont_write_bytecode = before
    return mod


M = _load()
TEST_KEY_HEX = M.keccak256(b"orphograph mcp x402 test key").hex()
TEST_ADDR = M.address_of_key(int(TEST_KEY_HEX, 16))


# ── signing: known answers and an independent verifier ──────────────

def _a32(x):
    return bytes(12) + bytes.fromhex(x[2:])


def test_eip712_specification_example_signs_exactly():
    k = M.keccak256
    dom = k(k(b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
            + k(b"Ether Mail") + k(b"1") + (1).to_bytes(32, "big")
            + _a32("0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC"))
    pt = k(b"Person(string name,address wallet)")
    mt = k(b"Mail(Person from,Person to,string contents)Person(string name,address wallet)")
    person = lambda n, w: k(pt + k(n) + _a32(w))  # noqa: E731
    mail = k(mt + person(b"Cow", "0xCD2a3d9F938E13CD947Ec05AbC7FE734Df8DD826")
             + person(b"Bob", "0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB") + k(b"Hello, Bob!"))
    z = k(b"\x19\x01" + dom + mail)
    assert z.hex() == "be609aee343fb3c4b28e1df9e632fca64fcfaede20f02e86244efddf30957bd2"
    d = int(k(b"cow").hex(), 16)
    assert M.address_of_key(d) == "0xCD2a3d9F938E13CD947Ec05AbC7FE734Df8DD826"
    r, s, v = M.ecdsa_sign(z, d)
    assert (v, r, s) == (28, 0x4355c47d63924e8a72e509b65029052eb6c299d53a04e167c5775fd466751c9d,
                         0x07299936d304c153f6443dfa05f40ff007d72911b6f72307f996231605b91562)
    assert M.ecdsa_recover(z, r, s, v) == "0xCD2a3d9F938E13CD947Ec05AbC7FE734Df8DD826"


def test_signatures_verify_under_an_independent_ecdsa_library():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    for i in range(8):
        d = int.from_bytes(M.keccak256(b"key%d" % i), "big") % (M._N - 1) + 1
        z = M.keccak256(b"msg%d" % i)
        r, s, v = M.ecdsa_sign(z, d)
        assert s <= M._N // 2, "high-s signatures are rejected by USDC"
        pub = ec.derive_private_key(d, ec.SECP256K1()).public_key()
        pub.verify(utils.encode_dss_signature(r, s), z, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        assert M.ecdsa_recover(z, r, s, v) == M.address_of_key(d)


def test_a_tampered_digest_recovers_someone_else():
    d = int(TEST_KEY_HEX, 16)
    z = M.keccak256(b"a")
    r, s, v = M.ecdsa_sign(z, d)
    assert M.ecdsa_recover(M.keccak256(b"b"), r, s, v) != TEST_ADDR


def test_usdc_constants_match_the_chain_read_on_2026_11_05():
    assert M._TRANSFER_WITH_AUTHORIZATION_TYPEHASH.hex() == \
        "7c7c6cdb67a18743f49ec6fa9b35f50d52ed05cbed4cc592e13b44501c1a2267"
    assert M.usdc_domain_separator("eip155:8453").hex() == \
        "02fa7265e7c5d81118673727957699e4d68f74cd74b7db77da710fe8a2c7834f"
    assert M.usdc_domain_separator("eip155:84532").hex() == \
        "71f17a3b2ff373b803d70a5a07c046c1a2bc8e89c09ef722fcb047abe94c9818"
    for net, (contract, *_rest) in M._X402_ASSETS.items():
        assert server_x402._KNOWN_NETWORKS[net] == contract


@pytest.mark.parametrize("data", [b"", b"abc", b"x" * 135, b"x" * 136, b"x" * 137, b"y" * 1000])
def test_keccak_matches_the_server_copy(data):
    assert M.keccak256(data) == server_x402._keccak256(data)


# ── configuration ───────────────────────────────────────────────────

def _key_file(tmp_path, mode=0o600, content=None):
    p = tmp_path / "payer.key"
    p.write_text((content if content is not None else "0x" + TEST_KEY_HEX) + "\n")
    p.chmod(mode)
    return str(p)


def test_off_by_default(monkeypatch):
    assert _load(monkeypatch)._x402_payer() is None


@pytest.mark.parametrize("which", ["key_only", "cap_only"])
def test_half_configured_is_an_error_not_a_silent_default(monkeypatch, tmp_path, which):
    env = {"ORPHO_X402_PAYER_KEY_FILE": _key_file(tmp_path)} if which == "key_only" \
        else {"ORPHO_X402_DAILY_CAP_CENTS": "10"}
    m = _load(monkeypatch, **env)
    with pytest.raises(m.X402ConfigError):
        m._x402_payer()
    out = m._anchor_post({"hash_hex": "ab" * 32})
    assert out["error"] == "x402_config"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_a_key_file_other_users_can_read_is_refused(monkeypatch, tmp_path):
    m = _load(monkeypatch, ORPHO_X402_PAYER_KEY_FILE=_key_file(tmp_path, 0o644),
              ORPHO_X402_DAILY_CAP_CENTS="10")
    with pytest.raises(m.X402ConfigError, match="chmod 600"):
        m._x402_payer()


@pytest.mark.parametrize("content", ["", "0x1234", "zz" * 32, "0x" + "00" * 32, "0x" + "ff" * 32])
def test_bad_keys_are_refused_without_echoing_them(monkeypatch, tmp_path, content):
    m = _load(monkeypatch, ORPHO_X402_PAYER_KEY_FILE=_key_file(tmp_path, content=content),
              ORPHO_X402_DAILY_CAP_CENTS="10")
    with pytest.raises(m.X402ConfigError) as ei:
        m._x402_payer()
    if content.strip("0x"):
        assert content not in str(ei.value)


@pytest.mark.parametrize("daily, per_call", [("0", "1"), ("5", "6"), ("abc", "1"), ("100001", "1")])
def test_caps_out_of_range_are_refused(monkeypatch, tmp_path, daily, per_call):
    m = _load(monkeypatch, ORPHO_X402_PAYER_KEY_FILE=_key_file(tmp_path),
              ORPHO_X402_DAILY_CAP_CENTS=daily, ORPHO_X402_MAX_CENTS_PER_CALL=per_call)
    with pytest.raises(m.X402ConfigError):
        m._x402_payer()


# ── what a 402 may ask for ──────────────────────────────────────────

def _req(**over):
    base = {"scheme": "exact", "network": "eip155:8453",
            "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "amount": "10000",
            "payTo": PAY_TO, "maxTimeoutSeconds": 60}
    base.update(over)
    return {"x402Version": 2, "accepts": [base]}


@pytest.mark.parametrize("over, why", [
    ({"asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e"}, "pinned USDC"),
    ({"asset": "0x" + "11" * 20}, "pinned USDC"),
    ({"network": "eip155:84532", "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e"}, "not accepted"),
    ({"network": "eip155:1"}, "not accepted"),
    ({"scheme": "upto"}, "not accepted"),
    ({"amount": "60000"}, "MAX_CENTS_PER_CALL"),
    ({"amount": "-1"}, "not positive"),
    ({"amount": "1e9"}, "whole number"),
    ({"payTo": "0x" + "00" * 20}, "not an address"),
    ({"payTo": "orphograph.eth"}, "not an address"),
])
def test_a_402_outside_the_pins_is_declined(monkeypatch, over, why):
    m = _load(monkeypatch)
    chosen, reason = m._choose_requirement(_req(**over), 5 * 10_000)
    assert chosen is None and why in reason, reason


def test_testnet_needs_its_own_switch(monkeypatch):
    sepolia = {"network": "eip155:84532", "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e"}
    assert _load(monkeypatch)._choose_requirement(_req(**sepolia), 50_000)[0] is None
    assert _load(monkeypatch, ORPHO_X402_ALLOW_TESTNET="1")._choose_requirement(
        _req(**sepolia), 50_000)[0] is not None


def test_a_good_mainnet_402_is_chosen(monkeypatch):
    chosen, _ = _load(monkeypatch)._choose_requirement(_req(), 10_000)
    assert chosen["amount"] == "10000"


# ── end to end against the real server ──────────────────────────────

@pytest.fixture()
def server(tmp_path):
    yield from _srv.server_processes(tmp_path, stub_calendars=True, **SERVER_ENV)


def _paying(monkeypatch, tmp_path, base, *, daily="10", per_call="5"):
    return _load(monkeypatch, ORPHO_BASE_URL=base,
                 ORPHO_X402_PAYER_KEY_FILE=_key_file(tmp_path),
                 ORPHO_X402_DAILY_CAP_CENTS=daily, ORPHO_X402_MAX_CENTS_PER_CALL=per_call,
                 ORPHO_X402_ALLOW_TESTNET="1",
                 ORPHO_X402_SPEND_FILE=str(tmp_path / "spend" / "x402_spend.json"))


def _spend(tmp_path):
    p = tmp_path / "spend" / "x402_spend.json"
    return json.loads(p.read_text()) if p.exists() else None


def test_without_paying_configured_the_mcp_keeps_the_classic_limit(server, monkeypatch):
    m = _load(monkeypatch, ORPHO_BASE_URL=server)
    assert "receipt_id" in m._anchor_post({"hash_hex": "a1" * 32})
    out = m._anchor_post({"hash_hex": "a2" * 32})
    assert out.get("status") == 429, out


def test_past_the_free_tier_the_mcp_pays_and_gets_a_receipt(server, monkeypatch, tmp_path, capsys):
    m = _paying(monkeypatch, tmp_path, server)
    first = m._anchor_post({"hash_hex": "b1" * 32})
    assert "receipt_id" in first and "_x402" not in first, "the free anchor needs no payment"
    paid = m._anchor_post({"hash_hex": "b2" * 32})
    assert paid.get("receipt_id"), paid
    x = paid["_x402"]
    assert x["amount_atomic"] == "50000" and x["network"] == "eip155:84532"
    assert x["payer"] == TEST_ADDR and x["pay_to"].lower() == PAY_TO.lower()
    assert str(x.get("transaction", "")).startswith("0xmock")
    spend = _spend(tmp_path)
    assert spend["atomic"] == 50000 and len(spend["payments"]) == 1
    assert stat.S_IMODE(os.stat(tmp_path / "spend" / "x402_spend.json").st_mode) == 0o600
    seen = json.dumps(paid) + json.dumps(spend) + "".join(capsys.readouterr())
    assert TEST_KEY_HEX not in seen.lower(), "the private key appeared in output"


def test_the_tool_reports_the_payment(server, monkeypatch, tmp_path):
    m = _paying(monkeypatch, tmp_path, server)
    m._anchor_post({"hash_hex": "c1" * 32})
    out = m.tool_anchor_output({"text": "agent step 2: paid anchor"})
    assert out["ok"] is True, out
    assert out["x402_payment"]["amount_atomic"] == "50000"


def test_the_daily_cap_stops_payment_before_anything_is_signed(server, monkeypatch, tmp_path):
    m = _paying(monkeypatch, tmp_path, server, daily="5", per_call="5")
    m._anchor_post({"hash_hex": "d1" * 32})
    assert m._anchor_post({"hash_hex": "d2" * 32}).get("receipt_id")
    out = m._anchor_post({"hash_hex": "d3" * 32})
    assert out["error"] == "payment_declined" and "DAILY_CAP" in out["reason"]
    assert _spend(tmp_path)["atomic"] == 50000 and len(_spend(tmp_path)["payments"]) == 1


def test_a_price_above_the_per_call_cap_is_declined(server, monkeypatch, tmp_path):
    m = _paying(monkeypatch, tmp_path, server, daily="10", per_call="1")
    m._anchor_post({"hash_hex": "e1" * 32})
    out = m._anchor_post({"hash_hex": "e2" * 32})
    assert out["error"] == "payment_declined" and "MAX_CENTS_PER_CALL" in out["reason"]
    assert _spend(tmp_path) is None, "nothing may be reserved or signed"


def test_the_signature_sent_is_the_payers_over_the_exact_authorization(server, monkeypatch, tmp_path):
    """Capture the header the MCP sends and check it recovers to the payer
    over USDC's real EIP-712 digest: the mock facilitator would accept junk."""
    m = _paying(monkeypatch, tmp_path, server)
    sent = []
    real = m._http_full

    def spy(method, path, body, extra=None):
        if extra and m._PAYMENT_SIGNATURE_HEADER in extra:
            sent.append(json.loads(base64.b64decode(extra[m._PAYMENT_SIGNATURE_HEADER])))
        return real(method, path, body, extra)

    monkeypatch.setattr(m, "_http_full", spy)
    m._anchor_post({"hash_hex": "f1" * 32})
    assert m._anchor_post({"hash_hex": "f2" * 32}).get("receipt_id")
    (payload,) = sent
    auth, sig = payload["payload"]["authorization"], payload["payload"]["signature"]
    assert payload["x402Version"] == 2 and payload["accepted"]["payTo"] == PAY_TO
    assert auth["from"] == TEST_ADDR and auth["value"] == "50000" and len(sig) == 132
    z = m.transfer_authorization_digest("eip155:84532", auth)
    raw = bytes.fromhex(sig[2:])
    assert m.ecdsa_recover(z, int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:64], "big"),
                           raw[64]) == TEST_ADDR
    assert 0 < int(auth["validBefore"]) - int(auth["validAfter"]) <= 900


def test_server_gives_a_first_party_client_the_402_only_when_it_opts_in(server):
    def post(h, extra):
        status, _raw, _headers = _srv.request(
            server, "/api/anchor", "POST", json.dumps({"hash_hex": h}).encode(),
            {"Content-Type": "application/json", "User-Agent": "OrphographMCP/0.1", **extra})
        return status

    assert post("9b" * 32, {}) == 200
    assert post("9c" * 32, {}) == 429, "a first-party client that did not opt in keeps the classic answer"
    assert post("9d" * 32, {"X-Orpho-X402": "accept"}) == 402
    assert post("9e" * 32, {"X-Orpho-X402": "yes please"}) == 429

def test_paying_refuses_a_plain_http_server(monkeypatch, tmp_path):
    m = _load(monkeypatch, ORPHO_BASE_URL="http://orphograph.example",
              ORPHO_X402_PAYER_KEY_FILE=_key_file(tmp_path), ORPHO_X402_DAILY_CAP_CENTS="10")
    with pytest.raises(m.X402ConfigError, match="https"):
        m._x402_payer()


def test_a_malformed_timeout_in_the_402_does_not_break_payment(server, monkeypatch, tmp_path):
    m = _paying(monkeypatch, tmp_path, server)
    real = m._choose_requirement

    def junk_timeout(required, cap):
        req, why = real(required, cap)
        if req:
            req = dict(req, maxTimeoutSeconds="soon")
        return req, why

    monkeypatch.setattr(m, "_choose_requirement", junk_timeout)
    m._anchor_post({"hash_hex": "a7" * 32})
    assert m._anchor_post({"hash_hex": "a8" * 32}).get("receipt_id")
