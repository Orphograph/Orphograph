from __future__ import annotations

import time

import pytest

import auth


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "TOKEN_LEDGER", tmp_path / "tokens.jsonl")
    monkeypatch.setattr(auth, "SESSION_LEDGER", tmp_path / "sessions.jsonl")
    monkeypatch.setattr(auth, "HMAC_SECRET_PATH", tmp_path / ".hmac_secret")
    monkeypatch.setattr(auth, "_HMAC_SECRET_CACHE", None)
    yield


def test_issue_and_redeem_roundtrip():
    token, _ = auth.issue_link_token("a@b.com")
    redeemed = auth.redeem_link_token(token)
    assert redeemed is not None
    assert redeemed["email"] == "a@b.com"


def test_token_is_single_use():
    token, _ = auth.issue_link_token("a@b.com")
    assert auth.redeem_link_token(token) is not None
    # second call must fail — replay defense
    assert auth.redeem_link_token(token) is None


def test_token_only_stored_as_hash(tmp_path):
    token, _ = auth.issue_link_token("a@b.com")
    raw = auth.TOKEN_LEDGER.read_text()
    assert token not in raw, "plaintext token must never be persisted"


def test_unknown_token_rejected():
    assert auth.redeem_link_token("not-a-real-token") is None
    assert auth.redeem_link_token("") is None


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setattr(auth, "LINK_TTL_SEC", 0)  # immediately expired
    token, _ = auth.issue_link_token("a@b.com")
    time.sleep(0.01)
    assert auth.redeem_link_token(token) is None


def test_session_roundtrip():
    sid, _ = auth.create_session("a@b.com")
    assert auth.session_email(sid) == "a@b.com"


def test_session_only_stored_as_hash():
    sid, _ = auth.create_session("a@b.com")
    raw = auth.SESSION_LEDGER.read_text()
    assert sid not in raw, "plaintext session id must never be persisted"


def test_revoked_session_rejected():
    sid, _ = auth.create_session("a@b.com")
    assert auth.session_email(sid) == "a@b.com"
    auth.revoke_session(sid)
    assert auth.session_email(sid) is None


def test_unknown_session_returns_none():
    assert auth.session_email("nope") is None
    assert auth.session_email("") is None


def test_expired_session_rejected(monkeypatch):
    monkeypatch.setattr(auth, "SESSION_TTL_SEC", 0)
    sid, _ = auth.create_session("a@b.com")
    time.sleep(0.01)
    assert auth.session_email(sid) is None


def test_build_cookie_has_httponly_samesite():
    s = auth.build_session_cookie("abc", secure=True)
    assert "HttpOnly" in s
    assert "SameSite=Lax" in s
    assert "Secure" in s
    assert "orpho_sid=abc" in s


def test_clear_cookie_max_age_zero():
    s = auth.clear_session_cookie(secure=True)
    # __Host- prefix in secure mode
    assert "__Host-orpho_sid=" in s
    assert "Max-Age=0" in s


def test_host_prefix_only_when_secure():
    """__Host- prefix requires Secure; never emit it in dev."""
    secure = auth.build_session_cookie("abc", secure=True)
    insecure = auth.build_session_cookie("abc", secure=False)
    assert "__Host-orpho_sid=abc" in secure
    assert "Secure" in secure
    assert "__Host-" not in insecure
    assert "orpho_sid=abc" in insecure


def test_mask_email_redacts_local_part():
    assert auth.mask_email("alex@example.com") == "a***@example.com"
    assert auth.mask_email("a@b.com") == "a***@b.com"
    assert auth.mask_email("(invalid)") == "(invalid)"
    assert auth.mask_email("") == "(invalid)"


def test_email_id_is_stable_and_hmac_keyed(tmp_path):
    """Same secret → same id; different secret → different id."""
    id1 = auth.email_id("a@b.com")
    id2 = auth.email_id("a@b.com")
    assert id1 == id2 and len(id1) == 16

    # Rotate the secret in place; cache reset
    (tmp_path / ".hmac_secret").write_bytes(b"\x00" * 32)
    auth._HMAC_SECRET_CACHE = None
    id3 = auth.email_id("a@b.com")
    assert id3 != id1, "different secret must produce different id"


def test_email_id_empty_handling():
    assert auth.email_id("") == ""


def test_issuing_new_link_supersedes_prior_unredeemed():
    """If a user requests two links, only the most recent is redeemable."""
    t1, _ = auth.issue_link_token("a@b.com")
    t2, _ = auth.issue_link_token("a@b.com")
    # First should be invalidated by second
    assert auth.redeem_link_token(t1) is None
    # Second still works
    assert auth.redeem_link_token(t2) is not None


def test_supersede_does_not_touch_other_users():
    """Requesting a link for one user must NOT invalidate another user's link."""
    t_alice, _ = auth.issue_link_token("alice@b.com")
    _ = auth.issue_link_function = auth.issue_link_token("bob@b.com")
    # Alice's token is still good after Bob's request
    assert auth.redeem_link_token(t_alice) is not None


def test_hmac_secret_file_has_restrictive_mode(tmp_path):
    """Generated secret file should be 0600 (user-only)."""
    auth._HMAC_SECRET_CACHE = None
    auth.email_id("kickoff@example.com")  # forces secret creation
    import stat
    mode = (tmp_path / ".hmac_secret").stat().st_mode
    assert mode & 0o077 == 0, f"secret file mode {oct(mode)} exposes group/other"
