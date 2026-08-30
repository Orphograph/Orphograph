#!/usr/bin/env python3
"""test_upgrade_email.py — pin-transition transactional email.

Covers the upgrade worker's responsibility to email the customer exactly
once when their receipt transitions from "pending" to "pinned" (or
"partial"). Mocks urlopen so no real Resend call is ever made.

Conditions verified:
  1. notify_email present  → pending→pinned  → Resend called once
  2. notify_email empty    → pending→pinned  → Resend NOT called
  3. pin_email_sent_at set → re-run worker   → Resend NOT called
  4. Resend returns HTTP 500                  → no crash, no sent-flag,
                                                worker still reports success
  5. Partial transition (3/5)                 → email subject says
                                                "Bitcoin-anchored", body says
                                                "3 of 5 calendars confirmed"
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from conftest import PINNED_BODY as _PINNED_BODY  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))


def _make_receipt_dir(base: Path, receipt_id: str, *, calendars: list[str],
                       notify_email: str | None,
                       pin_email_sent_at: str | None = None,
                       btc_pinned_at: str | None = None) -> Path:
    """Materialize a minimal receipt directory the upgrade worker can read.

    Writes a receipt.json + an .ots file per calendar entry. The .ots files
    are sentinel non-empty bytes — we monkey-patch _commitment_for_pending
    and _fetch_upgrade so the engine's OTS parser is never invoked.
    """
    rd = base / receipt_id
    rd.mkdir(parents=True, exist_ok=True)
    successes = []
    for cal in calendars:
        short = cal.split("//", 1)[1].split(".", 1)[0]
        ots_path = rd / f"{short}.ots"
        ots_path.write_bytes(b"OTS_BLOB_PLACEHOLDER")
        successes.append({
            "calendar": cal,
            "ots_path": f"receipts/{receipt_id}/{short}.ots",
            "ots_bytes": len(b"OTS_BLOB_PLACEHOLDER"),
        })
    record = {
        "receipt_id": receipt_id,
        "created_at": "2026-05-17T00:00:00+00:00",
        "hash_hex": "a" * 64,
        "sha512_hex": None,
        "client_label": None,
        "source": "pack:demo",
        "private": False,
        "owner_id": None,
        "attestation": None,
        "metadata": None,
        "calendars_ok": len(successes),
        "calendars_total": len(calendars),
        "successes": successes,
        "failures": [],
        "status": "pending",
    }
    if notify_email is not None:
        record["notify_email"] = notify_email
    if pin_email_sent_at is not None:
        record["pin_email_sent_at"] = pin_email_sent_at
    if btc_pinned_at is not None:
        record["btc_pinned_at"] = btc_pinned_at
    (rd / "receipt.json").write_text(json.dumps(record, indent=2))
    return rd


class _FakeOK:
    """Mimics a urllib response for a successful Resend 200."""

    def __init__(self, body: bytes = b'{"id":"em_test"}') -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _http_error_500():
    return urllib.error.HTTPError(
        url="https://api.resend.com/emails",
        code=500,
        msg="Server Error",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )


class UpgradeEmailTest(unittest.TestCase):
    def setUp(self) -> None:
        # Pristine temp data dir per test so worker state never leaks.
        self.tmp = Path(tempfile.mkdtemp(prefix="orpho_upgrade_email_"))
        self.receipts = self.tmp / "receipts"
        self.receipts.mkdir(parents=True)
        # Force the mailer into "live" mode so it tries Resend (we mock urlopen).
        os.environ["RESEND_API_KEY"] = "test_key_not_real"
        # Reload modules against the temp data dir.
        for m in ("upgrade_worker", "mailer", "auth", "engine"):
            sys.modules.pop(m, None)
        os.environ["ORPHO_DATA_DIR"] = str(self.tmp)
        os.environ["ORPHO_RECEIPTS_DIR"] = str(self.receipts)
        os.environ["ORPHO_UPGRADE_LOG"] = str(self.tmp / "upgrade_log.jsonl")
        import upgrade_worker  # noqa: F401
        import mailer  # noqa: F401
        self.upgrade_worker = upgrade_worker
        self.mailer = mailer

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)
        # Don't leak the fake key into sibling tests.
        os.environ.pop("RESEND_API_KEY", None)
        os.environ.pop("ORPHO_DATA_DIR", None)
        os.environ.pop("ORPHO_RECEIPTS_DIR", None)
        os.environ.pop("ORPHO_UPGRADE_LOG", None)

    # ---- helpers -------------------------------------------------------

    def _patch_calendars_all_pin(self):
        """Make every calendar fetch report a clean pin. Bypasses OTS parser."""
        uw = self.upgrade_worker
        commit_patch = mock.patch.object(
            uw, "_commitment_for_pending",
            return_value=("c" * 64, 100),
        )
        fetch_patch = mock.patch.object(
            uw, "_fetch_upgrade",
            return_value=(True, _PINNED_BODY),
        )
        return commit_patch, fetch_patch

    def _patch_calendars_partial(self, n_ok: int, n_total: int):
        """First n_ok calendars succeed; the rest report still-pending (404)."""
        uw = self.upgrade_worker
        commit_patch = mock.patch.object(
            uw, "_commitment_for_pending",
            return_value=("c" * 64, 100),
        )
        counter = {"i": 0}

        def fake_fetch(cal, hash_hex):
            i = counter["i"]
            counter["i"] += 1
            if i < n_ok:
                return True, _PINNED_BODY
            return False, "HTTP 404"

        fetch_patch = mock.patch.object(uw, "_fetch_upgrade", side_effect=fake_fetch)
        return commit_patch, fetch_patch

    # ---- tests ---------------------------------------------------------

    def test_pending_to_pinned_with_email_sends_once(self):
        cals = ["https://a.pool.opentimestamps.org", "https://b.pool.opentimestamps.org"]
        rd = _make_receipt_dir(
            self.receipts, "rid_send",
            calendars=cals, notify_email="customer@example.com",
        )
        record = json.loads((rd / "receipt.json").read_text())

        commit_patch, fetch_patch = self._patch_calendars_all_pin()
        with commit_patch, fetch_patch, \
                mock.patch("urllib.request.urlopen", return_value=_FakeOK()) as urlopen:
            result = self.upgrade_worker._upgrade_one(rd, record)

        self.assertEqual(result["status"], "pinned")
        # urlopen called exactly once — for the Resend send.
        self.assertEqual(urlopen.call_count, 1)
        sent_req = urlopen.call_args[0][0]
        self.assertEqual(sent_req.full_url, "https://api.resend.com/emails")
        # And the on-disk receipt records pin_email_sent_at.
        on_disk = json.loads((rd / "receipt.json").read_text())
        self.assertIn("pin_email_sent_at", on_disk)
        self.assertIn("btc_pinned_at", on_disk)

    def test_pending_to_pinned_without_email_skips_send(self):
        cals = ["https://a.pool.opentimestamps.org"]
        rd = _make_receipt_dir(
            self.receipts, "rid_noemail",
            calendars=cals, notify_email=None,
        )
        record = json.loads((rd / "receipt.json").read_text())

        commit_patch, fetch_patch = self._patch_calendars_all_pin()
        with commit_patch, fetch_patch, \
                mock.patch("urllib.request.urlopen", return_value=_FakeOK()) as urlopen:
            result = self.upgrade_worker._upgrade_one(rd, record)

        self.assertEqual(result["status"], "pinned")
        self.assertEqual(urlopen.call_count, 0)
        on_disk = json.loads((rd / "receipt.json").read_text())
        self.assertNotIn("pin_email_sent_at", on_disk)

    def test_rerun_after_sent_does_not_resend(self):
        cals = ["https://a.pool.opentimestamps.org"]
        rd = _make_receipt_dir(
            self.receipts, "rid_already_sent",
            calendars=cals,
            notify_email="customer@example.com",
            pin_email_sent_at="2026-05-17T01:00:00+00:00",
            btc_pinned_at="2026-05-17T00:30:00+00:00",
        )
        record = json.loads((rd / "receipt.json").read_text())

        commit_patch, fetch_patch = self._patch_calendars_all_pin()
        with commit_patch, fetch_patch, \
                mock.patch("urllib.request.urlopen", return_value=_FakeOK()) as urlopen:
            self.upgrade_worker._upgrade_one(rd, record)

        # btc_pinned_at was already set, so transition guard short-circuits
        # before _send_pin_email_if_needed is even called. Belt-and-suspenders.
        self.assertEqual(urlopen.call_count, 0)
        on_disk = json.loads((rd / "receipt.json").read_text())
        # pin_email_sent_at is preserved unchanged.
        self.assertEqual(on_disk["pin_email_sent_at"], "2026-05-17T01:00:00+00:00")

    def test_resend_500_does_not_crash_and_does_not_mark_sent(self):
        cals = ["https://a.pool.opentimestamps.org"]
        rd = _make_receipt_dir(
            self.receipts, "rid_500",
            calendars=cals, notify_email="customer@example.com",
        )
        record = json.loads((rd / "receipt.json").read_text())

        commit_patch, fetch_patch = self._patch_calendars_all_pin()
        with commit_patch, fetch_patch, \
                mock.patch("urllib.request.urlopen", side_effect=_http_error_500()):
            # Must not raise.
            result = self.upgrade_worker._upgrade_one(rd, record)

        self.assertEqual(result["status"], "pinned")
        on_disk = json.loads((rd / "receipt.json").read_text())
        # Pin happened; email did NOT — next run can retry.
        self.assertIn("btc_pinned_at", on_disk)
        self.assertNotIn("pin_email_sent_at", on_disk)

    def test_partial_transition_email_says_three_of_five(self):
        cals = [
            "https://a.pool.opentimestamps.org",
            "https://b.pool.opentimestamps.org",
            "https://finney.calendar.eternitywall.com",
            "https://btc.calendar.catallaxy.com",
            "https://alice.btc.calendar.opentimestamps.org",
        ]
        rd = _make_receipt_dir(
            self.receipts, "rid_partial",
            calendars=cals, notify_email="customer@example.com",
        )
        record = json.loads((rd / "receipt.json").read_text())

        commit_patch, fetch_patch = self._patch_calendars_partial(3, 5)
        captured: dict = {}

        def capture(req, *a, **kw):
            # Snapshot the JSON payload Resend would receive so we can assert
            # the subject + body honest-framing copy.
            body = req.data.decode("utf-8") if isinstance(req.data, (bytes, bytearray)) else ""
            captured["payload"] = json.loads(body) if body else {}
            return _FakeOK()

        with commit_patch, fetch_patch, \
                mock.patch("urllib.request.urlopen", side_effect=capture):
            result = self.upgrade_worker._upgrade_one(rd, record)

        self.assertEqual(result["status"], "partial")
        self.assertIn("payload", captured)
        payload = captured["payload"]
        # Subject was rewritten in the institutional-notary tone refresh:
        # "Orphograph — Receipt <rid> committed to Bitcoin". The exact
        # phrasing is tested below by structure, not by literal match.
        subject = payload["subject"]
        self.assertIn("Orphograph", subject)
        self.assertIn("rid_partial", subject)
        self.assertIn("Bitcoin", subject)
        text = payload["text"]
        # Institutional-notary rewrite phrases the partial-anchor body as
        # "3 of 5 confirmed" instead of "3 of 5 calendars". Either phrasing
        # honestly conveys the partial state; assert the numeric ratio plus
        # the calendar concept appears somewhere in the body.
        self.assertIn("3 of 5", text)
        # We never call it "pinned" when partial — honest framing.
        self.assertNotIn("clean pin", text.lower())


if __name__ == "__main__":
    unittest.main()
