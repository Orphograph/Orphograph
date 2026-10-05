"""test_api_keys_and_teams_integrity.py — API-key lifecycle and team-route
integrity, driven through the real HTTP routes.

Six defects reproduced on origin/master 2026-09-25 (no external host was
contacted; calendars stubbed, Stripe/Resend/NOWPayments keys blank):

  1. POST /api/me/api-key/revoke looked only at the NEWEST issued key, so an
     older key that was still live (what a double click leaves behind) kept
     authenticating while revoke answered revoked:false from then on.
  2. Two concurrent POST /api/me/api-key (a double click) left BOTH keys live,
     10 of 10 tries: issue() read, revoked and appended with no critical section.
  3. issue() re-read the whole ledger once per key the account had ever been
     issued (2,000 rotations took 41 s) and nothing limited how often it ran.
  4. The team routes refused an owner or member whose sign-in email had an
     uppercase letter. Sign-in keeps the case as typed, create_team stores the
     owner lowercased, and invite/remove/leave/role compared exact strings, so a
     mixed-case owner was answered 403 on their own team and shown as "member".
  5. create_team's one-team-per-owner check and its append were not atomic
     (30 concurrent creates wrote up to 3 teams for one owner), and a MEMBER of
     another team could still create a team of their own.
  6. Invite codes were unlimited and never expired (3,000 in 7 s, the first
     still redeemable), while the response promised an `expires_at`.

Six more, found by an adversarial review of the fix above and reproduced on
this branch 2026-09-26:

  7. A mixed-case owner could now fill seats, but members inherited nothing:
     the owner lookup returned the stored (lowercased) owner and the Stripe
     row kept the owner's capitals, so a seat was taken and the member got
     subscription_active:false.
  8. Two spellings of one mailbox could each hold a live API key, and a revoke
     from one spelling left the other key working.
  9. str.lower() maps U+212A KELVIN SIGN to "k", so a Kelvin-sign spelling of
     karl@x counted as the owner of karl@x's team and shared its key budget.
 10. Leaving went through the owner-gated remove, so a member of a team whose
     create row was missing or damaged could never leave.
 11. Nothing pinned the invite lock, the redeem lock, "a redeemed invite holds
     no seat" or "a NaN expiry is expired": each mutant survived the suite.
 12. The account page answered every failed invite with "check that your
     subscription is active", including a full team's 409.

Accounts are seeded by writing the same JSONL rows sign-in and the Stripe
webhook write. Each test uses its own emails, so the module can share one
server and no test depends on another's state.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import secrets
import shutil
import string
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import _srv

# Nothing here needs a payment or mail provider, and blank keys make sure a
# stray code path cannot reach one. The three kill switches stop the upgrade,
# cadence and funnel-digest threads, which have nothing to do with these routes.
SAFE_ENV = dict(
    STRIPE_SECRET_KEY="",
    STRIPE_WEBHOOK_SECRET="",
    NOWPAYMENTS_API_KEY="",
    NOWPAYMENTS_IPN_SECRET="",
    ORPHO_UPGRADE_LEADER="0",
    ORPHO_CADENCE_DISABLED="1",
    ORPHO_FUNNEL_DIGEST_DISABLED="1",
)
# The invite cap is "free seats": MAX_TEAM_MEMBERS minus current members. A
# small team size keeps the cap test short; the env knob exists on master.
MAX_MEMBERS = 3
WEEK = 7 * 24 * 3600
_counter = itertools.count()


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    data = tmp_path_factory.mktemp("keys_teams")
    for base in _srv.server_processes(data, stub_calendars=True,
                                      ORPHO_MAX_TEAM_MEMBERS=str(MAX_MEMBERS),
                                      **SAFE_ENV):
        yield base, data


@pytest.fixture(scope="module")
def two_srv(tmp_path_factory):
    """Two server processes on one data directory: what two machines sharing a
    volume look like. A lock that is only a threading.Lock passes a one-process
    test and fails here."""
    data = tmp_path_factory.mktemp("keys_teams_two")
    for bases in _srv.server_processes(data, n=2, stub_calendars=True,
                                       ORPHO_MAX_TEAM_MEMBERS=str(MAX_MEMBERS),
                                       **SAFE_ENV):
        yield bases, data


# ── seeding ───────────────────────────────────────────────────────────────


def _append_rows(path: Path, rows) -> None:
    with path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def account(data: Path, email: str, *, subscribed: bool = True) -> dict:
    """A signed-in session for `email`, exactly as sign-in records it (case
    kept), plus an active subscription row under the same spelling when
    `subscribed` (the Stripe webhook keeps the case Stripe sends)."""
    sid = "sess-" + secrets.token_hex(12)
    _append_rows(data / "auth_sessions.jsonl", [dict(
        event="created", session_hash=hashlib.sha256(sid.encode()).hexdigest(),
        email=email, expires_unix=time.time() + 3600)])
    if subscribed:
        _append_rows(data / "subscriptions.jsonl", [dict(
            email=email, status="active", stripe_sub="sub_" + sid)])
    return {"Cookie": "orpho_sid=" + sid, "Content-Type": "application/json"}


def fresh(stem: str) -> str:
    return f"{stem}.{next(_counter)}.{secrets.token_hex(3)}@example.test"


def post(base: str, path: str, who: dict, payload: dict | None = None):
    code, raw, headers = _srv.request(
        base, path, "POST", json.dumps(payload if payload is not None else {}).encode(),
        who, timeout=30)
    return code, _srv._json_object(raw), headers


def get(base: str, path: str, who: dict):
    code, raw, _ = _srv.request(base, path, headers=who, timeout=30)
    return code, _srv._json_object(raw)


def key_status(base: str, key: str) -> int:
    """The vault list authenticates by X-Orpho-Api-Key alone: 200 means the key
    is live, 401 means it is dead. The real entry point, not a ledger read."""
    code, _raw, _ = _srv.request(base, "/api/me/anchors", headers={"X-Orpho-Api-Key": key})
    return code


def issued_row(email: str, key: str) -> dict:
    return dict(ts="2026-09-25T00:00:00+00:00", event="issued",
                key_hash=hashlib.sha256(key.encode()).hexdigest(),
                key_prefix=key[:14], email=email)


def live_counts_after_each_issue(rows: list[dict], email: str) -> list[int]:
    """Replay the key ledger and record how many of `email`'s keys are live
    right after each of its `issued` rows. One key per account means every
    entry is 1. Checking only the final state would miss a race: a later call
    that runs alone revokes both survivors and hides it."""
    live: set[str] = set()
    counts = []
    for r in rows:
        if r.get("event") == "issued" and r.get("email") == email:
            live.add(r["key_hash"])
            counts.append(len(live))
        elif r.get("event") == "revoked":
            live.discard(r.get("key_hash"))
    return counts


# ── API keys ──────────────────────────────────────────────────────────────


def test_revoke_kills_every_live_key_not_only_the_newest(srv):
    """Defect 1. The ledger holds two live keys for one account, the state a
    double click left on master. Revoke must kill both, and say it did."""
    base, data = srv
    email = fresh("revoker")
    who = account(data, email)
    old, new = "orpho_OLD" + secrets.token_hex(12), "orpho_NEW" + secrets.token_hex(12)
    _append_rows(data / "api_keys.jsonl", [issued_row(email, old), issued_row(email, new)])
    assert (key_status(base, old), key_status(base, new)) == (200, 200)

    code, body, _ = post(base, "/api/me/api-key/revoke", who)
    assert code == 200 and body.get("revoked") is True, body
    assert (key_status(base, old), key_status(base, new)) == (401, 401), (
        "an older live key survived revoke")
    code, me = get(base, "/api/me", who)
    assert code == 200 and me.get("api_key_prefix") == "", me

    code, body, _ = post(base, "/api/me/api-key/revoke", who)
    assert code == 200 and body.get("revoked") is False, (
        "nothing is left to revoke, so the second revoke must say so")


def _double_click(bases: list[str], who: dict) -> list[tuple[int, dict]]:
    barrier = threading.Barrier(len(bases))
    out: list = [None] * len(bases)

    def click(i: int) -> None:
        barrier.wait()
        code, body, _ = post(bases[i], "/api/me/api-key", who)
        out[i] = (code, body)

    threads = [threading.Thread(target=click, args=(i,)) for i in range(len(bases))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def _assert_one_live_key(base: str, data: Path, email: str, who: dict,
                         results: list[tuple[int, dict]]) -> None:
    assert [c for c, _ in results] == [200, 200], results
    keys = [b["api_key"] for _, b in results]
    statuses = sorted(key_status(base, k) for k in keys)
    assert statuses == [200, 401], (
        f"a double click must leave exactly one live key, got {statuses}")
    live = next(k for k in keys if key_status(base, k) == 200)
    code, me = get(base, "/api/me", who)
    assert code == 200 and me.get("api_key_prefix") == live[:14], me
    counts = live_counts_after_each_issue(_read_rows(data / "api_keys.jsonl"), email)
    assert counts == [1, 1], counts


def test_double_click_on_issue_leaves_one_live_key(srv):
    """Defect 2, one server: its two handler threads must not interleave the
    read-revoke-append. A fresh account per trial so the issuance limit never
    answers in place of the handler."""
    base, data = srv
    for _ in range(4):
        email = fresh("clicker")
        who = account(data, email)
        _assert_one_live_key(base, data, email, who, _double_click([base, base], who))


def test_double_click_across_two_processes_leaves_one_live_key(two_srv):
    """Defect 2 across processes: each click lands on a different server that
    shares the ledger, so only a file lock can serialise them."""
    bases, data = two_srv
    for _ in range(4):
        email = fresh("twoclick")
        who = account(data, email)
        _assert_one_live_key(bases[0], data, email, who, _double_click(bases, who))


def test_concurrent_issue_calls_never_leave_two_live_keys(tmp_path, monkeypatch):
    """Defect 2 at the module: many threads issuing for one account. Replaying
    the ledger must show exactly one live key after every issuance."""
    import api_keys
    monkeypatch.setattr(api_keys, "KEY_LEDGER", tmp_path / "api_keys.jsonl")
    email = "racer@example.test"
    threads_n, per_thread = 6, 5
    barrier = threading.Barrier(threads_n)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            for _ in range(per_thread):
                api_keys.issue(email)
        except BaseException as e:  # noqa: BLE001 - surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    counts = live_counts_after_each_issue(_read_rows(api_keys.KEY_LEDGER), email)
    assert len(counts) == threads_n * per_thread
    assert set(counts) == {1}, f"live keys after each issuance: {counts}"


def test_issue_reads_the_ledger_a_constant_number_of_times(tmp_path, monkeypatch):
    """Defect 3, the superlinear part. issue() re-read the whole ledger once per
    key the account had ever held, so 2,000 rotations cost 41 s per call on one
    server thread. The count of reads is the defect itself, and unlike a timing
    bound it cannot flake under a loaded machine."""
    import api_keys
    ledger = tmp_path / "api_keys.jsonl"
    monkeypatch.setattr(api_keys, "KEY_LEDGER", ledger)
    email = "rotator@example.test"
    rows = []
    for i in range(200):
        kh = hashlib.sha256(f"k{i}".encode()).hexdigest()
        if i:
            rows.append(dict(ts="2026-09-25T00:00:00+00:00", event="revoked",
                             key_hash=hashlib.sha256(f"k{i - 1}".encode()).hexdigest(),
                             email=email, reason="superseded by new key"))
        rows.append(dict(ts="2026-09-25T00:00:00+00:00", event="issued",
                         key_hash=kh, key_prefix=f"orpho_{i:08d}", email=email))
    _append_rows(ledger, rows)

    real_read = api_keys._read_rows
    reads = []

    def counting_read():
        reads.append(1)
        return real_read()

    monkeypatch.setattr(api_keys, "_read_rows", counting_read)
    key = api_keys.issue(email)
    assert len(reads) <= 2, f"issue() read the ledger {len(reads)} times for 200 past keys"
    monkeypatch.setattr(api_keys, "_read_rows", real_read)
    assert api_keys.email_for_key(key) == email
    assert live_counts_after_each_issue(_read_rows(ledger), email)[-1] == 1


def test_issuance_is_rate_limited_per_account(srv):
    """Defect 3, the unthrottled part. 150 issuances in a row all answered 200
    on master. The limit is per account (case variants share it), so one
    account running into it does not stop another."""
    base, data = srv
    email = fresh("Limited")
    who = account(data, email)
    codes, limited_headers, limited_body = [], None, None
    for _ in range(20):
        code, body, headers = post(base, "/api/me/api-key", who)
        codes.append(code)
        if code == 429:
            limited_headers, limited_body = headers, body
            break
    assert 429 in codes, f"20 issuances in a row were never limited: {codes}"
    assert set(codes[:-1]) == {200} and 1 <= len(codes) - 1 <= 10, codes
    assert int(limited_headers.get("Retry-After", "0")) > 0, dict(limited_headers)
    assert limited_body.get("error"), limited_body

    # Same mailbox, other spelling: the same budget.
    variant = account(data, email.lower())
    code, body, _ = post(base, "/api/me/api-key", variant)
    assert code == 429, (code, body)

    # Another account is unaffected.
    other = account(data, fresh("unlimited"))
    code, body, _ = post(base, "/api/me/api-key", other)
    assert code == 200 and body.get("api_key", "").startswith("orpho_"), (code, body)


# ── teams ─────────────────────────────────────────────────────────────────


def _create(base: str, who: dict, name: str = "Team") -> str:
    code, body, _ = post(base, "/api/me/team/create", who, {"team_name": name})
    assert code == 200 and body.get("ok"), (code, body)
    return body["team_id"]


def _invite(base: str, who: dict) -> str:
    code, body, _ = post(base, "/api/me/team/invite", who)
    assert code == 200 and body.get("invite_code", "").startswith("tinv_"), (code, body)
    return body["invite_code"]


def test_mixed_case_owner_runs_their_own_team(srv):
    """Defect 4, owner side. The owner signed in as typed with capitals and
    Stripe kept the same spelling, so their subscription is active. Master
    created the team and then refused them as its owner."""
    base, data = srv
    tag = secrets.token_hex(3)
    owner_email = f"Alice.{tag}@Example.Test"
    owner = account(data, owner_email)
    member = account(data, f"Bob.{tag}@Example.Test", subscribed=False)
    tid = _create(base, owner, "A-Team")

    code, body = get(base, "/api/me/team", owner)
    assert code == 200 and body.get("role") == "owner", body
    code, me = get(base, "/api/me", owner)
    assert code == 200 and me.get("team_role") == "owner", me

    code_ = _invite(base, owner)
    code, body, _ = post(base, "/api/me/team/redeem", member, {"invite_code": code_})
    assert code == 200 and body == {"ok": True, "team_id": tid}, body

    code, body, _ = post(base, "/api/me/team/remove", owner,
                         {"member_email": f"Bob.{tag}@Example.Test"})
    assert code == 200 and body.get("ok") is True, body
    code, body = get(base, "/api/me/team", member)
    assert code == 200 and body.get("team") is None, body


def test_mixed_case_member_can_leave(srv):
    """Defect 4, member side. Master answered ok:false to every leave, so the
    member could neither leave nor join any other team."""
    base, data = srv
    tag = secrets.token_hex(3)
    owner = account(data, f"carol.{tag}@example.test")
    other_owner = account(data, f"gina.{tag}@example.test")
    member = account(data, f"Bob.{tag}@Example.Test", subscribed=False)
    tid = _create(base, owner, "Carol Co")
    other_tid = _create(base, other_owner, "Gina Co")

    code, body, _ = post(base, "/api/me/team/redeem", member, {"invite_code": _invite(base, owner)})
    assert code == 200 and body.get("team_id") == tid, body
    code, body, _ = post(base, "/api/me/team/leave", member)
    assert code == 200 and body.get("ok") is True, body
    code, body = get(base, "/api/me/team", member)
    assert code == 200 and body.get("team") is None, body

    code, body, _ = post(base, "/api/me/team/redeem", member,
                         {"invite_code": _invite(base, other_owner)})
    assert code == 200 and body.get("team_id") == other_tid, body


def test_member_of_another_team_cannot_create_a_team(srv):
    """Defect 5(b). A member creating a team of their own ended up in two
    teams at once; after leaving the first, a dormant team they never meant
    to own took over their account view."""
    base, data = srv
    owner = account(data, fresh("owner5"))
    member_email = fresh("member5")
    member = account(data, member_email)
    tid = _create(base, owner)
    code, body, _ = post(base, "/api/me/team/redeem", member, {"invite_code": _invite(base, owner)})
    assert code == 200 and body.get("team_id") == tid, body

    code, body, _ = post(base, "/api/me/team/create", member, {"team_name": "Shadow"})
    assert code == 400 and "leave" in (body.get("error") or "").lower(), (code, body)
    creates = [r for r in _read_rows(data / "teams.jsonl")
               if r.get("event") == "create" and r.get("owner_email") == member_email]
    assert creates == [], creates
    code, body = get(base, "/api/me/team", member)
    assert body.get("team", {}).get("team_id") == tid and body.get("role") == "member", body


def test_concurrent_create_makes_one_team(two_srv):
    """Defect 5(a). Many simultaneous creates for one owner, split across two
    processes, must write one team and hand every caller its id."""
    bases, data = two_srv
    for _ in range(3):
        email = fresh("racer5")
        who = account(data, email)
        n = 24
        barrier = threading.Barrier(n)
        results: list = []
        lock = threading.Lock()

        def go(i: int) -> None:
            barrier.wait()
            code, body, _ = post(bases[i % 2], "/api/me/team/create", who, {"team_name": "R"})
            with lock:
                results.append((code, body.get("team_id")))

        threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        creates = [r["team_id"] for r in _read_rows(data / "teams.jsonl")
                   if r.get("event") == "create" and r.get("owner_email") == email]
        assert len(creates) == 1, f"{len(creates)} teams written for one owner"
        assert {c for c, _ in results} == {200}, results
        assert {t for _, t in results} == set(creates), (results, creates)


def test_invite_response_expiry_is_real(srv):
    """Defect 6. The response names when the invite stops working, 7 days
    out, instead of null."""
    base, data = srv
    owner = account(data, fresh("owner6"))
    _create(base, owner)
    code, body, _ = post(base, "/api/me/team/invite", owner)
    assert code == 200, body
    raw = body.get("expires_at")
    assert isinstance(raw, str), f"expires_at must be a timestamp, got {raw!r}"
    expires = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    assert expires.tzinfo is not None, raw
    assert abs(expires.timestamp() - (time.time() + WEEK)) < 300, raw
    rows = [r for r in _read_rows(data / "team_invites.jsonl")
            if r.get("invite_code") == body["invite_code"]]
    assert len(rows) == 1 and abs(float(rows[0]["expires_at"]) - expires.timestamp()) < 1, rows


@pytest.mark.parametrize("row_age, stored_expiry", [
    (8 * 24 * 3600, False),   # issued before invites carried an expiry
    (60, True),               # a recorded expiry that has passed
])
def test_expired_invite_is_refused(srv, row_age, stored_expiry):
    """Defect 6. An invite past its expiry is refused at redeem. The first
    case is an invite already outstanding in production: it never had an
    expiry, so it lapses 7 days after it was issued."""
    base, data = srv
    owner_email = fresh("owner6x")
    owner = account(data, owner_email)
    tid = _create(base, owner)
    code_ = "tinv_" + secrets.token_urlsafe(12)
    row = dict(ts=time.time() - row_age, event="issue", team_id=tid,
               invite_code=code_, issued_by=owner_email)
    if stored_expiry:
        row["expires_at"] = time.time() - 1
    _append_rows(data / "team_invites.jsonl", [row])

    joiner = account(data, fresh("joiner6x"), subscribed=False)
    code, body, _ = post(base, "/api/me/team/redeem", joiner, {"invite_code": code_})
    assert code == 400 and "expired" in (body.get("error") or ""), (code, body)
    code, body = get(base, "/api/me/team", joiner)
    assert body.get("team") is None, body


def test_open_invites_are_capped_by_free_seats(srv):
    """Defect 6. A team can hold at most MAX_TEAM_MEMBERS minus its current
    members in open invites; expired ones do not count."""
    base, data = srv
    owner_email = fresh("owner6c")
    owner = account(data, owner_email)
    tid = _create(base, owner)
    # Expired invites hold no seat.
    _append_rows(data / "team_invites.jsonl", [
        dict(ts=time.time() - 8 * 24 * 3600, event="issue", team_id=tid,
             invite_code="tinv_" + secrets.token_urlsafe(12), issued_by=owner_email)
        for _ in range(MAX_MEMBERS)])

    codes = [_invite(base, owner) for _ in range(MAX_MEMBERS)]
    code, body, _ = post(base, "/api/me/team/invite", owner)
    assert code == 409 and body.get("error"), (code, body)

    # One seat filled leaves MAX-1 free seats for the MAX-1 invites still open.
    joiner = account(data, fresh("joiner6c"), subscribed=False)
    code, body, _ = post(base, "/api/me/team/redeem", joiner, {"invite_code": codes[0]})
    assert code == 200 and body.get("team_id") == tid, body
    code, body, _ = post(base, "/api/me/team/invite", owner)
    assert code == 409, (code, body)
    issued = [r for r in _read_rows(data / "team_invites.jsonl")
              if r.get("event") == "issue" and r.get("team_id") == tid]
    assert len(issued) == 2 * MAX_MEMBERS, len(issued)


# ── follow-up, 2026-09-26 ─────────────────────────────────────────────────

KELVIN = "K"  # KELVIN SIGN. str.lower() turns it into ASCII "k".
_ASCII_FOLD = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _fold(email: str) -> str:
    """ASCII A-Z lowercased and nothing else. Restated here rather than
    imported, so the ledger replay below does not trust the code under test."""
    return email.strip().translate(_ASCII_FOLD)


def live_counts_across_spellings(rows: list[dict], email: str) -> list[int]:
    """live_counts_after_each_issue for every spelling of one mailbox: how
    many of its keys are live right after each of its `issued` rows."""
    me, live, counts = _fold(email), set(), []
    for r in rows:
        if r.get("event") == "issued" and _fold(r.get("email") or "") == me:
            live.add(r["key_hash"])
            counts.append(len(live))
        elif r.get("event") == "revoked":
            live.discard(r.get("key_hash"))
    return counts


def _issue_key(base: str, who: dict) -> str:
    code, body, _ = post(base, "/api/me/api-key", who)
    assert code == 200 and body.get("api_key", "").startswith("orpho_"), (code, body)
    return body["api_key"]


def test_members_of_a_mixed_case_owner_inherit_the_subscription(srv):
    """Defect 7. Owner and Stripe row both "Alice...@Example.Test". The
    member's /api/me must say the subscription is active, and must stop
    saying so once the owner's subscription ends (so the check is not true
    for everyone)."""
    base, data = srv
    tag = secrets.token_hex(3)
    owner_email = f"Alice.{tag}@Example.Test"
    owner = account(data, owner_email)
    member = account(data, f"bob.{tag}@example.test", subscribed=False)
    tid = _create(base, owner, "Mixed Co")
    code, body, _ = post(base, "/api/me/team/redeem", member, {"invite_code": _invite(base, owner)})
    assert code == 200 and body.get("team_id") == tid, body

    code, me = get(base, "/api/me", member)
    assert code == 200 and me.get("team_role") == "member", me
    assert me.get("subscription_active") is True, (
        "a member of a subscribed mixed-case owner inherited nothing", me)
    # The page shows the stored owner; the sign-in spelling stays server-side.
    assert me["team"]["owner"] == owner_email.lower() and "owner_spelling" not in me["team"], me

    # End the owner's OWN subscription (account() names it "sub_" + the
    # session id). This row used a different id, which only read as an ending
    # while "active" meant the newest row of any subscription; since
    # 2026-09-27 each subscription is judged by its own rows.
    owner_sub = "sub_" + owner["Cookie"].split("orpho_sid=", 1)[1]
    _append_rows(data / "subscriptions.jsonl", [dict(
        email=owner_email, status="canceled", stripe_sub=owner_sub)])
    code, me = get(base, "/api/me", member)
    assert code == 200 and me.get("subscription_active") is False, me


def test_members_of_a_lowercase_owner_still_inherit(srv):
    """Defect 7, control: the spelling every team had before must keep working."""
    base, data = srv
    tag = secrets.token_hex(3)
    owner = account(data, f"carol.{tag}@example.test")
    member = account(data, f"dan.{tag}@example.test", subscribed=False)
    tid = _create(base, owner, "Lower Co")
    code, body, _ = post(base, "/api/me/team/redeem", member, {"invite_code": _invite(base, owner)})
    assert code == 200 and body.get("team_id") == tid, body
    code, me = get(base, "/api/me", member)
    assert code == 200 and me.get("subscription_active") is True, me


def test_two_spellings_of_one_mailbox_hold_one_live_key(srv):
    """Defect 8. Keys issued from either spelling replace each other, and a
    revoke from either spelling kills whichever is live. Three issuances
    stay inside the per-account budget of 5 the spellings share."""
    base, data = srv
    tag = secrets.token_hex(3)
    upper_email = f"Alice.{tag}@Example.Test"
    upper = account(data, upper_email)
    lower = account(data, upper_email.lower())

    k1 = _issue_key(base, upper)
    k2 = _issue_key(base, lower)
    assert (key_status(base, k1), key_status(base, k2)) == (401, 200), (
        "a key issued under one spelling survived an issue under the other")
    for who in (upper, lower):
        code, me = get(base, "/api/me", who)
        assert code == 200 and me.get("api_key_prefix") == k2[:14], me

    k3 = _issue_key(base, upper)
    assert (key_status(base, k2), key_status(base, k3)) == (401, 200)

    code, body, _ = post(base, "/api/me/api-key/revoke", lower)
    assert code == 200 and body.get("revoked") is True, body
    assert [key_status(base, k) for k in (k1, k2, k3)] == [401, 401, 401], (
        "a revoke from one spelling left the other spelling's key live")
    for who in (upper, lower):
        code, me = get(base, "/api/me", who)
        assert code == 200 and me.get("api_key_prefix") == "", me
    code, body, _ = post(base, "/api/me/api-key/revoke", upper)
    assert code == 200 and body.get("revoked") is False, body

    counts = live_counts_across_spellings(_read_rows(data / "api_keys.jsonl"), upper_email)
    assert counts == [1, 1, 1], counts


def test_kelvin_sign_spelling_does_not_own_the_team(srv):
    """Defect 9, teams. A Kelvin-sign spelling of karl's address is another
    mailbox. Since 2026-10-03 such a spelling holds no session at all
    (auth.session_email), so it reaches none of karl's team; a team row it
    created before then is still not stored as karl's."""
    base, data = srv
    tag = secrets.token_hex(3)
    karl_email = f"karl.{tag}@example.test"
    kelvin_email = f"{KELVIN}arl.{tag}@example.test"
    assert kelvin_email.lower() == karl_email  # the collision this test is about
    karl = account(data, karl_email)
    kelvin = account(data, kelvin_email)
    member_email = f"mia.{tag}@example.test"
    member = account(data, member_email, subscribed=False)
    tid = _create(base, karl, "Karl Co")
    code, body, _ = post(base, "/api/me/team/redeem", member, {"invite_code": _invite(base, karl)})
    assert code == 200 and body.get("team_id") == tid, body

    code, body = get(base, "/api/me/team", kelvin)
    assert code == 401, (code, body)
    for path, payload in (("/api/me/team/remove", {"member_email": member_email}),
                          ("/api/me/team/invite", None), ("/api/me/team/create", {"team_name": "K"})):
        code, body, _ = post(base, path, kelvin, payload)
        assert code == 401, (path, code, body)
    code, body = get(base, "/api/me/team", member)
    assert body.get("team", {}).get("team_id") == tid, body

    # A team the Kelvin spelling of another address created before the fix:
    # that address (with no team of its own, so the answer can tell) does not
    # own it. Review round 1 of PR #286: with karl's own team created first,
    # this held even when the Kelvin team was treated as karl's.
    other_email = f"kurt.{tag}@example.test"
    other = account(data, other_email)
    ktid = "team_" + secrets.token_urlsafe(10)
    _append_rows(data / "teams.jsonl", [dict(
        ts="2026-09-25T00:00:00+00:00", event="create", team_id=ktid,
        owner_email=KELVIN + other_email[1:], team_name="Kelvin Co")])
    code, body = get(base, "/api/me/team", other)
    assert code == 200 and body.get("team") is None, body


def test_kelvin_sign_spelling_has_its_own_key_and_budget(srv):
    """Defect 9, keys. The Kelvin-sign spelling can no longer issue keys (it
    holds no session), a key issued to it before 2026-10-03 is dead, and none
    of that touches karl's key or budget."""
    base, data = srv
    tag = secrets.token_hex(3)
    karl = account(data, f"karl.{tag}@example.test")
    kelvin_email = f"{KELVIN}arl.{tag}@example.test"
    kelvin = account(data, kelvin_email)
    karl_key = _issue_key(base, karl)

    code, body, _ = post(base, "/api/me/api-key", kelvin)
    assert code == 401, (code, body)
    old_kelvin_key = "orpho_" + secrets.token_urlsafe(24)
    _append_rows(data / "api_keys.jsonl", [issued_row(kelvin_email, old_kelvin_key)])
    assert (key_status(base, karl_key), key_status(base, old_kelvin_key)) == (200, 401)

    code, body, _ = post(base, "/api/me/api-key", karl)
    assert code == 200, ("karl was limited by another spelling's issuances", code, body)


def test_owner_removes_the_kelvin_spelling_not_the_plain_member(srv):
    """Defect 9, remove. The two spellings are two members. The route used to
    lowercase the address before remove, which turned the Kelvin-sign one
    into the plain one, so the wrong person was removed. The Kelvin member
    here joined before 2026-10-03 (its join row is written directly: such a
    spelling can no longer sign in to redeem an invite)."""
    base, data = srv
    tag = secrets.token_hex(3)
    owner = account(data, f"olga.{tag}@example.test")
    plain_email = f"karl.{tag}@example.test"
    kelvin_email = f"{KELVIN}arl.{tag}@example.test"
    plain = account(data, plain_email, subscribed=False)
    tid = _create(base, owner, "Two Karls")
    code, body, _ = post(base, "/api/me/team/redeem", plain, {"invite_code": _invite(base, owner)})
    assert code == 200 and body.get("team_id") == tid, body
    _append_rows(data / "teams.jsonl", [dict(
        ts="2026-09-25T00:00:00+00:00", event="join", team_id=tid, member_email=kelvin_email)])

    code, body, _ = post(base, "/api/me/team/remove", owner, {"member_email": kelvin_email})
    assert code == 200 and body.get("ok") is True, body
    members = [r for r in _read_rows(data / "teams.jsonl")
               if r.get("team_id") == tid and r.get("event") == "remove"]
    assert [r.get("member_email") for r in members] == [kelvin_email], members
    code, body = get(base, "/api/me/team", plain)
    assert body.get("team", {}).get("team_id") == tid, ("the plain-k member was removed", body)


@pytest.mark.parametrize("damage", ["missing", "truncated", "owner_not_text"])
def test_member_can_leave_a_team_whose_create_row_is_damaged(srv, damage):
    """Defect 10. With no readable create row the team's owner reduces to "",
    and leave went through the owner-gated remove, so the member was stuck:
    ok:false on every leave and no way into another team."""
    base, data = srv
    tid = "team_" + secrets.token_urlsafe(10)
    member_email = fresh("stranded")
    member = account(data, member_email, subscribed=False)
    lines = []
    if damage == "truncated":
        lines.append('{"ts": %d, "event": "create", "team_id": "%s", "owner_em' % (time.time(), tid))
    elif damage == "owner_not_text":
        lines.append(json.dumps(dict(ts=time.time(), event="create", team_id=tid,
                                     owner_email=42, name="Broken")))
    lines.append(json.dumps(dict(ts=time.time(), event="join", team_id=tid,
                                 member_email=member_email)))
    with (data / "teams.jsonl").open("a") as f:
        for line in lines:
            f.write(line + "\n")  # a line without its newline would fuse with the next append

    code, body = get(base, "/api/me/team", member)
    assert body.get("team", {}).get("team_id") == tid and body.get("role") == "member", body
    code, body, _ = post(base, "/api/me/team/leave", member)
    assert code == 200 and body.get("ok") is True, body
    code, body = get(base, "/api/me/team", member)
    assert code == 200 and body.get("team") is None, body

    other = account(data, fresh("rescuer"))
    other_tid = _create(base, other)
    code, body, _ = post(base, "/api/me/team/redeem", member, {"invite_code": _invite(base, other)})
    assert code == 200 and body.get("team_id") == other_tid, body


@pytest.fixture
def team_ledgers(tmp_path, monkeypatch):
    """The teams module on ledgers under tmp_path with a 3-seat cap. Both
    ledger paths are patched: test_teams.py re-imports the module, so a fresh
    import can point at the checkout's real data/ directory."""
    import teams
    monkeypatch.setattr(teams, "TEAMS_LEDGER", tmp_path / "teams.jsonl")
    monkeypatch.setattr(teams, "INVITES_LEDGER", tmp_path / "team_invites.jsonl")
    monkeypatch.setattr(teams, "MAX_TEAM_MEMBERS", MAX_MEMBERS)
    assert teams.TEAMS_LEDGER.parent == tmp_path and teams.INVITES_LEDGER.parent == tmp_path
    return teams


def _slowed(monkeypatch, module, name: str, delay: float = 0.05) -> None:
    """Make module.name sleep before it runs. Placed between a check and the
    write that depends on it, this holds the race window open, so a missing
    lock fails every run instead of now and then."""
    real = getattr(module, name)

    def slow(*args, **kwargs):
        time.sleep(delay)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, slow)


def _together(n: int, work) -> list:
    barrier = threading.Barrier(n)
    out: list = [None] * n

    def run(i: int) -> None:
        barrier.wait()
        try:
            out[i] = ("ok", work(i))
        except BaseException as e:  # noqa: BLE001 - asserted by the caller
            out[i] = ("raised", e)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def test_concurrent_invites_never_outnumber_free_seats(team_ledgers, monkeypatch):
    """Defect 11, the invite lock. Twelve owners' clicks at once on a 3-seat
    team: exactly 3 invites, the rest refused. _new_invite_code runs between
    the seat count and the append, so slowing it lets every thread count
    zero open invites first when nothing serialises them."""
    teams = team_ledgers
    owner = "seats@example.test"
    tid = teams.create_team(owner, "Seats")
    _slowed(monkeypatch, teams, "_new_invite_code")
    n = 12
    out = _together(n, lambda i: teams.issue_invite(tid, owner))
    issued = [v for kind, v in out if kind == "ok"]
    refused = [v for kind, v in out if kind == "raised" and isinstance(v, teams.InviteLimitReached)]
    assert all(issued) and len(issued) + len(refused) == n, out
    rows = [r for r in _read_rows(teams.INVITES_LEDGER)
            if r.get("event") == "issue" and r.get("team_id") == tid]
    assert (len(issued), len(rows)) == (MAX_MEMBERS, MAX_MEMBERS), (
        f"{len(issued)} invites issued for {MAX_MEMBERS} free seats")


def test_concurrent_redeems_never_overfill_a_team(team_ledgers, monkeypatch):
    """Defect 11, the redeem lock. A 3-seat team with 2 members and ten open
    invites (ledgers written before the invite cap hold such teams): ten
    redeems at once must admit exactly one. The reducer would silently drop
    the extra joins, so an unlocked redeem told them ok:true and gave them
    nothing."""
    teams = team_ledgers
    owner = "full@example.test"
    tid = teams.create_team(owner, "Nearly full")
    for i in range(MAX_MEMBERS - 1):
        code_ = teams.issue_invite_code(tid, owner)
        assert teams.redeem_invite_code(code_, f"m{i}@example.test").get("ok")
    now = time.time()
    codes = ["tinv_" + secrets.token_urlsafe(12) for _ in range(10)]
    _append_rows(teams.INVITES_LEDGER, [
        dict(ts=now, event="issue", team_id=tid, invite_code=c, issued_by=owner,
             expires_at=now + WEEK) for c in codes])
    _slowed(monkeypatch, teams, "_append")

    out = _together(len(codes), lambda i: teams.redeem_invite_code(codes[i], f"late{i}@example.test"))
    assert all(kind == "ok" for kind, _ in out), out
    results = [v for _, v in out]
    assert sum(1 for r in results if r.get("ok")) == 1, results
    assert {r.get("error") for r in results if not r.get("ok")} == {"team is full"}, results
    joins = [r for r in _read_rows(teams.TEAMS_LEDGER)
             if r.get("event") == "join" and r.get("team_id") == tid]
    assert len(joins) == MAX_MEMBERS, f"{len(joins)} join rows for {MAX_MEMBERS} seats"


def test_a_stored_spelling_of_another_mailbox_is_ignored(team_ledgers):
    """Defect 7, the guard on the new field. Members inherit through the
    create row's owner_spelling, so a row whose spelling is not the owner's
    own address (hand-edited, or damaged) must not steer inheritance to that
    other mailbox's subscription."""
    teams = team_ledgers
    tid = "team_" + secrets.token_urlsafe(10)
    _append_rows(teams.TEAMS_LEDGER, [
        dict(ts=time.time(), event="create", team_id=tid, owner_email="owner@example.test",
             owner_spelling="Victim@Example.Test", name="Steered"),
        dict(ts=time.time(), event="join", team_id=tid, member_email="m@example.test"),
    ])
    assert teams.owner_email_for("m@example.test") == "owner@example.test"
    own = "team_" + secrets.token_urlsafe(10)
    _append_rows(teams.TEAMS_LEDGER, [
        dict(ts=time.time(), event="create", team_id=own, owner_email="ann@example.test",
             owner_spelling="Ann@Example.Test", name="Own"),
        dict(ts=time.time(), event="join", team_id=own, member_email="n@example.test"),
    ])
    assert teams.owner_email_for("n@example.test") == "Ann@Example.Test"


def test_after_a_redeem_the_owner_can_fill_exactly_the_free_seats(srv):
    """Defect 11, the seat arithmetic. A redeemed invite holds no seat: with
    one member and no open invite, the owner can issue exactly MAX-1 more.
    Counting the redeemed invite as open refused the last one with 409."""
    base, data = srv
    owner = account(data, fresh("owner11"))
    tid = _create(base, owner)
    joiner = account(data, fresh("joiner11"), subscribed=False)
    code, body, _ = post(base, "/api/me/team/redeem", joiner, {"invite_code": _invite(base, owner)})
    assert code == 200 and body.get("team_id") == tid, body
    for _ in range(MAX_MEMBERS - 1):
        _invite(base, owner)
    code, body, _ = post(base, "/api/me/team/invite", owner)
    assert code == 409, (code, body)


@pytest.mark.parametrize("fields", [
    {"expires_at": float("nan")},
    {"expires_at": float("inf")},
    {"expires_at": "2099-01-01T00:00:00Z"},
    {"expires_at": None},
    {"expires_at": True},
    {"ts": float("nan")},
    {"ts": "2026-09-25T00:00:00Z"},
], ids=["expires-nan", "expires-inf", "expires-text", "expires-null", "expires-bool",
        "legacy-ts-nan", "legacy-ts-text"])
def test_invite_with_an_unreadable_expiry_is_refused(srv, fields):
    """Defect 11, NaN. A NaN expiry compares false against every clock
    reading, so letting it through meant an invite that never expires. The
    rows with `expires_at` have a fresh `ts`: a row that carries an expiry is
    judged on it, not on when it was issued."""
    base, data = srv
    owner_email = fresh("owner11n")
    owner = account(data, owner_email)
    tid = _create(base, owner)
    code_ = "tinv_" + secrets.token_urlsafe(12)
    row = dict(ts=time.time(), event="issue", team_id=tid, invite_code=code_,
               issued_by=owner_email)
    row.update(fields)
    _append_rows(data / "team_invites.jsonl", [row])

    joiner = account(data, fresh("joiner11n"), subscribed=False)
    code, body, _ = post(base, "/api/me/team/redeem", joiner, {"invite_code": code_})
    assert code == 400 and "expired" in (body.get("error") or ""), (code, body)
    code, body = get(base, "/api/me/team", joiner)
    assert body.get("team") is None, body


# Drives the real web/account.js in node: a DOM stub just wide enough for
# renderTeam, a fetch that answers the invite POST as given and never answers
# anything else (so main() stays idle), then one click on the invite button.
_INVITE_CLICK_JS = r"""
const fs = require("fs");
const vm = require("vm");
const [src, status, body] = process.argv.slice(2);
function mk() {
  const handlers = {};
  return {
    hidden: false, textContent: "", style: {}, dataset: {}, className: "", type: "",
    handlers,
    addEventListener(ev, fn) { handlers[ev] = fn; },
    replaceChildren() { this.textContent = ""; },
    appendChild(c) { this.textContent += c.textContent || ""; return c; },
    querySelector() { return mk(); },
    querySelectorAll() { return []; },
  };
}
const els = new Map();
const el = (sel) => { if (!els.has(sel)) els.set(sel, mk()); return els.get(sel); };
const document = {
  querySelector: el,
  querySelectorAll: () => [],
  getElementById: (id) => el("#" + id),
  createElement: () => mk(),
  addEventListener() {},
};
const fetch = (url) => {
  if (url === "/api/me/team/invite") {
    const code = Number(status);
    return Promise.resolve({ ok: code >= 200 && code < 300, status: code,
                             json: async () => JSON.parse(body) });
  }
  return new Promise(() => {});
};
const ctx = vm.createContext({ document, fetch, console, setTimeout, clearTimeout,
                               location: { reload() {} }, navigator: {}, window: {},
                               confirm: () => true, URLSearchParams, Date });
vm.runInContext(fs.readFileSync(src, "utf8"), ctx);
(async () => {
  ctx.renderTeam({ team: { team_id: "team_x", name: "T", owner: "o@example.test", members: [] },
                   team_role: "owner", subscription_active: true });
  await el("#team-invite-btn").handlers.click();
  const r = el("#team-invite-result");
  process.stdout.write(JSON.stringify({ text: r.textContent, hidden: r.hidden }));
})().catch((e) => { console.error((e && e.stack) || String(e)); process.exit(1); });
"""


def _click_invite(tmp_path: Path, status: int, body: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH: the account.js invite-message check runs the real script")
    driver = tmp_path / "invite_click.js"
    driver.write_text(_INVITE_CLICK_JS)
    proc = subprocess.run(
        [node, str(driver), str(Path(__file__).resolve().parent.parent / "web" / "account.js"),
         str(status), body],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["hidden"] is False, out
    return out["text"]


def test_account_page_shows_why_an_invite_was_refused(srv, tmp_path):
    """Defect 12. The refusal the owner sees is the server's own reason. The
    409 body is taken from the running server, so the page and the route
    are checked against each other, not against a copy of the message."""
    base, data = srv
    owner = account(data, fresh("owner12"))
    _create(base, owner)
    for _ in range(MAX_MEMBERS):
        _invite(base, owner)
    code, full, _ = post(base, "/api/me/team/invite", owner)
    assert code == 409 and full.get("error"), (code, full)

    shown = _click_invite(tmp_path, 409, json.dumps(full))
    assert shown == full["error"], shown
    assert "subscription" not in shown.lower(), shown

    no_sub = {"error": "active subscription required to issue invites"}
    assert _click_invite(tmp_path, 402, json.dumps(no_sub)) == no_sub["error"]

    # A proxy error page is not JSON: a fixed line, and no guess at the cause.
    shown = _click_invite(tmp_path, 502, "<html>Bad Gateway</html>")
    assert shown.startswith("Could not issue invite") and "subscription" not in shown, shown

    ok = {"ok": True, "invite_code": "tinv_x", "share_url": "/team/join?code=tinv_x"}
    assert _click_invite(tmp_path, 200, json.dumps(ok)).endswith("/team/join?code=tinv_x")
