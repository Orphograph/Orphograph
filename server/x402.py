#!/usr/bin/env python3
"""x402.py — x402 pay-per-anchor rail (USDC on Base), agent-pays path.

An AI agent with no Orphograph account pays cents in USDC for exactly one
anchor, no invoice, no account — the same shape as the L402 rail
(lightning.py) but over the x402 protocol (coinbase/x402), which speaks
plain HTTP + JSON instead of Lightning invoices:

    1. POST /api/anchor with no payment header, past the free tier
         -> 402 with a JSON PaymentRequired body naming price, asset,
            network and the pay-to address (the x402 "accepts" list)
    2. the agent signs an EIP-3009 transferWithAuthorization for that
       exact amount (the x402 client SDK does this; we never do)
    3. retry with the signed payload in the PAYMENT-SIGNATURE header
         -> we VERIFY it with the facilitator (cheap, no chain write);
            only once the anchor itself has actually succeeded do we
            SETTLE it (the facilitator submits the transfer on-chain).

Settle-after-work, not before: an on-chain USDC transfer cannot be
refunded the way a Pack credit or an L402 credential can, so nothing is
charged for an anchor that never happened. See _handle_anchor's x402
block in app.py for the full sequencing and why.

Custody posture: this module never holds a wallet key. Verification and
settlement happen at the FACILITATOR (a hosted service — the public
x402.org testnet facilitator by default, or a self-hosted/CDP one via
ORPHO_X402_FACILITATOR_URL); we only relay the two JSON calls the
protocol defines (/verify, /settle) and read the answer.

Backends (ORPHO_X402_BACKEND):
    (unset)   — real facilitator over HTTPS (ORPHO_X402_FACILITATOR_URL,
                default the public testnet one)
    mock      — deterministic in-process backend for tests ONLY; refuses
                to load unless ORPHO_X402_ALLOW_MOCK=1 so prod can never
                fake a payment.

Wire format verified against the reference implementation's own source
(github.com/coinbase/x402, python/x402/http/constants.py and
python/x402/schemas/payments.py, read 2026-09-29) rather than assumed:
header names, the /verify and /settle request body shape
({x402Version, paymentPayload, paymentRequirements}), and the V2
PaymentRequirements/PaymentPayload field names below are all taken from
that source, not from a summary of it.

Stdlib only, matching the rest of the server. The `x402` PyPI package
(the actual reference client, needed for EIP-712/EIP-3009 signing) is a
CLIENT-side concern for whatever wallet signs the payment; this
server-side module only builds/parses plain JSON and never signs
anything, so it needs no crypto dependency at all.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import file_lock

USER_AGENT = "orphograph/0.1 (stdlib)"
HTTP_TIMEOUT_SEC = 15

# ── protocol constants, verified against python/x402/http/constants.py ────
PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"   # v2 request header
PAYMENT_REQUIRED_HEADER = "PAYMENT-REQUIRED"      # v2, set on the 402 response
PAYMENT_RESPONSE_HEADER = "PAYMENT-RESPONSE"      # v2, set on the paid 200
X_PAYMENT_HEADER = "X-PAYMENT"                    # v1, legacy — still widely
X_PAYMENT_RESPONSE_HEADER = "X-PAYMENT-RESPONSE"  # v1, legacy — sent by tooling
DEFAULT_FACILITATOR_URL = "https://x402.org/facilitator"  # free, public, testnet-only

SCHEME = "exact"
# CAIP-2 network identifiers (confirmed against the spec: Base Sepolia is
# chain 84532, Base mainnet is chain 8453).
NETWORK_BASE_SEPOLIA = "eip155:84532"
NETWORK_BASE_MAINNET = "eip155:8453"
# USDC contract addresses, confirmed against Circle's own docs, BaseScan and
# Blockscout (three independent sources, 2026-09-29). 6 decimals on both.
USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
USDC_BASE_MAINNET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # not used until mainnet is approved

MAX_TIMEOUT_SECONDS = 60
_LEDGER_FILE = "x402_ledger.jsonl"


def _data_dir() -> Path:
    # Re-read the env each call: tests point ORPHO_DATA_DIR at temp dirs.
    return Path(os.environ.get("ORPHO_DATA_DIR", "."))


def configured() -> bool:
    """True iff a real payment can be accepted right now.

    Mirrors lightning.configured(): the mock backend is a separate, always
    explicit opt-in so a stray env var can never make production accept a
    fake payment.
    """
    backend = os.environ.get("ORPHO_X402_BACKEND", "").strip().lower()
    if backend == "mock":
        return os.environ.get("ORPHO_X402_ALLOW_MOCK") == "1"
    return bool(os.environ.get("ORPHO_X402_PAY_TO_ADDRESS", "").strip())


def network() -> str:
    return os.environ.get("ORPHO_X402_NETWORK", NETWORK_BASE_SEPOLIA).strip()


def asset() -> str:
    return os.environ.get("ORPHO_X402_ASSET", USDC_BASE_SEPOLIA).strip()


def facilitator_url() -> str:
    return os.environ.get("ORPHO_X402_FACILITATOR_URL", DEFAULT_FACILITATOR_URL).rstrip("/")


def pay_to() -> str:
    return os.environ.get("ORPHO_X402_PAY_TO_ADDRESS", "").strip()


def price_cents() -> int:
    return int(os.environ.get("ORPHO_X402_PRICE_CENTS", "5"))


def price_atomic() -> str:
    """USDC has 6 decimals: 1 cent = 10_000 atomic units."""
    return str(price_cents() * 10_000)


# ── requirements / challenge body (server -> agent) ────────────────────────

def build_payment_requirements(resource_url: str) -> dict:
    """The PaymentRequirements object we ask the agent to satisfy.

    Field names are the exact V2 wire names from
    python/x402/schemas/payments.py: scheme, network, asset, amount,
    payTo, maxTimeoutSeconds, extra.
    """
    return {
        "scheme": SCHEME,
        "network": network(),
        "asset": asset(),
        "amount": price_atomic(),
        "payTo": pay_to(),
        "maxTimeoutSeconds": MAX_TIMEOUT_SECONDS,
        "extra": {"resource": resource_url},
    }


def build_payment_required_body(resource_url: str, *, error: str | None = None) -> dict:
    """The 402 response body: PaymentRequired, V2 shape."""
    return {
        "x402Version": 2,
        "error": error,
        "resource": {"url": resource_url, "description": "Anchor a SHA-256 hash to Bitcoin",
                     "mimeType": "application/json"},
        "accepts": [build_payment_requirements(resource_url)],
        "extensions": None,
    }


# ── parsing the agent's retry (agent -> server) ─────────────────────────────

def parse_payment_header(headers) -> tuple[int, dict] | None:
    """Read the payment payload the agent sent back, if any.

    Checks the current header first, then the v1 legacy one — a lot of
    already-built x402 tooling still defaults to v1 (observed across
    third-party SDKs, 2026-09-29), and accepting either costs nothing.

    Returns (x402_version, payment_payload_dict), or None if neither
    header is present. Raises ValueError if a header is present but not
    valid base64 JSON — that is a caller bug, not "no payment offered".
    """
    raw = headers.get(PAYMENT_SIGNATURE_HEADER, "").strip()
    version = 2
    if not raw:
        raw = headers.get(X_PAYMENT_HEADER, "").strip()
        version = 1
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw + "=" * (-len(raw) % 4))
        payload = json.loads(decoded)
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        raise ValueError(f"malformed payment header: {e}") from e
    if not isinstance(payload, dict):
        raise ValueError("payment payload must be a JSON object")
    return version, payload


def encode_payment_response_header(settle_response: dict) -> str:
    """Base64-encode a SettleResponse for the PAYMENT-RESPONSE header."""
    return base64.b64encode(json.dumps(settle_response, separators=(",", ":")).encode()).decode()


# ── facilitator calls ───────────────────────────────────────────────────────

class FacilitatorUnavailable(RuntimeError):
    """The facilitator could not be reached at all (network/timeout).

    Never treated as "payment failed" and never as "payment ok" — same
    discipline as lightning.SpentSetUnavailable: an unknown answer must
    fail the request loudly, not resolve it in either direction.
    """


class FacilitatorError(RuntimeError):
    """The facilitator answered, but with an error or unparseable body."""


def _facilitator_call(path: str, body: dict) -> dict:
    """The ONE function that talks to the facilitator. Tests stub the
    mock backend below rather than this function directly, so the real
    JSON-building and header-parsing code always runs end to end."""
    backend = os.environ.get("ORPHO_X402_BACKEND", "").strip().lower()
    if backend == "mock":
        if os.environ.get("ORPHO_X402_ALLOW_MOCK") != "1":
            raise FacilitatorError("mock backend not allowed")
        return _mock_facilitator_call(path, body)
    url = facilitator_url() + path
    req = urllib.request.Request(
        url, method="POST", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read())
        except (ValueError, json.JSONDecodeError):
            detail = {"raw": str(e)}
        raise FacilitatorError(f"facilitator {path} HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, socket.timeout, OSError, json.JSONDecodeError) as e:
        raise FacilitatorUnavailable(f"{type(e).__name__}: {e}") from e


def verify(payment_payload: dict, payment_requirements: dict) -> dict:
    """Ask the facilitator whether this payload would settle. No chain
    write happens here — cheap, and safe to call before doing any work.

    Returns a dict shaped like VerifyResponse: {"isValid": bool, "payer":
    str | None, "invalidReason": str | None}. Raises FacilitatorUnavailable
    / FacilitatorError; never returns a fabricated isValid on a failure to
    reach the facilitator.
    """
    body = {
        "x402Version": payment_payload.get("x402Version", 2),
        "paymentPayload": payment_payload,
        "paymentRequirements": payment_requirements,
    }
    return _facilitator_call("/verify", body)


def settle(payment_payload: dict, payment_requirements: dict) -> dict:
    """Submit the payment for real. Only called after the paid work
    (the anchor) has already succeeded — see app.py's x402 block.

    Returns a dict shaped like SettleResponse: {"success": bool,
    "transaction": str | None, "network": str, "errorReason": str | None}.
    """
    body = {
        "x402Version": payment_payload.get("x402Version", 2),
        "paymentPayload": payment_payload,
        "paymentRequirements": payment_requirements,
    }
    return _facilitator_call("/settle", body)


# ── mock backend (tests only) ───────────────────────────────────────────────

_MOCK_SETTLED_NONCES: set[str] = set()


def _mock_inner_and_nonce(body: dict) -> tuple[dict, str]:
    inner = (body.get("paymentPayload") or {}).get("payload") or {}
    nonce = (inner.get("authorization") or {}).get("nonce") or inner.get("nonce") or ""
    return inner, nonce


def _mock_facilitator_call(path: str, body: dict) -> dict:
    """Deterministic stand-in for the real facilitator. A test controls the
    outcome by setting payload["mock_outcome"] on the PaymentPayload it
    builds: "ok" (default), "invalid", "settle_fail", "unreachable",
    "unreachable_settle". Enforces the SAME single-use-nonce property the
    real chain gives an EIP-3009 authorization for free, so a test can
    prove replay protection without a real signature.
    """
    inner, nonce = _mock_inner_and_nonce(body)
    outcome = inner.get("mock_outcome", "ok")
    payer = (inner.get("authorization") or {}).get("from") or "0xmockPayerAddress00000000000000000000000"
    net = (body.get("paymentRequirements") or {}).get("network", network())

    if path == "/verify":
        if outcome == "unreachable":
            raise FacilitatorUnavailable("mock: simulated network failure on verify")
        if outcome == "invalid":
            return {"isValid": False, "payer": payer, "invalidReason": "mock: invalid signature"}
        return {"isValid": True, "payer": payer, "invalidReason": None}

    if path == "/settle":
        if outcome == "unreachable_settle":
            raise FacilitatorUnavailable("mock: simulated network failure on settle")
        if outcome == "settle_fail":
            return {"success": False, "transaction": None, "network": net,
                    "payer": payer, "errorReason": "mock: settlement failed"}
        if nonce and nonce in _MOCK_SETTLED_NONCES:
            return {"success": False, "transaction": None, "network": net,
                    "payer": payer, "errorReason": "mock: authorization nonce already used"}
        if nonce:
            _MOCK_SETTLED_NONCES.add(nonce)
        return {"success": True, "transaction": f"0xmock{secrets.token_hex(16)}",
                "network": net, "payer": payer, "errorReason": None}

    raise FacilitatorError(f"mock: unknown path {path}")


def reset_mock_state() -> None:
    """TESTS ONLY: clear the mock nonce set between test cases."""
    _MOCK_SETTLED_NONCES.clear()


# ── single-use claim (atomic; mirrors lightning.py's spent-set exactly) ────
#
# verify() is side-effect-free — the facilitator marks nothing used, only
# settle() consumes the on-chain nonce, and settle() does not run here until
# AFTER the anchor. Without a claim of our own, N concurrent requests
# carrying the SAME signed payload would each pass verify(), each get a
# free anchor from engine.anchor_hash, and only one of the N settle() calls
# would actually land on-chain — the office paid once, N-1 anchors free.
# This is the identical bug class lightning.claim()'s docstring documents
# ("eight concurrent requests with one paid credential produced eight
# receipts"); the fix is the same shape, applied before it ever shipped.

_CLAIM_FILE = "x402_claimed.jsonl"


def _claim_path() -> Path:
    return _data_dir() / _CLAIM_FILE


def payment_identifier(payment_payload: dict) -> str:
    """A stable single-use identifier for a signed payment payload.

    The exact-EVM scheme's own single-use property is the EIP-3009
    authorization nonce — that is what actually gets consumed on-chain.
    Falling back to a hash of the whole payload keeps any other scheme
    safely single-use too, rather than silently skipping the claim for a
    shape this module doesn't specifically recognise.
    """
    inner = payment_payload.get("payload") or {}
    nonce = (inner.get("authorization") or {}).get("nonce")
    if isinstance(nonce, str) and nonce:
        return f"nonce:{nonce}"
    digest = hashlib.sha256(
        json.dumps(payment_payload, sort_keys=True).encode()).hexdigest()
    return f"payload:{digest}"


class ClaimSetUnavailable(RuntimeError):
    """The claim ledger exists but could not be read, so freshness is
    UNKNOWN. Mirrors lightning.SpentSetUnavailable: the caller must fail
    the request rather than resolve an unprovable state to "not claimed"."""


def _last_claim_state(fh, ident: str) -> str:
    state = ""
    for line in fh:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("id") != ident:
            continue
        state = "released" if row.get("released") else "claimed"
    return state


def is_claimed(ident: str) -> bool:
    path = _claim_path()
    if not path.exists():
        return False
    try:
        with path.open() as f:
            return _last_claim_state(f, ident) == "claimed"
    except OSError as e:
        raise ClaimSetUnavailable(str(e)) from e


def claim(payment_payload: dict, receipt_id: str = "") -> tuple[bool, str]:
    """Atomically reserve this payment payload. Returns (claimed, ident).

    Read and append happen under one exclusive lock (file_lock.locked),
    so the check and the mark cannot be interleaved by another thread or
    process — the same guarantee lightning.claim() gives L402 credentials.
    """
    ident = payment_identifier(payment_payload)
    path = _claim_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock.locked(path, mode="a+") as f:
        f.seek(0)
        try:
            if _last_claim_state(f, ident) == "claimed":
                return False, ident
        except OSError as e:
            raise ClaimSetUnavailable(str(e)) from e
        f.write(json.dumps({
            "id": ident, "receipt_id": receipt_id,
            "claimed_at": int(time.time()),
        }) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return True, ident


def release(ident: str) -> None:
    """Undo a claim that never produced an anchor (0-calendar outage)."""
    path = _claim_path()
    try:
        with file_lock.locked(path, mode="a") as f:
            f.write(json.dumps({
                "id": ident, "released": True,
                "released_at": int(time.time()),
            }) + "\n")
    except OSError:
        # Best effort — a stuck claim costs one customer a retry with a
        # fresh signature; a lost claim risks unbounded free anchors.
        # Fail in the safe direction, same call lightning.release() makes.
        pass


# ── ledger (append-only; same shape as credits.py / referrals.py) ─────────

def _ledger_path() -> Path:
    return _data_dir() / _LEDGER_FILE


def record_settlement(*, receipt_id: str, amount_atomic: str, asset_addr: str,
                       network_id: str, tx_hash: str | None, payer: str | None,
                       settled: bool, reason: str = "") -> None:
    """Append one row per settle() attempt. No payer identity beyond the
    on-chain address already present in `payer`/`tx_hash` — nothing else
    is logged, per the plan's "no payer identity beyond on-chain data"."""
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock.locked(path, mode="a") as f:
        f.write(json.dumps({
            "ts": int(time.time()),
            "receipt_id": receipt_id,
            "amount_atomic": amount_atomic,
            "asset": asset_addr,
            "network": network_id,
            "tx_hash": tx_hash,
            "payer": payer,
            "settled": settled,
            "reason": reason,
        }, separators=(",", ":")) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def iter_ledger_rows():
    path = _ledger_path()
    if not path.exists():
        return
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
