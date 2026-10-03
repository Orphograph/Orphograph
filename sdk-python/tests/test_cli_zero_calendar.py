"""`orphograph anchor` exits 2 for a root no calendar accepted (1 is the verify mismatch verdict).

A 200 with calendars_ok 0 is a receipt with no Bitcoin commitment, and it never
gets one. The CLI printed it and exited 0, so a CI step using it as a gate
passed. The client call is patched; nothing leaves the machine.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orphograph import _cli  # noqa: E402


def _run(calendars_ok):
    with tempfile.TemporaryDirectory() as d:
        Path(d, "a.txt").write_text("a")
        result = {"receipt_id": "RZEROCAL03", "root_hex": "0" * 64,
                  "calendars_ok": calendars_ok, "calendars_total": 5}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(_cli, "anchor_folder", return_value=result), \
                redirect_stdout(out), redirect_stderr(err):
            rc = _cli.main(["--server-url", "http://127.0.0.1:9", "anchor", d])
        return rc, out.getvalue(), err.getvalue()


class ZeroCalendarTest(unittest.TestCase):
    def test_no_calendar_accepted_exits_two(self):
        rc, out, err = _run(0)
        self.assertEqual(rc, 2, (out, err))
        self.assertEqual(json.loads(out)["receipt_id"], "RZEROCAL03")   # still printed
        self.assertIn("no calendar accepted", err)

    def test_an_answer_with_no_receipt_exits_two(self):
        # Review of PR #284: an empty 200 printed receipt_id null and exited 0.
        with tempfile.TemporaryDirectory() as d:
            Path(d, "a.txt").write_text("a")
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(_cli, "anchor_folder", return_value={"receipt_id": None}), \
                    redirect_stdout(out), redirect_stderr(err):
                rc = _cli.main(["--server-url", "http://127.0.0.1:9", "anchor", d])
        self.assertEqual(rc, 2, (out.getvalue(), err.getvalue()))
        self.assertIn("without a receipt", err.getvalue())

    def test_one_calendar_or_an_older_answer_exits_zero(self):
        for calendars_ok in (1, None):
            rc, out, err = _run(calendars_ok)
            self.assertEqual(rc, 0, (calendars_ok, out, err))



class EmptyServerUrlTest(unittest.TestCase):
    """Round 3 of PR #284: `--server-url ""` (an unset variable) fell through
    to ORPHO_SERVER_URL or the live default. The Node CLI refuses it; so does
    this one now, before any request."""

    def test_an_empty_server_url_exits_two_before_any_request(self):
        for value in ("", "  "):
            with tempfile.TemporaryDirectory() as d:
                Path(d, "a.txt").write_text("a")
                err = io.StringIO()
                with mock.patch.object(_cli, "anchor_folder", side_effect=AssertionError("request made")) as m, \
                        redirect_stdout(io.StringIO()), redirect_stderr(err):
                    rc = _cli.main(["--server-url", value, "anchor", d])
            self.assertEqual(rc, 2, err.getvalue())
            self.assertFalse(m.called)
            self.assertIn("--server-url is empty", err.getvalue())

    def test_an_empty_environment_value_means_the_default(self):
        with mock.patch.dict("os.environ", {"ORPHO_SERVER_URL": " "}):
            self.assertEqual(_cli._env_server(), _cli.DEFAULT_SERVER_URL)
        with mock.patch.dict("os.environ", {"ORPHO_SERVER_URL": "http://127.0.0.1:9"}):
            self.assertEqual(_cli._env_server(), "http://127.0.0.1:9")


if __name__ == "__main__":
    unittest.main()
