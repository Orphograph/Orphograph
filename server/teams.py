#!/usr/bin/env python3
"""teams.py — minimal team accounts for B2B.

A team has one owner and zero or more members. Members inherit the owner's
subscription benefits (rate-limit bypass, private receipts, API key
issuance, receipt vault visibility).

Append-only JSONL ledger keyed on (team_id, event). Reading the ledger
reduces to a {team_id: {owner, name, members}} map. Idempotent: replaying
the same event ID is a no-op.

Public API:
    create_team(owner_email, team_name) -> team_id
    issue_invite(team_id, owner_email) -> {invite_code, expires_at} | None
    issue_invite_code(team_id, owner_email) -> invite_code | None
    redeem_invite_code(invite_code, joiner_email) -> dict
    remove_member(team_id, owner_email, member_email) -> bool
    leave_team(member_email) -> bool
    team_for_email(email) -> dict | None     # the team this email belongs to
    team_for_member(email) -> dict | None    # the team where email is OWNER OR MEMBER
    is_owner(team, email) -> bool            # ownership, compared case-insensitively
    owner_email_for(email) -> str | None
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import secrets
import sys
import threading
import time
from pathlib import Path

from email_fold import fold_email
from file_lock import locked

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
TEAMS_LEDGER = Path(os.environ.get(
    "ORPHO_TEAMS_LEDGER", str(DATA_DIR / "teams.jsonl")
))
INVITES_LEDGER = Path(os.environ.get(
    "ORPHO_TEAM_INVITES_LEDGER", str(DATA_DIR / "team_invites.jsonl")
))

# Soft cap on members per team. Avoids accidental ledger blowup if an
# invite code is reused. Founder can raise via env var.
MAX_TEAM_MEMBERS = int(os.environ.get("ORPHO_MAX_TEAM_MEMBERS", "25"))

# An invite can be redeemed for this long after it is issued. Invites used
# to live forever, and nothing bounded how many a team could hold, so one
# owner minted 3,000 in 7 seconds and the first stayed redeemable (2026-09-25).
# Seven days covers a colleague who is away for a week; the owner can issue
# a fresh one after that.
INVITE_TTL_SECONDS = 7 * 24 * 3600

_state_lock = threading.RLock()  # reentrant so nested helpers can re-acquire


class InviteLimitReached(Exception):
    """Every free seat in the team already has an open invite."""


def _same(email) -> str:
    """The form two emails are compared in, and the form create and redeem
    store. Sign-in keeps the case the person typed, so an exact comparison
    refused a mixed-case owner on their own team (403 on invite and remove,
    shown as "member") and answered a mixed-case member's leave with ok:false.
    Every ownership and membership check compares this form on both sides.

    email_fold folds ASCII A-Z only. str.lower() turned U+212A KELVIN SIGN
    into "k", so a Kelvin-sign spelling of "karl@x" counted as the owner of
    karl@x's team and could remove its members; casefold() would also merge
    "ß" with "ss". Rows written before this used str.lower(), which is the
    same thing for every ASCII address."""
    return fold_email(email)


def _lock_path() -> Path:
    """Sibling .lock of the teams ledger. Resolved at call time so an
    override of TEAMS_LEDGER moves the lock with it. A separate file from
    either ledger because _append flocks the ledger itself, and a second
    flock on the same file from this process would wait forever."""
    return TEAMS_LEDGER.with_suffix(TEAMS_LEDGER.suffix + ".lock")


@contextlib.contextmanager
def _ledger_lock():
    """One critical section for every read-check-append on team state:
    create_team, issue_invite, redeem_invite_code, remove_member and
    leave_team. The threading lock covers handler threads in one process;
    the file lock covers processes and machines that share the data volume.
    Taking both in the same order everywhere, and never calling one of those
    five from inside another, is what keeps it free of deadlock: the RLock
    re-enters, the flock does not."""
    with _state_lock, locked(_lock_path(), exclusive=True):
        yield


def _as_time(value) -> float | None:
    """A unix time from a ledger field, or None. bool is excluded (it is an
    int), and so are NaN and infinity: a NaN expiry compares false against
    every clock reading, so it would never expire."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _invite_expiry(ev: dict) -> float:
    """When an issued invite stops working. Rows written since invites got a
    TTL carry `expires_at`; older rows lapse INVITE_TTL_SECONDS after they
    were issued. A time that cannot be read counts as already expired: an
    invite that cannot show it is fresh is not.

    A row that carries `expires_at` is judged on it alone. Falling back to
    `ts` when it is NaN or garbage let a damaged new row live another week
    on its issue time; only a row with no `expires_at` at all is an old one."""
    if "expires_at" in ev:
        stored = _as_time(ev.get("expires_at"))
        return stored if stored is not None else 0.0
    issued = _as_time(ev.get("ts"))
    return issued + INVITE_TTL_SECONDS if issued is not None else 0.0


def _now() -> float:
    return time.time()


def _new_team_id() -> str:
    return "team_" + secrets.token_urlsafe(10)


def _new_invite_code() -> str:
    return "tinv_" + secrets.token_urlsafe(12)


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path, mode="a", exclusive=True) as f:
        f.write(json.dumps(row) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError as e:
        # Don't fail the write — but log so a permissions regression that
        # leaves team-member emails world-readable becomes visible.
        sys.stderr.write(f"[teams] chmod 0600 failed on {path}: {e}\n")


def _read_all(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    try:
        with path.open() as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as e:
                    # A truncated/corrupted line silently dropped here would
                    # erase team membership state. Log loudly so the founder
                    # sees a corrupt ledger before it causes mystery sub-
                    # benefit losses for paying customers.
                    sys.stderr.write(
                        f"[teams] corrupt JSON in {path}:{line_num}: {e}; line dropped\n"
                    )
                    continue
    except OSError as e:
        # An ENOENT we already handled above. Anything else (permissions,
        # I/O) is operationally significant: returning [] would silently
        # downgrade members off their subscription.
        sys.stderr.write(f"[teams] could not read {path}: {e}\n")
    return out


def _team_state() -> dict[str, dict]:
    """Reduce the teams ledger to {team_id: {owner, name, members:set, created_at, deleted}}.

    Membership is the set of emails currently in the team. Owner is the
    creator; can re-issue invites, remove members, dissolve.
    """
    with _state_lock:
        events = _read_all(TEAMS_LEDGER)
    teams: dict[str, dict] = {}
    for ev in events:
        et = ev.get("event")
        tid = ev.get("team_id")
        if not tid:
            continue
        t = teams.setdefault(tid, {
            "team_id": tid,
            "owner": "",
            "name": "",
            "members": set(),
            "created_at": ev.get("ts", ""),
            "deleted": False,
        })
        if et == "create":
            owner = ev.get("owner_email")
            t["owner"] = owner if isinstance(owner, str) else ""
            # The owner's address as they signed in, which is the spelling
            # their Stripe row carries. Taken only when it is a spelling of
            # the stored owner, so a damaged row cannot point members'
            # inheritance at another mailbox's subscription.
            spelling = ev.get("owner_spelling")
            t["owner_spelling"] = (
                spelling if isinstance(spelling, str) and spelling.strip()
                and _same(spelling) == _same(t["owner"]) else t["owner"])
            t["name"] = ev.get("name", "")
            t["created_at"] = ev.get("ts", t["created_at"])
        elif et == "join":
            email = ev.get("member_email", "")
            if email and email != t["owner"] and len(t["members"]) < MAX_TEAM_MEMBERS:
                t["members"].add(email)
        elif et == "remove":
            email = ev.get("member_email", "")
            t["members"].discard(email)
        elif et == "delete":
            t["deleted"] = True
            t["members"] = set()
    return teams


def _invite_state() -> dict[str, dict]:
    """Reduce invite ledger to {invite_code: {team_id, created_at, expires_at, redeemed_by, redeemed_at}}."""
    events = _read_all(INVITES_LEDGER)
    invites: dict[str, dict] = {}
    for ev in events:
        et = ev.get("event")
        code = ev.get("invite_code")
        if not code:
            continue
        i = invites.setdefault(code, {
            "invite_code": code,
            "team_id": "",
            "created_at": "",
            "expires_at": 0.0,
            "redeemed_by": "",
            "redeemed_at": "",
        })
        if et == "issue":
            i["team_id"] = ev.get("team_id", "")
            i["created_at"] = ev.get("ts", "")
            i["expires_at"] = _invite_expiry(ev)
        elif et == "redeem":
            i["redeemed_by"] = ev.get("member_email", "")
            i["redeemed_at"] = ev.get("ts", "")
    return invites


# ── public API ─────────────────────────────────────────────────────────


def create_team(owner_email: str, team_name: str) -> str:
    """Create a new team. Returns the team_id.

    One team per owner: an owner who already has an active team gets its id
    back and no second team is written, so a double click on "Create" still
    lands on one team. A member of another team is refused (ValueError) until
    they leave it, the same rule redeem applies; before, such a member ended
    up in two teams, and after leaving the first a team they never meant to
    own took over their account view.

    The check and the append share one lock with redeem: 30 simultaneous
    creates for one owner used to write up to 3 teams (2026-09-25).

    The row stores the owner in the _same form and, beside it, the spelling
    the owner signed in with. Members inherit through that spelling:
    subscriptions.is_active matches the Stripe row's spelling exactly, so an
    owner signed in as "Alice@Example.com" with a Stripe row to match could
    fill seats while every member got nothing (2026-09-26).
    """
    spelling = owner_email.strip() if isinstance(owner_email, str) else ""
    owner_email = _same(spelling)
    team_name = (team_name or "").strip()[:80] or "Team"
    if not owner_email or "@" not in owner_email:
        raise ValueError("invalid owner email")
    me = owner_email
    with _ledger_lock():
        owned = member_of = None
        for t in _team_state().values():
            if t.get("deleted"):
                continue
            if _same(t.get("owner")) == me:
                owned = owned or t
            elif any(_same(m) == me for m in t.get("members", set())):
                member_of = member_of or t
        if owned:
            return owned["team_id"]
        if member_of:
            raise ValueError("you must leave your current team first")
        team_id = _new_team_id()
        _append(TEAMS_LEDGER, {
            "ts": _now(),
            "event": "create",
            "team_id": team_id,
            "owner_email": owner_email,
            "owner_spelling": spelling,
            "name": team_name,
        })
        return team_id


def issue_invite(team_id: str, owner_email: str) -> dict | None:
    """Issue a single-use invite for a team. Only the owner can issue.

    Returns {"invite_code", "expires_at"} with expires_at in unix seconds, the
    same instant redeem enforces, or None if the caller isn't the owner.
    Raises InviteLimitReached when every free seat (MAX_TEAM_MEMBERS minus
    current members) already has an open invite. An invite stops holding a
    seat once it is redeemed or expires.
    """
    if not _same(owner_email):
        return None
    with _ledger_lock():
        t = _team_state().get(team_id)
        if not t or t.get("deleted") or _same(t.get("owner")) != _same(owner_email):
            return None
        now = _now()
        free_seats = MAX_TEAM_MEMBERS - len(t.get("members", set()))
        open_invites = sum(
            1 for inv in _invite_state().values()
            if inv.get("team_id") == team_id and not inv.get("redeemed_by")
            and inv.get("expires_at", 0.0) > now
        )
        if open_invites >= free_seats:
            raise InviteLimitReached(
                "team is full" if free_seats <= 0 else
                f"every free seat already has an open invite ({open_invites}); an "
                f"unredeemed invite frees its seat {INVITE_TTL_SECONDS // 86400} days "
                "after it was issued")
        code = _new_invite_code()
        expires_at = now + INVITE_TTL_SECONDS
        _append(INVITES_LEDGER, {
            "ts": now,
            "event": "issue",
            "team_id": team_id,
            "invite_code": code,
            "issued_by": owner_email,
            "expires_at": expires_at,
        })
        return {"invite_code": code, "expires_at": expires_at}


def issue_invite_code(team_id: str, owner_email: str) -> str | None:
    """issue_invite for callers that need only the code. Returns None if the
    caller isn't the owner; raises InviteLimitReached like issue_invite."""
    issued = issue_invite(team_id, owner_email)
    return issued["invite_code"] if issued else None


def redeem_invite_code(invite_code: str, joiner_email: str) -> dict:
    """Redeem an invite code. Returns {ok: bool, team_id?, error?}.

    Atomic: the cap check, double-redeem check, and the two ledger appends
    all happen under `_ledger_lock`. Without the lock two concurrent redeems
    could both pass the cap check; the 26th would be silently dropped by
    the reducer ("ghost member") because the reducer enforces MAX_TEAM_MEMBERS.
    The same lock guards create_team, so a redeem and a create by one person
    cannot both pass the "already in a team" check. Its file half extends
    that to other processes sharing the data volume.
    """
    joiner_email = _same(joiner_email)
    invite_code = (invite_code or "").strip()
    if not joiner_email or "@" not in joiner_email:
        return {"ok": False, "error": "invalid joiner email"}
    me = joiner_email
    with _ledger_lock():
        invites = _invite_state()
        inv = invites.get(invite_code)
        if not inv or not inv.get("team_id"):
            return {"ok": False, "error": "invalid invite code"}
        if inv.get("redeemed_by"):
            return {"ok": False, "error": "invite code already redeemed"}
        if _now() >= inv.get("expires_at", 0.0):
            return {"ok": False, "error": "invite code expired"}
        team_id = inv["team_id"]
        teams_state = _team_state()
        t = teams_state.get(team_id)
        if not t or t.get("deleted"):
            return {"ok": False, "error": "team no longer exists"}
        if _same(t.get("owner")) == me:
            return {"ok": False, "error": "owner cannot redeem own invite"}
        if any(_same(m) == me for m in t.get("members", set())):
            return {"ok": False, "error": "already a member"}
        if len(t.get("members", set())) >= MAX_TEAM_MEMBERS:
            return {"ok": False, "error": "team is full"}
        # If joiner is in a different team, they must leave first.
        for other in teams_state.values():
            if other.get("deleted") or other.get("team_id") == team_id:
                continue
            if _same(other.get("owner")) == me or any(
                    _same(m) == me for m in other.get("members", set())):
                return {"ok": False, "error": "you must leave your current team first"}
        _append(INVITES_LEDGER, {
            "ts": _now(),
            "event": "redeem",
            "team_id": team_id,
            "invite_code": invite_code,
            "member_email": joiner_email,
        })
        _append(TEAMS_LEDGER, {
            "ts": _now(),
            "event": "join",
            "team_id": team_id,
            "member_email": joiner_email,
        })
        return {"ok": True, "team_id": team_id}


def _append_removes(t: dict, member_email: str) -> bool:
    """Write a remove row for every stored spelling of `member_email` in team
    `t`. The reducer drops a member by exact string, so each row carries the
    spelling a join row stored, whatever case the caller used. Called with
    _ledger_lock held."""
    stored = sorted(m for m in t.get("members", set()) if _same(m) == _same(member_email))
    for m in stored:
        _append(TEAMS_LEDGER, {
            "ts": _now(),
            "event": "remove",
            "team_id": t["team_id"],
            "member_email": m,
        })
    return bool(stored)


def remove_member(team_id: str, owner_email: str, member_email: str) -> bool:
    if not _same(owner_email) or not _same(member_email):
        return False
    with _ledger_lock():
        t = _team_state().get(team_id)
        if not t or t.get("deleted") or _same(t.get("owner")) != _same(owner_email):
            return False
        return _append_removes(t, member_email)


def leave_team(member_email: str) -> bool:
    """The member writes their own remove row. Leaving used to go through
    remove_member with the owner's address as the credential, so a team whose
    create row was missing or damaged, which reduces to owner "", kept its
    members forever: nobody could pass as that owner, and a member cannot
    join another team until they leave (2026-09-26)."""
    me = _same(member_email)
    if not me:
        return False
    with _ledger_lock():
        t = _team_of(me)
        if not t or _same(t.get("owner")) == me:
            return False
        return _append_removes(t, me)


def _team_of(me: str) -> dict | None:
    """The first active team where `me` (already in _same form) is the owner
    or a member, as the reducer holds it, or None."""
    if not me:
        return None
    for t in _team_state().values():
        if t.get("deleted"):
            continue
        if _same(t.get("owner")) == me or any(_same(m) == me for m in t.get("members", set())):
            return t
    return None


def team_for_member(email: str) -> dict | None:
    """Return the team where `email` is the owner OR a member, or None."""
    t = _team_of(_same(email))
    return _serialize(t) if t else None


def is_owner(team: dict | None, email: str | None) -> bool:
    """True if `email` owns `team`. The route handlers ask this instead of
    comparing strings themselves: the session keeps the case the person
    typed and the team stores it lowercased, see _same."""
    return bool(team) and bool(_same(email)) and _same(team.get("owner")) == _same(email)


def team_for_email(email: str) -> dict | None:
    """Alias kept for callsite clarity."""
    return team_for_member(email)


def owner_email_for(email: str) -> str | None:
    """Return the subscription-bearing owner email for this email, or None.

    If `email` is the owner, returns the owner's address. If `email` is a
    team member, returns the team owner's address. Otherwise None.

    The address comes back in the spelling the owner signed in with when the
    team was created, because the caller hands it to subscriptions.is_active,
    which matches the Stripe row's spelling exactly. The stored owner is
    lowercased, so a mixed-case owner's members inherited nothing. Rows
    written before the spelling was kept give the stored owner.
    """
    t = _team_of(_same(email))
    if not t:
        return None
    return t.get("owner_spelling") or t.get("owner")


def _serialize(t: dict) -> dict:
    """Convert the in-memory set to a list for JSON output. The owner's
    sign-in spelling stays server-side: `owner` is what the pages show."""
    out = dict(t)
    out.pop("owner_spelling", None)
    out["members"] = sorted(t.get("members", set()))
    return out
