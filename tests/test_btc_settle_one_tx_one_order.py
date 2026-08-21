"""Regression: settlement must not pay out the same transaction twice.

Pinned to the defects fixed 2026-07-26 in scripts/btc_settle.py.

[B] One tx settled MANY orders. `settle_all` re-scanned the same tx list for
    every pending order and never marked a tx as used. Two pending orders with
    an identical `amount_sats` were therefore BOTH settled by the SAME inbound
    payment — one payment minted two claim codes, to two different emails.
    Amount is the only discriminator in this matcher, so equal amounts are not
    exotic; the per-order tag that prevents them is bounded and best-effort.

[A] Orders were only ever matched against `btc_payments.address()`, which
    returns the single-address fallback. With HD/pool addressing configured and
    no fallback set, the worker scanned "" while orders sat at per-order
    addresses — so a paying customer could never settle.

These tests fail against the previous implementation and are permanent.
"""

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "scripts"))

btc_settle = importlib.import_module("btc_settle")


@pytest.fixture
def harness(monkeypatch):
    """Fake chain + capture every credit grant, with no network or disk."""
    granted: list[dict] = []
    settled: list[str] = []

    monkeypatch.setattr(btc_settle.btc_payments, "is_configured", lambda: True)
    monkeypatch.setattr(btc_settle, "_current_block_height", lambda: 900_000)
    monkeypatch.setattr(btc_settle, "_confirmations_for", lambda tx, tip: 6)
    monkeypatch.setattr(btc_settle, "MIN_CONFIRMATIONS", 1, raising=False)

    # A tx pays whatever its fixture says it pays, to whichever address.
    monkeypatch.setattr(
        btc_settle, "_sats_to_address",
        lambda tx, addr: tx.get("paid", {}).get(addr, 0),
    )

    monkeypatch.setattr(btc_settle.credits, "new_claim_code",
                        lambda: f"CLAIM{len(granted):03d}")
    monkeypatch.setattr(
        btc_settle.credits, "add_credits",
        lambda code, email, amount, source: granted.append(
            {"code": code, "email": email, "amount": amount, "source": source}
        ),
    )
    monkeypatch.setattr(btc_settle.mailer, "send_pack_claim_email",
                        lambda *a, **k: True)
    monkeypatch.setattr(
        btc_settle.btc_payments, "mark_settled",
        lambda order_id, tx_hash, sats_received=0: settled.append(order_id) or True,
    )
    monkeypatch.setattr(sys.stdout, "write", lambda *a, **k: None)

    return {"granted": granted, "settled": settled, "monkeypatch": monkeypatch}


def _orders(monkeypatch, rows):
    monkeypatch.setattr(btc_settle.btc_payments, "pending_orders",
                        lambda include_expired=False: rows)


def _txs(monkeypatch, mapping):
    """mapping: address -> list of tx dicts."""
    monkeypatch.setattr(btc_settle, "_address_txs",
                        lambda addr: mapping.get(addr, []))


def test_one_tx_cannot_settle_two_identical_orders(harness):
    """THE defect: two orders, same amount, one payment -> exactly one settles."""
    mp = harness["monkeypatch"]
    addr = "bc1qSHARED"
    mp.setattr(btc_settle.btc_payments, "address", lambda: addr)

    _orders(mp, [
        {"order_id": "ord_victim", "address": addr,
         "amount_sats": 31_667, "email": "victim@example.com"},
        {"order_id": "ord_attacker", "address": addr,
         "amount_sats": 31_667, "email": "attacker@example.com"},
    ])
    # ONE payment of that exact amount arrives.
    _txs(mp, {addr: [
        {"txid": "tx_single_payment", "paid": {addr: 31_667}},
    ]})

    out = btc_settle.settle_all()

    assert out["ok"] is True
    assert out["settled"] == 1, (
        f"one payment settled {out['settled']} orders — a single transaction "
        f"minted credits more than once"
    )
    assert len(harness["granted"]) == 1
    assert len(harness["settled"]) == 1


def test_two_payments_settle_two_identical_orders(harness):
    """The guard must not over-correct: two real payments still settle both."""
    mp = harness["monkeypatch"]
    addr = "bc1qSHARED"
    mp.setattr(btc_settle.btc_payments, "address", lambda: addr)
    _orders(mp, [
        {"order_id": "ord_a", "address": addr, "amount_sats": 31_667,
         "email": "a@example.com"},
        {"order_id": "ord_b", "address": addr, "amount_sats": 31_667,
         "email": "b@example.com"},
    ])
    _txs(mp, {addr: [
        {"txid": "tx_one", "paid": {addr: 31_667}},
        {"txid": "tx_two", "paid": {addr: 31_667}},
    ]})

    out = btc_settle.settle_all()
    assert out["settled"] == 2
    assert {g["email"] for g in harness["granted"]} == {"a@example.com", "b@example.com"}


def test_per_order_addresses_are_scanned(harness):
    """[A] HD/pool addressing: no global fallback, each order has its own address."""
    mp = harness["monkeypatch"]
    mp.setattr(btc_settle.btc_payments, "address", lambda: "")  # no fallback
    _orders(mp, [
        {"order_id": "ord_hd1", "address": "bc1qHD_ONE", "amount_sats": 31_667,
         "email": "one@example.com"},
        {"order_id": "ord_hd2", "address": "bc1qHD_TWO", "amount_sats": 31_667,
         "email": "two@example.com"},
    ])
    _txs(mp, {
        "bc1qHD_ONE": [{"txid": "tx_hd1", "paid": {"bc1qHD_ONE": 31_667}}],
        "bc1qHD_TWO": [{"txid": "tx_hd2", "paid": {"bc1qHD_TWO": 31_667}}],
    })

    out = btc_settle.settle_all()
    assert out["settled"] == 2, "per-order addresses were not scanned"
    assert set(out["addresses_scanned"]) == {"bc1qHD_ONE", "bc1qHD_TWO"}


def test_order_without_address_and_no_fallback_is_skipped_not_crashed(harness):
    """A legacy row with no address must not take the whole run down."""
    mp = harness["monkeypatch"]
    mp.setattr(btc_settle.btc_payments, "address", lambda: "")
    _orders(mp, [
        {"order_id": "ord_legacy", "amount_sats": 31_667, "email": "x@example.com"},
    ])
    _txs(mp, {})
    out = btc_settle.settle_all()
    assert out["ok"] is True
    assert out["settled"] == 0
    assert harness["granted"] == []


def test_wrong_amount_never_settles(harness):
    """Sanity: a payment that does not match the exact amount is ignored."""
    mp = harness["monkeypatch"]
    addr = "bc1qSHARED"
    mp.setattr(btc_settle.btc_payments, "address", lambda: addr)
    _orders(mp, [
        {"order_id": "ord_a", "address": addr, "amount_sats": 31_667,
         "email": "a@example.com"},
    ])
    _txs(mp, {addr: [{"txid": "tx_short", "paid": {addr: 31_000}}]})
    out = btc_settle.settle_all()
    assert out["settled"] == 0
    assert harness["granted"] == []
