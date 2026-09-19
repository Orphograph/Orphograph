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

import ast
import hashlib
import sys
import unittest
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _srv  # noqa: E402

# The handler module this suite drives over HTTP. Addressed through _srv so
# there is one name for it and the fixture hygiene gate sees a module that
# really does spin a server (it does — every test below anchors over the wire).
HANDLER_SOURCE = _srv.APP

# Short tokens are the .ots filenames engine._calendar_short produces.
ALL = ("a", "b", "alice", "finney", "btc")


def _leaf_hex(file_sha256_hex: str, rel_path: str = "a.txt") -> str:
    return hashlib.sha256(
        b"\x00" + rel_path.encode() + b"\x00" + bytes.fromhex(file_sha256_hex)
    ).hexdigest()


def _leaf_root(file_sha256_hex: str, rel_path: str = "a.txt") -> str:
    """A one-leaf tree: the lone node is promoted, so root == leaf."""
    return _leaf_hex(file_sha256_hex, rel_path)


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
    assert verified["calendars_distinct_total"] == 4, verified


def test_a_degraded_receipt_never_renders_as_a_full_score(aggregators_only):
    """One meaning per field name. The denominators are what we submit to —
    5 servers, 4 calendars — so the receipt that this whole change exists to
    expose reads "3 of 5 servers, 2 of 4 calendars", never "3 of 3, 2 of 2".
    """
    rec = _anchor(aggregators_only, "denominators")
    code, verified = _srv.get_json(
        aggregators_only, f"/api/verify/{rec['receipt_id']}", timeout=20)
    verified = _srv.ok_json(code, verified)
    # The anchor response and the verify response agree on both denominators.
    assert rec["calendars_distinct_total"] == 4, rec
    assert verified["calendars_distinct_total"] == 4, verified
    assert verified["calendars_submitted_total"] == 5, verified
    assert verified["calendars_ok"] == 3, verified
    assert verified["calendars_distinct_ok"] == 2, verified
    assert verified["calendars_ok"] != verified["calendars_submitted_total"]
    assert verified["calendars_distinct_ok"] != verified["calendars_distinct_total"]


def test_verify_separates_stamped_from_bitcoin_confirmed(aggregators_only):
    """A stub calendar returns a PENDING proof: valid, matching, and not yet
    in a block. "Confirmed" must stay at zero while "valid" is three."""
    rec = _anchor(aggregators_only, "pending-not-confirmed")
    code, verified = _srv.get_json(
        aggregators_only, f"/api/verify/{rec['receipt_id']}", timeout=20)
    verified = _srv.ok_json(code, verified)
    assert verified["calendars_ok"] == 3, verified
    assert verified["calendars_pinned_ok"] == 0, verified
    assert verified["calendars_distinct_pinned"] == 0, verified


def test_the_folder_anchor_returns_the_same_verdict(aggregators_only):
    """The folder path reported the counts but not the flag, so a dataset
    anchored across two calendars looked as healthy as one across four."""
    leaf = hashlib.sha256(b"folder-leaf").hexdigest()
    manifest = {
        "algorithm": "orphograph-merkle-v1-rfc6962",
        "version": 1,
        "root_hex": _leaf_root(leaf),
        "leaves": [{"path": "a.txt", "file_sha256_hex": leaf,
                    "leaf_hex": _leaf_hex(leaf), "size_bytes": 11}],
    }
    code, rec = _srv.post_json(aggregators_only, "/api/anchor_folder",
                               {"manifest": manifest}, timeout=20)
    rec = _srv.ok_json(code, rec)
    assert rec["calendars_ok"] == 3, rec
    assert rec["calendars_distinct_ok"] == 2, rec
    assert rec["calendars_distinct_total"] == 4, rec
    assert rec["low_redundancy"] is True, rec


def test_the_folder_control_is_not_flagged(three_operators):
    """NEGATIVE CONTROL for the folder arm."""
    leaf = hashlib.sha256(b"folder-leaf-ok").hexdigest()
    manifest = {
        "algorithm": "orphograph-merkle-v1-rfc6962",
        "version": 1,
        "root_hex": _leaf_root(leaf),
        "leaves": [{"path": "a.txt", "file_sha256_hex": leaf,
                    "leaf_hex": _leaf_hex(leaf), "size_bytes": 14}],
    }
    code, rec = _srv.post_json(three_operators, "/api/anchor_folder",
                               {"manifest": manifest}, timeout=20)
    rec = _srv.ok_json(code, rec)
    assert rec["calendars_distinct_ok"] == 3, rec
    assert rec["low_redundancy"] is False, rec


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


class TestWebhookPayloadsCarryTheVerdict(unittest.TestCase):
    """The anchor.created payloads carry the verdict, read structurally.

    STATED LIMIT: this is an AST check on the dispatch call sites in the
    handler module, NOT a wire test — driving a real anchor.created delivery
    needs a signed-in subscriber with a registered webhook endpoint, which no
    fixture in this suite provides. It fails if the keys are removed from the
    payload, and proves nothing about delivery.
    """

    @staticmethod
    def _anchor_created_payloads() -> list[ast.Dict]:
        tree = ast.parse(HANDLER_SOURCE.read_text(encoding="utf-8"))
        out = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "dispatch"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            if node.args[0].value != "anchor.created":
                continue
            if len(node.args) >= 3 and isinstance(node.args[2], ast.Dict):
                out.append(node.args[2])
        return out

    def test_both_anchor_created_payloads_carry_the_flag_and_the_counts(self):
        payloads = self._anchor_created_payloads()
        self.assertEqual(len(payloads), 2,
                         "expected the single-file and folder dispatch sites")
        for i, payload in enumerate(payloads):
            literal = {k.value for k in payload.keys
                       if isinstance(k, ast.Constant)}
            self.assertIn("low_redundancy", literal,
                          f"anchor.created payload {i} omits the verdict")
            unpacked = [ast.unparse(v) for k, v in
                        zip(payload.keys, payload.values) if k is None]
            self.assertTrue(
                any("distinct" in u for u in unpacked) or
                "calendars_distinct_ok" in literal,
                f"anchor.created payload {i} omits the distinct counts "
                f"(keys={sorted(literal)}, unpacked={unpacked})")

    def test_negative_control_the_scan_finds_the_call_sites(self):
        self.assertTrue(self._anchor_created_payloads(),
                        "the AST scan found no anchor.created dispatch at all")
