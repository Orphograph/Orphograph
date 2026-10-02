#!/usr/bin/env python3
"""Tests for server/newsletter.py — confirmation-token integrity + inert-mode
safety. (This module had no test file; the waitlist/newsletter path is a
customer-facing surface and must (a) never crash when Resend env is unset, and
(b) reject tampered confirmation tokens.)

Runs offline — the Resend network paths are exercised only in their inert form.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

import auth
import newsletter


@pytest.fixture(autouse=True)
def _pinned_secret(monkeypatch):
    # The confirm token is signed with the installation secret. Without this,
    # the first signer in the process created a real `.hmac_secret` in the
    # checkout's data directory (the repo root in a worktree), found 2026-09-27.
    monkeypatch.setattr(auth, "_HMAC_SECRET_CACHE", b"test-newsletter-secret-0123456789")


class TestConfirmToken:
    """The token names one confirmation the server sent (the nonce of its
    {event: "confirm_sent"} ledger row) and when it expires. Nothing else: a
    link is copied into browser history and logs, and the first version of
    this token carried the address in plain base64."""

    def test_roundtrip_recovers_the_nonce(self):
        token, exp = newsletter.make_confirm_token("n0nce-AbC_123")
        assert isinstance(token, str) and token
        assert isinstance(exp, int) and exp > 0
        out = newsletter.verify_confirm_token(token)
        assert out == {"nonce": "n0nce-AbC_123", "exp": exp}

    def test_the_token_carries_only_the_nonce_and_the_expiry(self):
        token, exp = newsletter.make_confirm_token("only-this")
        body = token.split(".")[0]
        decoded = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        assert decoded == {"exp": exp, "n": "only-this"}

    def test_tampered_token_rejected(self):
        token, _ = newsletter.make_confirm_token("tamper-me")
        # Mutate the FIRST char of the signature segment — its bits always
        # matter (unlike a base64 string's trailing char, whose low bits are
        # padding), so this reliably breaks the HMAC.
        body_b64, sig_b64 = token.split(".", 1)
        bad_sig = ("A" if sig_b64[0] != "A" else "B") + sig_b64[1:]
        assert newsletter.verify_confirm_token(body_b64 + "." + bad_sig) is None

    def test_a_character_outside_base64_is_not_skipped(self):
        """base64 decoding drops characters outside its alphabet, so a token
        with two inserted decoded to the same bytes and verified. (One alone
        trips the padding check, which would make this test vacuous.)"""
        token, _ = newsletter.make_confirm_token("strict")
        assert newsletter.verify_confirm_token(token[:3] + "!!" + token[3:]) is None
        assert newsletter.verify_confirm_token(token + "!!") is None

    def test_a_second_spelling_of_the_same_bytes_is_rejected(self):
        """The signature's last base64 character carries two padding bits, so
        four spellings decode to the same 32 bytes. Only ours is accepted."""
        token, _ = newsletter.make_confirm_token("one-spelling")
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        last = alphabet.index(token[-1])
        twin = token[:-1] + alphabet[last ^ 1]
        assert base64.urlsafe_b64decode(twin.split(".")[1] + "=") == \
            base64.urlsafe_b64decode(token.split(".")[1] + "=")
        assert newsletter.verify_confirm_token(twin) is None

    def test_a_signature_made_for_another_purpose_is_rejected(self):
        """The installation secret also keys auth.keyed_hex and auth.email_id.
        The token's MAC is taken over a label first, so an HMAC of the same
        bytes made for anything else does not verify."""
        token, _ = newsletter.make_confirm_token("label")
        body_b64 = token.split(".")[0]
        body = base64.urlsafe_b64decode(body_b64 + "=" * (-len(body_b64) % 4))
        bare = hmac.new(auth._hmac_secret(), body, hashlib.sha256).digest()
        forged = body_b64 + "." + base64.urlsafe_b64encode(bare).rstrip(b"=").decode()
        assert newsletter.verify_confirm_token(forged) is None

    def test_an_expired_token_is_rejected(self, monkeypatch):
        monkeypatch.setattr(newsletter, "CONFIRM_TTL_SEC", -5)
        token, _ = newsletter.make_confirm_token("late")
        assert newsletter.verify_confirm_token(token) is None

    def test_garbage_token_rejected(self):
        assert newsletter.verify_confirm_token("not-a-real-token") is None
        assert newsletter.verify_confirm_token("") is None
        assert newsletter.verify_confirm_token(None) is None

    def test_a_malformed_token_never_reads_the_secret(self, monkeypatch):
        """Reading the secret creates it in a fresh data directory, and the
        confirm page is a GET, which must not write."""
        def _no(*_a):
            raise AssertionError("the secret was read for a malformed token")
        monkeypatch.setattr(auth, "_hmac_secret", _no)
        for junk in ("", "abc", "a.b", "AAAA.AAAA!", "a." + "A" * 42 + "!", "<svg>.x"):
            assert newsletter.verify_confirm_token(junk) is None

    def test_token_segments_swapped_rejected(self):
        t1, _ = newsletter.make_confirm_token("one")
        t2, _ = newsletter.make_confirm_token("two")
        spliced = t1.split(".")[0] + "." + t2.split(".", 1)[1]
        assert newsletter.verify_confirm_token(spliced) is None


class TestInertMode:
    def test_add_contact_inert_returns_false(self, monkeypatch):
        monkeypatch.setattr(newsletter, "RESEND_API_KEY", "")
        # must not raise, must return False (local ledger stays source of truth)
        assert newsletter.add_contact("x@y.co", "writers") is False

    def test_send_confirmation_inert_returns_false(self, monkeypatch):
        monkeypatch.setattr(newsletter, "RESEND_API_KEY", "")
        token, _ = newsletter.make_confirm_token("inert")
        assert newsletter.send_confirmation_email("x@y.co", "writers", token) is False

    def test_add_contact_inert_when_audience_unset(self, monkeypatch):
        monkeypatch.setattr(newsletter, "RESEND_API_KEY", "re_live_xxx")
        monkeypatch.setattr(newsletter, "ORPHO_AUDIENCE_ID", "")
        assert newsletter.add_contact("x@y.co", "writers") is False


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
