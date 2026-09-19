"""`low_redundancy` over the wire, counted in DISTINCT calendars.

The unit tests next door prove `engine.distinct_calendars` counts correctly.
This proves the SERVER uses it: the same three acknowledgements produce a
different verdict depending on WHICH three servers answered, which is the
whole point of the change and the only part a pure-function test cannot see.

  a.pool + alice + b.pool  → 3 acknowledgements, 2 calendars, ONE operator
                             → low_redundancy TRUE  (was FALSE: 3 >= 3)
  a.pool + finney + btc    → 3 acknowledgements, 3 calendars, 3 operators
                             → low_redundancy FALSE (unchanged)

Both cases anchor successfully. The threshold has never rejected an anchor —
it only sets this flag — so no receipt is refused that used to be issued.

Calendar outcomes are shaped through `_srv.spin(..., fail_calendars=...)`,
which reaches the stub in tests/_run_server.py. That is a harness-side
process patch, not a product knob: the handler, request parsing, engine
persistence and response serialization are all real, and the shipped list of
submitted servers is untouched.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _srv  # noqa: E402

# Short tokens are the .ots filenames engine._calendar_short produces.
ALL = ("a", "b", "alice", "finney", "btc")


def _anchor(base: str, seed: str) -> dict:
    digest = hashlib.sha256(seed.encode()).hexdigest()
    code, body = _srv.anchor(base, {"hash_hex": digest}, timeout=20)
    return _srv.ok_json(code, body)


@pytest.fixture(scope="module")
def aggregators_only(tmp_path_factory):
    """Only a.pool, b.pool and alice answer: three acks, two calendars."""
    refused = ",".join(t for t in ALL if t not in ("a", "b", "alice"))
    yield from _srv.server_processes(
        tmp_path_factory.mktemp("distinct-agg"),
        stub_calendars=True, fail_calendars=refused)


@pytest.fixture(scope="module")
def three_operators(tmp_path_factory):
    """a.pool, finney and catallaxy answer: three acks, three calendars."""
    refused = ",".join(t for t in ALL if t not in ("a", "finney", "btc"))
    yield from _srv.server_processes(
        tmp_path_factory.mktemp("distinct-ops"),
        stub_calendars=True, fail_calendars=refused)


def test_three_acks_on_two_calendars_is_low_redundancy(aggregators_only):
    """THE REGRESSION. Before the change this returned low_redundancy: false
    because three servers acknowledged — while the receipt rested on alice
    and bob only, both inside one operator's domain."""
    rec = _anchor(aggregators_only, "agg")
    assert rec["calendars_ok"] == 3, rec
    assert rec["calendars_total"] == 5, rec
    assert rec["calendars_distinct_ok"] == 2, rec
    assert rec["calendars_distinct_total"] == 4, rec
    assert rec["low_redundancy"] is True, rec


def test_the_anchor_still_succeeds_and_is_still_a_receipt(aggregators_only):
    """The flag is not a gate. A flagged anchor is issued, has a receipt id,
    and carries its proofs — no anchor fails that used to succeed."""
    rec = _anchor(aggregators_only, "agg-issued")
    assert rec["receipt_id"]
    assert len(rec["successes"]) == 3, rec
    code, verified = _srv.get_json(
        aggregators_only, f"/api/verify/{rec['receipt_id']}", timeout=20)
    verified = _srv.ok_json(code, verified)
    assert verified["found"] is True, verified
    assert verified["calendars_ok"] == 3, verified
    assert verified["calendars_distinct_ok"] == 2, verified


def test_three_acks_on_three_calendars_is_not_low_redundancy(three_operators):
    """CONTROL. Same acknowledgement count, genuinely distinct calendars —
    the verdict must not move. Without this the test above would also pass on
    code that simply flagged every partial receipt."""
    rec = _anchor(three_operators, "ops")
    assert rec["calendars_ok"] == 3, rec
    assert rec["calendars_distinct_ok"] == 3, rec
    assert rec["low_redundancy"] is False, rec


def test_verify_reports_the_distinct_count_for_the_control_too(three_operators):
    rec = _anchor(three_operators, "ops-verify")
    code, verified = _srv.get_json(
        three_operators, f"/api/verify/{rec['receipt_id']}", timeout=20)
    verified = _srv.ok_json(code, verified)
    assert verified["calendars_distinct_ok"] == 3, verified
    assert verified["calendars_distinct_total"] == 3, verified


def test_the_stub_shaping_actually_shapes(aggregators_only, three_operators):
    """NEGATIVE CONTROL on the harness. If --fail-calendars were ignored both
    fixtures would return 5/5 and every assertion above would be vacuous."""
    agg = _anchor(aggregators_only, "shape-a")
    ops = _anchor(three_operators, "shape-b")
    assert agg["calendars_ok"] == 3 and ops["calendars_ok"] == 3
    agg_cals = {s["calendar"] for s in agg["successes"]}
    ops_cals = {s["calendar"] for s in ops["successes"]}
    assert agg_cals != ops_cals, (agg_cals, ops_cals)
    assert len(agg["failures"]) == 2 and len(ops["failures"]) == 2
