"""Tests for btc_hd — BIP-32 + BIP-173 stdlib HD derivation."""
import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import btc_hd


# Published BIP-32 master xpub from test vector 1 (seed = 000102…0e0f)
MASTER_XPUB = (
    "xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGh"
    "ePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8"
)


def test_parses_known_xpub():
    x = btc_hd.parse_xpub(MASTER_XPUB)
    assert x.depth == 0
    assert x.version == bytes.fromhex("0488B21E")
    assert len(x.chain_code) == 32
    assert len(x.public_key) == 33
    assert x.public_key[0] in (0x02, 0x03)


def test_rejects_invalid_xpubs():
    assert not btc_hd.is_valid_xpub("")
    assert not btc_hd.is_valid_xpub("not-an-xpub")
    assert not btc_hd.is_valid_xpub("xpub_with_bad_checksum_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")


def test_accepts_known_good_xpub():
    assert btc_hd.is_valid_xpub(MASTER_XPUB)


def test_derives_known_bip32_child():
    """BIP-32 vector 1: m/0 non-hardened pubkey is fixed."""
    pubkey = btc_hd.derive_pubkey(MASTER_XPUB, [0])
    expected = bytes.fromhex(
        "027c4b09ffb985c298afe7e5813266cbfcb7780b480ac294b0b43dc21f2be3d13c"
    )
    assert pubkey == expected


def test_bech32_p2wpkh_format():
    """Generic property test: derived addresses are well-formed P2WPKH."""
    for idx in range(5):
        addr = btc_hd.derive_address(MASTER_XPUB, idx)
        assert addr.startswith("bc1q"), f"index {idx} got {addr}"
        assert len(addr) == 42, f"index {idx} length {len(addr)} != 42"
        # bech32 charset
        valid_chars = set("qpzry9x8gf2tvdw0s3jn54khce6mua7l")
        body = addr.split("1", 1)[1]
        assert all(c in valid_chars for c in body), f"non-bech32 char in {addr}"


def test_each_index_yields_unique_address():
    """Privacy requirement: incrementing index must produce distinct addresses."""
    addrs = {btc_hd.derive_address(MASTER_XPUB, i) for i in range(20)}
    assert len(addrs) == 20, "duplicate addresses across 20 sequential indices"


def test_derivation_is_deterministic():
    """Same xpub + index → same address, every time."""
    a1 = btc_hd.derive_address(MASTER_XPUB, 7)
    a2 = btc_hd.derive_address(MASTER_XPUB, 7)
    assert a1 == a2


def test_change_chain_differs_from_receive_chain():
    """m/0/i and m/1/i must yield different addresses (BIP-44 separation)."""
    receive = btc_hd.derive_address(MASTER_XPUB, 0, change=0)
    change = btc_hd.derive_address(MASTER_XPUB, 0, change=1)
    assert receive != change


def test_bip173_test_vector_bech32():
    """BIP-173 published test: pubkey-hash → known bech32 address."""
    pkh = bytes.fromhex("751e76e8199196d454941c45d1b3a323f1433bd6")
    addr = btc_hd._bech32_encode("bc", 0, pkh)
    assert addr == "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"


def test_payments_module_falls_back_when_no_xpub(monkeypatch, tmp_path):
    """Without ORPHO_BTC_XPUB set, address_for_order returns the single address.

    Patches module-level attributes directly (no reload) so subsequent tests
    in the suite see clean state.
    """
    import btc_payments
    monkeypatch.setattr(btc_payments, "BTC_XPUB", "")
    monkeypatch.setattr(btc_payments, "BTC_RECEIVE_ADDRESS",
                        "bc1qfallback00000000000000000000000000000q")
    monkeypatch.setattr(btc_payments, "HD_INDEX_PATH", tmp_path / "btc_hd_index.txt")
    assert btc_payments.address_for_order("ord_test") == \
        "bc1qfallback00000000000000000000000000000q"


def test_payments_module_derives_fresh_when_xpub_set(monkeypatch, tmp_path):
    """With ORPHO_BTC_XPUB set, each order gets a fresh address."""
    import btc_payments
    monkeypatch.setattr(btc_payments, "BTC_XPUB", MASTER_XPUB)
    monkeypatch.setattr(btc_payments, "BTC_RECEIVE_ADDRESS",
                        "bc1qfallback00000000000000000000000000000q")
    monkeypatch.setattr(btc_payments, "HD_INDEX_PATH", tmp_path / "btc_hd_index.txt")
    a1 = btc_payments.address_for_order("ord_1")
    a2 = btc_payments.address_for_order("ord_2")
    a3 = btc_payments.address_for_order("ord_3")
    assert a1.startswith("bc1q") and a2.startswith("bc1q") and a3.startswith("bc1q")
    assert a1 != a2 != a3, f"got duplicates: {a1} {a2} {a3}"


def test_no_private_key_material_in_btc_hd():
    """Belt-and-suspenders: the module must not contain spending-capable code."""
    import inspect
    src = inspect.getsource(btc_hd).lower()
    for forbidden in ("private_key =", "seed_phrase", "mnemonic", "wif"):
        assert forbidden not in src, f"btc_hd.py contains forbidden token: {forbidden}"
