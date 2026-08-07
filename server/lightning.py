#!/usr/bin/env python3
"""lightning.py — L402 pay-per-anchor rail (Lightning, no accounts, no token).

An AI agent with no Orphograph account pays sats for exactly one anchor:

    1. POST /api/ln/quote  (or hit /api/anchor past the free tier)
         → 402 with  WWW-Authenticate: L402 token="<macaroon>", invoice="<bolt11>"
    2. pay the invoice over Lightning → obtain the preimage
    3. retry with  Authorization: L402 <macaroon>:<preimage_hex>
         → SHA256(preimage) must equal the payment_hash bound inside the
           macaroon, the HMAC must verify, the invoice must be settled at
           the backend, and the macaroon must be unspent (single-use).

Custody posture: inbound payments only, held by the configured provider
under the FOUNDER's account (armed via fly secrets, exactly like Stripe).
This module never holds Lightning keys and never sends funds.

Backends (ORPHO_LN_BACKEND):
    lnbits    — self-hosted or hosted LNbits instance (REST)
    opennode  — OpenNode custodial API (REST)
    mock      — deterministic in-process backend for tests ONLY; refuses to
                load unless ORPHO_LN_ALLOW_MOCK=1 so prod can never fake pay.

Stdlib only, matching the rest of the server.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import file_lock

DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", "."))
USER_AGENT = "orphograph/0.1 (stdlib)"
HTTP_TIMEOUT_SEC = 15

PRICE_SATS = int(os.environ.get("ORPHO_LN_PRICE_SATS", "100"))
MACAROON_TTL_SEC = int(os.environ.get("ORPHO_LN_MACAROON_TTL", "3600"))

_SPENT_FILE = "ln_spent.jsonl"
_SECRET_FILE = "ln_macaroon_secret"


def configured() -> bool:
    backend = os.environ.get("ORPHO_LN_BACKEND", "").strip().lower()
    if backend == "lnbits":
        return bool(os.environ.get("ORPHO_LN_LNBITS_URL")
                    and os.environ.get("ORPHO_LN_LNBITS_KEY"))
    if backend == "opennode":
        return bool(os.environ.get("ORPHO_LN_OPENNODE_KEY"))
    if backend == "mock":
        return os.environ.get("ORPHO_LN_ALLOW_MOCK") == "1"
    return False


# ── macaroon-lite: HMAC-signed single-purpose bearer token ─────────────────

def _secret() -> bytes:
    path = _data_dir() / _SECRET_FILE
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    s = secrets.token_bytes(32)
    path.write_bytes(s)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return s


def _data_dir() -> Path:
    # Re-read the env each call: tests point ORPHO_DATA_DIR at temp dirs.
    return Path(os.environ.get("ORPHO_DATA_DIR", "."))


def mint_macaroon(payment_hash: str, price_sats: int) -> str:
    body = json.dumps({
        "v": 1,
        "payment_hash": payment_hash,
        "price_sats": price_sats,
        "expires_at": int(time.time()) + MACAROON_TTL_SEC,
    }, separators=(",", ":")).encode()
    sig = hmac.new(_secret(), body, hashlib.sha256).digest()
    return (base64.urlsafe_b64encode(body).decode().rstrip("=") + "."
            + base64.urlsafe_b64encode(sig).decode().rstrip("="))


def _b64pad(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def parse_macaroon(token: str) -> dict | None:
    """Return the macaroon body iff the HMAC verifies and it is unexpired."""
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = _b64pad(body_b64)
        sig = _b64pad(sig_b64)
    except (ValueError, TypeError):
        return None
    want = hmac.new(_secret(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, want):
        return None
    try:
        doc = json.loads(body)
    except json.JSONDecodeError:
        return None
    if doc.get("v") != 1 or not isinstance(doc.get("payment_hash"), str):
        return None
    if int(doc.get("expires_at", 0)) < time.time():
        return None
    return doc


# ── spent-set (single-use enforcement, append-only on disk) ────────────────

def _spent_path() -> Path:
    return _data_dir() / _SPENT_FILE


def _last_state(fh, payment_hash: str) -> str:
    """LAST state recorded for this hash: "claimed", "released", or "".

    Order matters, not mere presence. The ledger is append-only, so a release
    tombstone follows the claim it undoes; treating any mention as "spent"
    would permanently burn a credential we had just refunded. A torn or
    corrupt LINE is skipped — the FILE is still readable, so the verdict from
    the other lines stands.
    """
    state = ""
    for line in fh:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("payment_hash") != payment_hash:
            continue
        state = "released" if row.get("released") else "claimed"
    return state


class SpentSetUnavailable(RuntimeError):
    """The spent-set could not be read, so freshness is UNKNOWN.

    Never downgrade this to "not spent". `is_spent` used to swallow OSError
    and return False, i.e. "I could not check, so go ahead" — which on this
    system is not hypothetical: root-owned files under /data have twice made
    server-side reads fail (api_keys.jsonl 2026-07-27, webhooks.jsonl
    2026-07-28). A credential replayed while the ledger is unreadable is
    money lost with no record that it happened.
    """


def is_spent(payment_hash: str) -> bool:
    """True iff this payment_hash has already bought an anchor.

    Raises SpentSetUnavailable when the ledger exists but cannot be read.
    The caller must fail the request — an unreadable spent-set means we
    cannot prove freshness, and unprovable freshness is not freshness.
    """
    path = _spent_path()
    if not path.exists():
        return False
    try:
        with path.open() as f:
            return _last_state(f, payment_hash) == "claimed"
    except OSError as e:
        raise SpentSetUnavailable(str(e)) from e


def claim(payment_hash: str, receipt_id: str = "") -> bool:
    """Atomically reserve this credential. True iff WE claimed it.

    check-then-act was the whole bug: /api/anchor tested is_spent() 176 lines
    before mark_spent(), with five OpenTimestamps calendar submissions in the
    gap and a threading server underneath. Eight concurrent requests carrying
    one paid credential produced eight receipts and zero rejections
    (reproduced 2026-08-07). verify_l402's own docstring says the caller is
    responsible for making "spend atomic with the anchor" — it never was.

    Read and append happen under one exclusive fcntl lock, so the check and
    the mark cannot be interleaved by another thread or another process.
    """
    path = _spent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock.locked(path, mode="a+") as f:
        f.seek(0)
        try:
            if _last_state(f, payment_hash) == "claimed":
                return False
        except OSError as e:
            raise SpentSetUnavailable(str(e)) from e
        f.write(json.dumps({
            "payment_hash": payment_hash,
            "receipt_id": receipt_id,
            "claimed_at": int(time.time()),
        }) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return True


def release(payment_hash: str) -> None:
    """Undo a claim that never produced an anchor.

    Appends a tombstone rather than rewriting the ledger: the file is
    append-only by design, and rewriting it under contention is how an
    audit log loses rows.
    """
    path = _spent_path()
    try:
        with file_lock.locked(path, mode="a") as f:
            f.write(json.dumps({
                "payment_hash": payment_hash,
                "released": True,
                "released_at": int(time.time()),
            }) + "\n")
    except OSError:
        # Best effort. A stuck claim costs the customer one anchor; a lost
        # claim costs us an unbounded number. Fail in the safe direction.
        pass


def mark_spent(payment_hash: str, receipt_id: str) -> None:
    path = _spent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"payment_hash": payment_hash,
                            "receipt_id": receipt_id,
                            "ts": int(time.time())},
                           separators=(",", ":")) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ── backends ───────────────────────────────────────────────────────────────

def _http_json(url: str, method: str = "GET", body: dict | None = None,
               headers: dict | None = None) -> tuple[bool, dict | str]:
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 "User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return True, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except (urllib.error.URLError, socket.timeout, OSError,
            json.JSONDecodeError) as e:
        return False, f"{type(e).__name__}: {e}"


# In-process state for the mock backend (tests only).
_MOCK_INVOICES: dict[str, dict] = {}


def create_invoice(amount_sats: int, memo: str) -> tuple[bool, dict | str]:
    """Returns (True, {"payment_hash", "bolt11"}) or (False, error_str)."""
    backend = os.environ.get("ORPHO_LN_BACKEND", "").strip().lower()
    if backend == "mock":
        if os.environ.get("ORPHO_LN_ALLOW_MOCK") != "1":
            return False, "mock backend not allowed"
        preimage = secrets.token_bytes(32)
        payment_hash = hashlib.sha256(preimage).hexdigest()
        _MOCK_INVOICES[payment_hash] = {"preimage": preimage.hex(),
                                        "settled": False,
                                        "amount_sats": amount_sats}
        return True, {"payment_hash": payment_hash,
                      "bolt11": f"lnmock1{payment_hash[:20]}"}
    if backend == "lnbits":
        ok, out = _http_json(
            os.environ["ORPHO_LN_LNBITS_URL"].rstrip("/") + "/api/v1/payments",
            method="POST",
            body={"out": False, "amount": amount_sats, "memo": memo},
            headers={"X-Api-Key": os.environ["ORPHO_LN_LNBITS_KEY"]})
        if not ok:
            return False, str(out)
        return True, {"payment_hash": out.get("payment_hash", ""),
                      "bolt11": out.get("payment_request", "")}
    if backend == "opennode":
        ok, out = _http_json(
            "https://api.opennode.com/v1/charges", method="POST",
            body={"amount": amount_sats, "currency": "BTC",
                  "description": memo},
            headers={"Authorization": os.environ["ORPHO_LN_OPENNODE_KEY"]})
        if not ok:
            return False, str(out)
        data = out.get("data", {})
        ln = data.get("lightning_invoice", {})
        return True, {"payment_hash": ln.get("payment_hash", data.get("id", "")),
                      "bolt11": ln.get("payreq", "")}
    return False, "lightning not configured"


def invoice_settled(payment_hash: str) -> bool:
    """Backend-truth settlement check — preimage alone is not trusted."""
    backend = os.environ.get("ORPHO_LN_BACKEND", "").strip().lower()
    if backend == "mock":
        inv = _MOCK_INVOICES.get(payment_hash)
        return bool(inv and inv["settled"])
    if backend == "lnbits":
        ok, out = _http_json(
            os.environ["ORPHO_LN_LNBITS_URL"].rstrip("/")
            + f"/api/v1/payments/{payment_hash}",
            headers={"X-Api-Key": os.environ["ORPHO_LN_LNBITS_KEY"]})
        return bool(ok and out.get("paid"))
    if backend == "opennode":
        ok, out = _http_json(
            f"https://api.opennode.com/v1/charge/{payment_hash}",
            headers={"Authorization": os.environ["ORPHO_LN_OPENNODE_KEY"]})
        return bool(ok and out.get("data", {}).get("status") == "paid")
    return False


def mock_pay(payment_hash: str) -> str | None:
    """TESTS ONLY: settle a mock invoice, return its preimage hex."""
    if os.environ.get("ORPHO_LN_ALLOW_MOCK") != "1":
        return None
    inv = _MOCK_INVOICES.get(payment_hash)
    if not inv:
        return None
    inv["settled"] = True
    return inv["preimage"]


# ── the verification entrypoint app.py calls ───────────────────────────────

def verify_l402(auth_header: str) -> tuple[str | None, str]:
    """Validate 'L402 <macaroon>:<preimage_hex>'.

    Returns (payment_hash, "") on success or (None, reason). The caller is
    responsible for the spent check + mark (so spend is atomic with the
    anchor) — this function proves payment, not freshness.
    """
    if not auth_header.startswith("L402 "):
        return None, "not an L402 header"
    try:
        token, preimage_hex = auth_header[5:].strip().rsplit(":", 1)
    except ValueError:
        return None, "malformed L402 credentials"
    doc = parse_macaroon(token)
    if doc is None:
        return None, "invalid or expired macaroon"
    try:
        preimage = bytes.fromhex(preimage_hex)
    except ValueError:
        return None, "preimage is not hex"
    if hashlib.sha256(preimage).hexdigest() != doc["payment_hash"]:
        return None, "preimage does not match payment_hash"
    if not invoice_settled(doc["payment_hash"]):
        return None, "invoice not settled"
    return doc["payment_hash"], ""
