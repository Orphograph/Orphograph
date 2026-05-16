from __future__ import annotations

import json
import os

import pytest

import btc_payments


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(btc_payments, "ORDERS_PATH", tmp_path / "btc_orders.jsonl")
    monkeypatch.setattr(btc_payments, "BTC_RECEIVE_ADDRESS", "bc1qtest000000000000000000000000000000000")
    yield


def test_is_configured_reflects_env():
    assert btc_payments.is_configured() is True


def test_is_configured_false_when_unset(monkeypatch):
    monkeypatch.setattr(btc_payments, "BTC_RECEIVE_ADDRESS", "")
    assert btc_payments.is_configured() is False


def test_create_order_persists_pending():
    order = btc_payments.create_order("alice@b.com", 7.0, 12345)
    assert order["order_id"].startswith("btc_")
    assert order["status"] == "pending"
    assert order["amount_sats"] == 12345
    assert order["address"] == "bc1qtest000000000000000000000000000000000"

    fetched = btc_payments.get_order(order["order_id"])
    assert fetched["amount_sats"] == 12345
    assert fetched["status"] == "pending"


def test_create_order_rejects_dust():
    with pytest.raises(ValueError):
        btc_payments.create_order("a@b.com", 7.0, 500)


def test_create_order_requires_address(monkeypatch):
    monkeypatch.setattr(btc_payments, "BTC_RECEIVE_ADDRESS", "")
    with pytest.raises(RuntimeError):
        btc_payments.create_order("a@b.com", 7.0, 12345)


def test_mark_settled_appends_event():
    order = btc_payments.create_order("alice@b.com", 7.0, 11650)
    ok = btc_payments.mark_settled(order["order_id"], "abc123" * 10, 11650)
    assert ok is True

    after = btc_payments.get_order(order["order_id"])
    assert after["status"] == "settled"
    assert after["tx_hash"].startswith("abc123")

    # Idempotent: second call should NOT re-credit.
    ok = btc_payments.mark_settled(order["order_id"], "different_tx", 11650)
    assert ok is False


def test_mark_settled_unknown_order():
    assert btc_payments.mark_settled("btc_nope", "txhash", 1000) is False


def test_pending_orders_excludes_settled():
    a = btc_payments.create_order("a@b.com", 7.0, 11111)
    b = btc_payments.create_order("b@b.com", 7.0, 22222)
    btc_payments.mark_settled(a["order_id"], "hash_a", 11111)

    pending = btc_payments.pending_orders()
    ids = {o["order_id"] for o in pending}
    assert b["order_id"] in ids
    assert a["order_id"] not in ids


def test_status_of_expired(monkeypatch):
    monkeypatch.setattr(btc_payments, "ORDER_TTL_SEC", -1)  # already expired
    o = btc_payments.create_order("c@b.com", 7.0, 33333)
    assert btc_payments.status_of(o["order_id"]) == "expired"


def test_append_only_ledger_preserves_history():
    """The whole ledger should be reconstructible from the JSONL."""
    a = btc_payments.create_order("x@b.com", 7.0, 44444)
    btc_payments.mark_settled(a["order_id"], "tx_xyz", 44444)
    content = btc_payments.ORDERS_PATH.read_text().strip().splitlines()
    assert len(content) == 2  # one create, one settle
    create_evt = json.loads(content[0])
    settle_evt = json.loads(content[1])
    assert create_evt["event"] == "created"
    assert settle_evt["event"] == "settled"
    assert create_evt["order_id"] == settle_evt["order_id"]


def test_address_returns_only_public_data():
    """Server never holds anything beyond the address itself."""
    assert btc_payments.address() == "bc1qtest000000000000000000000000000000000"
    # Nothing called "private_key" or "seed" or "xpub" exists in this module.
    import inspect
    src = inspect.getsource(btc_payments)
    for forbidden in ("private_key", "seed_phrase", "mnemonic", "xpriv", "private wif"):
        assert forbidden.lower() not in src.lower(), \
            f"btc_payments.py must not reference {forbidden}"
