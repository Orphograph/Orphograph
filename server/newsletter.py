#!/usr/bin/env python3
"""newsletter.py — Resend Audiences integration for proper newsletter management.

Source-of-truth: local ``data/waitlist.jsonl`` ledger stays canonical.
Resend Audiences is a sync target so the founder can send broadcasts
through Resend's deliverability/UI instead of hand-rolling SMTP.

Double opt-in flow (CASL + CAN-SPAM friendly):
    1. POST /api/waitlist (server/app.py::_handle_waitlist):
         append to local ledger + email a confirmation link with a
         24h HMAC token.
    2. GET /api/waitlist/confirm?token=... :
         validate token + POST contact to Resend Audiences +
         append a {event: "confirmed"} row to the local ledger.

Inert mode:
    If ``RESEND_API_KEY`` or ``ORPHO_AUDIENCE_ID`` is unset, every
    Resend call logs to stderr and returns False. Nothing crashes —
    the local ledger continues to be the source of truth.

Privacy invariants (see ``feedback_orphograph_privacy_doctrine.md``):
    - We log only the masked email (`auth.mask_email`) to stderr.
    - The founder snapshot endpoint never returns individual emails.
    - PII lives in Resend's service (covered by their SOC 2 + GDPR DPA).

Public API
----------
    make_confirm_token(email, interest) -> (token, expires_unix)
    verify_confirm_token(token) -> dict | None
    send_confirmation_email(email, interest, token) -> bool
    add_contact(email, interest) -> bool
    unsubscribe_contact(email) -> bool
    list_contacts() -> list[dict]
    audience_snapshot() -> dict  # counts only, no emails
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth as _auth  # noqa: E402  — mask_email + HMAC secret reuse
import mailer as _mailer  # noqa: E402  — _send + footer compliance
import waitlist as _waitlist  # noqa: E402  — ledger path + interests

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
ORPHO_AUDIENCE_ID = os.environ.get("ORPHO_AUDIENCE_ID", "")
RESEND_BASE = "https://api.resend.com"
HTTP_TIMEOUT = 10
SITE_URL = os.environ.get("SITE_URL", "https://orphograph.com")

CONFIRM_TTL_SEC = int(os.environ.get("ORPHO_NEWSLETTER_CONFIRM_TTL_SEC", str(60 * 60 * 24)))  # 24h


# ---------------------------------------------------------------------------
# HMAC token (re-uses auth._hmac_secret per-installation secret pattern)
# ---------------------------------------------------------------------------

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(token: str) -> bytes:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad)


def make_confirm_token(email: str, interest: str) -> tuple[str, int]:
    """Mint a tamper-evident confirmation token.

    Encodes (email, interest, expires_unix) and signs with the per-install
    HMAC secret. Stateless — no DB row needed; expiry is enforced on verify.
    """
    expires = int(time.time()) + CONFIRM_TTL_SEC
    payload = {"email": email.strip().lower(), "i": interest, "exp": expires}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_auth._hmac_secret(), body, hashlib.sha256).digest()
    token = _b64url_encode(body) + "." + _b64url_encode(sig)
    return token, expires


def verify_confirm_token(token: str) -> dict | None:
    """Returns {"email", "interest", "exp"} on valid, None otherwise.

    Constant-time signature check; rejects expired tokens.
    """
    if not isinstance(token, str) or "." not in token:
        return None
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = _b64url_decode(body_b64)
        sig = _b64url_decode(sig_b64)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return None
    expected = hmac.new(_auth._hmac_secret(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    email = payload.get("email", "")
    interest = payload.get("i", "")
    if not isinstance(email, str) or "@" not in email:
        return None
    if interest not in _waitlist.ALLOWED_INTERESTS:
        interest = "other"
    return {"email": email, "interest": interest, "exp": int(payload["exp"])}


# ---------------------------------------------------------------------------
# Resend HTTP helpers
# ---------------------------------------------------------------------------

def _resend_request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    """Low-level Resend API call. Returns (status, parsed_body_or_error_dict).

    On network/transport failure returns (0, {"error": str}).
    Caller decides how to react to non-2xx status codes.
    """
    url = RESEND_BASE + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {RESEND_API_KEY}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read()
            status = resp.getcode()
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
        except Exception:  # noqa: BLE001
            raw = b""
        status = e.code
    except (urllib.error.URLError, OSError) as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        body = {"raw": (raw or b"").decode("utf-8", errors="replace")}
    return status, body


def _inert(reason: str, email: str) -> bool:
    sys.stderr.write(
        f"[newsletter:inert] {reason} email={_auth.mask_email(email)}\n"
    )
    return False


# ---------------------------------------------------------------------------
# Audience contact CRUD
# ---------------------------------------------------------------------------

def add_contact(email: str, interest: str, *, unsubscribed: bool = False) -> bool:
    """Add a contact to the Resend Audience. Inert if env missing.

    We tag the contact via `first_name` = interest so the founder can
    segment broadcasts by tier inside the Resend dashboard until Resend
    exposes a free-form tag field on the contacts endpoint.
    """
    if not isinstance(email, str) or "@" not in email:
        return False
    if not RESEND_API_KEY:
        return _inert("RESEND_API_KEY unset", email)
    if not ORPHO_AUDIENCE_ID:
        return _inert("ORPHO_AUDIENCE_ID unset", email)
    payload = {
        "email": email.strip().lower(),
        "first_name": interest if interest in _waitlist.ALLOWED_INTERESTS else "other",
        "unsubscribed": bool(unsubscribed),
    }
    status, body = _resend_request(
        "POST",
        f"/audiences/{urllib.parse.quote(ORPHO_AUDIENCE_ID)}/contacts",
        payload,
    )
    if 200 <= status < 300:
        return True
    sys.stderr.write(
        f"[newsletter:error] add_contact status={status} email={_auth.mask_email(email)} body={body}\n"
    )
    return False


def unsubscribe_contact(email: str) -> bool:
    """Mark a contact as unsubscribed in Resend. Inert if env missing.

    Resend's PATCH endpoint accepts a contact lookup by email under the
    audience's contacts collection. If the contact isn't in Resend we
    silently return True — the local suppression list is the authority.
    """
    if not isinstance(email, str) or "@" not in email:
        return False
    if not RESEND_API_KEY:
        return _inert("RESEND_API_KEY unset", email)
    if not ORPHO_AUDIENCE_ID:
        return _inert("ORPHO_AUDIENCE_ID unset", email)
    normalized = email.strip().lower()
    path = (
        f"/audiences/{urllib.parse.quote(ORPHO_AUDIENCE_ID)}"
        f"/contacts/{urllib.parse.quote(normalized)}"
    )
    status, body = _resend_request("PATCH", path, {"unsubscribed": True})
    if 200 <= status < 300:
        return True
    if status == 404:
        # Never synced to Resend in the first place — local suppression
        # is still authoritative, so report success.
        return True
    sys.stderr.write(
        f"[newsletter:error] unsubscribe_contact status={status} "
        f"email={_auth.mask_email(email)} body={body}\n"
    )
    return False


def list_contacts() -> list[dict]:
    """Fetch every contact in the audience (founder-only call site).

    Returns [] if env missing or Resend errors. The caller MUST gate
    this behind the founder token — never expose to customers.
    """
    if not RESEND_API_KEY or not ORPHO_AUDIENCE_ID:
        sys.stderr.write("[newsletter:inert] list_contacts env unset\n")
        return []
    status, body = _resend_request(
        "GET",
        f"/audiences/{urllib.parse.quote(ORPHO_AUDIENCE_ID)}/contacts",
    )
    if not (200 <= status < 300):
        sys.stderr.write(f"[newsletter:error] list_contacts status={status} body={body}\n")
        return []
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, list):
        return data
    return []


# ---------------------------------------------------------------------------
# Confirmation email
# ---------------------------------------------------------------------------

def send_confirmation_email(email: str, interest: str, token: str) -> bool:
    """Send the double opt-in confirmation. Transactional category.

    CASL + CAN-SPAM both treat a confirmation request as transactional
    (user-initiated), so we don't include marketing footer / unsub.
    But we DO include the confirm CTA prominently — that's the entire
    point of the message.
    """
    confirm_url = f"{SITE_URL}/api/waitlist/confirm?token={urllib.parse.quote(token)}"
    interest_label = {
        "personal": "Personal ($5/mo)",
        "creator": "Creator ($19/mo)",
        "capture": "Orphograph Capture (capture-time provenance)",
        "b2b": "B2B / Team tier",
        "pack": "Pack tier ($7 one-shot)",
        "other": "Orphograph updates",
    }.get(interest, "Orphograph updates")
    subject = "Confirm your Orphograph waitlist spot"
    text = (
        f"Thanks for signing up for Orphograph — {interest_label}.\n\n"
        f"Tap the link below to confirm you want updates from us. "
        f"If you don't tap it within 24 hours, the request expires "
        f"and we won't add you to the list.\n\n"
        f"{confirm_url}\n\n"
        f"If you didn't sign up, just ignore this email — nothing happens.\n"
    )
    html = (
        f"<p>Thanks for signing up for Orphograph — <strong>{interest_label}</strong>.</p>"
        f"<p>Tap the button below to confirm you want updates from us. "
        f"If you don't tap it within 24 hours, the request expires "
        f"and we won't add you to the list.</p>"
        f"<p><a href=\"{confirm_url}\" "
        f"style=\"display:inline-block;padding:10px 16px;background:#4a9a73;"
        f"color:#fff;border-radius:6px;text-decoration:none;\">Confirm subscription</a></p>"
        f"<p style=\"font-size:12px;color:#837e75;\">Or paste this link: "
        f"<code>{confirm_url}</code></p>"
        f"<p>If you didn't sign up, just ignore this email — nothing happens.</p>"
    )
    return _mailer._send(
        email,
        subject,
        text,
        html,
        transactional=True,
        category="newsletter-confirm",
    )


# ---------------------------------------------------------------------------
# Local ledger helpers (confirmed state)
# ---------------------------------------------------------------------------

def mark_confirmed(email: str, interest: str) -> None:
    """Append a {event: "confirmed"} row to the waitlist ledger so the
    local source-of-truth records the double-opt-in completion.
    """
    from datetime import datetime, timezone

    from file_lock import locked

    if not isinstance(email, str) or "@" not in email:
        return
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "email": email.strip().lower(),
        "interest": interest if interest in _waitlist.ALLOWED_INTERESTS else "other",
        "event": "confirmed",
    }
    with locked(_waitlist.WAITLIST_PATH, mode="a", exclusive=True) as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


# ---------------------------------------------------------------------------
# Founder-only audience snapshot — counts only, NEVER emails
# ---------------------------------------------------------------------------

def audience_snapshot() -> dict:
    """Aggregate statistics for the founder dashboard.

    Reads the LOCAL waitlist ledger (source of truth). NEVER returns
    individual emails — only counts and per-interest breakdown.
    Privacy doctrine rule #3 (no exposing client private info).
    """
    counts: dict[str, int] = {}
    confirmed_emails: set[str] = set()
    pending_emails: set[str] = set()
    total_rows = 0
    if _waitlist.WAITLIST_PATH.exists():
        with _waitlist.WAITLIST_PATH.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total_rows += 1
                email = (row.get("email") or "").strip().lower()
                interest = row.get("interest") or "other"
                if interest not in _waitlist.ALLOWED_INTERESTS:
                    interest = "other"
                counts[interest] = counts.get(interest, 0) + 1
                if row.get("event") == "confirmed":
                    confirmed_emails.add(email)
                else:
                    pending_emails.add(email)
    # An email is "confirmed" if it has any confirmed row.
    pending_emails -= confirmed_emails
    return {
        "ledger_rows": total_rows,
        "unique_signups": len(confirmed_emails | pending_emails),
        "confirmed": len(confirmed_emails),
        "pending": len(pending_emails),
        "by_interest": counts,
        "resend_audience_configured": bool(RESEND_API_KEY and ORPHO_AUDIENCE_ID),
    }
