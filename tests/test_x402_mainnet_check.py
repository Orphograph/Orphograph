"""tools/x402_mainnet_check.py: the founder's checks before and after mainnet.

Offline: the RPC and the discovery search are replaced with recorded answer
shapes (read live 2026-11-05: eth_getCode on mainnet.base.org; the discovery
search at api.cdp.coinbase.com, which returns {"resources": [...]})."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("x402_mainnet_check", ROOT / "tools/x402_mainnet_check.py")
chk = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = chk
spec.loader.exec_module(chk)

WALLET = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"


def _rpc(code: str):
    return lambda url, body=None: {"jsonrpc": "2.0", "id": 1, "result": code}


@pytest.mark.parametrize("code", ["0x", "0xef0100" + "5a" * 20])
def test_a_wallet_passes(monkeypatch, code):
    monkeypatch.setattr(chk, "_json", _rpc(code))
    ok, msg = chk.check_pay_to(WALLET)
    assert ok and msg.startswith("PASS"), msg
    assert ("EIP-7702" in msg) == (code != "0x")


def test_a_contract_fails(monkeypatch):
    monkeypatch.setattr(chk, "_json", _rpc("0x6080604052" + "00" * 100))
    ok, msg = chk.check_pay_to(WALLET)
    assert not ok and "contract" in msg


def test_a_7702_prefix_with_the_wrong_length_is_a_contract(monkeypatch):
    monkeypatch.setattr(chk, "_json", _rpc("0xef0100" + "5a" * 21))
    assert chk.check_pay_to(WALLET)[0] is False


@pytest.mark.parametrize("bad, why", [
    (WALLET[:-1] + "6", "checksum"),
    ("0x" + "0" * 40, "zero address"),
    ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "token contract"),
    ("0x1234", "40-hex"),
])
def test_bad_addresses_fail_before_any_network_call(monkeypatch, bad, why):
    def no_network(*a, **k):
        raise AssertionError("network called for an address that fails offline")
    monkeypatch.setattr(chk, "_json", no_network)
    ok, msg = chk.check_pay_to(bad)
    assert not ok and why in msg, msg
    assert "ORPHO_X402" not in msg


def test_a_lowercase_address_passes_and_gets_its_checksummed_form(monkeypatch):
    monkeypatch.setattr(chk, "_json", _rpc("0x"))
    ok, msg = chk.check_pay_to(WALLET.lower())
    assert ok and WALLET in msg


def test_an_rpc_error_is_could_not_check_not_a_pass(monkeypatch):
    monkeypatch.setattr(chk, "_json", lambda url, body=None: {"error": {"code": -32000}})
    with pytest.raises(chk.CheckError):
        chk.check_pay_to(WALLET)


def _listing(*resources):
    return {"partialResults": False, "x402Version": 2, "resources": [
        {"resource": r, "accepts": [{"resource": r}]} for r in resources]}


def test_bazaar_finds_our_resource_and_ignores_lookalikes():
    answer = _listing("https://www.api-xpay.com/x402/quote",
                      "https://orphograph.com.evil.example/api/anchor",
                      "https://orphograph.com/api/anchor")
    assert chk.find_listing(answer) == ["https://orphograph.com/api/anchor"]


def test_bazaar_not_listed(monkeypatch):
    monkeypatch.setattr(chk, "_json", lambda url, body=None: _listing("https://www.api-xpay.com/x402/quote"))
    ok, msg = chk.check_bazaar()
    assert not ok and msg.startswith("NOT LISTED")


def test_bazaar_unexpected_shape_is_could_not_check():
    with pytest.raises(chk.CheckError):
        chk.find_listing({"error": "bad request"})


def test_main_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(chk, "_json", _rpc("0x"))
    assert chk.main(["payto", WALLET]) == 0
    assert chk.main(["payto", WALLET[:-1] + "6"]) == 1
    def down(url, body=None):
        raise chk.CheckError("down")
    monkeypatch.setattr(chk, "_json", down)
    assert chk.main(["payto", WALLET]) == 2
    assert chk.main(["bazaar"]) == 2
