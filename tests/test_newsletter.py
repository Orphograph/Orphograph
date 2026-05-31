#!/usr/bin/env python3
"""Tests for server/newsletter.py — confirmation-token integrity + inert-mode
safety. (This module had no test file; the waitlist/newsletter path is a
customer-facing surface and must (a) never crash when Resend env is unset, and
(b) reject tampered confirmation tokens.)

Runs offline — the Resend network paths are exercised only in their inert form.
"""
from __future__ import annotations

import pytest

import newsletter


class TestConfirmToken:
    def test_roundtrip_recovers_email_and_interest(self):
        # use a recognized interest (ALLOWED_INTERESTS); unknown ones normalize to "other"
        token, exp = newsletter.make_confirm_token("writer@example.com", "capture")
        assert isinstance(token, str) and token
        assert isinstance(exp, int) and exp > 0
        out = newsletter.verify_confirm_token(token)
        assert out is not None
        assert out["email"] == "writer@example.com"
        assert out["interest"] == "capture"

    def test_unknown_interest_normalizes_to_other(self):
        token, _ = newsletter.make_confirm_token("w@x.co", "writers")  # not in ALLOWED_INTERESTS
        out = newsletter.verify_confirm_token(token)
        assert out is not None and out["interest"] == "other"

    def test_tampered_token_rejected(self):
        token, _ = newsletter.make_confirm_token("a@b.co", "creator")
        # Mutate the FIRST char of the signature segment — its bits always
        # matter (unlike a base64 string's trailing char, whose low bits are
        # padding), so this reliably breaks the HMAC.
        body_b64, sig_b64 = token.split(".", 1)
        bad_sig = ("A" if sig_b64[0] != "A" else "B") + sig_b64[1:]
        assert newsletter.verify_confirm_token(body_b64 + "." + bad_sig) is None

    def test_garbage_token_rejected(self):
        assert newsletter.verify_confirm_token("not-a-real-token") is None
        assert newsletter.verify_confirm_token("") is None

    def test_token_segments_swapped_rejected(self):
        t1, _ = newsletter.make_confirm_token("one@x.co", "writers")
        t2, _ = newsletter.make_confirm_token("two@x.co", "writers")
        # splice the payload of one onto the signature of another (if dotted)
        if "." in t1 and "." in t2:
            spliced = t1.split(".")[0] + "." + t2.split(".", 1)[1]
            assert newsletter.verify_confirm_token(spliced) is None


class TestInertMode:
    def test_add_contact_inert_returns_false(self, monkeypatch):
        monkeypatch.setattr(newsletter, "RESEND_API_KEY", "")
        # must not raise, must return False (local ledger stays source of truth)
        assert newsletter.add_contact("x@y.co", "writers") is False

    def test_send_confirmation_inert_returns_false(self, monkeypatch):
        monkeypatch.setattr(newsletter, "RESEND_API_KEY", "")
        token, _ = newsletter.make_confirm_token("x@y.co", "writers")
        assert newsletter.send_confirmation_email("x@y.co", "writers", token) is False

    def test_add_contact_inert_when_audience_unset(self, monkeypatch):
        monkeypatch.setattr(newsletter, "RESEND_API_KEY", "re_live_xxx")
        monkeypatch.setattr(newsletter, "ORPHO_AUDIENCE_ID", "")
        assert newsletter.add_contact("x@y.co", "writers") is False


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
