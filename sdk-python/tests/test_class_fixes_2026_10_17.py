"""Parity with the Node fixes of @node-sdk-lows, on the Python side.

* ``verify`` against an answer with no manifest root printed the mismatch
  verdict (exit 1, "these bytes were changed"); it is an error (exit 2), and
  verify_folder_detail says so with root_served False. verify_folder keeps
  answering False (VERIFIER_SPEC 4.2).
* The User-Agent named 0.1 while the package is 0.1.1.

The service is a stub on 127.0.0.1.
"""
from __future__ import annotations

import http.server
import io
import json
import re
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import orphograph  # noqa: E402
from orphograph import _cli, _client  # noqa: E402


class _Stub:
    def __init__(self, answer):
        outer = self
        self.seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen.append((self.path, dict(self.headers)))
                body = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _cli_run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = _cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class BrokenAnswerIsAnError(unittest.TestCase):
    def test_no_manifest_root_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "a.txt").write_text("a")
            for answer in ({"receipt": {"receipt_id": "RNOROOT001"}},
                           {"receipt": {}, "manifest": {}},
                           {"receipt": {}, "manifest": {"root_hex": 7}},
                           {"receipt": {}, "manifest": "x"}):
                stub = _Stub(answer)
                try:
                    code, out, err = _cli_run(["--server-url", stub.url, "verify", d, "RNOROOT001"])
                    self.assertEqual(code, 2, (answer, out, err))
                    self.assertEqual(out, "")
                    self.assertIn("no manifest root_hex", err)
                    self.assertFalse(orphograph.verify_folder(d, "RNOROOT001", server_url=stub.url))
                    self.assertFalse(orphograph.verify_folder_detail(
                        d, "RNOROOT001", server_url=stub.url)["root_served"])
                finally:
                    stub.close()

    def test_a_real_mismatch_stays_exit_1(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "a.txt").write_text("a")
            stub = _Stub({"receipt": {"calendars_ok": 3}, "manifest": {"root_hex": "0" * 64}})
            try:
                code, out, _ = _cli_run(["--server-url", stub.url, "verify", d, "RMISMATCH1"])
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(out), {"match": False})
            finally:
                stub.close()


class UserAgentNamesThePackageVersion(unittest.TestCase):
    def test_versions_agree(self):
        pyproject = (ROOT / "pyproject.toml").read_text()
        version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
        self.assertEqual(orphograph.__version__, version)
        self.assertEqual(_client.USER_AGENT.split(" ")[0], "orphograph-python-sdk/" + version)

    def test_header_on_the_wire(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "a.txt").write_text("a")
            stub = _Stub({"receipt": {}, "manifest": {"root_hex": "0" * 64}})
            try:
                orphograph.verify_folder(d, "RUAVER0001", server_url=stub.url)
                self.assertEqual(stub.seen[0][1].get("User-Agent"), _client.USER_AGENT)
            finally:
                stub.close()


if __name__ == "__main__":
    unittest.main()
