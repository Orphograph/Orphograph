#!/usr/bin/env python3
"""newsletter.py — Resend Audiences integration for proper newsletter management.

Source-of-truth: local ``data/waitlist.jsonl`` ledger stays canonical.
Resend Audiences is a sync target so the founder can send broadcasts
through Resend's deliverability/UI instead of hand-rolling SMTP.

Double opt-in flow (CASL + CAN-SPAM friendly), wired 2026-09-30
(founder decision 2026-09-28):
    1. POST /api/waitlist (server/app.py::_handle_waitlist) appends the
       signup row and answers. After the answer, one worker thread calls
       request_confirmation(): unless the address is suppressed, already
       confirmed, or was sent a confirmation in the last 24 hours, it
       appends a {event: "confirm_sent"} row and emails a link carrying a
       24h HMAC token.
    2. GET /api/waitlist/confirm?token=... answers a page with one button
       and writes nothing: mail scanners follow links with GET.
    3. The button POSTs to the same URL. confirm() finds the confirm_sent
       row the token names and appends one {event: "confirmed"} row (a
       second press writes nothing). Only then is the contact added to the
       Resend Audience, when that is configured.

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
    request_confirmation(email, interest) -> bool
    confirm(token) -> (state, email, interest) | None
    make_confirm_token(nonce) -> (token, expires_unix)
    verify_confirm_token(token) -> dict | None
    send_confirmation_email(email, interest, token) -> bool
    mark_confirmed(email, interest) -> bool
    add_confirmed_contact(email, interest) -> bool
    add_contact(email, interest) -> bool
    unsubscribe_contact(email) -> bool
    list_contacts() -> list[dict]
    audience_snapshot() -> dict  # counts only, no emails
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth as _auth  # noqa: E402  — mask_email + HMAC secret reuse
import mailer as _mailer  # noqa: E402  — _send + footer compliance
import unsubscribe as _unsubscribe  # noqa: E402  — suppression list
import waitlist as _waitlist  # noqa: E402  — ledger path + interests
from email_fold import fold_email  # noqa: E402
from file_lock import ends_mid_line, locked  # noqa: E402

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
ORPHO_AUDIENCE_ID = os.environ.get("ORPHO_AUDIENCE_ID", "")
RESEND_BASE = "https://api.resend.com"
HTTP_TIMEOUT = 10
SITE_URL = os.environ.get("SITE_URL", "https://orphograph.com")

CONFIRM_TTL_SEC = int(os.environ.get("ORPHO_NEWSLETTER_CONFIRM_TTL_SEC", str(60 * 60 * 24)))  # 24h
# At most one confirmation email per address in this window, however often
# the public form is posted with that address.
CONFIRM_RESEND_SEC = 60 * 60 * 24


# ---------------------------------------------------------------------------
# HMAC token (re-uses auth._hmac_secret per-installation secret pattern)
# ---------------------------------------------------------------------------

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(token: str) -> bytes:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad)


# The installation secret also keys auth.keyed_hex and auth.email_id. The
# token's MAC is taken over this label and then the body, so it can never be
# an HMAC this installation made for some other purpose.
_TOKEN_LABEL = b"orphograph waitlist-confirm v2\n"
# Two base64url segments, the second exactly one SHA-256 MAC long. base64
# decoding skips characters outside its alphabet, so without this a token
# with junk inserted decoded to the same bytes and verified. It is checked
# before the secret is read: reading it creates it in a fresh data
# directory, and the confirm page is a GET, which must not write.
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{1,200}\.[A-Za-z0-9_-]{43}")


def make_confirm_token(nonce: str) -> tuple[str, int]:
    """Mint the token for one confirmation email.

    It carries an expiry and `nonce`, the id of the {event: "confirm_sent"}
    ledger row it belongs to, and nothing else. The address stays in the
    ledger: a link ends up in browser history and in logs, and the first
    version of this token carried the address in plain base64.
    """
    expires = int(time.time()) + CONFIRM_TTL_SEC
    body = json.dumps({"exp": expires, "n": nonce},
                      separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_auth._hmac_secret(), _TOKEN_LABEL + body, hashlib.sha256).digest()
    return _b64url_encode(body) + "." + _b64url_encode(sig), expires


def verify_confirm_token(token) -> dict | None:
    """{"nonce", "exp"} for an unexpired token this server signed, else None.

    Constant-time signature check. Only the spelling make_confirm_token
    produces is accepted: base64's trailing padding bits give some byte
    strings several spellings, and a second spelling is not our token.
    """
    if not isinstance(token, str) or not _TOKEN_SHAPE.fullmatch(token):
        return None
    body_b64, sig_b64 = token.split(".")
    try:
        body = _b64url_decode(body_b64)
        sig = _b64url_decode(sig_b64)
    except (ValueError, binascii.Error):
        return None
    if _b64url_encode(body) != body_b64 or _b64url_encode(sig) != sig_b64:
        return None
    # Never create the secret here: this runs on the confirm page's GET, and
    # a GET must not write. With no secret yet, no token was ever minted, so
    # none verifies (bundle review round 1).
    secret = _auth.existing_hmac_secret()
    if secret is None:
        return None
    expected = hmac.new(secret, _TOKEN_LABEL + body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    exp, nonce = payload.get("exp"), payload.get("n")
    if not isinstance(exp, int) or not isinstance(nonce, str) or not nonce:
        return None
    if exp < int(time.time()):
        return None
    return {"nonce": nonce, "exp": exp}


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
    (user-initiated), so it carries the transactional footer (entity, site,
    privacy and terms links) that mailer._send adds, not the marketing one.
    The link opens a page with one button; following it confirms nothing.
    """
    confirm_url = f"{SITE_URL}/api/waitlist/confirm?token={urllib.parse.quote(token)}"
    topic = {
        "personal": "the Standing Order",
        "creator": "the Creator plan",
        "capture": "Orphograph Capture",
        "b2b": "team plans",
        "card_pack": "card checkout",
        "card_pack50": "card checkout",
        "card_personal": "card checkout",
    }.get(interest, "Orphograph")
    subject = "Confirm your Orphograph waitlist spot"
    text = (
        f"Thanks for asking us to keep you posted about {topic}.\n\n"
        "To confirm, open the link below and press the button on the page "
        "it opens. Until you do, this address is not added to our mailing "
        "list. The link works for 24 hours.\n\n"
        f"{confirm_url}\n\n"
        "If you did not sign up, ignore this email and nothing more happens.\n"
    )
    url = html.escape(confirm_url)
    html_body = (
        f"<p>Thanks for asking us to keep you posted about <strong>{html.escape(topic)}</strong>.</p>"
        "<p>To confirm, open the confirmation page and press the button on it. "
        "Until you do, this address is not added to our mailing list. "
        "The link works for 24 hours.</p>"
        f"<p><a href=\"{url}\" "
        "style=\"display:inline-block;padding:10px 16px;background:#4a9a73;"
        "color:#fff;border-radius:6px;text-decoration:none;\">Open the confirmation page</a></p>"
        "<p style=\"font-size:12px;color:#837e75;\">Or paste this link: "
        f"<code>{url}</code></p>"
        "<p>If you did not sign up, ignore this email and nothing more happens.</p>"
    )
    return _mailer._send(
        email,
        subject,
        text,
        html_body,
        transactional=True,
        category="newsletter-confirm",
    )


# ---------------------------------------------------------------------------
# Local ledger helpers (confirmed state)
# ---------------------------------------------------------------------------

def _rows(f) -> list[dict]:
    """The ledger's object rows from an open file; unusable lines skipped."""
    rows = []
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _ts(value) -> float | None:
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request_confirmation(email: str, interest: str) -> bool:
    """Email `email` a confirmation link, unless it should get none.

    None goes to an address on the suppression list (or when that list
    cannot be read: unknown consent is not consent), to one already
    confirmed, or to one sent a confirmation in the last CONFIRM_RESEND_SEC,
    so the public form cannot be used to flood someone's inbox. Addresses
    compare with email_fold.fold_email, as the subscription ledger does. The
    check and the {event: "confirm_sent"} row it writes share one ledger
    lock, so two signups racing each other send one email.

    Returns True when an email was handed to the mailer, delivered or not.
    A failed send still counts against the day: retrying it on every signup
    would be the flood this prevents.
    """
    if not isinstance(email, str) or "@" not in email or len(email) > 320:
        return False
    email = email.strip()
    if not is_bare_address(email):
        return False
    interest = interest if interest in _waitlist.ALLOWED_INTERESTS else "other"
    try:
        if _unsubscribe.is_unsubscribed(email):
            return False
    except _unsubscribe.SuppressionUnavailable:
        sys.stderr.write("[newsletter:skip] suppression list unreadable; no confirmation "
                         f"email to {_auth.mask_email(email)}\n")
        return False
    folded = fold_email(email)
    now = time.time()
    nonce = _b64url_encode(secrets.token_bytes(16))
    with locked(_waitlist.WAITLIST_PATH, mode="a+", exclusive=True) as f:
        f.seek(0)
        for row in _rows(f):
            if fold_email(row.get("email")) != folded:
                continue
            if row.get("event") == "confirmed":
                return False
            if row.get("event") == "confirm_sent":
                sent_at = _ts(row.get("ts"))
                # An unreadable time is treated as recent: no email.
                if sent_at is None or now - sent_at < CONFIRM_RESEND_SEC:
                    return False
        f.seek(0, os.SEEK_END)
        f.write(("\n" if ends_mid_line(_waitlist.WAITLIST_PATH) else "")
                + json.dumps({"ts": _now_iso(), "email": email, "interest": interest,
                              "event": "confirm_sent", "n": nonce},
                             separators=(",", ":")) + "\n")
    token, _expires = make_confirm_token(nonce)
    send_confirmation_email(email, interest, token)
    return True


def mark_confirmed(email: str, interest: str) -> bool:
    """Append the {event: "confirmed"} row that records the double-opt-in
    completion, once per address (fold_email). True when it wrote the row,
    False when the address was already confirmed or is malformed. The check
    and the append share one ledger lock, so two presses write one row.
    """
    if not isinstance(email, str) or "@" not in email:
        return False
    folded = fold_email(email)
    with locked(_waitlist.WAITLIST_PATH, mode="a+", exclusive=True) as f:
        f.seek(0)
        if any(row.get("event") == "confirmed" and fold_email(row.get("email")) == folded
               for row in _rows(f)):
            return False
        f.seek(0, os.SEEK_END)
        f.write(("\n" if ends_mid_line(_waitlist.WAITLIST_PATH) else "") + json.dumps({
            "ts": _now_iso(),
            "email": email.strip(),
            "interest": interest if interest in _waitlist.ALLOWED_INTERESTS else "other",
            "event": "confirmed",
        }, separators=(",", ":")) + "\n")
    return True


def confirm(token) -> tuple[str, str, str] | None:
    """The confirm button. Returns ("confirmed" | "already" | "unsubscribed",
    email, interest), or None when the token is forged, expired, or names no
    confirmation this ledger sent. Raises OSError when the ledger or the
    suppression list cannot be read or written.

    An address that unsubscribed after its confirmation email went out is
    never confirmed: the press writes nothing (bundle review round 1).
    """
    claims = verify_confirm_token(token)
    if claims is None or not _waitlist.WAITLIST_PATH.exists():
        return None
    with locked(_waitlist.WAITLIST_PATH, mode="r", exclusive=False) as f:
        sent = next((row for row in _rows(f)
                     if row.get("event") == "confirm_sent" and row.get("n") == claims["nonce"]),
                    None)
    if sent is None or not isinstance(sent.get("email"), str):
        return None
    email = sent["email"]
    interest = sent.get("interest") if sent.get("interest") in _waitlist.ALLOWED_INTERESTS else "other"
    try:
        if _unsubscribe.is_unsubscribed(email):
            return "unsubscribed", email, interest
    except _unsubscribe.SuppressionUnavailable as e:
        raise OSError(str(e)) from e   # unknown consent is not consent: the 503 page
    return ("confirmed" if mark_confirmed(email, interest) else "already"), email, interest


def is_bare_address(email: str) -> bool:
    """Only a bare addr-spec. "x<victim@...>", "<victim@...>" and
    '"victim"@...' pass the server's shape check, are not recognised as the
    suppressed victim, and a mail provider delivers them to the victim
    (bundle review round 1)."""
    from email.utils import parseaddr
    return (isinstance(email, str) and not any(c in email for c in '<>"')
            and parseaddr(email)[1] == email)


def add_confirmed_contact(email: str, interest: str) -> bool:
    """Add a person who just confirmed to the Resend Audience. Inert without
    the env. Skipped for an address on the suppression list, or when that
    list cannot be read, so a broadcast can never reach someone who opted out.
    """
    try:
        if _unsubscribe.is_unsubscribed(email):
            return False
    except _unsubscribe.SuppressionUnavailable:
        return False
    return add_contact(email, interest)


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
                # A syntactically valid but non-object line ("null", "[]", a
                # bare number) would sail past the decode guard and then blow
                # up on .get(). Skip it like any other unusable row rather
                # than taking down the whole readout.
                if not isinstance(row, dict):
                    continue
                total_rows += 1
                email = (row.get("email") or "").strip().lower()
                interest = row.get("interest") or "other"
                if interest not in _waitlist.ALLOWED_INTERESTS:
                    interest = "other"
                event = row.get("event")
                if event == "confirmed":
                    confirmed_emails.add(email)
                    continue
                if event is not None:
                    # A confirm_sent row records our email, not a person
                    # asking. Only signup rows (no event) are counted as
                    # asks, so one person who signs up and confirms is one.
                    continue
                counts[interest] = counts.get(interest, 0) + 1
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
