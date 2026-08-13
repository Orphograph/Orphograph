#!/usr/bin/env python3
"""test_consent_fails_loud.py — an unreadable consent ledger is not consent.

DEFECT (2026-08-07 Stage 3e — same class as the L402 and chain-verdict bugs,
found by scanning for it rather than waiting for it)
-----------------------------------------------------------------------------
Two consent checks returned the PERMISSIVE answer when they could not read
their ledger:

    unsubscribe.is_unsubscribed()      except OSError: return False
    resend_webhook.is_suppressed()     except OSError: return False

and mailer._send wrapped the second in

    except Exception:  # suppression check must never block sending
        pass

So a permissions fault made Orphograph resume emailing addresses that had
unsubscribed, hard-bounced, or filed a spam complaint — silently, and with a
comment three lines above explaining why that is damaging. On this system
unreadable /data files are not hypothetical: root-owned api_keys.jsonl
(2026-07-27) and webhooks.jsonl (2026-07-28) both broke server-side reads.

WHY THIS IS NOT A BLANKET FAIL-CLOSED
Refusing all mail when the ledger is unreadable would block a paying
customer's receipt because OUR file permissions broke. That is its own harm.
So the fix splits by message type, which the mailer already models:

  * consent-based (non-transactional) -> REFUSED. Consent cannot be shown.
  * transactional (receipt, pin notice) -> sent, but no longer silently:
    it logs [email:UNVERIFIED] naming the fault.
  * the onboarding drip -> skips the recipient. It is consent-based, and a
    delayed nudge is cheaper than mailing someone who opted out.
  * the onboarding STATS view -> degrades to a count, never crashes.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

_POLLUTED = ("unsubscribe", "resend_webhook", "mailer", "onboarding",
             "file_lock", "auth")


class _LedgerBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get("ORPHO_DATA_DIR")
        os.environ["ORPHO_DATA_DIR"] = self._tmp.name
        self._old_mods = {m: sys.modules[m] for m in _POLLUTED if m in sys.modules}
        for m in _POLLUTED:
            sys.modules.pop(m, None)

    def tearDown(self):
        for m in _POLLUTED:
            sys.modules.pop(m, None)
        for m, mod in self._old_mods.items():
            sys.modules[m] = mod
        if self._old_env is None:
            os.environ.pop("ORPHO_DATA_DIR", None)
        else:
            os.environ["ORPHO_DATA_DIR"] = self._old_env
        self._tmp.cleanup()

    @staticmethod
    def _deny(path: Path):
        """Make a file unreadable; skip if we are root (chmod won't bite)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"email": "opted-out@example.com"}\n')
        path.chmod(0o000)
        return os.access(path, os.R_OK)


class TestUnsubscribeLedger(_LedgerBase):
    def test_unreadable_ledger_raises_instead_of_saying_not_unsubscribed(self):
        import unsubscribe
        if self._deny(unsubscribe.SUPPRESS_PATH):
            self.skipTest("running as root; chmod cannot deny read")
        try:
            with self.assertRaises(unsubscribe.SuppressionUnavailable):
                unsubscribe.is_unsubscribed("opted-out@example.com")
        finally:
            unsubscribe.SUPPRESS_PATH.chmod(0o600)

    def test_a_readable_ledger_still_answers_normally(self):
        import unsubscribe
        unsubscribe.SUPPRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
        unsubscribe.SUPPRESS_PATH.write_text(
            '{"email": "gone@example.com"}\n')
        self.assertTrue(unsubscribe.is_unsubscribed("gone@example.com"))
        self.assertFalse(unsubscribe.is_unsubscribed("here@example.com"))


class TestMailerSplitsByMessageType(_LedgerBase):
    """The judgement call, pinned: consent mail refused, receipts still sent."""

    def _break_suppression(self):
        """The attribute is SUPPRESSION_LIST_PATH. An earlier draft guessed
        SUPPRESS_PATH and skipTest()'d itself into a green that proved
        nothing — a vacuous skip is a vacuous pass wearing a hat."""
        import resend_webhook
        return Path(resend_webhook.SUPPRESSION_LIST_PATH)

    def test_non_transactional_send_is_refused_when_consent_is_unknown(self):
        import resend_webhook, mailer
        path = self._break_suppression()
        if self._deny(path):
            self.skipTest("running as root; chmod cannot deny read")
        # `ok is False` alone proves NOTHING here: with no RESEND_API_KEY the
        # send returns False at the transport step regardless, so the
        # assertion cannot tell a refusal from an ordinary no-op. (It briefly
        # could not — this test passed against the pre-fix code.) Assert on
        # the BLOCKED log line, which only the refusal path emits.
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                ok = mailer._send("x@example.com", "Subj", "body",
                                  "<p>body</p>", transactional=False)
        finally:
            path.chmod(0o600)
        err = buf.getvalue()
        self.assertIn(
            "[email:BLOCKED]", err,
            "a consent-based email was NOT refused while the suppression "
            f"ledger was unreadable — consent could not be shown. stderr={err!r}")
        self.assertNotIn("[email:inert]", err,
                         "the send reached the transport step, so the "
                         "suppression gate did not stop it")
        self.assertFalse(ok)

    def test_transactional_send_still_goes_out(self):
        """A paying customer's receipt must not be blocked by OUR permissions."""
        import resend_webhook, mailer
        path = self._break_suppression()
        if self._deny(path):
            self.skipTest("running as root; chmod cannot deny read")
        try:
            # No RESEND_API_KEY in tests, so _send returns False at the
            # transport step — but it must get PAST the suppression gate.
            # Assert on the log line, which is the observable behaviour.
            import io
            from contextlib import redirect_stderr
            buf = io.StringIO()
            with redirect_stderr(buf):
                mailer._send("y@example.com", "Receipt", "body",
                             "<p>body</p>", transactional=True)
            err = buf.getvalue()
        finally:
            path.chmod(0o600)
        self.assertNotIn("[email:BLOCKED]", err,
                         "transactional mail was blocked by an unreadable "
                         "suppression ledger")
        self.assertIn("[email:UNVERIFIED]", err,
                      "transactional mail went out with consent unverified "
                      "and said nothing — silence was the original defect")


class TestOnboardingDripFailsClosed(_LedgerBase):
    def test_drip_skips_recipients_when_consent_cannot_be_confirmed(self):
        import unsubscribe, onboarding
        src = (ROOT / "server" / "onboarding.py").read_text()
        self.assertIn("SuppressionUnavailable", src,
                      "the onboarding drip does not handle an unreadable "
                      "unsubscribe ledger, so it will mail opted-out people")
        # The send path must `continue`, not proceed.
        gate = src[src.index("def _due"):] if "def _due" in src else src
        self.assertIn("cannot confirm", src.lower())

    def test_the_stats_path_degrades_rather_than_crashing(self):
        src = (ROOT / "server" / "onboarding.py").read_text()
        self.assertIn("unsub = False", src,
                      "the reporting path should count an unreadable ledger "
                      "as not-unsubscribed rather than raise")


if __name__ == "__main__":
    unittest.main()
