from __future__ import annotations

import pytest

import credits


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    yield


def test_new_claim_code_prefix():
    code = credits.new_claim_code()
    assert code.startswith("pk_")
    assert len(code) > 8


def test_balance_zero_when_no_ledger():
    assert credits.balance("pk_unknown") == 0


def test_add_then_consume():
    code = credits.new_claim_code()
    credits.add_credits(code, "a@b.com", 10, "test")
    assert credits.balance(code) == 10

    ok, remaining = credits.consume_credit(code)
    assert ok is True
    assert remaining == 9
    assert credits.balance(code) == 9


def test_consume_until_empty():
    code = credits.new_claim_code()
    credits.add_credits(code, "a@b.com", 2, "test")
    assert credits.consume_credit(code) == (True, 1)
    assert credits.consume_credit(code) == (True, 0)
    assert credits.consume_credit(code) == (False, 0)


def test_refund_credit_returns_one():
    """A failed anchor refunds exactly the consumed credit."""
    code = credits.new_claim_code()
    credits.add_credits(code, "a@b.com", 1, "stripe:cs_x")
    assert credits.consume_credit(code) == (True, 0)
    credits.refund_credit(code)
    assert credits.balance(code) == 1
    # The refunded credit is spendable again.
    assert credits.consume_credit(code) == (True, 0)


def test_refund_credit_tagged_source():
    import json as _json
    code = credits.new_claim_code()
    credits.add_credits(code, "a@b.com", 1, "stripe:cs_y")
    credits.consume_credit(code)
    credits.refund_credit(code, reason="anchor-refund:no-calendars")
    rows = [_json.loads(l) for l in credits.LEDGER_PATH.read_text().splitlines() if l.strip()]
    assert any(r["source"] == "anchor-refund:no-calendars" and r["credits_delta"] == 1
               for r in rows)


def test_torn_previous_write_does_not_lose_next_record():
    """If a prior append was interrupted (no trailing newline), the next append
    must start a fresh line so the new VALID record still parses and counts —
    rather than being glued onto the broken tail and dropped by _scan()."""
    code = credits.new_claim_code()
    credits.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Simulate a torn write: partial JSON, NO trailing newline.
    credits.LEDGER_PATH.write_text('{"claim_code":"' + code + '","credits_delta":5')
    credits.add_credits(code, "a@b.com", 10, "stripe:cs_torn")
    assert credits.balance(code) == 10  # only the valid +10 row counts
    text = credits.LEDGER_PATH.read_text()
    assert '"source":"stripe:cs_torn"' in text
    assert text.endswith("\n")


def test_codes_isolated():
    a = credits.new_claim_code()
    b = credits.new_claim_code()
    credits.add_credits(a, "x@y.com", 5, "test")
    assert credits.balance(b) == 0
    credits.consume_credit(a)
    assert credits.balance(b) == 0
    assert credits.balance(a) == 4


def test_negative_add_rejected():
    code = credits.new_claim_code()
    with pytest.raises(ValueError):
        credits.add_credits(code, "x@y.com", 0, "test")
    with pytest.raises(ValueError):
        credits.add_credits(code, "x@y.com", -1, "test")


def test_empty_claim_code_rejected():
    ok, rem = credits.consume_credit("")
    assert ok is False
    assert rem == 0
    assert credits.balance("") == 0


def test_concurrent_processes_cannot_double_spend(tmp_path):
    """Two parallel processes consuming the same code must total exactly
    the credits available, never more (cross-process fcntl lock check)."""
    import multiprocessing
    import sys

    ledger = tmp_path / "credit_ledger.jsonl"
    code = credits.new_claim_code()
    # Seed 5 credits into the shared ledger.
    import os as _os
    _os.environ["ORPHO_CREDIT_LEDGER"] = str(ledger)

    def worker(claim_code, ledger_path, n_attempts, q):
        import os as _o; _o.environ["ORPHO_CREDIT_LEDGER"] = str(ledger_path)
        import importlib, sys as _s
        _s.path.insert(0, "server")
        import credits as _credits
        importlib.reload(_credits)
        consumed = 0
        for _ in range(n_attempts):
            ok, _rem = _credits.consume_credit(claim_code)
            if ok:
                consumed += 1
        q.put(consumed)

    import importlib
    importlib.reload(credits)
    credits.add_credits(code, "x@y.com", 5, "test")

    ctx = multiprocessing.get_context("fork")
    q = ctx.Queue()
    procs = [ctx.Process(target=worker, args=(code, str(ledger), 20, q)) for _ in range(4)]
    for p in procs: p.start()
    for p in procs: p.join(timeout=30)
    totals = [q.get() for _ in procs]

    # Critical invariant: across all workers, exactly 5 successful consumes.
    assert sum(totals) == 5, (
        f"double-spend detected: workers consumed {totals} = {sum(totals)} from a 5-credit code"
    )
    _os.environ.pop("ORPHO_CREDIT_LEDGER", None)
    importlib.reload(credits)


def _write_rows(rows):
    import json
    credits.LEDGER_PATH.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_nowpayments_mint_is_found_by_its_own_order_id_only():
    _write_rows([
        {"claim_code": "pk_real", "email": "a@x.test", "credits_delta": 50,
         "source": "nowpayments:4401:np_pack_50_real"},
        {"claim_code": "pk_card", "email": "b@x.test", "credits_delta": 10,
         "source": "stripe:cs_live_abc"},
    ])
    assert credits.find_nowpayments_mint("np_pack_50_real")["claim_code"] == "pk_real"
    for probe in ("nowpayments", "4401", "stripe", "cs_live_abc", "np_pack_50"):
        assert credits.find_nowpayments_mint(probe) is None, probe


def test_a_later_row_sharing_a_part_does_not_hide_the_orders_mint():
    """The status route used to take the LATEST any-part match and then filter
    it, so a later unrelated row carrying the order id as another part made a
    credited order read as not credited."""
    _write_rows([
        {"claim_code": "pk_real", "email": "a@x.test", "credits_delta": 50,
         "source": "nowpayments:4402:np_pack_50_x"},
        {"claim_code": "pk_other", "email": "c@x.test", "credits_delta": 5,
         "source": "affiliate:np_pack_50_x:payout"},
    ])
    row = credits.find_nowpayments_mint("np_pack_50_x")
    assert row is not None and row["claim_code"] == "pk_real"
    # The loose lookup still answers with the later row; that is why the
    # strict one exists.
    assert credits.find_claim_code_by_source("np_pack_50_x")["claim_code"] == "pk_other"


def test_one_malformed_row_does_not_stop_the_lookups():
    """A non-string source, a non-integer delta or a non-object row used to
    raise inside the scan; the webhook's exactly-once check runs it under its
    dedup lock, so one bad row would block every crypto mint."""
    import json
    credits.LEDGER_PATH.write_text("".join(json.dumps(r) + "\n" for r in [
        {"claim_code": "pk_bad", "credits_delta": 5, "source": 123},
        ["not", "an", "object"],
        {"claim_code": "pk_ok", "email": "o@x.test", "credits_delta": 20,
         "source": "nowpayments:77:np_pack_20_ok"},
    ]))
    assert credits.find_nowpayments_mint("np_pack_20_ok")["claim_code"] == "pk_ok"
    assert credits.find_mint_by_exact_source({"nowpayments:77:np_pack_20_ok"})["claim_code"] == "pk_ok"


def test_a_decimal_string_delta_is_still_a_mint():
    """Skipping a "10.0" delta made an exactly-once check read "not minted"
    and would have minted again; it is parsed as 10 instead."""
    import json
    credits.LEDGER_PATH.write_text(json.dumps(
        {"claim_code": "pk_dec", "email": "d@x.test", "credits_delta": "10.0",
         "source": "stripe:cs_dec"}) + "\n")
    assert credits.find_mint_by_exact_source({"stripe:cs_dec"})["credits_delta"] == 10


def test_a_two_part_scaffold_row_does_not_answer_for_its_invoice_id():
    """The scaffold wrote "nowpayments:<invoice or order>", usually the
    numeric invoice id; accepting two parts let that id answer as an order."""
    import json
    credits.LEDGER_PATH.write_text(json.dumps(
        {"claim_code": "pk_two", "email": "t@x.test", "credits_delta": 5,
         "source": "nowpayments:4401"}) + "\n")
    assert credits.find_nowpayments_mint("4401") is None


def test_only_the_whole_order_id_answers_never_a_fragment():
    """Anchored at the invoice part: the remainder must BE the order id. A
    suffix match (tried in review round six) let a trailing fragment of
    someone else's order id answer, which is the oracle this lookup closes."""
    import json
    credits.LEDGER_PATH.write_text(json.dumps(
        {"claim_code": "pk_colon", "email": "c@x.test", "credits_delta": 5,
         "source": "nowpayments:inv9:shop:ord:7"}) + "\n")
    assert credits.find_nowpayments_mint("shop:ord:7")["claim_code"] == "pk_colon"
    for fragment in ("ord:7", "7", "inv9", "inv9:shop:ord:7"):
        assert credits.find_nowpayments_mint(fragment) is None, fragment


def test_a_non_numeric_delta_fails_closed():
    """Skipping it would make an exactly-once check (the NOWPayments webhook
    uses find_claim_code_by_source) read a real mint as absent and mint again;
    it raises, as it did before the shared scan, so no mint happens."""
    import json
    credits.LEDGER_PATH.write_text(json.dumps(
        {"claim_code": "pk_lots", "credits_delta": "lots", "source": "nowpayments:1:np_a_b"}) + "\n")
    with pytest.raises(ValueError):
        credits.find_claim_code_by_source("np_a_b")


def test_a_two_part_row_answers_only_for_a_server_order_id():
    import json
    credits.LEDGER_PATH.write_text("".join(json.dumps(r) + "\n" for r in [
        {"claim_code": "pk_ord", "email": "o@x.test", "credits_delta": 5,
         "source": "nowpayments:np_pack_5_scaffold"},
        {"claim_code": "pk_inv", "email": "i@x.test", "credits_delta": 5,
         "source": "nowpayments:4401"},
    ]))
    assert credits.find_nowpayments_mint("np_pack_5_scaffold")["claim_code"] == "pk_ord"
    assert credits.find_nowpayments_mint("4401") is None
