#!/usr/bin/env python3
"""auth.py — email magic-link authentication. No passwords.

Flow:
    1. POST /api/auth/email-link {email}
       → issue_link_token(email) → returns a one-time URL-safe token,
         stored as SHA-256(token) in auth_tokens.jsonl with an
         expires_at. The plaintext token is emailed via mailer.
    2. GET /a/<token>
       → redeem_link_token(token) → if found, not expired, not
         already used: mark consumed, mint a session, return
         (email, session_cookie).
    3. Subsequent requests carry the session cookie. session_email(cookie)
       resolves it to an email or None.

Storage: append-only JSONL ledgers under DATA_DIR.
Tokens never persisted in plaintext — only SHA-256 hashes.

Public API:
    issue_link_token(email) -> (plaintext_token, expires_at)
    redeem_link_token(token) -> dict|None
    create_session(email) -> (session_id, expires_at)
    session_email(session_id) -> str|None
    revoke_session(session_id) -> None
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_lock import locked  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
TOKEN_LEDGER = Path(os.environ.get("ORPHO_AUTH_TOKENS", str(DATA_DIR / "auth_tokens.jsonl")))
SESSION_LEDGER = Path(os.environ.get("ORPHO_AUTH_SESSIONS", str(DATA_DIR / "auth_sessions.jsonl")))
HMAC_SECRET_PATH = Path(os.environ.get("ORPHO_HMAC_SECRET_PATH", str(DATA_DIR / ".hmac_secret")))

LINK_TTL_SEC = int(os.environ.get("ORPHO_LINK_TTL_SEC", str(60 * 60 * 24)))   # 24h
SESSION_TTL_SEC = int(os.environ.get("ORPHO_SESSION_TTL_SEC", str(30 * 86400)))  # 30d


def _load_or_create_hmac_secret() -> bytes:
    """Per-installation HMAC secret. Persisted to disk so it survives restarts.

    Used to keep dictionary attacks against the on-disk `source` field of
    receipts (which encodes the subscriber's email hash) infeasible without
    also stealing the secret.
    """
    env_secret = os.environ.get("ORPHO_HMAC_SECRET", "")
    if env_secret:
        return env_secret.encode("utf-8")
    HMAC_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if HMAC_SECRET_PATH.exists():
        return HMAC_SECRET_PATH.read_bytes()
    secret = secrets.token_bytes(32)
    HMAC_SECRET_PATH.write_bytes(secret)
    try:
        os.chmod(HMAC_SECRET_PATH, 0o600)
    except OSError:
        pass
    return secret


_HMAC_SECRET_CACHE: bytes | None = None


def _hmac_secret() -> bytes:
    global _HMAC_SECRET_CACHE
    if _HMAC_SECRET_CACHE is None:
        _HMAC_SECRET_CACHE = _load_or_create_hmac_secret()
    return _HMAC_SECRET_CACHE


def email_id(email: str) -> str:
    """Stable, non-reversible identifier for an email, safe for on-disk storage.

    HMAC-SHA256 with a per-installation secret. An attacker with disk access
    cannot dictionary-attack the receipts→email mapping without also stealing
    the secret. Truncated to 16 hex chars (64 bits) — enough to avoid
    collisions in the foreseeable future.
    """
    if not email:
        return ""
    return hmac.new(_hmac_secret(), email.lower().encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def mask_email(email: str) -> str:
    """Return a log-safe rendering of an email: first char + domain.

    `alex@example.com` → `a***@example.com`. Enough to recognise in
    customer support without dumping PII into log aggregators.
    """
    if not isinstance(email, str) or "@" not in email:
        return "(invalid)"
    local, _, domain = email.partition("@")
    if not local:
        return "***@" + domain
    return local[0] + "***@" + domain


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _append(path: Path, row: dict) -> None:
    with locked(path, mode="a", exclusive=True) as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _read_all(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _supersede_prior_tokens_for_email(email: str) -> int:
    """Mark all previously-issued, still-valid tokens for this email as
    superseded so they can no longer be redeemed. Defense against the
    "user requested two links, the older one persists in browser history
    on a now-shared device" scenario."""
    if not email:
        return 0
    rows = _read_all(TOKEN_LEDGER)
    # Latest event per token_hash wins; collect those still in "issued" state for this email.
    state: dict[str, dict] = {}
    for row in rows:
        h = row.get("token_hash")
        if not h:
            continue
        state[h] = row
    superseded = 0
    for h, row in state.items():
        if row.get("event") != "issued":
            continue
        if row.get("email") != email:
            continue
        _append(TOKEN_LEDGER, {
            "ts": _iso(_now()),
            "event": "superseded",
            "token_hash": h,
            "email": email,
        })
        superseded += 1
    return superseded


def issue_link_token(email: str) -> tuple[str, float]:
    """Mint a one-time login token. Returns (plaintext_token, expires_at_unix).

    Implicitly supersedes any prior unredeemed tokens for the same email —
    only the most recently issued link is usable. Lets the user safely
    "send me another link" without leaving live tokens behind.
    """
    _supersede_prior_tokens_for_email(email)
    token = secrets.token_urlsafe(24)
    expires = _now() + LINK_TTL_SEC
    _append(TOKEN_LEDGER, {
        "ts": _iso(_now()),
        "event": "issued",
        "token_hash": _hash(token),
        "email": email,
        "expires_at": _iso(expires),
        "expires_unix": expires,
    })
    return token, expires


def redeem_link_token(token: str) -> dict | None:
    """One-time consume. Returns {email, issued_at} on success, None on failure
    (unknown, expired, already redeemed)."""
    if not token:
        return None
    h = _hash(token)
    # Cross-process atomicity: hold a sentinel lock during scan+append.
    lockfile = TOKEN_LEDGER.with_suffix(TOKEN_LEDGER.suffix + ".lock")
    with locked(lockfile, mode="a", exclusive=True):
        rows = _read_all(TOKEN_LEDGER)
        # Latest-wins per token: scan in order, track state.
        state: dict | None = None
        for row in rows:
            if row.get("token_hash") != h:
                continue
            state = row  # overwrites with latest event for this hash
        if state is None:
            return None
        if state.get("event") != "issued":
            # redeemed / superseded / any non-issued state — refuse
            return None
        if _now() > float(state.get("expires_unix", 0)):
            return None
        _append(TOKEN_LEDGER, {
            "ts": _iso(_now()),
            "event": "redeemed",
            "token_hash": h,
            "email": state["email"],
        })
        return {"email": state["email"], "issued_at": state.get("ts")}


def create_session(email: str) -> tuple[str, float]:
    """Mint a new session for email. Returns (session_id, expires_at_unix)."""
    sid = secrets.token_urlsafe(24)
    expires = _now() + SESSION_TTL_SEC
    _append(SESSION_LEDGER, {
        "ts": _iso(_now()),
        "event": "created",
        "session_hash": _hash(sid),
        "email": email,
        "expires_at": _iso(expires),
        "expires_unix": expires,
    })
    return sid, expires


def session_email(session_id: str) -> str | None:
    """Return the email for a live session, or None if missing/expired/revoked."""
    if not session_id:
        return None
    h = _hash(session_id)
    rows = _read_all(SESSION_LEDGER)
    state: dict | None = None
    for row in rows:
        if row.get("session_hash") != h:
            continue
        state = row
    if state is None or state.get("event") == "revoked":
        return None
    if _now() > float(state.get("expires_unix", 0)):
        return None
    return state.get("email")


def revoke_session(session_id: str) -> None:
    if not session_id:
        return
    _append(SESSION_LEDGER, {
        "ts": _iso(_now()),
        "event": "revoked",
        "session_hash": _hash(session_id),
    })


def revoke_all_sessions(email: str) -> int:
    """Revoke every live session for an email — "log out of all devices".

    Appends a `revoked` event for each currently-live (created, unexpired,
    not-already-revoked) session belonging to this email. Returns the count
    revoked. Other users' sessions are untouched. Idempotent: re-running once
    no live sessions remain returns 0.
    """
    if not email:
        return 0
    # Hold one ledger lock across the scan-and-append so two concurrent
    # logout-all calls under ThreadingHTTPServer can't double-write revoked
    # rows (mirrors redeem_link_token; security review 2026-06-22). The lock is
    # a separate sentinel file, so the per-row _append (which locks the ledger
    # itself) does not deadlock.
    lockfile = SESSION_LEDGER.with_suffix(SESSION_LEDGER.suffix + ".lock")
    with locked(lockfile, mode="a", exclusive=True):
        rows = _read_all(SESSION_LEDGER)
        # Latest event per session_hash wins (same pattern as token supersede).
        state: dict[str, dict] = {}
        for row in rows:
            h = row.get("session_hash")
            if not h:
                continue
            state[h] = row
        now = _now()
        revoked = 0
        for h, row in state.items():
            if row.get("event") != "created":
                continue
            if row.get("email") != email:
                continue
            if now > float(row.get("expires_unix", 0)):
                continue  # already expired — no need to revoke
            _append(SESSION_LEDGER, {
                "ts": _iso(now),
                "event": "revoked",
                "session_hash": h,
            })
            revoked += 1
        return revoked


def cookie_name(secure: bool) -> str:
    """`__Host-` prefix enforces same-host + Secure + Path=/ by the browser.
    Skipped in dev where Secure is off (browsers reject __Host- without Secure)."""
    return "__Host-orpho_sid" if secure else "orpho_sid"


def build_session_cookie(session_id: str, secure: bool = True, max_age: int = SESSION_TTL_SEC) -> str:
    """Build a Set-Cookie header value for the session.

    HttpOnly: blocks JS access (XSS exfil defense).
    SameSite=Lax: blocks CSRF on cross-site POSTs.
    Secure: only sent over HTTPS. Set False for dev over http://localhost.
    __Host- prefix: browser enforces same-host + Secure + Path=/.
    """
    name = cookie_name(secure)
    parts = [
        f"{name}={session_id}",
        "Path=/",
        f"Max-Age={max_age}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_session_cookie(secure: bool = True) -> str:
    name = cookie_name(secure)
    parts = [
        f"{name}=",
        "Path=/",
        "Max-Age=0",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)
