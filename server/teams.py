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
    issue_invite_code(team_id, owner_email) -> invite_code | None
    redeem_invite_code(invite_code, joiner_email) -> dict
    remove_member(team_id, owner_email, member_email) -> bool
    leave_team(member_email) -> bool
    team_for_email(email) -> dict | None     # the team this email belongs to
    team_for_member(email) -> dict | None    # the team where email is OWNER OR MEMBER
    owner_email_for(email) -> str | None
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path

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

_state_lock = threading.RLock()  # reentrant so nested helpers can re-acquire


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
            t["owner"] = ev.get("owner_email", "")
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
    """Reduce invite ledger to {invite_code: {team_id, created_at, redeemed_by, redeemed_at}}."""
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
            "redeemed_by": "",
            "redeemed_at": "",
        })
        if et == "issue":
            i["team_id"] = ev.get("team_id", "")
            i["created_at"] = ev.get("ts", "")
        elif et == "redeem":
            i["redeemed_by"] = ev.get("member_email", "")
            i["redeemed_at"] = ev.get("ts", "")
    return invites


# ── public API ─────────────────────────────────────────────────────────


def create_team(owner_email: str, team_name: str) -> str:
    """Create a new team. Returns the team_id."""
    owner_email = (owner_email or "").strip().lower()
    team_name = (team_name or "").strip()[:80] or "Team"
    if not owner_email or "@" not in owner_email:
        raise ValueError("invalid owner email")
    # Reject if the owner already owns an active team — one team per owner.
    existing = team_for_member(owner_email)
    if existing and not existing.get("deleted") and existing.get("owner") == owner_email:
        return existing["team_id"]
    team_id = _new_team_id()
    _append(TEAMS_LEDGER, {
        "ts": _now(),
        "event": "create",
        "team_id": team_id,
        "owner_email": owner_email,
        "name": team_name,
    })
    return team_id


def issue_invite_code(team_id: str, owner_email: str) -> str | None:
    """Issue a single-use invite code for a team. Only the owner can issue.

    Returns the invite_code, or None if the caller isn't the owner.
    """
    teams = _team_state()
    t = teams.get(team_id)
    if not t or t.get("deleted") or t.get("owner") != owner_email:
        return None
    code = _new_invite_code()
    _append(INVITES_LEDGER, {
        "ts": _now(),
        "event": "issue",
        "team_id": team_id,
        "invite_code": code,
        "issued_by": owner_email,
    })
    return code


def redeem_invite_code(invite_code: str, joiner_email: str) -> dict:
    """Redeem an invite code. Returns {ok: bool, team_id?, error?}.

    Atomic: the cap check, double-redeem check, and the two ledger appends
    all happen under `_state_lock`. Without the lock two concurrent redeems
    could both pass the cap check; the 26th would be silently dropped by
    the reducer ("ghost member") because the reducer enforces MAX_TEAM_MEMBERS.
    """
    joiner_email = (joiner_email or "").strip().lower()
    invite_code = (invite_code or "").strip()
    if not joiner_email or "@" not in joiner_email:
        return {"ok": False, "error": "invalid joiner email"}
    with _state_lock:
        invites = _invite_state()
        inv = invites.get(invite_code)
        if not inv or not inv.get("team_id"):
            return {"ok": False, "error": "invalid invite code"}
        if inv.get("redeemed_by"):
            return {"ok": False, "error": "invite code already redeemed"}
        team_id = inv["team_id"]
        teams_state = _team_state()
        t = teams_state.get(team_id)
        if not t or t.get("deleted"):
            return {"ok": False, "error": "team no longer exists"}
        if t.get("owner") == joiner_email:
            return {"ok": False, "error": "owner cannot redeem own invite"}
        if joiner_email in t.get("members", set()):
            return {"ok": False, "error": "already a member"}
        if len(t.get("members", set())) >= MAX_TEAM_MEMBERS:
            return {"ok": False, "error": "team is full"}
        # If joiner is in a different team, they must leave first.
        for other in teams_state.values():
            if other.get("deleted") or other.get("team_id") == team_id:
                continue
            if other.get("owner") == joiner_email or joiner_email in other.get("members", set()):
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


def remove_member(team_id: str, owner_email: str, member_email: str) -> bool:
    teams = _team_state()
    t = teams.get(team_id)
    if not t or t.get("deleted") or t.get("owner") != owner_email:
        return False
    if member_email not in t.get("members", set()):
        return False
    _append(TEAMS_LEDGER, {
        "ts": _now(),
        "event": "remove",
        "team_id": team_id,
        "member_email": member_email,
    })
    return True


def leave_team(member_email: str) -> bool:
    t = team_for_member(member_email)
    if not t or t.get("deleted") or t.get("owner") == member_email:
        return False
    return remove_member(t["team_id"], t["owner"], member_email)


def team_for_member(email: str) -> dict | None:
    """Return the team where `email` is the owner OR a member, or None."""
    if not email:
        return None
    email = email.lower()
    teams = _team_state()
    # Active teams only
    for t in teams.values():
        if t.get("deleted"):
            continue
        if t.get("owner") == email or email in t.get("members", set()):
            return _serialize(t)
    return None


def team_for_email(email: str) -> dict | None:
    """Alias kept for callsite clarity."""
    return team_for_member(email)


def owner_email_for(email: str) -> str | None:
    """Return the subscription-bearing owner email for this email, or None.

    If `email` is the owner, returns email. If `email` is a team member,
    returns the team owner's email. Otherwise None.
    """
    t = team_for_member(email)
    return t.get("owner") if t else None


def _serialize(t: dict) -> dict:
    """Convert the in-memory set to a list for JSON output."""
    out = dict(t)
    out["members"] = sorted(t.get("members", set()))
    return out
