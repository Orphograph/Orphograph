"""test_account_lifecycle.py — what deleting an account, and signing out,
leave behind (2026-09-26).

Four defects, each reproduced over HTTP against origin/master before the fix:

  1. POST /api/me/delete made no Stripe call. The subscription went on
     billing an account that no longer existed, and cancel-subscription
     answered 404 because the email no longer resolved to it.
  2. The next Stripe webhook for the deleted account's customer stamped the
     deleted email back on, so the subscription came back for whoever signed
     in with that address next.
  3. Delete revoked only the session that asked for it. A session on another
     device and the API key stayed live.
  4. POST /api/auth/signout appended a `revoked` row for any cookie value,
     unauthenticated and unthrottled (1,237 rows a second from one address),
     into the ledger that every session lookup reads line by line.

Everything goes through the real routes of a real server process. Stripe is
replaced inside that process (tests/_run_server.py --stub-stripe): each call
is recorded to a file and never sent anywhere.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import _srv

ALICE = "alice@example.test"
BOB = "bob@example.test"
CAROL = "carol@example.test"
WEBHOOK_SECRET = "account-lifecycle-test-signing-key"  # not whsec_-shaped: no credential lookalikes in tracked files
CLEARED = "orpho_sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
LEDGERS = ("auth_sessions.jsonl", "auth_tokens.jsonl", "subscriptions.jsonl",
           "stripe_customer_emails.jsonl", "api_keys.jsonl", "gdpr_deletions.jsonl")


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _append(path: Path, *rows: dict) -> None:
    with path.open("a") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _session_row(sid: str, email: str) -> dict:
    return {"event": "created", "session_hash": _sha(sid), "email": email,
            "expires_unix": time.time() + 86400}


def _sub_row(customer: str, sub: str, email: str, status: str = "active") -> dict:
    return {"ts": "2026-09-01T00:00:00+00:00", "event_type": "customer.subscription.created",
            "stripe_customer": customer, "stripe_sub": sub, "email": email,
            "status": status, "current_period_end": time.time() + 20 * 86400,
            "cancel_at_period_end": False}


def _seed(data: Path) -> None:
    _append(data / "auth_sessions.jsonl",
            _session_row("sess-alice-A", ALICE), _session_row("sess-alice-B", ALICE),
            _session_row("sess-bob-C", BOB), _session_row("sess-carol-D", CAROL))
    _append(data / "stripe_customer_emails.jsonl",
            {"ts": "2026-09-01T00:00:00+00:00", "stripe_customer": "cus_alice", "email": ALICE},
            {"ts": "2026-09-01T00:00:00+00:00", "stripe_customer": "cus_bob", "email": BOB})
    _append(data / "subscriptions.jsonl",
            _sub_row("cus_alice", "sub_alice", ALICE), _sub_row("cus_bob", "sub_bob", BOB))


@pytest.fixture
def server(tmp_path):
    _seed(tmp_path)
    # STRIPE_SECRET_KEY is blanked because _srv passes the caller's own
    # environment through; the stub already keeps every call on this machine.
    for base in _srv.server_processes(tmp_path, stub_calendars=True, stub_stripe=True,
                                      STRIPE_SECRET_KEY="",
                                      STRIPE_WEBHOOK_SECRET=WEBHOOK_SECRET):
        yield base, tmp_path


def _cookie(sid: str) -> dict:
    return {"Cookie": f"orpho_sid={sid}"}


def _post(base: str, path: str, sid: str | None = None, headers: dict | None = None):
    h = {**(_cookie(sid) if sid else {}), **(headers or {})}
    status, raw, hdrs = _srv.request(base, path, "POST", b"", h)
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    return status, body if isinstance(body, dict) else {"_raw": raw.decode("utf-8", "replace")}, hdrs


def _me(base: str, sid: str) -> tuple[int, dict]:
    return _srv.get_json(base, "/api/me", headers=_cookie(sid))


def _stripe_calls(data: Path) -> list[dict]:
    return _rows(data / "stub_stripe_calls.jsonl")


def _webhook(base: str, event_type: str, event_id: str, obj: dict) -> None:
    payload = json.dumps({"id": event_id, "type": event_type,
                          "data": {"object": obj}}).encode()
    ts = str(int(time.time()))
    sig = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.".encode() + payload,
                   hashlib.sha256).hexdigest()
    status, raw, _ = _srv.request(base, "/api/stripe/webhook", "POST", payload, {
        "Content-Type": "application/json", "Stripe-Signature": f"t={ts},v1={sig}"})
    assert status == 200, raw


def _sign_in(base: str, data: Path, email: str) -> str:
    """Sign in through the real magic link, the way a person would after
    their account was deleted. The token row is the one auth.issue_link_token
    writes; only its plaintext has to be known here."""
    token = f"relink-{time.time_ns()}"
    _append(data / "auth_tokens.jsonl", {
        "ts": "2026-09-26T00:00:00+00:00", "event": "issued", "token_hash": _sha(token),
        "email": email, "expires_unix": time.time() + 3600})
    status, raw, hdrs = _srv.request(base, f"/a/{token}")
    assert status == 303, raw
    cookie = hdrs.get("Set-Cookie") or ""
    m = re.match(r"orpho_sid=([^;]+);", cookie)
    assert m, cookie
    return m.group(1)


def _resubscribe(base: str, data: Path, email: str, customer: str, sub: str) -> None:
    """A new subscription bought after the delete: checkout links the new
    customer to the email, then Stripe's subscription event arrives."""
    _append(data / "stripe_customer_emails.jsonl", {
        "ts": "2026-09-26T00:00:00+00:00", "stripe_customer": customer, "email": email})
    _webhook(base, "customer.subscription.created", f"evt_{sub}", {
        "id": sub, "customer": customer, "status": "active",
        "current_period_end": int(time.time() + 30 * 86400), "cancel_at_period_end": False})


# --- 1. delete stops the billing, or changes nothing -------------------------

def test_delete_stops_the_subscription_billing(server):
    base, data = server
    status, body, _ = _post(base, "/api/me/delete", "sess-alice-A")
    assert status == 200, body
    assert _stripe_calls(data) == [{"method": "POST", "path": "/subscriptions/sub_alice",
                                    "form": {"cancel_at_period_end": "true"}}]
    # The customer is told what happens to the money, in words.
    billing = body.get("billing") or {}
    assert billing.get("outcome") == "cancel_at_period_end", body
    end = _rows(data / "subscriptions.jsonl")[0]["current_period_end"]
    day = datetime.fromtimestamp(end, timezone.utc).date().isoformat()
    assert billing.get("period_end") == day, body
    message = body.get("message") or ""
    assert f"will not renew. It is set to cancel at the end of the current billing period, on {day}." in message, body
    assert "No refund was issued" in message, body
    # Bob's subscription is not touched.
    status, me = _me(base, "sess-bob-C")
    assert status == 200 and me["subscription_active"] is True


def test_delete_without_a_known_period_end_still_cancels(server):
    """A trial can arrive with no period end. The date is left out of the
    words; the cancel is not."""
    base, data = server
    _append(data / "stripe_customer_emails.jsonl",
            {"ts": "2026-09-01T00:00:00+00:00", "stripe_customer": "cus_carol", "email": CAROL})
    _append(data / "subscriptions.jsonl", {**_sub_row("cus_carol", "sub_carol", CAROL, "trialing"),
                                           "current_period_end": None})
    status, body, _ = _post(base, "/api/me/delete", "sess-carol-D")
    assert status == 200, body
    assert [c["path"] for c in _stripe_calls(data)] == ["/subscriptions/sub_carol"]
    billing = body.get("billing") or {}
    assert billing.get("outcome") == "cancel_at_period_end", body
    assert billing.get("period_end") is None, body
    assert "at the end of the current billing period. No refund" in (body.get("message") or ""), body


def test_delete_changes_nothing_when_stripe_cannot_be_told(server):
    """Deleting first and failing to cancel would leave a subscription
    renewing with no account to cancel it from, so a failed cancel must leave
    the account exactly as it was: no tombstones, no revoked sessions."""
    base, data = server
    status, _body, _ = _post(base, "/api/me/api-key", "sess-alice-A")
    assert status == 200
    before = {name: (data / name).read_bytes() if (data / name).exists() else None
              for name in LEDGERS}
    (data / "stub_stripe_down").touch()

    status, body, _ = _post(base, "/api/me/delete", "sess-alice-A")
    assert status == 503, body
    assert "not deleted" in (body.get("message") or ""), body
    assert len(_stripe_calls(data)) == 1, "the cancel was never attempted"
    after = {name: (data / name).read_bytes() if (data / name).exists() else None
             for name in LEDGERS}
    assert after == before, "a failed cancel still changed the account's ledgers"
    for sid in ("sess-alice-A", "sess-alice-B"):
        status, me = _me(base, sid)
        assert status == 200 and me["subscription_active"] is True, (sid, status, me)
        assert me["api_key_prefix"], "the API key was revoked by a delete that did not happen"

    # Once Stripe answers again, the same request goes through.
    (data / "stub_stripe_down").unlink()
    status, body, _ = _post(base, "/api/me/delete", "sess-alice-B")
    assert status == 200, body
    assert len(_stripe_calls(data)) == 2


def test_delete_of_an_ended_or_absent_subscription_needs_no_stripe_call(server):
    """Stripe refuses to update a subscription that has already ended, so
    asking would block these deletions for good."""
    base, data = server
    _append(data / "subscriptions.jsonl", {**_sub_row("cus_alice", "sub_alice", ALICE, "canceled"),
                                           "event_type": "customer.subscription.deleted"})
    status, body, _ = _post(base, "/api/me/delete", "sess-alice-A")
    assert status == 200, body
    assert (body.get("billing") or {}).get("outcome") == "already_ended", body
    assert "will not renew" in (body.get("message") or ""), body

    status, body, _ = _post(base, "/api/me/delete", "sess-carol-D")
    assert status == 200, body
    assert (body.get("billing") or {}).get("outcome") == "no_subscription", body
    assert "nothing to bill" in (body.get("message") or ""), body
    assert _stripe_calls(data) == []


# --- 2. the deleted account's subscription stays deleted ---------------------

def test_a_webhook_after_delete_does_not_bring_the_subscription_back(server):
    base, data = server
    status, body, _ = _post(base, "/api/me/delete", "sess-alice-A")
    assert status == 200, body
    calls_after_delete = len(_stripe_calls(data))

    # Stripe keeps sending the old subscription's events until it ends.
    _webhook(base, "customer.subscription.updated", "evt_old_renewal", {
        "id": "sub_alice", "customer": "cus_alice", "status": "active",
        "current_period_end": int(time.time() + 50 * 86400), "cancel_at_period_end": False})
    # And one that looked the email up just before the delete landed appends
    # just after it, still carrying the email.
    _append(data / "subscriptions.jsonl", {**_sub_row("cus_alice", "sub_alice", ALICE),
                                           "ts": "2026-09-26T00:00:01+00:00"})

    sid = _sign_in(base, data, ALICE)
    status, me = _me(base, sid)
    assert status == 200, me
    assert me["subscription_active"] is False, me
    assert me["subscription_status"] is None, me
    assert _post(base, "/api/me/cancel-subscription", sid)[0] == 404
    assert _post(base, "/api/me/reactivate-subscription", sid)[0] == 404
    assert len(_stripe_calls(data)) == calls_after_delete
    status, me = _me(base, "sess-bob-C")
    assert status == 200 and me["subscription_active"] is True

    # POSITIVE CONTROL: a subscription the new account buys is its own.
    # Without this, an email that could never subscribe again would pass.
    _resubscribe(base, data, ALICE, "cus_alice_new", "sub_alice_new")
    status, me = _me(base, sid)
    assert me["subscription_active"] is True, me
    assert me["subscription_status"]["stripe_sub"] == "sub_alice_new", me
    assert _post(base, "/api/me/cancel-subscription", sid)[0] == 200
    assert _stripe_calls(data)[-1]["path"] == "/subscriptions/sub_alice_new"


# --- 3. delete ends every way in ---------------------------------------------

def test_delete_ends_every_session_and_the_api_key(server):
    base, data = server
    status, body, _ = _post(base, "/api/me/api-key", "sess-alice-A")
    assert status == 200, body
    key = {"X-Orpho-Api-Key": body["api_key"]}
    assert _srv.get_json(base, "/api/me/anchors", headers=key)[0] == 200

    status, body, _ = _post(base, "/api/me/delete", "sess-alice-A")
    assert status == 200, body
    for sid in ("sess-alice-A", "sess-alice-B"):
        assert _me(base, sid)[0] == 401, f"{sid} outlived the delete"
    assert _me(base, "sess-bob-C")[0] == 200, "another account's session was revoked"

    # The key is refused anyway while its owner has no subscription, so that
    # alone proves nothing. Give the address a live subscription again: only
    # a revoked key stays refused now.
    _resubscribe(base, data, ALICE, "cus_alice_new", "sub_alice_new")
    status, rec = _srv.get_json(base, "/api/me/anchors", headers=key)
    assert status == 401, ("the deleted account's API key still authenticates", rec)
    assert body.get("sessions_revoked") == 2, body
    assert body.get("api_key_revoked") is True, body


# --- 4. sign-out writes only for a live session, and misses are limited ------

def test_signout_writes_only_for_a_live_session(server):
    base, data = server
    ledger = data / "auth_sessions.jsonl"

    def signout(cookie: dict) -> None:
        status, raw, hdrs = _srv.request(base, "/api/auth/signout", "POST", b"", cookie)
        # What the client sees is the same on every path.
        assert (status, json.loads(raw)) == (200, {"ok": True}), (cookie, status, raw)
        assert hdrs.get_all("Set-Cookie") == [CLEARED], cookie

    before = ledger.read_bytes()
    signout({})
    for junk in ("never-a-session", "same-junk", "same-junk", "same-junk"):
        signout(_cookie(junk))
    assert ledger.read_bytes() == before, "a cookie that is no session was written down"

    signout(_cookie("sess-bob-C"))
    added = _rows(ledger)[len(before.splitlines()):]
    assert [(r["event"], r["session_hash"]) for r in added] == [("revoked", _sha("sess-bob-C"))]
    assert _me(base, "sess-bob-C")[0] == 401

    # Already revoked: not live any more, so nothing to write.
    grown = ledger.read_bytes()
    signout(_cookie("sess-bob-C"))
    assert ledger.read_bytes() == grown


def _junk_signouts_until_refused(base: str, limit: int = 500) -> tuple[int, object]:
    """Sign out with cookies that are no session until one is refused.
    Returns (how many were answered 200, the refusal's headers)."""
    for i in range(limit):
        status, raw, hdrs = _srv.request(base, "/api/auth/signout", "POST", b"",
                                         _cookie(f"junk-{i}"))
        if status == 429:
            assert json.loads(raw)["error"] == "too many requests"
            return i, hdrs
        assert status == 200, (i, status, raw)
    pytest.fail(f"{limit} sign-outs with a cookie that is no session, from one "
                "address, and none was refused")


def test_signout_limits_misses_per_address_not_real_sign_outs(tmp_path):
    """Every request here comes from 127.0.0.1, one address, so these run on
    servers of their own: the bucket they empty would skew the tests above.
    The budget is measured, not read from the source, so the second server
    can prove that real sign-outs leave it whole."""
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    for base in _srv.server_processes(fresh, stub_calendars=True):
        allowed, hdrs = _junk_signouts_until_refused(base)
        assert allowed > 0
        assert int(hdrs.get("Retry-After") or 0) > 0
        # Refused, and the browser is still signed out: both shipped clients
        # ignore the status and go home.
        assert hdrs.get_all("Set-Cookie") == [CLEARED]
        # No cookie costs no scan, so it is never refused.
        status, raw, _ = _srv.request(base, "/api/auth/signout", "POST", b"")
        assert status == 200, raw
        assert not (fresh / "auth_sessions.jsonl").exists(), "a miss was written down"

    # More real sign-outs than the whole budget: none is refused, each is
    # revoked, and the misses that follow still get the full budget.
    spent = tmp_path / "spent"
    spent.mkdir()
    real = [f"sess-real-{i}" for i in range(allowed + 5)]
    ledger = spent / "auth_sessions.jsonl"
    _append(ledger, *(_session_row(sid, f"user{i}@example.test") for i, sid in enumerate(real)))
    for base in _srv.server_processes(spent, stub_calendars=True):
        for sid in real:
            status, raw, _ = _srv.request(base, "/api/auth/signout", "POST", b"", _cookie(sid))
            assert status == 200, (sid, raw)
        assert sum(1 for r in _rows(ledger) if r["event"] == "revoked") == len(real)
        assert _junk_signouts_until_refused(base)[0] == allowed, (
            "signing out of real sessions spent the budget meant for misses")
