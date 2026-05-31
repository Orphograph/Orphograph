#!/usr/bin/env python3
"""resend_webhook.py — Resend (Svix) webhook handler for bounce/complaint events.

Resend signs webhooks via Svix:
  headers: svix-id, svix-timestamp, svix-signature
  signature: base64( HMAC-SHA256( base64decode(secret_after_whsec_prefix),
                                  f"{svix_id}.{svix_timestamp}.{raw_body}" ) )
  svix-signature header is space-separated "v1,<b64> v1,<b64>" (try each).

On `email.bounced` / `email.complained` we record the recipient to a suppression
ledger so the mailer stops emailing it (a hard bounce / spam complaint repeatedly
mailed tanks sender reputation and can get the domain blocked).

WIRING (deferred — app.py is mid-edit at time of writing; apply when clean):
  In app.py do_POST, beside the other webhook routes:
      if self.path == "/api/resend/webhook":
          self._handle_resend_webhook()
          return
  and a _handle_resend_webhook() that:
      raw = self.rfile.read(content_length)
      headers = {k.lower(): v for k, v in self.headers.items()}
      if not RESEND_WEBHOOK_SECRET or not verify_signature(raw, headers, RESEND_WEBHOOK_SECRET):
          _json_response(self, 400, {"error": "bad signature"}); return
      _json_response(self, 200, handle_event(raw))
  Add at app.py config: RESEND_WEBHOOK_SECRET = os.environ.get("RESEND_WEBHOOK_SECRET", "")
  (it's a Svix signing secret, prefix `whsec_`, NOT the `re_` API key).

Inert until RESEND_WEBHOOK_SECRET is set. Stdlib only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get(
    "ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
SUPPRESSION_LIST_PATH = Path(os.environ.get(
    "ORPHO_RESEND_SUPPRESSION_LIST", str(DATA_DIR / "resend_suppressed_emails.jsonl")))
PROCESSED_EVENTS_PATH = Path(os.environ.get(
    "ORPHO_RESEND_PROCESSED_EVENTS", str(DATA_DIR / "resend_processed_events.jsonl")))
WEBHOOK_SECRET = os.environ.get("RESEND_WEBHOOK_SECRET", "")
TOLERANCE_SEC = int(os.environ.get("ORPHO_RESEND_TOLERANCE_SEC", "300"))

SUPPRESSED_EVENTS = {"email.bounced", "email.complained"}
_lock = threading.Lock()


# ── Svix signature verification ─────────────────────────────────────────────
def verify_signature(payload: bytes, headers: dict, secret: str,
                     tolerance_sec: int = TOLERANCE_SEC, now: float | None = None) -> bool:
    """Verify a Svix-signed Resend webhook. `headers` keys are matched
    case-insensitively. `secret` is the `whsec_...` value."""
    if not payload or not secret:
        return False
    low = {str(k).lower(): v for k, v in headers.items()}
    svix_id = (low.get("svix-id") or "").strip()
    ts = (low.get("svix-timestamp") or "").strip()
    sig_header = (low.get("svix-signature") or "").strip()
    if not svix_id or not ts or not sig_header:
        return False
    try:
        ts_int = int(ts)
    except ValueError:
        return False
    now = time.time() if now is None else now
    if abs(now - ts_int) > tolerance_sec:
        return False
    key = secret[len("whsec_"):] if secret.startswith("whsec_") else secret
    try:
        secret_bytes = base64.b64decode(key)
    except Exception:
        return False
    signed = f"{svix_id}.{ts}.".encode("utf-8") + payload
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed, hashlib.sha256).digest()).decode("ascii")
    for part in sig_header.split():
        val = part.split(",", 1)[1] if "," in part else part
        if hmac.compare_digest(val, expected):
            return True
    return False


# ── dedup + suppression ledgers ─────────────────────────────────────────────
def _has_processed(event_id: str) -> bool:
    if not event_id or not PROCESSED_EVENTS_PATH.exists():
        return False
    try:
        with PROCESSED_EVENTS_PATH.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    if json.loads(line).get("event_id") == event_id:
                        return True
                except json.JSONDecodeError:
                    continue
    except OSError:
        return False
    return False


def _mark_processed(event_id: str, result: dict) -> None:
    try:
        PROCESSED_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with PROCESSED_EVENTS_PATH.open("a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "event_id": event_id, "result": result},
                               separators=(",", ":")) + "\n")
    except OSError:
        pass


def record_suppression(email: str, reason: str) -> None:
    """Append an address to the suppression ledger (so the mailer skips it)."""
    email = (email or "").strip().lower()
    if not email:
        return
    try:
        SUPPRESSION_LIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with SUPPRESSION_LIST_PATH.open("a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "email": email, "reason": reason},
                               separators=(",", ":")) + "\n")
    except OSError:
        pass


def is_suppressed(email: str) -> bool:
    """True if the address has a recorded hard-bounce / complaint. Crash-safe."""
    email = (email or "").strip().lower()
    if not email or not SUPPRESSION_LIST_PATH.exists():
        return False
    try:
        with SUPPRESSION_LIST_PATH.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    if (json.loads(line).get("email") or "").strip().lower() == email:
                        return True
                except json.JSONDecodeError:
                    continue
    except OSError:
        return False
    return False


# ── event handler ───────────────────────────────────────────────────────────
def _recipients(data: dict) -> list[str]:
    to = data.get("to")
    if isinstance(to, str):
        out = [to]
    elif isinstance(to, list):
        out = [t for t in to if isinstance(t, str)]
    else:
        out = []
    if not out and isinstance(data.get("email"), str):
        out = [data["email"]]
    return [e for e in (x.strip() for x in out) if e]


def handle_event(payload: bytes) -> dict:
    """Process a verified Resend webhook. The caller MUST verify the Svix
    signature first. On bounce/complaint, record recipients to the suppression
    ledger. Idempotent per (email_id, type)."""
    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error": "invalid event JSON"}
    etype = str(event.get("type", ""))
    data = event.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    event_id = f"{data.get('email_id') or event.get('id') or ''}:{etype}"
    with _lock:
        if event_id != ":" and _has_processed(event_id):
            return {"ok": True, "duplicate": event_id}
        result: dict = {"ok": True, "type": etype}
        if etype in SUPPRESSED_EVENTS:
            recips = _recipients(data)
            for e in recips:
                record_suppression(e, etype)
            result["suppressed"] = recips
        else:
            result["ignored"] = True
        if event_id != ":":
            _mark_processed(event_id, result)
        return result
