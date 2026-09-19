"""web/receipt.js hand-mirrors two calendar tables from server/engine.py.

Hand-mirrored constants drift. The page's `CALENDAR_HOSTS` and
`CALENDAR_UPSTREAM` are copies of `engine.CALENDARS` and
`engine.CALENDAR_UPSTREAM`, keyed by the short `.ots` stem, and nothing
checked that they still agreed — so a calendar added, removed or re-pointed
on the server would have left the receipt page counting yesterday's
calendars while the API reported today's.

Two layers, matching tests/test_verify_js_page.py:

1. Parity (always runs): the JS object literals are parsed out of the file
   and compared to the engine's tables.
2. Behaviour (when node is available): tests/js/receipt_calendar_counts.test.mjs
   evaluates the page's real counting code, including the prototype-key case
   ("constructor.ots" resolved to Object.prototype.constructor and was
   counted as a calendar).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import engine  # noqa: E402

RECEIPT_JS = ROOT / "web" / "receipt.js"
JS_SUITE = ROOT / "tests" / "js" / "receipt_calendar_counts.test.mjs"


def js_object_literal(name: str) -> dict:
    """The `const <name> = { ... };` object literal, as a dict.

    Parsed rather than eval'd: the literal is JSON once the trailing comma is
    removed, and a literal this test cannot parse is a failure, never a pass.
    """
    src = RECEIPT_JS.read_text(encoding="utf-8")
    m = re.search(r"const\s+" + re.escape(name) + r"\s*=\s*(\{.*?\});",
                  src, re.DOTALL)
    if m is None:
        raise AssertionError(f"{name} not found as an object literal in receipt.js")
    body = re.sub(r",(\s*\})", r"\1", m.group(1))
    return json.loads(body)


def js_const_int(name: str) -> int:
    src = RECEIPT_JS.read_text(encoding="utf-8")
    m = re.search(r"const\s+" + re.escape(name) + r"\s*=\s*(\d+)\s*;", src)
    if m is None:
        raise AssertionError(f"{name} not found as an integer const in receipt.js")
    return int(m.group(1))


class TestCalendarTableParity(unittest.TestCase):
    def test_the_parser_reads_real_content(self):
        """NEGATIVE CONTROL. An empty or unparsed literal would make every
        comparison below pass over nothing."""
        hosts = js_object_literal("CALENDAR_HOSTS")
        upstream = js_object_literal("CALENDAR_UPSTREAM")
        self.assertGreaterEqual(len(hosts), 5)
        self.assertGreaterEqual(len(upstream), 5)

    def test_hosts_match_the_shipped_calendar_list(self):
        want = {engine._calendar_short(c): c for c in engine.CALENDARS}
        self.assertEqual(js_object_literal("CALENDAR_HOSTS"), want,
                         "web/receipt.js CALENDAR_HOSTS has drifted from "
                         "engine.CALENDARS")

    def test_upstream_matches_the_engine_map(self):
        want = {engine._calendar_short(url): upstream
                for url, upstream in engine.CALENDAR_UPSTREAM.items()}
        self.assertEqual(js_object_literal("CALENDAR_UPSTREAM"), want,
                         "web/receipt.js CALENDAR_UPSTREAM has drifted from "
                         "engine.CALENDAR_UPSTREAM")

    def test_the_page_totals_match_the_engine_totals(self):
        self.assertEqual(js_const_int("CALENDARS_DISTINCT_TOTAL"),
                         engine.CALENDARS_DISTINCT_TOTAL)
        self.assertEqual(js_const_int("CALENDARS_SUBMITTED_TOTAL"),
                         len(engine.CALENDARS))

    def test_lookups_are_own_property_only(self):
        """The prototype-key defect, pinned in the source as well as in the
        node suite: neither table may be indexed bare."""
        src = RECEIPT_JS.read_text(encoding="utf-8")
        self.assertIn("Object.hasOwn(map, key)", src,
                      "the lookup helper no longer guards own properties")
        for table in ("CALENDAR_HOSTS", "CALENDAR_UPSTREAM"):
            self.assertNotRegex(
                src, re.escape(table) + r"\[",
                f"{table} is indexed directly; a key like 'constructor' then "
                "resolves to a prototype member. Use lookup().")


class TestLowRedundancyCopyNamesTheReason(unittest.TestCase):
    """web/app.js is the only customer-facing consumer of `low_redundancy`.

    It used to say "Only 3/5 calendars confirmed", which names the count the
    flag is NOT decided on and calls a pending proof confirmed. A reader who
    saw the warning could not learn why it fired.
    """

    def setUp(self):
        self.src = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        start = self.src.index("record.low_redundancy")
        self.warning = self.src[start:start + 1400]

    def test_the_warning_names_the_distinct_calendar_count(self):
        self.assertIn("calendars_distinct_ok", self.warning,
                      "the low_redundancy warning still reports only the "
                      "server count, which is not what set the flag")

    def test_the_warning_distinguishes_servers_from_calendars(self):
        self.assertIn("calendar servers", self.warning)
        self.assertRegex(self.warning, r"aggregator")

    def test_the_warning_never_claims_independence(self):
        """Three of the five servers reach calendars under one operator, so
        "independent" is the one word this copy may never use."""
        self.assertNotIn("independent", self.src.lower())

    def test_the_old_confirmed_wording_is_gone(self):
        """`low_redundancy` fires at anchor time, when nothing is confirmed
        yet — every proof is still pending its calendar."""
        self.assertNotIn("calendars confirmed", self.warning)


class TestReceiptJsBehaviour(unittest.TestCase):
    def test_node_suite_calendar_counts(self):
        node = shutil.which("node")
        if not node:
            self.skipTest(
                "node not available: the receipt.js calendar-count suite "
                f"({JS_SUITE.name}) is the documented JS coverage gap here")
        self.assertTrue(JS_SUITE.is_file(), f"missing {JS_SUITE}")
        proc = subprocess.run([node, "--test", str(JS_SUITE)],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0,
                         f"node --test failed:\n{proc.stdout}\n{proc.stderr}")


if __name__ == "__main__":
    unittest.main()
