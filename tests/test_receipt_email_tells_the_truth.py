"""The receipt email says what the receipt is, including when no calendar accepted it.

Cycle 9 (review of PR #284): the plain-text half still promised Bitcoin
"within a few hours" (the scan read the source, where the sentence spans two
f-string literals), and a paid anchor no calendar accepted got "Calendar
attestations are complete" and "A second notice will be issued upon Bitcoin
commitment" — for a receipt with no commitment, ever. _send is stubbed;
nothing is sent.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import mailer  # noqa: E402

HOUR_PROMISE = re.compile(r"within (a few )?hours?|within (about |~)?(an|one|1) hour|about an hour\.", re.I)


def _render(monkeypatch, calendars_ok, kind=None):
    sent = {}

    def fake_send(to, subject, text, html, **kw):
        sent.update(text=text, html=html)
        return True
    monkeypatch.setattr(mailer, "_send", fake_send)
    receipt = {"receipt_id": "REMAIL00000001", "hash_hex": "ab" * 32, "created_at": "2026-10-03T05:00:00Z",
               "calendars_ok": calendars_ok, "calendars_total": 5}
    if kind:
        receipt.update(kind=kind, leaf_count=2)
    mailer.send_receipt_email("buyer@example.test", receipt)
    return sent["text"], re.sub(r"<[^>]+>", " ", sent["html"])


@pytest.mark.parametrize("kind", [None, "folder"])
def test_neither_half_promises_pinning_within_hours(monkeypatch, kind):
    for body in _render(monkeypatch, 5, kind):
        assert not HOUR_PROMISE.search(body), body[:200]
        assert "from about an hour to several days" in body


@pytest.mark.parametrize("kind", [None, "folder"])
def test_a_receipt_no_calendar_accepted_is_told_so(monkeypatch, kind):
    for body in _render(monkeypatch, 0, kind):
        low = re.sub(r"\s+", " ", body).lower()
        assert "no calendar accepted" in low, body[:300]
        assert "attestations are complete" not in low
        assert "second notice will be issued" not in low


def test_a_committed_receipt_keeps_its_notice(monkeypatch):
    text, html = _render(monkeypatch, 1)
    assert "second notice will be issued" in text.lower() and "attestations are complete" in text.lower()
