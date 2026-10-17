"""Pre-release review of the Python CLI (@pr284-sdk-cli-prerelease-review)
and @agent-sdk-verify-zero-cal, driven against stub servers on 127.0.0.1.

1. A redirect is not followed. urllib followed a 3xx to any host, carried
   X-Orpho-Api-Key there, and the CLI printed that host's answer as a
   receipt with exit 0.
2. verify refuses a receipt no calendar accepted: matching bytes, but no
   Bitcoin commitment, so nothing bounds them in time.
"""
from __future__ import annotations

import http.server
import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orphograph import _cli  # noqa: E402
from orphograph._merkle import MerkleTree  # noqa: E402


class _Stub:
    def __init__(self, handler):
        class H(http.server.BaseHTTPRequestHandler):
            def _go(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                status, headers, body = handler(self)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            do_GET = do_POST = _go

            def log_message(self, *a):
                pass

        self.seen = []
        self.server = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _cli_run(argv, env_key=None):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = _cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class RedirectTest(unittest.TestCase):
    def setUp(self):
        self.other_hits = []

        def other(req):
            self.other_hits.append((req.command, req.path, req.headers.get("X-Orpho-Api-Key")))
            body = json.dumps({"receipt_id": "FAKE1", "calendars_ok": 3,
                               "manifest": {"root_hex": "0" * 64},
                               "receipt": {"calendars_ok": 3}}).encode()
            return 200, {"Content-Type": "application/json"}, body
        self.other = _Stub(other)

        def named(req):
            # "localhost", not 127.0.0.1: a different host name for the stub.
            target = self.other.base.replace("127.0.0.1", "localhost") + req.path
            return 302, {"Location": target}, b""
        self.named = _Stub(named)

    def tearDown(self):
        self.named.close()
        self.other.close()

    def test_no_subcommand_follows_a_redirect_or_carries_the_key(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "a.txt").write_text("a")
            for argv in (["anchor", d], ["verify", d, "RID00000001"],
                         ["inclusion-proof", "RID00000001", "a.txt"]):
                rc, out, err = _cli_run(["--server-url", self.named.base, "--api-key", "SECRETKEY", *argv])
                self.assertEqual(rc, 2, (argv, out, err))
                self.assertIn("302", err, argv)
                self.assertNotIn("FAKE1", out, argv)
        self.assertEqual(self.other_hits, [], "a request reached a host the caller did not name")


class NonObjectAnswerTest(unittest.TestCase):
    def test_a_non_object_200_is_an_error_not_a_traceback(self):
        for body in (b"[]", b'"hi"', b"null"):
            stub = _Stub(lambda req, b=body: (200, {"Content-Type": "application/json"}, b))
            try:
                with tempfile.TemporaryDirectory() as d:
                    Path(d, "a.txt").write_text("a")
                    for argv in (["anchor", d], ["verify", d, "RID00000001"],
                                 ["inclusion-proof", "RID00000001", "a.txt"]):
                        rc, out, err = _cli_run(["--server-url", stub.base, *argv])
                        self.assertEqual(rc, 2, (body, argv, out, err))
                        self.assertIn("not a JSON object", err, (body, argv))
            finally:
                stub.close()


class VerifyZeroCalendarTest(unittest.TestCase):
    def _verify(self, calendars_ok, tamper=False):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "a.txt").write_text("a")
            root = MerkleTree.from_folder(Path(d)).root_hex()

            def answer(req):
                receipt = {"receipt_id": "RZEROCAL06", "kind": "folder"}
                if calendars_ok is not None:
                    receipt["calendars_ok"] = calendars_ok
                body = json.dumps({"receipt": receipt,
                                   "manifest": {"root_hex": "0" * 64 if tamper else root}}).encode()
                return 200, {"Content-Type": "application/json"}, body
            stub = _Stub(answer)
            try:
                return _cli_run(["--server-url", stub.base, "verify", d, "RZEROCAL06"])
            finally:
                stub.close()

    def test_a_matching_receipt_no_calendar_accepted_does_not_verify(self):
        rc, out, err = self._verify(0)
        self.assertEqual(rc, 1, (out, err))
        self.assertEqual(json.loads(out), {"match": True, "verified": False, "calendars_ok": 0})
        self.assertIn("no calendar accepted", err)

    def test_a_calendar_or_an_older_answer_still_verifies(self):
        for cal in (1, None):
            rc, out, err = self._verify(cal)
            self.assertEqual(rc, 0, (cal, out, err))
            self.assertEqual(json.loads(out), {"match": True})

    def test_a_mismatch_is_still_the_plain_mismatch(self):
        rc, out, err = self._verify(0, tamper=True)
        self.assertEqual(rc, 1, (out, err))
        self.assertEqual(json.loads(out), {"match": False})


if __name__ == "__main__":
    unittest.main()
