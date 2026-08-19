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
import re
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

    def test_form_ships_hidden_and_is_revealed_by_script(self):
        """A form with no action/method that renders WITHOUT JS is a PII leak.

        preventDefault never fires, so the browser GETs this same page with
        ?email=... in the query string. Handler.log_message writes the request
        line to stderr, so the address lands in the server log -- on a page
        whose own copy promises not to share it, in a server that truncates
        IPs for privacy. pricing.html's notify forms ship `hidden` for exactly
        this reason; this one must too.
        """
        html = LP_HTML.read_text(encoding="utf-8")
        m = re.search(r"<form[^>]*id=\"lp-notify-form\"[^>]*>", html)
        self.assertIsNotNone(m, "capture form not found")
        self.assertIn("hidden", m.group(0),
                      "capture form is visible without JS: a no-JS submit "
                      "would put the visitor's email in the URL and the log")
        self.assertNotRegex(m.group(0), r"\baction=",
                            "a form action would make the no-JS submit real")
        js = LP_JS.read_text(encoding="utf-8")
        self.assertIn("form.hidden = false", js,
                      "form ships hidden but the script never reveals it")

    def test_noscript_gives_a_route_that_does_not_leak(self):
        html = LP_HTML.read_text(encoding="utf-8")
        self.assertIn("<noscript", html)
        self.assertIn("mailto:", html.split("<noscript")[1][:600])

    def test_client_validation_mirrors_the_server_regex(self):
        """/api/waitlist answers a neutral 200 for an address it REJECTS and
        does not store it. A looser client check therefore tells someone they
        are on the list when they are not, and the meter under-counts -- which
        reads as no-demand, the exact wrong conclusion this page exists to
        prevent."""
        import app
        js = LP_JS.read_text(encoding="utf-8")
        m = re.search(r"var EMAIL_RE = /(.+?)/;", js)
        self.assertIsNotNone(m, "client has no EMAIL_RE")
        client = m.group(1).replace("\\s", "\\s")
        self.assertEqual(client, app.EMAIL_RE.pattern,
                         "client regex has drifted from server EMAIL_RE")

    def test_both_outcomes_are_tracked(self):
        """A submit that never lands is the one event a demand meter must not
        drop: a missing failure reads as absent interest."""
        import app
        js = LP_JS.read_text(encoding="utf-8")
        for ev in ("lp_notify_submit", "lp_notify_error"):
            self.assertIn(ev, js, f"{ev} never emitted")
            self.assertIn(ev, app.FUNNEL_EVENTS,
                          f"{ev} not in FUNNEL_EVENTS -- /api/event would 400 it")

    def test_live_region_is_outside_the_form(self):
        """The form is hidden on success. A status region inside it leaves a
        screen reader on 'Adding you...' and invites a resubmit."""
        html = LP_HTML.read_text(encoding="utf-8")
        form = html[html.index('id="lp-notify-form"'):html.index("</form>")]
        self.assertNotIn('id="lp-notify-msg"', form,
                         "live region is inside the form that gets hidden")
        self.assertIn('id="lp-notify-msg"', html)

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


class TestDisclosure(unittest.TestCase):
    """A capture form implies a privacy disclosure.

    The privacy page enumerates what we collect, and its own framing is that
    "the privacy claim is enforced by the structure of the protocol, not by
    our promise to behave". Before 2026-08-19 that list named exactly one
    source of email addresses -- Pack purchases through Stripe Checkout --
    while the card-notify forms in checkout-cta.js were already posting
    addresses to /api/waitlist. This page adds a third, more prominent one.

    On a product that sells privacy, collecting an address the policy does not
    mention is a product defect, not a paperwork nit. This test fails the
    moment a capture exists without the disclosure, so the pair cannot drift.
    """

    def test_privacy_page_discloses_optional_email_capture(self):
        privacy = (ROOT / "web" / "privacy.html").read_text(encoding="utf-8")
        pages_with_capture = [
            p for p in (ROOT / "web").rglob("*.html")
            if 'type="email"' in p.read_text(encoding="utf-8")
            and "_mockups/" not in p.as_posix()
            and "/dist/" not in p.as_posix()
        ]
        self.assertTrue(pages_with_capture,
                        "no capture form found anywhere -- this test would "
                        "pass vacuously; the scan is not reading pages")
        self.assertIn("keep me posted", privacy.lower(),
                      f"{len(pages_with_capture)} page(s) collect an email "
                      f"address but the privacy page does not disclose "
                      f"optional email capture")

    def test_disclosure_states_the_deletion_route(self):
        privacy = (ROOT / "web" / "privacy.html").read_text(encoding="utf-8")
        self.assertRegex(privacy.lower(), r"delete it at any time")


class TestReadout(unittest.TestCase):
    """The readout half. counts() was REMOVED in review: newsletter's
    audience_snapshot() already read this same ledger and did it better --
    it separates rows from people, understands the 'confirmed' rows that
    mark_confirmed appends, and normalizes out-of-set interests. A second,
    weaker counter competing with it was the defect, not the fix."""

    def setUp(self):
        import importlib, tempfile, os
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "waitlist.jsonl"
        self._prev = os.environ.get("ORPHO_WAITLIST")
        os.environ["ORPHO_WAITLIST"] = str(self.path)
        import waitlist, newsletter
        self.waitlist = importlib.reload(waitlist)
        self.newsletter = importlib.reload(newsletter)

    def tearDown(self):
        # Restore BOTH the env var and the module state. Leaving them set
        # points the process-global WAITLIST_PATH at a deleted tmpdir, and
        # locked() re-creates parents -- so a later write "succeeds" against
        # the wrong path instead of failing loudly.
        import importlib, os
        self.tmp.cleanup()
        if self._prev is None:
            os.environ.pop("ORPHO_WAITLIST", None)
        else:
            os.environ["ORPHO_WAITLIST"] = self._prev
        import waitlist, newsletter
        importlib.reload(waitlist)
        importlib.reload(newsletter)

    def test_missing_file_reports_zero_not_an_error(self):
        snap = self.newsletter.audience_snapshot()
        self.assertEqual(snap["unique_signups"], 0)
        self.assertEqual(snap["ledger_rows"], 0)

    def test_counts_separate_agent_receipts_from_other_sources(self):
        self.waitlist.add("a@example.com", "agent_receipts")
        self.waitlist.add("b@example.com", "agent_receipts")
        self.waitlist.add("c@example.com", "card_pack")
        snap = self.newsletter.audience_snapshot()
        self.assertEqual(snap["by_interest"]["agent_receipts"], 2)
        self.assertEqual(snap["by_interest"]["card_pack"], 1)
        self.assertEqual(snap["unique_signups"], 3)

    def test_people_are_not_rows(self):
        """One visitor submitting three times is ONE ask, not three. The unit
        of the meter must be the unit of the claim."""
        for _ in range(3):
            self.waitlist.add("same@example.com", "agent_receipts")
        snap = self.newsletter.audience_snapshot()
        self.assertEqual(snap["ledger_rows"], 3)
        self.assertEqual(snap["unique_signups"], 1)

    def test_synthetic_positive_before_arming(self):
        """Feed the instrument a fake signal and watch it register. A meter
        only ever observed reading zero is indistinguishable from one that
        cannot count."""
        self.assertEqual(
            self.newsletter.audience_snapshot()["by_interest"].get("agent_receipts", 0), 0)
        self.waitlist.add("synthetic@example.com", "agent_receipts")
        self.assertEqual(
            self.newsletter.audience_snapshot()["by_interest"]["agent_receipts"], 1)

    def test_unregistered_interest_still_collapses_to_other(self):
        self.waitlist.add("d@example.com", "not-a-real-interest")
        self.assertEqual(
            self.newsletter.audience_snapshot()["by_interest"].get("other"), 1)

    def test_malformed_ledger_line_does_not_take_down_the_readout(self):
        """A syntactically valid but non-object line sails past the decode
        guard and then blows up on .get(). That turned the whole readout into
        a permanent 'unavailable'."""
        self.waitlist.add("ok@example.com", "agent_receipts")
        with self.path.open("a") as f:
            f.write("null\n[]\n42\n")
        snap = self.newsletter.audience_snapshot()
        self.assertEqual(snap["by_interest"]["agent_receipts"], 1)


class TestFounderSurfaceShowsIt(unittest.TestCase):
    """A readout nobody can see is not a readout. The number lived only in
    JSON for anyone who curled the API with a token; the page the founder
    actually opens ignored it, which made the UNAVAILABLE branch dead code."""

    def test_metrics_page_renders_the_waitlist(self):
        js = (ROOT / "web" / "founder" / "metrics.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "founder" / "metrics.html").read_text(encoding="utf-8")
        self.assertIn("waitlist-readout", html, "no element to render into")
        self.assertIn("renderWaitlist", js)
        self.assertIn("data.waitlist", js)

    def test_unavailable_is_rendered_as_unavailable_not_zero(self):
        js = (ROOT / "web" / "founder" / "metrics.js").read_text(encoding="utf-8")
        self.assertIn("UNAVAILABLE", js,
                      "a failed read must not be shown as a measured zero")


if __name__ == "__main__":
    unittest.main()
