"""The /lp/agent-receipts demand instrument (2026-08-19).

The page shipped 2026-07-16 and served 200 with 12,466 bytes of real content
for 33 days while having zero forms, zero inputs, no mailto and no checkout
link. It was recorded in memory as a LIVE demand test. It was not one: with no
capture mechanism it returns UNKNOWN forever, and UNKNOWN is not no-demand.

These tests pin both halves, because either alone is still a blind spot:

  CAPTURE  the page can record interest, and records it under an interest
           value that is distinguishable from every other source.
  READOUT  the founder can read the resulting number.

The attribution half is the subtle one. waitlist.add() silently rewrites any
interest outside ALLOWED_INTERESTS to "other". Posting an unregistered value
would look like it worked, land in the file, and be indistinguishable from a
pricing-page signup forever.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

LP_HTML = ROOT / "web" / "lp" / "agent-receipts.html"
LP_JS = ROOT / "web" / "lp" / "agent-receipts.js"


class TestCaptureExists(unittest.TestCase):
    def test_page_has_a_capture_mechanism(self):
        """The exact condition demand_instrument_check greps for."""
        html = LP_HTML.read_text(encoding="utf-8")
        self.assertIn("<form", html, "no form: the page cannot record interest")
        self.assertIn('type="email"', html, "no email input")
        self.assertIn("<button", html, "no submit control")

    def test_capture_posts_to_the_vetted_endpoint(self):
        js = LP_JS.read_text(encoding="utf-8")
        self.assertIn('"/api/waitlist"', js,
                      "capture must reuse the rate-limited, validated endpoint "
                      "rather than introduce a second PII path")

    def test_interest_is_registered_and_therefore_attributable(self):
        """The load-bearing one. An unregistered interest is coerced to
        'other' by waitlist.add and the lead becomes unattributable."""
        import waitlist
        js = LP_JS.read_text(encoding="utf-8")
        self.assertIn('interest: "agent_receipts"', js)
        self.assertIn("agent_receipts", waitlist.ALLOWED_INTERESTS,
                      "interest posted by the page is NOT in ALLOWED_INTERESTS, "
                      "so every lead from it silently becomes 'other'")

    def test_script_is_actually_loaded_by_the_page(self):
        """A wired form whose script the page never loads is still no
        instrument. This is the failure mode a source-only test misses."""
        html = LP_HTML.read_text(encoding="utf-8")
        self.assertRegex(html, r'src="/lp/agent-receipts\.js\?v=\d+"')

    def test_no_inline_script_introduced(self):
        """Strict CSP: an inline handler would be silently blocked, leaving a
        form that looks wired and captures nothing."""
        html = LP_HTML.read_text(encoding="utf-8")
        for bad in ("onsubmit=", "onclick=", "javascript:"):
            self.assertNotIn(bad, html, f"inline handler {bad} would be CSP-blocked")


class TestReadout(unittest.TestCase):
    """counts() is the half that turns a capture into a measurement."""

    def setUp(self):
        import importlib, tempfile, os
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "waitlist.jsonl"
        os.environ["ORPHO_WAITLIST"] = str(self.path)
        import waitlist
        self.waitlist = importlib.reload(waitlist)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_reports_zero_not_an_error(self):
        """Nobody has signed up yet is a real answer."""
        self.assertEqual(self.waitlist.counts(), {"total": 0})

    def test_counts_separate_agent_receipts_from_other_sources(self):
        self.waitlist.add("a@example.com", "agent_receipts")
        self.waitlist.add("b@example.com", "agent_receipts")
        self.waitlist.add("c@example.com", "card_pack")
        c = self.waitlist.counts()
        self.assertEqual(c["agent_receipts"], 2)
        self.assertEqual(c["card_pack"], 1)
        self.assertEqual(c["total"], 3)

    def test_synthetic_positive_before_arming(self):
        """Feed the instrument a fake signal and watch it register.

        A capture that has only ever been observed reading zero is
        indistinguishable from a capture that cannot count. This proves the
        success branch fires, which is the check that was skipped when the
        page was first called a demand test.
        """
        self.assertEqual(self.waitlist.counts().get("agent_receipts", 0), 0)
        self.waitlist.add("synthetic@example.com", "agent_receipts")
        self.assertEqual(self.waitlist.counts()["agent_receipts"], 1)

    def test_unregistered_interest_still_collapses_to_other(self):
        """Documents the coercion this suite exists to guard against."""
        self.waitlist.add("d@example.com", "not-a-real-interest")
        self.assertEqual(self.waitlist.counts().get("other"), 1)


if __name__ == "__main__":
    unittest.main()
