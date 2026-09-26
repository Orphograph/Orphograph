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

Accounts are seeded by writing the same JSONL rows sign-in and the Stripe
webhook write. Each test uses its own emails, so the module can share one
server and no test depends on another's state.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import secrets
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
