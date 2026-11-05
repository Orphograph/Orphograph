#!/usr/bin/env python3
"""test_x402_payto_check.py — the x402 rail refuses to arm on a bad pay-to.

Added 2026-11-04. Before this, any non-empty ORPHO_X402_PAY_TO_ADDRESS armed
the rail. A typo would publish a payTo nobody controls; on mainnet each
payment to it is USDC lost. The suite's own fixture was such a value for a
month: 39 hex digits, accepted without complaint.

  * Keccak-256 is checked against go-ethereum's published constants, and the
    same sponge with the SHA3 padding byte is checked against hashlib's
    sha3_256, an implementation that shares no code with ours;
  * EIP-55 is checked against the EIP's own test cases (vendored in
    tests/vectors with the page's sha256);
  * malformed, mistyped, zero and token-contract addresses do not arm, and
    the env var's value never appears in a message;
  * on a running server a bad pay-to reads armed:false on /api/health and no
    402 is offered, with a good-address control on the same setup.

No test here reaches the network. The server tests point the facilitator
at a closed local https port and never present a payment.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

import _srv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import x402  # noqa: E402

VEC = json.loads((ROOT / "tests" / "vectors" / "eip55_keccak256.json").read_text())
EIP55 = VEC["eip55"]
ALL_EIP55 = EIP55["all_caps"] + EIP55["all_lower"] + EIP55["normal"]
GOOD = EIP55["normal"][0]


@pytest.fixture()
def env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("ORPHO_X402_"):
            monkeypatch.delenv(k)
    x402.pay_to_problem.cache_clear()
    yield monkeypatch
    x402.pay_to_problem.cache_clear()


# ── Keccak-256 ──────────────────────────────────────────────────────────────

def test_keccak256_matches_go_ethereum_constants():
    assert len(VEC["keccak256"]["vectors"]) == 3
    for v in VEC["keccak256"]["vectors"]:
        assert x402._keccak256(bytes.fromhex(v["input_hex"])).hex() == v["digest"], v["name"]


@pytest.mark.parametrize("n", [0, 1, 55, 134, 135, 136, 137, 271, 272, 273, 1000])
def test_same_sponge_with_sha3_padding_matches_hashlib(n):
    """The permutation and sponge are shared; only the padding byte differs.
    Lengths straddle the 136-byte rate, where sponge bugs live."""
    msg = bytes((i * 7 + 3) % 256 for i in range(n))
    assert x402._keccak256(msg, suffix=0x06) == hashlib.sha3_256(msg).digest()


def test_keccak_is_not_sha3():
    """Control: the padding byte is really used. Keccak-256 of the empty
    string differs from SHA3-256 of it."""
    assert x402._keccak256(b"") != hashlib.sha3_256(b"").digest()


# ── EIP-55 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("addr", ALL_EIP55)
def test_eip55_test_cases_are_their_own_checksum_and_accepted(addr):
    assert x402.eip55_checksum(addr) == addr
    assert x402.pay_to_problem(addr) is None


def _flip_case_of_first_letter(addr: str) -> str:
    body = addr[2:]
    i = next(i for i, ch in enumerate(body) if ch.isalpha())
    return "0x" + body[:i] + body[i].swapcase() + body[i + 1:]


@pytest.mark.parametrize("addr", EIP55["normal"])
def test_one_wrong_letter_case_fails_the_checksum(addr):
    bad = _flip_case_of_first_letter(addr)
    assert bad != addr and bad.lower() == addr.lower()
    why = x402.pay_to_problem(bad)
    assert why and "checksum" in why and bad not in why


@pytest.mark.parametrize("addr", EIP55["normal"])
def test_one_mistyped_digit_fails_the_checksum(addr):
    """A real typo: one character replaced by another, case kept mixed."""
    body = addr[2:]
    i = next(i for i, ch in enumerate(body) if ch.isdigit())
    repl = "0" if body[i] != "0" else "1"
    bad = "0x" + body[:i] + repl + body[i + 1:]
    assert x402.pay_to_problem(bad) and "checksum" in x402.pay_to_problem(bad)


@pytest.mark.parametrize("addr", EIP55["normal"])
def test_single_case_forms_carry_no_checksum_and_pass(addr):
    assert x402.pay_to_problem(addr.lower()) is None
    assert x402.pay_to_problem("0x" + addr[2:].upper()) is None


@pytest.mark.parametrize("addr,word", [
    ("0x" + "0" * 40, "zero"),
    (x402.USDC_BASE_MAINNET, "token contract"),
    (x402.USDC_BASE_SEPOLIA, "token contract"),
    (x402.USDC_BASE_MAINNET.lower(), "token contract"),
    ("0x00000000000000000000000000000000000BEEF", "40-hex"),    # the old fixture: 39 digits
    (GOOD + "0", "40-hex"),
    (GOOD[2:], "40-hex"),
    (GOOD[:-1] + "g", "40-hex"),
    (GOOD[:20] + " " + GOOD[21:], "40-hex"),
])
def test_unusable_addresses_are_named_without_their_value(addr, word):
    why = x402.pay_to_problem(addr)
    assert why and word in why and addr not in why and "ORPHO_X402_PAY_TO_ADDRESS" in why


# ── arming ──────────────────────────────────────────────────────────────────

def test_bad_pay_to_does_not_arm_and_good_one_does(env):
    env.setenv("ORPHO_X402_PAY_TO_ADDRESS", _flip_case_of_first_letter(GOOD))
    assert not x402.configured()
    assert len(x402.config_problems()) == 1
    env.setenv("ORPHO_X402_PAY_TO_ADDRESS", GOOD)
    assert x402.configured() and x402.config_problems() == []


def test_mock_backend_is_unaffected(env):
    """The mock backend never publishes a real payTo; its arming rule is the
    explicit ORPHO_X402_ALLOW_MOCK opt-in, unchanged."""
    env.setenv("ORPHO_X402_BACKEND", "mock")
    env.setenv("ORPHO_X402_ALLOW_MOCK", "1")
    env.setenv("ORPHO_X402_PAY_TO_ADDRESS", "not-an-address")
    assert x402.configured()


def test_checksum_runs_once_per_address_not_per_request(env):
    """configured() runs up to three times per anchor request, and the
    checksum is pure-Python Keccak (~1 ms). Cached per address string; a
    changed address is checked again on the next call."""
    env.setenv("ORPHO_X402_PAY_TO_ADDRESS", GOOD)
    calls = {"n": 0}
    real = x402._keccak256

    def counting(data, suffix=0x01):
        calls["n"] += 1
        return real(data, suffix)

    env.setattr(x402, "_keccak256", counting)
    for _ in range(50):
        assert x402.configured()
    assert calls["n"] <= 1, calls
    env.setenv("ORPHO_X402_PAY_TO_ADDRESS", _flip_case_of_first_letter(GOOD))
    assert not x402.configured()


# ── on a running server ─────────────────────────────────────────────────────

def _real_backend_env(pay_to: str) -> dict:
    # Real (non-mock) backend on the testnet defaults. The facilitator is a
    # closed local port, so an unexpected facilitator call fails at once
    # instead of reaching the internet; these tests never present a payment.
    return {"ORPHO_X402_PAY_TO_ADDRESS": pay_to,
            "ORPHO_X402_FACILITATOR_URL": "https://127.0.0.1:9/facilitator",
            "RATE_LIMIT_PER_DAY": "1"}


def _health_and_second_anchor(base: str):
    _, raw, _ = _srv.request(base, "/api/health")
    health = json.loads(raw)["x402"]
    h = {"Content-Type": "application/json"}
    _srv.request(base, "/api/anchor", "POST", json.dumps({"hash_hex": "d1" * 32}).encode(), h)
    status, raw, headers = _srv.request(base, "/api/anchor", "POST",
                                        json.dumps({"hash_hex": "d2" * 32}).encode(), h)
    return health, status, headers


def test_server_with_bad_pay_to_is_disarmed_and_offers_no_402(tmp_path):
    bad = _flip_case_of_first_letter(GOOD)
    for base in _srv.server_processes(tmp_path, stub_calendars=True, **_real_backend_env(bad)):
        health, status, headers = _health_and_second_anchor(base)
        assert health["armed"] is False and health["config_problems"] >= 1, health
        assert status == 429 and not headers.get(x402.PAYMENT_REQUIRED_HEADER), status


def test_control_same_server_with_good_pay_to_offers_402(tmp_path):
    for base in _srv.server_processes(tmp_path, stub_calendars=True, **_real_backend_env(GOOD)):
        health, status, headers = _health_and_second_anchor(base)
        assert health == {"armed": True, "network": x402.NETWORK_BASE_SEPOLIA, "config_problems": 0,
                          "price_atomic": "10000", "asset": x402.USDC_BASE_SEPOLIA,
                          "stripe_record": False, "stripe_pending": 0}, health
        assert status == 402 and headers.get(x402.PAYMENT_REQUIRED_HEADER), status
