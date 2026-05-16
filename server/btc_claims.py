"""btc_claims.py — append-only ledger for self-reported Bitcoin payments.

A buyer pays to our bc1q… address via any BTC wallet, then submits the
transaction ID + their email through the /api/btc/claim endpoint. We log
the claim here for manual fulfillment (founder verifies on-chain, then
issues a Pack claim code).

Auto-verification via mempool.space is a roadmap item — for v0.1 this is
a human-in-the-loop pipeline. The ledger is the source of truth for
"who is waiting on a Pack code."

Privacy: email is stored hashed (HMAC keyed by ORPHO_HMAC_SECRET) so the
ledger doesn't leak a list of customer emails to anyone who reads the
disk. The plaintext email is only printed to stderr at submission time
(so the founder sees it in `fly logs`) and never written to a tracked file.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import file_lock

DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
LEDGER = DATA_DIR / "btc_claims.jsonl"

# We use the existing HMAC secret if available; otherwise generate a stable
# per-disk fallback so we don't deanonymize emails in the at-rest ledger.
_FALLBACK_KEY_FILE = DATA_DIR / ".btc_claims_pepper"
def _get_pepper() -> bytes:
    secret = os.environ.get("ORPHO_HMAC_SECRET", "")
    if secret:
        return secret.encode("utf-8")
    if _FALLBACK_KEY_FILE.exists():
        return _FALLBACK_KEY_FILE.read_bytes()
    pepper = os.urandom(32)
    _FALLBACK_KEY_FILE.write_bytes(pepper)
    try:
        os.chmod(_FALLBACK_KEY_FILE, 0o600)
    except OSError:
        pass
    return pepper

_EMAIL_RE  = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_TXID_RE   = re.compile(r"^[0-9a-fA-F]{64}$")
_PACK_SIZES = {10, 50, 250}
MAX_NOTE_LEN = 500

_lock = threading.Lock()


def _hash_email(email: str) -> str:
    return hmac.new(_get_pepper(), email.lower().encode("utf-8"), hashlib.sha256).hexdigest()


def submit(
    *,
    email: str,
    txid: str,
    pack_size: int,
    usd: float | None = None,
    btc_amount: float | None = None,
    btc_address: str = "",
    note: str = "",
    source_ip: str = "",
) -> tuple[bool, str]:
    """Append a claim. Returns (ok, error_or_id)."""
    email = (email or "").strip()
    txid  = (txid or "").strip().lower()
    note  = (note or "")[:MAX_NOTE_LEN]
    if not _EMAIL_RE.match(email):
        return False, "invalid email"
    if not _TXID_RE.match(txid):
        return False, "invalid txid"
    if pack_size not in _PACK_SIZES:
        return False, "invalid pack size"

    claim_id = "btc_" + os.urandom(8).hex()
    record = {
        "claim_id": claim_id,
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "email_hash": _hash_email(email),
        "txid": txid,
        "pack_size": int(pack_size),
        "usd": float(usd) if usd is not None else None,
        "btc_amount": float(btc_amount) if btc_amount is not None else None,
        "btc_address": btc_address[:90] if isinstance(btc_address, str) else "",
        "note": note,
        "source_ip": source_ip[:48] if isinstance(source_ip, str) else "",
        "status": "pending",  # pending | confirmed | fulfilled | rejected
    }
    line = json.dumps(record, separators=(",", ":")) + "\n"
    with _lock:
        with file_lock.locked(LEDGER, mode="a") as f:
            f.write(line)
    # Founder-visible log line (no plaintext email written to disk).
    sys.stderr.write(f"[btc_claim] new {claim_id} email={email} txid={txid} pack={pack_size}\n")
    return True, claim_id


def list_pending() -> list[dict]:
    """Read all pending claims — used by the founder dashboard."""
    if not LEDGER.exists():
        return []
    out = []
    with LEDGER.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") == "pending":
                out.append(r)
    return out
