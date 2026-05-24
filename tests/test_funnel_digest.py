"""test_funnel_digest.py — gate scripts/funnel_digest.py.

The script runs autonomously on the Fly machine once a week (Monday
14:00 UTC). Three behaviours must hold:

  1. The text body it formats contains the required sections so the
     founder sees a parseable digest.
  2. --dry-run never invokes the mailer (no Resend calls during testing
     and no actual sends on staging).
  3. Same-day re-runs are refused via the state file (matches the
     cadence_scheduler idempotency contract).

Tests use subprocess to invoke the script as the scheduler would so the
real argv parsing and module-load path are exercised.
"""
from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "funnel_digest.py"


def _load_module():
    """Load scripts/funnel_digest.py as an importable module for unit-level tests."""
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "server"))
    spec = importlib.util.spec_from_file_location("funnel_digest_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestFormatTextBody(unittest.TestCase):
    """The formatted text body must contain every required section."""

    def test_format_text_body_contains_expected_sections(self) -> None:
        mod = _load_module()
        week_ending = _dt.date(2026, 5, 24)
        totals_7d = {
            "drop_zone_visible": 50,
            "file_anchored": 12,
            "checkout_clicked": 4,
            "checkout_returned_success": 2,
        }
        per_day_7d = {
            "2026-05-24": {
                "drop_zone_visible": 10,
                "file_anchored": 3,
                "checkout_clicked": 1,
                "checkout_returned_success": 1,
            }
        }
        totals_30d = {
            "drop_zone_visible": 200,
            "file_anchored": 48,
            "checkout_clicked": 16,
            "checkout_returned_success": 8,
        }
        body = mod._format_text_body(
            week_ending=week_ending,
            totals_7d=totals_7d,
            per_day_7d=per_day_7d,
            totals_30d=totals_30d,
            events_scanned=512,
        )
        # Header
        self.assertIn("Orphograph weekly funnel digest", body)
        self.assertIn("Week ending 2026-05-24", body)
        # Totals block
        self.assertIn("Totals (last 7 days):", body)
        self.assertIn("Drop zone visible: 50", body)
        self.assertIn("File anchored:     12", body)
        self.assertIn("Checkout clicked:  4", body)
        self.assertIn("Checkout paid:     2", body)
        # End-to-end conversion line present
        self.assertIn("End-to-end conversion", body)
        self.assertIn("visible → paid", body)
        # Daily series — must list exactly 7 days ending on week_ending
        self.assertIn("Daily series:", body)
        for i in range(7):
            day = (week_ending - _dt.timedelta(days=i)).isoformat()
            self.assertIn(day, body)
        # The day we planted data for shows the right counts
        self.assertIn(
            "2026-05-24  visible=10  anchored=3  checkout=1  paid=1",
            body,
        )
        # 30-day context block
        self.assertIn("30-day totals for context:", body)
        self.assertIn("Drop zone visible: 200", body)
        self.assertIn("File anchored:     48", body)
        # Source footer
        self.assertIn("Source: data/events.jsonl on the Fly machine", body)
        self.assertIn("512 lines scanned", body)

    def test_rate_helper_handles_zero_denominator(self) -> None:
        mod = _load_module()
        self.assertEqual(mod._rate(0, 0), 0.0)
        self.assertEqual(mod._rate(1, 2), 50.0)
        self.assertEqual(mod._rate(0, 100), 0.0)


class TestDryRunDoesNotSend(unittest.TestCase):
    """--dry-run prints the email but must never reach mailer._send."""

    def test_dry_run_prints_and_does_not_send(self) -> None:
        # Run via subprocess so the real argv parser executes. The script's
        # mailer import is lazy and only happens on the live-send path, so
        # --dry-run cannot accidentally call out even if RESEND_API_KEY is
        # set on the test machine.
        env = dict(os.environ)
        # Force-disable Resend at the env layer as belt-and-suspenders so a
        # leaked credential in CI cannot send a real email.
        env.pop("RESEND_API_KEY", None)
        # Pin the destination to a non-PII value so this assertion does not
        # depend on whichever ORPHO_FOUNDER_EMAIL is set on the dev box.
        env["ORPHO_FOUNDER_EMAIL"] = "founder@example.test"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            cwd=str(REPO),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        # Dry-run prints SUBJECT / TO / TEXT block on stdout.
        self.assertIn("SUBJECT: Orphograph weekly funnel digest", proc.stdout)
        self.assertIn("TO: founder@example.test", proc.stdout)
        self.assertIn("---- TEXT ----", proc.stdout)
        self.assertIn("Totals (last 7 days):", proc.stdout)
        # The inert mailer log marker MUST NOT appear — mailer is never imported.
        self.assertNotIn("[email:inert]", proc.stderr)
        self.assertNotIn("[email:inert]", proc.stdout)


class TestIdempotencyGuard(unittest.TestCase):
    """If the state file says today, the script refuses to re-send."""

    def test_state_file_with_today_blocks_send(self) -> None:
        mod = _load_module()
        today = _dt.date.today()

        with tempfile.TemporaryDirectory() as td:
            state_path = pathlib.Path(td) / ".funnel_digest_last_run"
            state_path.write_text(today.isoformat(), encoding="utf-8")
            # Unit-level: function returns True for today.
            self.assertTrue(mod._already_ran_today(state_path, today))
            # And False for any other date.
            self.assertFalse(
                mod._already_ran_today(
                    state_path, today - _dt.timedelta(days=1)
                )
            )
            # Missing file -> False.
            missing = pathlib.Path(td) / "nope"
            self.assertFalse(mod._already_ran_today(missing, today))

    def test_main_refuses_when_state_says_today(self) -> None:
        """Run the script with the real state path stamped to today.

        Back up any existing state file so the test does not clobber a
        live deployment's idempotency record.
        """
        mod = _load_module()
        today = _dt.date.today()
        state_path = mod.STATE_PATH

        backup: bytes | None = None
        existed = state_path.exists()
        if existed:
            backup = state_path.read_bytes()
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(today.isoformat(), encoding="utf-8")

            env = dict(os.environ)
            env.pop("RESEND_API_KEY", None)
            proc = subprocess.run(
                [sys.executable, str(SCRIPT)],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                cwd=str(REPO),
            )
            # Returns 0 (no-op) and logs the refusal.
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertIn("already sent", proc.stderr)
            # And — critically — never prints the dry-run body or hits the mailer.
            self.assertNotIn("SUBJECT: Orphograph weekly funnel digest", proc.stdout)
            self.assertNotIn("[email:inert]", proc.stderr)
        finally:
            if existed and backup is not None:
                state_path.write_bytes(backup)
            else:
                try:
                    state_path.unlink()
                except FileNotFoundError:
                    pass


if __name__ == "__main__":
    unittest.main()
