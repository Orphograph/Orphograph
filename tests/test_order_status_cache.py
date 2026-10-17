"""@order-status-index: every unauthenticated poll of
/api/nowpayments/order/<id> read the whole credit ledger. The status answer
is now kept per order id while the ledger file is unchanged; any append or
rewrite drops it."""
from __future__ import annotations

import json
import os

import pytest

import credits


@pytest.fixture(autouse=True)
def _ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    monkeypatch.setattr(credits, "_ORDER_STATUS_CACHE", {"sig": None, "answers": {}})


def _count_scans(monkeypatch):
    calls = {"n": 0}
    real = credits._mint_rows

    def counting(matches):
        calls["n"] += 1
        return real(matches)
    monkeypatch.setattr(credits, "_mint_rows", counting)
    return calls


def _mint(order_id, delta=50):
    credits._append({"claim_code": "pk_x", "email": "a@x.test", "credits_delta": delta,
                     "source": f"nowpayments:inv1:{order_id}"})


def test_a_repeated_poll_does_not_reread_the_ledger(monkeypatch):
    _mint("np_pack_50_a")
    calls = _count_scans(monkeypatch)
    for _ in range(6):
        assert credits.nowpayments_order_credits("np_pack_50_a") == 50
    assert calls["n"] == 1


def test_a_new_mint_is_seen_on_the_next_poll(monkeypatch):
    _mint("np_other")
    assert credits.nowpayments_order_credits("np_pack_20_b") is None
    _mint("np_pack_20_b", 20)
    assert credits.nowpayments_order_credits("np_pack_20_b") == 20


def test_a_rewritten_ledger_drops_the_kept_answers(monkeypatch):
    _mint("np_pack_50_c")
    assert credits.nowpayments_order_credits("np_pack_50_c") == 50
    st = credits.LEDGER_PATH.stat()
    # Same size, new content (a revoke-style rewrite), mtime moved on.
    text = credits.LEDGER_PATH.read_text().replace("np_pack_50_c", "np_pack_50_z")
    tmp = credits.LEDGER_PATH.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(tmp, credits.LEDGER_PATH)
    assert credits.LEDGER_PATH.stat().st_size == st.st_size
    assert credits.nowpayments_order_credits("np_pack_50_c") is None


def test_the_answer_matches_the_strict_lookup():
    credits.LEDGER_PATH.write_text("".join(json.dumps(r) + "\n" for r in [
        {"claim_code": "pk_real", "email": "a@x.test", "credits_delta": 50,
         "source": "nowpayments:4402:np_pack_50_x"},
        {"claim_code": "pk_other", "email": "c@x.test", "credits_delta": 5,
         "source": "affiliate:np_pack_50_x:payout"},
    ]))
    assert credits.nowpayments_order_credits("np_pack_50_x") == 50
    assert credits.nowpayments_order_credits("np_pack_50") is None
    assert credits.nowpayments_order_credits("") is None
