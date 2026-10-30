"""tools/xrpl_anchor/xrpl_verify.py — the stdlib checker for XRPL memo anchors.

Every test feeds check() a response dict; nothing here reaches the network.
The real-response test replays a captured testnet transaction, so the parser
is held to what a node actually returns, not to a shape written from docs.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools" / "xrpl_anchor"))
import xrpl_verify as xv  # noqa: E402

ROOT = "a51e140920f3a874ef2348c1bd7e0c9db1c28aa52284799e1863e1e2119eb891"
ACCT = "rTestAnchorAccount000000000000000"


def memo(root=ROOT, mtype=xv.MEMO_TYPE, v=0):
    data = json.dumps({"alg": "orphograph-merkle-v1-rfc6962", "receipt_id": "X",
                       "root_hex": root, "v": v}, sort_keys=True, separators=(",", ":"))
    return {"Memo": {"MemoType": mtype.encode().hex(),
                     "MemoFormat": b"application/json".hex(),
                     "MemoData": data.encode().hex()}}


def resp(memos=None, validated=True, result="tesSUCCESS", account=ACCT):
    return {"result": {"status": "success", "validated": validated, "hash": "AB" * 32,
                       "ledger_index": 1, "close_time_iso": "2026-10-30T00:00:00Z",
                       "meta": {"TransactionResult": result},
                       "tx_json": {"Account": account, "TransactionType": "AccountSet",
                                   "Memos": [memo()] if memos is None else memos}}}


class Check(unittest.TestCase):
    def test_ok_when_bound_to_the_account(self):
        self.assertEqual(xv.check(resp(), ROOT, ACCT)[:2], ("OK", 0))

    def test_unbound_without_account(self):
        self.assertEqual(xv.check(resp(), ROOT)[:2], ("OK-UNBOUND", 0))

    def test_root_mismatch_fails(self):
        self.assertEqual(xv.check(resp(), "0" * 64, ACCT)[1], 1)

    def test_other_sender_fails(self):
        self.assertEqual(xv.check(resp(account="rSomeoneElse"), ROOT, ACCT)[1], 1)

    def test_failed_transaction_fails(self):
        self.assertEqual(xv.check(resp(result="tecNO_DST"), ROOT, ACCT)[1], 1)

    def test_unvalidated_is_pending_not_ok(self):
        self.assertEqual(xv.check(resp(validated=False), ROOT, ACCT)[:2], ("PENDING", 2))

    def test_not_found_is_pending(self):
        self.assertEqual(xv.check({"result": {"error": "txnNotFound", "status": "error"}}, ROOT)[1], 2)

    def test_foreign_memo_alone_fails(self):
        self.assertEqual(xv.check(resp(memos=[memo(mtype="someone/else")]), ROOT, ACCT)[1], 1)

    def test_two_orphograph_memos_fail(self):
        # Two roots in one tx would let a reader pick either; refuse it.
        self.assertEqual(xv.check(resp(memos=[memo(), memo("1" * 64)]), ROOT, ACCT)[1], 1)

    def test_unknown_version_fails(self):
        self.assertEqual(xv.check(resp(memos=[memo(v=1)]), ROOT, ACCT)[1], 1)

    def test_garbage_memo_data_fails(self):
        bad = memo(); bad["Memo"]["MemoData"] = "zz"
        self.assertEqual(xv.check(resp(memos=[bad]), ROOT, ACCT)[1], 1)


class RealTestnetResponse(unittest.TestCase):
    """tx 8AF25F1D…9FAA, anchored 2026-10-30 from rNmKSNLr… (anchors/testnet.jsonl)."""
    REAL = json.loads((HERE / "vectors" / "xrpl_testnet_anchor_tx.json").read_text())
    SENDER = "rNmKSNLrCAEVckmw251APFez7zgxAcsNSb"

    def test_real_response_verifies(self):
        verdict, code, detail = xv.check(self.REAL, ROOT, self.SENDER)
        self.assertEqual((verdict, code), ("OK", 0))
        self.assertEqual(detail["close_time_iso"], "2026-10-30T23:27:21Z")
        self.assertEqual(detail["memo"]["receipt_id"], "DatasetProvenanceSample")

    def test_real_response_rejects_another_root(self):
        self.assertEqual(xv.check(self.REAL, "f" * 64, self.SENDER)[1], 1)


class Main(unittest.TestCase):
    def test_unreachable_node_is_unavailable_not_fail(self):
        with mock.patch.object(xv, "fetch_tx", side_effect=xv.Unavailable("down")), \
             mock.patch("builtins.print"):
            self.assertEqual(xv.main(["AB" * 32, "--root", ROOT]), 3)

    def test_bad_hash_rejected_before_any_fetch(self):
        with mock.patch.object(xv, "fetch_tx") as f, self.assertRaises(SystemExit), \
             mock.patch("sys.stderr"):
            xv.main(["nothex", "--root", ROOT])
        f.assert_not_called()


if __name__ == "__main__":
    unittest.main()
