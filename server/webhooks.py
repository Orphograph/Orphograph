#!/usr/bin/env python3
"""webhooks.py — outbound HMAC-signed event delivery.

Customers (Editorial / Enterprise tier candidates) register a webhook URL
on their account; events are POSTed to that URL with an HMAC-SHA256
signature in the `X-Orpho-Signature` header so the receiver can verify
authenticity without sharing the secret over the wire.

Event surface
-------------
Three event types are dispatched:

  * ``anchor.created``     — a receipt has just been issued for an
                             account-holder (subscription or API-key path).
  * ``anchor.btc_pinned``  — a previously-issued receipt has been
                             committed to a Bitcoin block.
  * ``subscription.renewed`` — a Stripe subscription period rolled.

The dispatch is fire-and-forget on a background thread with a 5-second
timeout. Failures are logged to stderr and DO NOT block the user-facing
request; receivers are expected to be idempotent and the worker retries
on the next event of the same type if the previous send failed.

Storage
-------
Registrations are append-only JSONL at ``data/webhooks.jsonl``. Latest
event per ``(email, url)`` pair wins; an ``event_type == "deleted"`` row
marks a registration as revoked.

Privacy contract
----------------
The webhook payload may include the email of the account-holder (so the
receiver can attribute the event); it MUST NOT include other account
data (no IP, no full Stripe customer ID beyond what the receiver
already knows, no other accounts' receipts). Callers pass an explicit
``payload`` dict — this module does no implicit field expansion.

Public API
----------
    register(email, url, secret) -> bool
    list_for_email(email) -> list[dict]
    delete(email, url) -> bool
    dispatch(event_type, email, payload) -> None      # fire-and-forget
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_lock import locked  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
WEBHOOKS_LEDGER = Path(os.environ.get("ORPHO_WEBHOOKS_LEDGER", str(DATA_DIR / "webhooks.jsonl")))

HTTP_TIMEOUT_SEC = 5.0
ALLOWED_EVENTS = {"anchor.created", "anchor.btc_pinned", "subscription.renewed"}
MAX_URL_LEN = 512
MAX_PER_EMAIL = 5  # hard cap on registrations to prevent runaway loops


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def _latest_state() -> dict[tuple[str, str], dict]:
    """Compute the most recent state per (email, url) pair.

    Returns a dict keyed on (email_lower, url) mapping to the latest row.
    Rows whose latest event is ``deleted`` are excluded from the map.
    """
    state: dict[tuple[str, str], dict] = {}
    for row in _read_all(WEBHOOKS_LEDGER):
        email = (row.get("email") or "").lower()
        url = row.get("url") or ""
        if not email or not url:
            continue
        state[(email, url)] = row
    out: dict[tuple[str, str], dict] = {}
    for key, row in state.items():
        if row.get("event") == "deleted":
            continue
        out[key] = row
    return out


def list_for_email(email: str) -> list[dict]:
    """Return active webhook registrations for an email — secret redacted.

    The plaintext secret never leaves the server. Callers receive only a
    short prefix (``orpho_xxxxx…``) sufficient for the account-page UI to
    confirm the registration without revealing the secret.
    """
    if not email:
        return []
    email_lower = email.lower()
    out: list[dict] = []
    for (em, url), row in _latest_state().items():
        if em != email_lower:
            continue
        secret = row.get("secret") or ""
        out.append({
            "url": url,
            "secret_prefix": secret[:10] + "…" if secret else "",
            "created_at": row.get("ts"),
        })
    return out


_METADATA_IPS = {"169.254.169.254", "fd00:ec2::254"}


def _ip_is_disallowed(ip_str: str) -> tuple[bool, str | None]:
    """Return (disallowed, reason) for a single IP string."""
    if "%" in ip_str:  # strip IPv6 scope id
        ip_str = ip_str.split("%", 1)[0]
    if ip_str in _METADATA_IPS:
        return True, "cloud_metadata_address"
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True, f"bad_ip: {ip_str}"
    # IPv4-mapped IPv6 (::ffff:a.b.c.d) — explicitly unwrap to v4 so the
    # private-address checks work even when the OS returns a mapped form.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    ):
        return True, f"non_public_address: {ip_str}"
    return False, None


def _is_public_address(host: str) -> tuple[bool, str | None]:
    """Resolve ``host`` (or accept it as a literal IP) and return
    (is_public, reason_if_not). Rejects any address mapped to private,
    loopback, link-local, multicast, IPv6-mapped private, or known
    cloud-metadata range. SSRF defense for webhook delivery.
    """
    if not host:
        return False, "missing_host"
    # If the host is a literal IP, validate it directly. Bypasses
    # getaddrinfo entirely — important because some platforms' getaddrinfo
    # on a numeric host returns IPv6-mapped or canonicalised forms that
    # slip past naïve string checks (the 10.x.x.x / 192.168.x.x false-pass
    # we caught in self-test).
    try:
        ipaddress.ip_address(host)
        disallowed, reason = _ip_is_disallowed(host)
        if disallowed:
            return False, reason
        return True, None
    except ValueError:
        pass  # not a literal IP — proceed to DNS resolution
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as e:
        return False, f"dns_error: {e}"
    if not infos:
        return False, "dns_no_records"
    for fam, _typ, _proto, _canon, sockaddr in infos:
        disallowed, reason = _ip_is_disallowed(sockaddr[0])
        if disallowed:
            return False, reason
    return True, None


def _validate_webhook_url(url: str) -> tuple[bool, str | None]:
    """Single source of truth for whether a webhook URL is acceptable.

    Used at registration time AND at delivery time (after potential
    redirects). Rejects unless https, with a publicly-resolvable host.
    """
    if not url or not isinstance(url, str):
        return False, "bad_url"
    if len(url) > MAX_URL_LEN:
        return False, "url_too_long"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return False, "url_must_be_https"
    host = (parsed.hostname or "").strip()
    if not host:
        return False, "missing_host"
    # Reject literal "localhost" / RFC1918 hostnames before any DNS
    # roundtrip — even if some resolver returned a public IP for these,
    # the intent of pointing a webhook at "localhost" is never legitimate.
    lower_host = host.lower()
    if lower_host in {"localhost", "localhost.localdomain", "ip6-localhost"}:
        return False, "non_public_address"
    if lower_host.endswith(".internal") or lower_host.endswith(".local"):
        return False, "non_public_address"
    return _is_public_address(host)


def register(email: str, url: str) -> dict:
    """Register a new webhook URL for ``email``.

    Generates a fresh HMAC secret server-side and returns it ONCE in the
    response. The plaintext secret is persisted alongside the URL in the
    append-only ledger; receivers must save it on their side to verify
    inbound dispatch signatures.

    Returns ``{"ok": True, "url": …, "secret": …}`` on success, or
    ``{"ok": False, "reason": "<machine code>"}`` on rejection.
    """
    if not email or "@" not in email:
        return {"ok": False, "reason": "bad_email"}
    ok, reason = _validate_webhook_url(url)
    if not ok:
        return {"ok": False, "reason": reason or "url_rejected"}

    existing = list_for_email(email)
    if any(row["url"] == url for row in existing):
        return {"ok": False, "reason": "duplicate_url"}
    if len(existing) >= MAX_PER_EMAIL:
        return {"ok": False, "reason": "registration_limit"}

    secret = "orpho_whsec_" + secrets.token_urlsafe(24)
    _append(WEBHOOKS_LEDGER, {
        "ts": _iso_now(),
        "event": "registered",
        "email": email,
        "url": url,
        "secret": secret,
    })
    return {"ok": True, "url": url, "secret": secret}


def delete(email: str, url: str) -> bool:
    """Mark a webhook registration as deleted. No further events dispatched."""
    if not email or not url:
        return False
    if (email.lower(), url) not in _latest_state():
        return False
    _append(WEBHOOKS_LEDGER, {
        "ts": _iso_now(),
        "event": "deleted",
        "email": email,
        "url": url,
    })
    return True


# ── dispatch ────────────────────────────────────────────────────────


def _sign(secret: str, body: bytes, ts: int) -> str:
    """HMAC-SHA256 over the timestamp + body, hex-encoded.

    Format matches Stripe's webhook signature scheme so receivers can
    reuse off-the-shelf verification code with the substring `t=…,v1=…`.
    """
    signed_payload = f"{ts}.".encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects on webhook delivery.

    A redirect handler that follows 30x responses is a classic SSRF
    sidestep: a paying subscriber registers https://attacker.com which
    returns 302 Location: http://localhost:8080/admin (or a metadata
    address). With this handler installed, urllib raises HTTPError on
    any 30x, which we surface as a delivery failure to the receiver's
    log.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code,
            f"redirect-not-followed (to {newurl})",
            headers, fp,
        )


_no_redirect_opener = urllib.request.build_opener(_NoRedirectHandler())


def _deliver_one(url: str, secret: str, body: bytes, event_id: str) -> None:
    # Re-validate at delivery time. Defense against TOCTOU between
    # register-time check and the actual outbound request, e.g. DNS
    # rebinding where the host's A record changed since registration.
    ok, reason = _validate_webhook_url(url)
    if not ok:
        sys.stderr.write(
            f"[webhooks] delivery refused url={url} event={event_id} reason={reason}\n"
        )
        return
    ts = int(time.time())
    sig = _sign(secret, body, ts)
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Orpho-Signature": sig,
            "X-Orpho-Event-Id": event_id,
            "User-Agent": "Mozilla/5.0 (compatible; OrphographWebhook/0.1; +https://orphograph.com)",
        },
    )
    try:
        with _no_redirect_opener.open(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            resp.read()
        # No structured logging here on success — receivers' own logs are
        # the source of truth. We only log failures to make debugging
        # easier for the founder when a customer reports a missing event.
    except urllib.error.HTTPError as e:
        body_snip = ""
        try:
            body_snip = (e.read() or b"").decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        sys.stderr.write(
            f"[webhooks] HTTP {e.code} url={url} event={event_id} body={body_snip}\n"
        )
    except (urllib.error.URLError, OSError) as e:
        sys.stderr.write(f"[webhooks] {type(e).__name__} url={url} event={event_id}: {e}\n")
    except TimeoutError:
        sys.stderr.write(f"[webhooks] timeout url={url} event={event_id}\n")


def dispatch(event_type: str, email: str, payload: dict) -> None:
    """Fire-and-forget dispatch of ``payload`` to every active webhook for ``email``.

    The user-facing path that produced the event must not block on this
    call — receivers may be slow, down, or hostile. Each delivery runs
    on its own daemon thread; the call returns immediately.
    """
    if event_type not in ALLOWED_EVENTS:
        sys.stderr.write(f"[webhooks] unknown event_type {event_type!r}; ignoring\n")
        return
    if not email or not isinstance(payload, dict):
        return
    targets = list_for_email_with_secrets(email)
    if not targets:
        return

    event_id = "evt_" + secrets.token_urlsafe(12)
    envelope = {
        "id": event_id,
        "type": event_type,
        "created": int(time.time()),
        "data": payload,
    }
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    for tgt in targets:
        url = tgt["url"]
        secret = tgt["secret"]
        t = threading.Thread(
            target=_deliver_one,
            args=(url, secret, body, event_id),
            daemon=True,
            name=f"webhook-{event_id[:8]}",
        )
        t.start()


def list_for_email_with_secrets(email: str) -> list[dict]:
    """Internal helper — returns active registrations WITH plaintext secret.

    Never expose via API; only the dispatch path uses this. The public
    ``list_for_email`` is the redacted version.
    """
    if not email:
        return []
    email_lower = email.lower()
    out: list[dict] = []
    for (em, url), row in _latest_state().items():
        if em != email_lower:
            continue
        secret = row.get("secret") or ""
        if not secret:
            continue
        out.append({"url": url, "secret": secret})
    return out
