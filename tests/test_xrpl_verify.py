"""tools/xrpl_anchor/xrpl_verify.py — the stdlib checker for XRPL memo anchors.

Every test feeds check() a response dict; nothing here reaches the network.
The real-response test replays a captured testnet transaction, so the parser
is held to what a node actually returns, not to a shape written from docs.
"""
import json
import re
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

    def test_not_found_is_a_history_gap_not_pending(self):
        self.assertEqual(xv.check({"result": {"error": "txnNotFound", "status": "error"}}, ROOT)[1], 3)

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



class HostileResponses(unittest.TestCase):
    """A node is untrusted input: odd shapes must become verdicts, never tracebacks."""

    def test_non_object_memo_payload_is_a_fail_not_a_crash(self):
        bad = memo(); bad["Memo"]["MemoData"] = b"[1,2]".hex()
        self.assertEqual(xv.check(resp(memos=[bad]), ROOT, ACCT)[1], 1)

    def test_non_object_memo_entries_are_ignored(self):
        self.assertEqual(xv.check(resp(memos=["junk", None, memo()]), ROOT, ACCT)[:2], ("OK", 0))

    def test_oversized_body_is_unavailable(self):
        big = mock.MagicMock()
        big.__enter__.return_value.read.return_value = b"x" * (xv.MAX_BODY + 1)
        with mock.patch("urllib.request.urlopen", return_value=big):
            with self.assertRaises(xv.Unavailable):
                xv.fetch_tx("AB" * 32, "https://node.invalid/")


class Quorum(unittest.TestCase):
    """OK needs >= min_agree nodes that agree; one dissenting node is a FAIL."""
    A, B = "https://a.invalid/", "https://b.invalid/"

    def run_with(self, answers, min_agree=2):
        def fake(tx_hash, node, timeout=20):
            a = answers[node]
            if isinstance(a, Exception):
                raise a
            return a
        return xv.verify("AB" * 32, ROOT, ACCT, [self.A, self.B], min_agree, fetch=fake)

    def test_two_agreeing_nodes_ok(self):
        self.assertEqual(self.run_with({self.A: resp(), self.B: resp()})[:2], ("OK", 0))

    def test_one_dissenting_node_fails(self):
        self.assertEqual(self.run_with({self.A: resp(), self.B: resp(result="tecFAILED")})[:2][1], 1)

    def test_nodes_reporting_different_ledgers_fail(self):
        other = resp(); other["result"]["ledger_hash"] = "CD" * 32
        base = resp(); base["result"]["ledger_hash"] = "EF" * 32
        self.assertEqual(self.run_with({self.A: base, self.B: other})[1], 1)

    def test_history_gap_on_one_node_is_unavailable_not_ok(self):
        gap = {"result": {"error": "txnNotFound", "status": "error"}}
        self.assertEqual(self.run_with({self.A: resp(), self.B: gap})[:2], ("UNAVAILABLE", 3))

    def test_unreachable_node_is_unavailable_not_ok(self):
        self.assertEqual(self.run_with({self.A: resp(), self.B: xv.Unavailable("down")})[1], 3)

    def test_explicit_single_node_is_labelled(self):
        gap = {"result": {"error": "txnNotFound", "status": "error"}}
        v, code, _ = self.run_with({self.A: resp(), self.B: gap}, min_agree=1)
        self.assertEqual((v, code), ("OK-SINGLE-NODE", 0))

    def test_found_but_unvalidated_everywhere_is_pending(self):
        self.assertEqual(self.run_with({self.A: resp(validated=False),
                                        self.B: resp(validated=False)})[:2], ("PENDING", 2))


class Transport(unittest.TestCase):
    def test_sends_an_honest_named_user_agent(self):
        # 2026-10-30: a CDN in front of a public testnet node answered 403 to
        # urllib's default "Python-urllib/x" agent, so the node was never asked.
        seen = {}
        def fake_urlopen(req, timeout=None):
            seen["ua"] = req.get_header("User-agent")
            raise xv.urllib.error.URLError("stop")
        with mock.patch("urllib.request.urlopen", fake_urlopen), self.assertRaises(xv.Unavailable):
            xv.fetch_tx("AB" * 32, "https://node.invalid/")
        self.assertTrue(seen["ua"] and seen["ua"].startswith("orphograph-xrpl-verify/"))
        self.assertNotIn("Mozilla", seen["ua"])


class ReviewFindings(unittest.TestCase):
    """Code review 2026-10-31: each test failed on the code before its fix."""
    A, B = "https://a.invalid/", "https://b.invalid/"
    H = "AB" * 32
    GAP = {"result": {"error": "txnNotFound", "status": "error"}}

    def quorum(self, answers, nodes=None, min_agree=2):
        def fake(tx_hash, node, timeout=20):
            a = answers[node]
            if isinstance(a, BaseException):
                raise a
            return a
        return xv.verify(self.H, ROOT, ACCT, nodes or [self.A, self.B], min_agree, fetch=fake)

    def test_the_same_node_twice_is_one_vote(self):
        v, code, _ = self.quorum({self.A: resp()}, nodes=[self.A, self.A.rstrip("/")])
        self.assertEqual(code, 3)

    def test_deeply_nested_body_is_unavailable_not_a_crash(self):
        deep = mock.MagicMock()
        deep.__enter__.return_value.read.return_value = b"[" * 100000 + b"]" * 100000
        with mock.patch("urllib.request.urlopen", return_value=deep):
            with self.assertRaises(xv.Unavailable):
                xv.fetch_tx(self.H, self.A)

    def test_deeply_nested_memo_is_a_fail_not_a_crash(self):
        bad = memo(); bad["Memo"]["MemoData"] = (b"[" * 100000 + b"]" * 100000).hex()
        self.assertEqual(xv.check(resp(memos=[bad]), ROOT, ACCT)[1], 1)

    def test_connection_reset_mid_read_is_unavailable(self):
        r = mock.MagicMock()
        r.__enter__.return_value.read.side_effect = ConnectionResetError("reset")
        with mock.patch("urllib.request.urlopen", return_value=r):
            with self.assertRaises(xv.Unavailable):
                xv.fetch_tx(self.H, self.A)

    def test_incomplete_read_is_unavailable(self):
        import http.client
        r = mock.MagicMock()
        r.__enter__.return_value.read.side_effect = http.client.IncompleteRead(b"x")
        with mock.patch("urllib.request.urlopen", return_value=r):
            with self.assertRaises(xv.Unavailable):
                xv.fetch_tx(self.H, self.A)

    def test_min_agree_above_node_count_is_a_usage_error(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            xv.main([self.H, "--root", ROOT, "--node", self.A, "--min-agree", "2"])

    def test_not_found_everywhere_is_unavailable_not_pending(self):
        # A history gap is not "wait for validation"; a caller retrying on 2 would loop.
        self.assertEqual(self.quorum({self.A: self.GAP, self.B: self.GAP})[1], 3)

    def test_a_different_hash_than_requested_fails(self):
        other = resp(); other["result"]["hash"] = "CD" * 32
        self.assertEqual(xv.check(other, ROOT, ACCT, tx_hash=self.H)[1], 1)

    def test_nodes_naming_different_senders_disagree_when_unbound(self):
        a, b = resp(account="rAAA"), resp(account="rBBB")
        def fake(tx_hash, node, timeout=20):
            return {self.A: a, self.B: b}[node]
        self.assertEqual(xv.verify(self.H, ROOT, None, [self.A, self.B], 2, fetch=fake)[1], 1)

    def test_one_malformed_node_is_unavailable_not_a_fail(self):
        broken = resp(); broken["result"]["meta"] = "not-an-object"
        self.assertEqual(self.quorum({self.A: resp(), self.B: broken})[1], 3)

    def test_a_node_omitting_ledger_hash_still_agrees(self):
        a = resp(); a["result"]["ledger_hash"] = "EF" * 32
        b = resp(); b["result"].pop("ledger_hash", None); b["result"]["ledger_index"] = "1"
        self.assertEqual(self.quorum({self.A: a, self.B: b})[:2], ("OK", 0))

    def test_version_false_is_not_version_zero(self):
        # Python's False == 0; the memo version must be the integer 0.
        data = json.dumps({"alg": "a", "receipt_id": "X", "root_hex": ROOT, "v": False},
                          sort_keys=True, separators=(",", ":"))
        bad = memo(); bad["Memo"]["MemoData"] = data.encode().hex()
        self.assertEqual(xv.check(resp(memos=[bad]), ROOT, ACCT)[1], 1)


class NodeUsePolicy(unittest.TestCase):
    """Public nodes are someone else's: one honest identity, blocked means stop,
    light traffic. Driven through a fake transport (urlopen patched): no network."""
    A, B = "https://a.invalid/", "https://b.invalid/"
    H = "AB" * 32
    BROWSER = ("Mozilla", "Chrome", "Safari", "AppleWebKit")

    def setUp(self):
        xv.BLOCKED.clear()
        self.calls = []          # (host, user_agent, timeout)

    def transport(self, answers):
        """answers: host -> dict body | int HTTP status | Exception."""
        def fake_urlopen(req, timeout=None):
            host = xv.urllib.parse.urlsplit(req.full_url).hostname
            self.calls.append((host, req.get_header("User-agent"), timeout))
            a = answers[host]
            if isinstance(a, int):
                raise xv.urllib.error.HTTPError(req.full_url, a, "refused", {}, None)
            if isinstance(a, BaseException):
                raise a
            body = mock.MagicMock()
            body.__enter__.return_value.read.return_value = json.dumps(a).encode()
            return body
        return mock.patch("urllib.request.urlopen", fake_urlopen)

    def count(self, host):
        return sum(1 for c in self.calls if c[0] == host)

    def test_every_request_carries_the_one_honest_user_agent(self):
        with self.transport({"a.invalid": resp(), "b.invalid": resp()}):
            xv.verify(self.H, ROOT, ACCT, [self.A, self.B], 2)
        self.assertEqual(len(self.calls), 2)
        for _, ua, _ in self.calls:
            self.assertEqual(ua, xv.USER_AGENT)
            for token in self.BROWSER:
                self.assertNotIn(token, ua)

    def test_the_user_agent_cannot_be_overridden(self):
        src = (HERE.parent / "tools" / "xrpl_anchor" / "xrpl_verify.py").read_text()
        self.assertNotRegex(src, r"environ|getenv", "no env var may change the identity")
        flags = re.findall(r'add_argument\(\s*"(--[a-z-]+)"', src)
        self.assertFalse([f for f in flags if "agent" in f or f in ("--ua", "--proxy")], flags)
        for token in self.BROWSER:
            self.assertNotIn(token, xv.USER_AGENT)

    def test_a_refusing_node_gets_exactly_one_request_and_is_operator_refused(self):
        for status in (401, 403, 429):
            with self.subTest(status=status):
                xv.BLOCKED.clear(); self.calls.clear()
                with self.transport({"a.invalid": status, "b.invalid": resp()}):
                    v1 = xv.verify(self.H, ROOT, ACCT, [self.A, self.B], 2)
                    v2 = xv.verify(self.H, ROOT, ACCT, [self.A, self.B], 2)  # same run
                self.assertEqual(self.count("a.invalid"), 1)
                self.assertEqual((v1[0], v1[1]), ("UNAVAILABLE", 3))      # never FAIL
                a = [n for n in v1[2]["nodes"] if n["node"] == self.A][0]
                self.assertEqual(a["reason"], "operator refused")
                self.assertEqual(v2[1], 3)

    def test_a_reply_naming_a_rate_limit_is_a_block(self):
        slow = {"result": {"error": "slowDown", "status": "error"}}
        with self.transport({"a.invalid": slow, "b.invalid": resp()}):
            xv.verify(self.H, ROOT, ACCT, [self.A, self.B], 2)
            xv.verify(self.H, ROOT, ACCT, [self.A, self.B], 2)
        self.assertEqual(self.count("a.invalid"), 1)
        self.assertIn(self.A, xv.BLOCKED)

    def test_at_most_one_request_per_node_per_verify_and_no_retry(self):
        with self.transport({"a.invalid": resp(), "b.invalid": TimeoutError("slow")}):
            xv.verify(self.H, ROOT, ACCT, [self.A, self.B, self.A], 2)
        self.assertEqual((self.count("a.invalid"), self.count("b.invalid")), (1, 1))
        self.assertFalse(hasattr(xv, "confirm"), "no retry helper may exist")

    def test_timeout_is_at_most_ten_seconds(self):
        with self.transport({"a.invalid": resp()}):
            xv.verify(self.H, ROOT, ACCT, [self.A], 1)
        self.assertTrue(all(t is not None and t <= 10 for _, _, t in self.calls), self.calls)


if __name__ == "__main__":
    unittest.main()
