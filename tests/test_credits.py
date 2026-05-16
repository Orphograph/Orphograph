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
