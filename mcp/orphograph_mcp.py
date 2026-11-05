#!/usr/bin/env python3
"""orphograph_mcp.py — Model Context Protocol server for Orphograph.

A single-file MCP server that lets a Claude / Claude Code / Cursor /
any-MCP-host instance anchor files to the Bitcoin chain via Orphograph
without the file ever leaving the user's machine. The server reads
files from the local filesystem, computes their SHA-256 (and SHA-512
sibling witness) in-process, and transmits ONLY those fingerprints
to https://orphograph.com/api/anchor. The file body itself is never
transmitted.

Protocol
--------
MCP 2024-11-05 over stdio. Newline-delimited JSON-RPC 2.0 messages
on stdin (request) and stdout (response). All logging goes to stderr
so the protocol channel stays clean.

Tools exposed
-------------
- orphograph_anchor_file(path)
    Read the file at `path` locally. Compute SHA-256 and SHA-512.
    POST {hash_hex, sha512_hex} to /api/anchor. Return the receipt
    id and receipt URL.

- orphograph_anchor_folder(path)
    Walk `path`, hash every file locally into an RFC-6962 Merkle tree,
    and POST only the manifest (relative paths + SHA-256 digests + the
    root) to /api/anchor_folder. Return the receipt id and a hosted
    certificate URL. No file body leaves the device; each file stays
    independently verifiable via an inclusion proof.

- orphograph_anchor_output(text, label?, c2pa_manifest_hash?, zk_proof?)
    Hash a string of generated output (an agent result, a model
    response, a transcript) in-process and POST only the digests to
    /api/anchor. The text never leaves the device. An optional
    zk_proof is forwarded and echoed back as zk_provenance; it proves
    a fixed published hash-chain procedure ran, NOT that a particular
    AI model ran.

- orphograph_verify_receipt(receipt_id)
    GET /api/verify/<id>. Returns what the OFFICE RECORDED: calendar
    counts, btc_pinned_at when present, and the receipt URL. This is a
    lookup, not an independent check — it does not consult the chain,
    and btc_pinned_at is when the pin was OBSERVED, not the block's own
    timestamp. For an independent chain check run `ots verify` against
    the receipt's .ots files.

- orphograph_verify_lineage(receipt_id)
    Walk an edit-lineage chain from a receipt back through its
    committed parents, reporting the ordering the anchors establish.

- orphograph_list_vault(limit?)
    GET /api/me/anchors with X-Orpho-Api-Key header. Returns the
    signed-in subscriber's anchored receipts. Requires ORPHO_API_KEY
    to be set in the environment, otherwise the server returns a
    clean error directing the user to /account.html for a key.

- orphograph_anchor_run(steps, label?)
    Anchor every step of an agent run under one receipt, as a folder
    manifest with paths "step-NNNNNN" (POST /api/anchor_folder,
    paths_public). Only one SHA-256 per step and the root are sent.

- orphograph_verify_output(receipt_id, text | sha256_hex)
    For the agent receiving an output: does this receipt cover it?
    Trusts only the anchored hash; a run's manifest is recomputed
    locally and must equal it before any step counts as a match.

Privacy contract
----------------
- File path stays on the user's machine.
- Only the SHA-256 and SHA-512 fingerprints cross the wire.
- The script makes no network call until a tool is explicitly invoked.
- The script never reads file contents into memory beyond what's
  needed for the hash computation (streamed in 1MB chunks).

Configuration
-------------
Environment variables:
  ORPHO_API_KEY    Optional. If set, used as X-Orpho-Api-Key on all
                   requests, applying the customer's subscription
                   limits and enabling list_vault. Issued from
                   https://orphograph.com/account.html.
  ORPHO_BASE_URL   Optional. Defaults to https://orphograph.com.
                   Override for self-hosted instances or testing.

  Paying per anchor with x402 (0.1.6; off unless BOTH of the first two are set):
  ORPHO_X402_PAYER_KEY_FILE    Path to a file holding the hex private key of a
                   wallet with USDC on Base. chmod 600, or it is refused.
                   Use a dedicated wallet holding only what the cap can spend.
  ORPHO_X402_DAILY_CAP_CENTS   Most this server may sign for in one UTC day.
  ORPHO_X402_MAX_CENTS_PER_CALL  Most for one anchor (default 5).
  Past the free tier, an anchor answered with 402 is paid (1 cent today):
  the USDC contract, its EIP-712 domain and the chain are pinned here,
  and the key never leaves this process. Spend is kept in
  ~/.orphograph/x402_spend.json (ORPHO_X402_SPEND_FILE to move it).

License: MIT. See https://github.com/Orphograph/Orphograph
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# ── configuration ──────────────────────────────────────────────────

BASE_URL = os.environ.get("ORPHO_BASE_URL", "https://orphograph.com").rstrip("/")
API_KEY = os.environ.get("ORPHO_API_KEY", "").strip()
HTTP_TIMEOUT_SEC = 15
HASH_CHUNK_BYTES = 1024 * 1024   # 1 MB stream chunk
# Cloudflare sits in front of api.orphograph.com; the default urllib UA
# is recognised as a bot and 403'd (CF error 1010). A browser-shaped UA
# combined with `Accept-Encoding: identity` is the same pattern used by
# the website's outbound clients — keep them aligned.
COMMON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "OrphographMCP/0.1 (+https://orphograph.com/mcp.html)",
    "Accept-Encoding": "identity",
}
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "orphograph"
SERVER_VERSION = "0.1.6"


# ── stderr logger ──────────────────────────────────────────────────


def log(*parts: Any) -> None:
    """Log to stderr — stdout is the JSON-RPC channel and must stay clean."""
    sys.stderr.write("[orphograph-mcp] " + " ".join(str(p) for p in parts) + "\n")
    sys.stderr.flush()


# ── hashing ────────────────────────────────────────────────────────


def hash_file_local(path: str) -> tuple[str, str, int]:
    """Stream-hash a local file with SHA-256 and SHA-512.

    Returns (sha256_hex, sha512_hex, size_bytes). Never loads the
    whole file into memory; reads in 1 MB chunks.
    """
    h256 = hashlib.sha256()
    h512 = hashlib.sha512()
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(HASH_CHUNK_BYTES)
            if not chunk:
                break
            h256.update(chunk)
            h512.update(chunk)
            size += len(chunk)
    return h256.hexdigest(), h512.hexdigest(), size


# ── HTTP ───────────────────────────────────────────────────────────


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A 3xx is an answer, not an instruction to go elsewhere.

    urllib's default opener follows a redirect to any host and copies every
    header but the body's, so X-Orpho-Api-Key went to whatever host a 3xx
    named (shown 2026-11-05 with two local servers). The same fix the other
    clients carry (tests/test_credential_clients_refuse_redirects.py): the 3xx
    surfaces as an HTTPError and the key never leaves BASE_URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirects)


def _urlopen(req, timeout):
    """Every request this server makes goes through here."""
    return _OPENER.open(req, timeout=timeout)


def _http(method: str, path: str, body: dict | None = None) -> dict:
    """JSON request to BASE_URL. Returns the parsed response body or {error: ...}."""
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = dict(COMMON_HEADERS)
    if API_KEY:
        headers["X-Orpho-Api-Key"] = API_KEY
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return {"error": "bad_json", "status": resp.status, "raw": raw[:400]}
    except urllib.error.HTTPError as e:
        body_raw = ""
        try:
            body_raw = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        if 300 <= e.code < 400:
            # https://www.orphograph.com and http://orphograph.com answer 301
            # (probed 2026-11-05); the default base URL answers directly.
            location = (e.headers.get("Location") if e.headers else None) or ""
            return {"error": "redirect_refused", "status": e.code, "location": location[:300],
                    "detail": "the server answered with a redirect; it was not followed, "
                              "so the API key stayed with " + BASE_URL
                              + (". If that target is right, set ORPHO_BASE_URL to its "
                                 "scheme and host." if location else "")}
        return {"error": "http_error", "status": e.code, "body": body_raw}
    except urllib.error.URLError as e:
        return {"error": "network_error", "reason": str(getattr(e, "reason", e))}
    except TimeoutError:
        return {"error": "timeout"}


# ── tools ──────────────────────────────────────────────────────────


# ── x402 payment (opt-in, 0.1.6) ───────────────────────────────────
# Past the free tier, /api/anchor answers 402 with a 1-cent USDC price on
# Base. With a payer key file AND a daily cap configured, this server pays
# it: it signs an EIP-3009 TransferWithAuthorization locally and retries.
# The key never leaves this process; only the signature is sent.
#
# Everything in the 402 is a request from the server, not an instruction:
# the asset, its EIP-712 domain and the chain are pinned here, and the
# amount must fit both caps. Checked 2026-11-05 against the live Base USDC
# contract: TRANSFER_WITH_AUTHORIZATION_TYPEHASH = 0x7c7c6cdb…2267 and
# DOMAIN_SEPARATOR = 0x02fa7265…834f match what this code computes.
#
# The secp256k1 arithmetic below is plain Python: correct (tested against
# the EIP-712 specification's signature and an independent ECDSA library)
# but not constant-time. It signs on the operator's own machine for cents;
# use a dedicated wallet holding only what the daily cap can spend.

X402_OPT_IN_HEADER = "X-Orpho-X402"
_PAYMENT_REQUIRED_HEADER = "PAYMENT-REQUIRED"
_PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"
_PAYMENT_RESPONSE_HEADER = "PAYMENT-RESPONSE"

# network -> (USDC contract, EIP-712 name, version, chain id)
_X402_ASSETS = {
    "eip155:8453": ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "USD Coin", "2", 8453),
    "eip155:84532": ("0x036CbD53842c5426634e7929541eC2318f3dCF7e", "USDC", "2", 84532),
}
_X402_DEFAULT_MAX_CENTS_PER_CALL = 5
_X402_MAX_DAILY_CAP_CENTS = 100_000

# Keccak-256 (the pre-NIST padding Ethereum uses), same as server/x402.py.
_KECCAK_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_KECCAK_ROT = [
    [0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56], [27, 20, 39, 8, 14],
]
_M64 = (1 << 64) - 1


def _keccak_f(a: list) -> None:
    for rc in _KECCAK_RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ (((c[(x + 1) % 5] << 1) | (c[(x + 1) % 5] >> 63)) & _M64)
             for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                r = _KECCAK_ROT[x][y]
                v = a[x][y]
                b[y][(2 * x + 3 * y) % 5] = ((v << r) | (v >> (64 - r))) & _M64 if r else v
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        a[0][0] ^= rc


def keccak256(data: bytes) -> bytes:
    rate = 136
    msg = bytearray(data) + b"\x01"
    msg += b"\x00" * (-len(msg) % rate)
    msg[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), rate):
        block = msg[off:off + rate]
        for i in range(rate // 8):
            a[i % 5][i // 5] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        _keccak_f(a)
    out = b"".join(a[i % 5][i // 5].to_bytes(8, "little") for i in range(4))
    return out


# secp256k1
_P = 2 ** 256 - 2 ** 32 - 977
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
      0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def _pt_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    if p1[0] == p2[0] and (p1[1] + p2[1]) % _P == 0:
        return None
    if p1 == p2:
        lam = 3 * p1[0] * p1[0] * pow(2 * p1[1], -1, _P) % _P
    else:
        lam = (p2[1] - p1[1]) * pow(p2[0] - p1[0], -1, _P) % _P
    x = (lam * lam - p1[0] - p2[0]) % _P
    return x, (lam * (p1[0] - x) - p1[1]) % _P


def _pt_mul(k: int, pt):
    out = None
    while k:
        if k & 1:
            out = _pt_add(out, pt)
        pt = _pt_add(pt, pt)
        k >>= 1
    return out


def _rfc6979_k(d: int, z: bytes):
    """Deterministic nonces (RFC 6979, HMAC-SHA256), in order."""
    import hmac
    x = d.to_bytes(32, "big")
    h = (int.from_bytes(z, "big") % _N).to_bytes(32, "big")
    v, k = b"\x01" * 32, b"\x00" * 32
    k = hmac.new(k, v + b"\x00" + x + h, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + x + h, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac.new(k, v, hashlib.sha256).digest()
        cand = int.from_bytes(v, "big")
        if 1 <= cand < _N:
            yield cand
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()


def ecdsa_sign(z: bytes, d: int) -> tuple[int, int, int]:
    """(r, s, v) over a 32-byte digest; low-s, v = 27 + recovery id."""
    e = int.from_bytes(z, "big")
    for k in _rfc6979_k(d, z):
        R = _pt_mul(k, _G)
        r = R[0] % _N
        if r == 0:
            continue
        s = pow(k, -1, _N) * (e + r * d) % _N
        if s == 0:
            continue
        recid = (R[1] & 1) | (2 if R[0] >= _N else 0)
        if s > _N // 2:
            s, recid = _N - s, recid ^ 1
        return r, s, 27 + recid
    raise RuntimeError("unreachable")


def ecdsa_recover(z: bytes, r: int, s: int, v: int) -> str:
    """The checksummed address that signed digest z, or raise ValueError."""
    recid = v - 27
    if not (0 <= recid <= 3 and 1 <= r < _N and 1 <= s < _N):
        raise ValueError("bad signature values")
    x = r + (recid >> 1) * _N
    if x >= _P:
        raise ValueError("bad r")
    y = pow((x * x * x + 7) % _P, (_P + 1) // 4, _P)
    if (y * y - (x * x * x + 7)) % _P:
        raise ValueError("r is not on the curve")
    if (y & 1) != (recid & 1):
        y = _P - y
    e = int.from_bytes(z, "big")
    rinv = pow(r, -1, _N)
    q = _pt_add(_pt_mul(s * rinv % _N, (x, y)), _pt_mul((-e * rinv) % _N, _G))
    if q is None:
        raise ValueError("recovered the point at infinity")
    return _address_of_point(q)


def _address_of_point(pt) -> str:
    raw = keccak256(pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big"))[-20:]
    return checksum_address("0x" + raw.hex())


def checksum_address(addr: str) -> str:
    body = addr[2:].lower()
    h = keccak256(body.encode("ascii")).hex()
    return "0x" + "".join(c.upper() if c.isalpha() and int(h[i], 16) >= 8 else c
                          for i, c in enumerate(body))


def address_of_key(d: int) -> str:
    return _address_of_point(_pt_mul(d, _G))


_EIP712_DOMAIN_TYPEHASH = keccak256(
    b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
_TRANSFER_WITH_AUTHORIZATION_TYPEHASH = keccak256(
    b"TransferWithAuthorization(address from,address to,uint256 value,"
    b"uint256 validAfter,uint256 validBefore,bytes32 nonce)")


def _addr32(addr: str) -> bytes:
    return bytes(12) + bytes.fromhex(addr[2:])


def _u256(n: int) -> bytes:
    return int(n).to_bytes(32, "big")


def usdc_domain_separator(network: str) -> bytes:
    contract, name, version, chain_id = _X402_ASSETS[network]
    return keccak256(_EIP712_DOMAIN_TYPEHASH + keccak256(name.encode()) + keccak256(version.encode())
                     + _u256(chain_id) + _addr32(contract))


def transfer_authorization_digest(network: str, auth: dict) -> bytes:
    struct = keccak256(_TRANSFER_WITH_AUTHORIZATION_TYPEHASH + _addr32(auth["from"]) + _addr32(auth["to"])
                       + _u256(int(auth["value"])) + _u256(int(auth["validAfter"]))
                       + _u256(int(auth["validBefore"])) + bytes.fromhex(auth["nonce"][2:]))
    return keccak256(b"\x19\x01" + usdc_domain_separator(network) + struct)


class X402ConfigError(ValueError):
    """Paying is configured wrongly; the message names the setting, never a value."""


def _x402_payer():
    """(private key int, address, daily cap cents, per-call cap cents), or None
    when paying is off. Raises X402ConfigError when it is half-configured."""
    key_file = os.environ.get("ORPHO_X402_PAYER_KEY_FILE", "").strip()
    cap_raw = os.environ.get("ORPHO_X402_DAILY_CAP_CENTS", "").strip()
    if not key_file and not cap_raw:
        return None
    if not key_file or not cap_raw:
        raise X402ConfigError("x402 paying needs both ORPHO_X402_PAYER_KEY_FILE and "
                              "ORPHO_X402_DAILY_CAP_CENTS; one is missing")
    try:
        daily = int(cap_raw)
        per_call = int(os.environ.get("ORPHO_X402_MAX_CENTS_PER_CALL", "").strip()
                       or _X402_DEFAULT_MAX_CENTS_PER_CALL)
    except ValueError:
        raise X402ConfigError("ORPHO_X402_DAILY_CAP_CENTS and ORPHO_X402_MAX_CENTS_PER_CALL "
                              "must be whole numbers of cents") from None
    if not (1 <= daily <= _X402_MAX_DAILY_CAP_CENTS) or not (1 <= per_call <= daily):
        raise X402ConfigError("caps out of range: 1 <= ORPHO_X402_MAX_CENTS_PER_CALL <= "
                              f"ORPHO_X402_DAILY_CAP_CENTS <= {_X402_MAX_DAILY_CAP_CENTS}")
    path = os.path.expanduser(key_file)
    try:
        st = os.stat(path)
        if os.name == "posix" and st.st_mode & 0o077:
            raise X402ConfigError("ORPHO_X402_PAYER_KEY_FILE is readable by other users; "
                                  "run chmod 600 on it")
        with open(path, encoding="ascii") as fh:
            raw = fh.read().strip()
    except OSError:
        raise X402ConfigError("ORPHO_X402_PAYER_KEY_FILE cannot be read") from None
    except UnicodeDecodeError:
        raise X402ConfigError("ORPHO_X402_PAYER_KEY_FILE is not a hex private key") from None
    hexkey = raw[2:] if raw[:2].lower() == "0x" else raw
    if len(hexkey) != 64 or any(c not in "0123456789abcdefABCDEF" for c in hexkey):
        raise X402ConfigError("ORPHO_X402_PAYER_KEY_FILE is not a 32-byte hex private key")
    d = int(hexkey, 16)
    if not 1 <= d < _N:
        raise X402ConfigError("ORPHO_X402_PAYER_KEY_FILE is not a valid secp256k1 key")
    # Over plain http anyone on the path could rewrite payTo in the 402.
    host = (urllib.parse.urlsplit(BASE_URL).hostname or "").lower()
    if not BASE_URL.startswith("https://") and host not in ("127.0.0.1", "localhost", "::1"):
        raise X402ConfigError("x402 paying needs an https ORPHO_BASE_URL")
    return d, address_of_key(d), daily, per_call


def _x402_allowed_networks() -> set:
    nets = {"eip155:8453"}
    if os.environ.get("ORPHO_X402_ALLOW_TESTNET", "").strip() == "1":
        nets.add("eip155:84532")
    return nets


def _spend_path() -> str:
    return os.path.expanduser(os.environ.get("ORPHO_X402_SPEND_FILE", "").strip()
                              or "~/.orphograph/x402_spend.json")


def _reserve_spend(amount: int, daily_cap_atomic: int, record: dict) -> tuple[bool, int]:
    """Add `amount` to today's spend if it fits under the cap; (ok, spent today).

    Reserved before signing and never released: a signed authorization can
    still settle until it expires, so it counts as spent."""
    import datetime
    path = _spend_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - not POSIX
            pass
        try:
            with open(path, encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, ValueError):
            state = {}
        if not isinstance(state, dict) or state.get("day") != today:
            state = {"day": today, "atomic": 0, "payments": []}
        spent = int(state.get("atomic", 0))
        if spent + amount > daily_cap_atomic:
            return False, spent
        state["atomic"] = spent + amount
        state["payments"] = (list(state.get("payments") or []) + [record])[-200:]
        tmp = path + ".tmp"
        with open(os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), "w",
                  encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
        return True, state["atomic"]
    finally:
        os.close(fd)


def _choose_requirement(required: dict, per_call_atomic: int) -> tuple[dict | None, str]:
    nets = _x402_allowed_networks()
    reasons = []
    for req in required.get("accepts") or []:
        if not isinstance(req, dict):
            continue
        net = req.get("network")
        if req.get("scheme") != "exact" or net not in nets:
            reasons.append(f"scheme/network {req.get('scheme')}/{net} not accepted")
            continue
        if str(req.get("asset", "")).lower() != _X402_ASSETS[net][0].lower():
            reasons.append("asset is not the pinned USDC for " + net)
            continue
        pay_to = str(req.get("payTo", ""))
        if len(pay_to) != 42 or not pay_to.startswith("0x") or any(
                c not in "0123456789abcdefABCDEF" for c in pay_to[2:]) or int(pay_to, 16) == 0:
            reasons.append("payTo is not an address")
            continue
        try:
            amount = int(str(req.get("amount")))
        except ValueError:
            reasons.append("amount is not a whole number")
            continue
        if amount <= 0:
            reasons.append("amount is not positive")
            continue
        if amount > per_call_atomic:
            reasons.append(f"price {amount} is above ORPHO_X402_MAX_CENTS_PER_CALL")
            continue
        return req, ""
    return None, "; ".join(reasons) or "the 402 offered no payment option"


def _b64json(raw: str):
    import base64
    try:
        return json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, TypeError):
        return None


def _http_full(method: str, path: str, body: dict | None, extra: dict | None = None):
    """(status, headers, parsed body) without following redirects, or raise URLError."""
    headers = dict(COMMON_HEADERS)
    if API_KEY:
        headers["X-Orpho-Api-Key"] = API_KEY
    headers.update(extra or {})
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, method=method, headers=headers)
    try:
        with _urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            status, hdrs, raw = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        status, hdrs = e.code, e.headers
        try:
            raw = e.read()
        except Exception:
            raw = b""
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
    except json.JSONDecodeError:
        parsed = {"raw": raw[:400].decode("utf-8", errors="replace")}
    return status, hdrs, parsed if isinstance(parsed, dict) else {"raw": str(parsed)[:400]}


def _anchor_post(payload: dict) -> dict:
    """POST /api/anchor; pays a 402 only when x402 paying is configured."""
    try:
        payer = _x402_payer()
    except X402ConfigError as e:
        return {"error": "x402_config", "reason": str(e)}
    if payer is None:
        return _http("POST", "/api/anchor", payload)
    d, address, daily_cents, per_call_cents = payer
    try:
        status, hdrs, body = _http_full("POST", "/api/anchor", payload,
                                        {X402_OPT_IN_HEADER: "accept"})
    except urllib.error.URLError as e:
        return {"error": "network_error", "reason": str(getattr(e, "reason", e))}
    except TimeoutError:
        return {"error": "timeout"}
    if status != 402:
        if 200 <= status < 300:
            return body
        if 300 <= status < 400:
            return {"error": "redirect_refused", "status": status,
                    "detail": "the server answered with a redirect; it was not followed"}
        return {"error": "http_error", "status": status, "body": json.dumps(body)[:400]}
    required = _b64json((hdrs.get(_PAYMENT_REQUIRED_HEADER) or "").strip()) if hdrs else None
    if not isinstance(required, dict):
        required = body
    req, why = _choose_requirement(required, per_call_cents * 10_000)
    if req is None:
        return {"error": "payment_declined", "status": 402,
                "reason": "x402: no acceptable payment option (" + why + "); nothing was signed"}
    import time
    now = int(time.time())
    try:
        timeout = max(30, min(300, int(str(req.get("maxTimeoutSeconds") or 60))))
    except ValueError:
        timeout = 60
    auth = {"from": address, "to": checksum_address(req["payTo"]), "value": str(int(req["amount"])),
            "validAfter": str(now - 600), "validBefore": str(now + timeout),
            "nonce": "0x" + os.urandom(32).hex()}
    ok, spent = _reserve_spend(int(auth["value"]), daily_cents * 10_000,
                               {"t": now, "nonce": auth["nonce"], "amount": auth["value"],
                                "payTo": auth["to"], "network": req["network"]})
    if not ok:
        return {"error": "payment_declined", "status": 402,
                "reason": (f"x402: today's spend {spent} + {auth['value']} would pass "
                           "ORPHO_X402_DAILY_CAP_CENTS; nothing was signed")}
    digest = transfer_authorization_digest(req["network"], auth)
    r, s, v = ecdsa_sign(digest, d)
    if ecdsa_recover(digest, r, s, v) != address:  # a signer bug must never reach the wire
        return {"error": "x402_signer_selfcheck_failed", "reason": "nothing was sent"}
    signed = {"x402Version": 2, "accepted": req,
              "payload": {"signature": "0x" + r.to_bytes(32, "big").hex() + s.to_bytes(32, "big").hex()
                          + bytes([v]).hex(), "authorization": auth}}
    if isinstance(required.get("resource"), dict):
        signed["resource"] = required["resource"]
    import base64
    header = base64.b64encode(json.dumps(signed, separators=(",", ":")).encode()).decode()
    extra = {X402_OPT_IN_HEADER: "accept", _PAYMENT_SIGNATURE_HEADER: header}
    status = hdrs = None
    last = ""
    for _attempt in range(2):  # an identical replay is safe: the server answers it once
        try:
            status, hdrs, body = _http_full("POST", "/api/anchor", payload, extra)
            break
        except (urllib.error.URLError, TimeoutError) as e:
            last = str(getattr(e, "reason", e))
    if status is None:
        return {"error": "network_error", "reason": last,
                "detail": "a signed 1-payment authorization was sent or may have been; it is "
                          "counted in today's spend and expires at validBefore"}
    paid = {"amount_atomic": auth["value"], "network": req["network"], "pay_to": auth["to"],
            "payer": address, "nonce": auth["nonce"], "spent_today_atomic": spent,
            "daily_cap_cents": daily_cents}
    if 200 <= status < 300:
        settle = _b64json((hdrs.get(_PAYMENT_RESPONSE_HEADER) or "").strip()) if hdrs else None
        if isinstance(settle, dict):
            paid["transaction"] = settle.get("transaction")
        body["_x402"] = paid
        return body
    return {"error": "payment_rejected", "status": status, "body": json.dumps(body)[:400],
            "x402": paid}


def _no_calendar_accepted(result: dict) -> dict | None:
    """The refusal for a 200 with calendars_ok 0: a receipt with no Bitcoin
    commitment that never gets one. Counted as not anchored, as the GitHub
    Action and the agent and dataset CLIs count it. A missing field (an older
    server) is not a refusal."""
    if result.get("calendars_ok") == 0:
        return {"ok": False,
                "error": "no calendar accepted the hash: this receipt has no "
                         "Bitcoin commitment and never will; try again later",
                "receipt_id": result.get("receipt_id", ""),
                "calendars_ok": 0,
                "calendars_total": result.get("calendars_total")}
    return None


def tool_anchor_file(args: dict) -> dict:
    path = args.get("path")
    if not isinstance(path, str) or not path:
        return {"error": "missing required argument: path"}
    if not os.path.isfile(path):
        return {"error": f"not a file: {path}"}
    try:
        sha256_hex, sha512_hex, size = hash_file_local(path)
    except OSError as e:
        return {"error": f"could not read file: {type(e).__name__}: {e}"}
    label = args.get("label")
    payload: dict = {"hash_hex": sha256_hex, "sha512_hex": sha512_hex}
    if isinstance(label, str) and label.strip():
        payload["client_label"] = label.strip()[:200]
    c2pa_hash = args.get("c2pa_manifest_hash")
    if isinstance(c2pa_hash, str) and c2pa_hash.strip():
        payload["c2pa_manifest_hash"] = c2pa_hash.strip().lower()
    log(f"anchor_file: hashing {path} ({size} bytes), submitting hash …")
    result = _anchor_post(payload)
    if result.get("error"):
        return {
            "ok": False,
            "error": result.get("error"),
            "detail": result.get("body") or result.get("reason"),
        }
    refused = _no_calendar_accepted(result)
    if refused:
        return refused
    rid = result.get("receipt_id", "")
    return {
        "ok": True,
        "receipt_id": rid,
        "receipt_url": f"{BASE_URL}/r/{rid}" if rid else None,
        "sha256_hex": sha256_hex,
        "sha512_hex": sha512_hex,
        **_bitcoin_state(result.get("btc_pinned_at")),
        "calendars_ok": result.get("calendars_ok"),
        "calendars_total": result.get("calendars_total"),
        # Distinct upstream calendars, which is what low_redundancy measures:
        # five servers reach four calendars, and three acknowledgements can
        # rest on two of them under one operator.
        "calendars_distinct_ok": result.get("calendars_distinct_ok"),
        "calendars_distinct_total": result.get("calendars_distinct_total"),
        "low_redundancy": result.get("low_redundancy", False),
        "pack_consumed": result.get("pack_consumed", False),
        "pack_remaining": result.get("pack_remaining", 0),
        "subscription_active": result.get("subscription_active", False),
        "size_bytes": size,
        "x402_payment": result.get("_x402"),
        "note": (
            "The file body did not leave this device. Only the SHA-256 "
            "and SHA-512 fingerprints were transmitted."
        ),
    }


# ── folder (dataset) anchoring ─────────────────────────────────────
# Mirrors server/merkle.py (orphograph-merkle-v1-rfc6962) so the root the
# server recomputes from the submitted manifest matches ours. Stdlib only.
_MERKLE_ALGORITHM = "orphograph-merkle-v1-rfc6962"
_MERKLE_VERSION = 1
_FOLDER_EXCLUDE = (
    ".DS_Store", "Thumbs.db", "desktop.ini", ".git/*", "node_modules/*",
    "__pycache__/*", "*.tmp", "*.swp", "*.swo", "~$*",
)


def _folder_excluded(rel_posix: str) -> bool:
    # Faithful port of server/merkle._matches_any: a slash pattern matches the
    # FULL posix path (top-level prefix only — e.g. ".git/*" catches ".git/x"
    # but NOT "a/.git/x"); a no-slash pattern matches the basename at any depth.
    import fnmatch
    name = rel_posix.rsplit("/", 1)[-1]
    for pat in _FOLDER_EXCLUDE:
        if "/" in pat:
            if fnmatch.fnmatch(rel_posix, pat):
                return True
            prefix = pat.rstrip("*").rstrip("/")
            if prefix and (rel_posix == prefix or rel_posix.startswith(prefix + "/")):
                return True
        else:
            if fnmatch.fnmatch(name, pat):
                return True
    return False


def _merkle_root(leaves: list) -> bytes:
    level = list(leaves)
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(hashlib.sha256(b"\x01" + level[i] + level[i + 1]).digest())
            else:
                nxt.append(level[i])  # lone-last node promoted, never duplicated
        level = nxt
    return level[0]


def _build_folder_manifest(root: str) -> tuple:
    """Walk root, hash every file locally, build the RFC-6962 manifest.

    Sort is UTF-8 byte order of the POSIX relative path (the canonical order);
    symlinks are skipped. Raises ValueError for an empty folder.
    """
    root = os.path.abspath(root)
    entries = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for fn in filenames:
            ap = os.path.join(dirpath, fn)
            if os.path.islink(ap) or not os.path.isfile(ap):
                continue
            rel_posix = os.path.relpath(ap, root).replace(os.sep, "/")
            if _folder_excluded(rel_posix):
                continue
            entries.append((rel_posix, ap))
    if not entries:
        raise ValueError("empty folder (no files to anchor)")
    entries.sort(key=lambda e: e[0].encode("utf-8"))

    leaves_meta = []
    leaf_hashes = []
    total = 0
    for rel_posix, ap in entries:
        h = hashlib.sha256()
        size = 0
        with open(ap, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
                size += len(chunk)
        digest = h.digest()
        leaf = hashlib.sha256(b"\x00" + rel_posix.encode("utf-8") + b"\x00" + digest).digest()
        leaves_meta.append({
            "path": rel_posix,
            "file_sha256_hex": digest.hex(),
            "leaf_hex": leaf.hex(),
            "size_bytes": size,
        })
        leaf_hashes.append(leaf)
        total += size
    manifest = {
        "algorithm": _MERKLE_ALGORITHM,
        "version": _MERKLE_VERSION,
        "root_hex": _merkle_root(leaf_hashes).hex(),
        "leaves": leaves_meta,
    }
    return manifest, total


def tool_anchor_folder(args: dict) -> dict:
    path = args.get("path")
    if not isinstance(path, str) or not path:
        return {"error": "missing required argument: path"}
    if not os.path.isdir(path):
        return {"error": f"not a directory: {path}"}
    try:
        manifest, total = _build_folder_manifest(path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"could not read folder: {type(e).__name__}: {e}"}
    body = {"manifest": manifest}
    label = args.get("label")
    if isinstance(label, str) and label.strip():
        body["client_label"] = label.strip()[:200]
    log(f"anchor_folder: {len(manifest['leaves'])} files, "
        f"root {manifest['root_hex'][:12]}…, submitting manifest …")
    result = _http("POST", "/api/anchor_folder", body)
    if result.get("error"):
        return {"ok": False, "error": result.get("error"),
                "detail": result.get("body") or result.get("reason")}
    refused = _no_calendar_accepted(result)
    if refused:
        return refused
    rid = result.get("receipt_id", "")
    return {
        "ok": True,
        "receipt_id": rid,
        "certificate_url": f"{BASE_URL}/certificate/{rid}" if rid else None,
        "root_hex": manifest["root_hex"],
        "file_count": len(manifest["leaves"]),
        "size_bytes": total,
        **_bitcoin_state(result.get("btc_pinned_at")),
        "calendars_ok": result.get("calendars_ok"),
        "calendars_distinct_ok": result.get("calendars_distinct_ok"),
        "calendars_distinct_total": result.get("calendars_distinct_total"),
        "low_redundancy": result.get("low_redundancy", False),
        "note": ("No file body left this device — only the manifest (relative "
                 "paths + file sizes + SHA-256 digests + the Merkle root) and "
                 "any label were transmitted."),
    }


def hash_text(text: str, salt: bytes = b"") -> tuple[str, str, int]:
    """SHA-256 + SHA-512 of salt ‖ UTF-8 text. Returns (sha256_hex,
    sha512_hex, byte_len of the text). With no salt this is the plain hash of
    the text, exactly as before."""
    raw = text.encode("utf-8")
    msg = salt + raw
    return hashlib.sha256(msg).hexdigest(), hashlib.sha512(msg).hexdigest(), len(raw)


# A receipt's fingerprint is public. For short or guessable output
# ("approve", "yes", an amount) anyone can hash the likely candidates and
# confirm which one was anchored, so "the text never left the device" does
# not make it private. A 32-byte random salt kept by the holder closes that:
# the anchored value is SHA-256(salt ‖ text), which reveals nothing without
# the salt, and the holder proves the output later by handing over both.
_SALT_BYTES = 32


def _parse_salt(args: dict) -> tuple[bytes | None, str | None]:
    """Return (salt, error). salt_hex wins; salt=true makes a fresh one."""
    given = args.get("salt_hex")
    if given is not None:
        if not (isinstance(given, str) and len(given) == 2 * _SALT_BYTES
                and set(given) <= _HEX64):
            return None, (f"salt_hex must be {2 * _SALT_BYTES} lowercase hex "
                          f"characters ({_SALT_BYTES} bytes)")
        return bytes.fromhex(given), None
    flag = args.get("salt")
    if flag is not None and not isinstance(flag, bool):
        # "true" as a string would otherwise anchor UNSALTED without a word.
        return None, "salt must be a boolean (true or false)"
    if flag:
        return os.urandom(_SALT_BYTES), None
    return b"", None


def _bitcoin_state(btc_pinned_at: Any) -> dict:
    """Say plainly what a receipt is worth right now. Agents act in
    milliseconds; a Bitcoin commitment takes hours. Until it lands, the proof
    ends at the OpenTimestamps calendar servers, not at Bitcoin."""
    if btc_pinned_at:
        return {"bitcoin_status": "confirmed",
                "bitcoin_note": ("The anchor is committed to a Bitcoin block "
                                 "and can be checked against any Bitcoin "
                                 "node.")}
    return {"bitcoin_status": "pending",
            "bitcoin_note": ("Not yet in a Bitcoin block. The calendars "
                             "normally commit within a few hours; until then "
                             "the proof ends at the calendar servers, not at "
                             "Bitcoin. Check later with "
                             "orphograph_verify_receipt.")}


def tool_anchor_output(args: dict) -> dict:
    """Anchor a string of generated output (e.g. an AI agent's result, a model
    response, a transcript) directly — without writing it to a file first. The
    text is hashed in-process; ONLY the SHA-256/512 fingerprints are transmitted,
    so the output itself never leaves the device. Proves "this exact output
    existed at time T" — re-verify later by hashing the identical text."""
    text = args.get("text")
    if not isinstance(text, str) or not text:
        return {"error": "missing required argument: text"}
    salt, salt_error = _parse_salt(args)
    if salt_error:
        return {"error": salt_error}
    sha256_hex, sha512_hex, size = hash_text(text, salt)
    payload: dict = {"hash_hex": sha256_hex, "sha512_hex": sha512_hex}
    label = args.get("label")
    if isinstance(label, str) and label.strip():
        payload["client_label"] = label.strip()[:200]
    c2pa_hash = args.get("c2pa_manifest_hash")
    if isinstance(c2pa_hash, str) and c2pa_hash.strip():
        payload["c2pa_manifest_hash"] = c2pa_hash.strip().lower()
    # llms.txt tells relaying agents an output receipt "may carry an optional
    # zero-knowledge execution proof (zk_proof on the request)" and names the
    # MCP tools as one of the two ways to anchor an output. The tool did not
    # forward the field, so the one client population the feature was built
    # for could not reach it. /api/anchor has accepted it since it shipped.
    zk_proof = args.get("zk_proof")
    if isinstance(zk_proof, dict) and zk_proof:
        payload["zk_proof"] = zk_proof
    if salt:
        privacy = {
            "salted": True,
            "salt_hex": salt.hex(),
            "note": (
                "The output text did not leave this device. The anchored "
                "fingerprints are of salt + text, so they reveal nothing about "
                "the text without the salt. KEEP salt_hex with the output: it "
                "was not transmitted, the office cannot recover it, and "
                "without it this output can never be re-verified. To verify, "
                "pass the text and salt_hex to orphograph_verify_output."
            ),
        }
    else:
        privacy = {
            "salted": False,
            "note": (
                "The output text did not leave this device — only the SHA-256 "
                "and SHA-512 fingerprints were transmitted. The fingerprint is "
                "public on the receipt, so short or guessable text (\"approve\", "
                "an amount) can be confirmed by anyone who guesses it; anchor "
                "with salt: true to prevent that. To re-verify, hash the exact "
                "same text and compare to the receipt's anchored hash."
            ),
        }
    log(f"anchor_output: hashing {size} bytes of output, submitting hash …")
    result = _anchor_post(payload)
    if result.get("error"):
        return {
            "ok": False,
            "error": result.get("error"),
            "detail": result.get("body") or result.get("reason"),
        }
    refused = _no_calendar_accepted(result)
    if refused:
        return refused
    rid = result.get("receipt_id", "")
    return {
        "ok": True,
        "receipt_id": rid,
        "receipt_url": f"{BASE_URL}/r/{rid}" if rid else None,
        "sha256_hex": sha256_hex,
        "sha512_hex": sha512_hex,
        **_bitcoin_state(result.get("btc_pinned_at")),
        "calendars_ok": result.get("calendars_ok"),
        "calendars_total": result.get("calendars_total"),
        # Distinct upstream calendars, which is what low_redundancy measures:
        # five servers reach four calendars, and three acknowledgements can
        # rest on two of them under one operator.
        "calendars_distinct_ok": result.get("calendars_distinct_ok"),
        "calendars_distinct_total": result.get("calendars_distinct_total"),
        "low_redundancy": result.get("low_redundancy", False),
        "pack_consumed": result.get("pack_consumed", False),
        "pack_remaining": result.get("pack_remaining", 0),
        "subscription_active": result.get("subscription_active", False),
        "size_bytes": size,
        # Echo the server's view so a caller can confirm the proof was stored
        # rather than silently dropped — the failure mode this fix addresses.
        "zk_provenance": result.get("zk_provenance"),
        "x402_payment": result.get("_x402"),
        **privacy,
    }


def tool_verify_receipt(args: dict) -> dict:
    rid = args.get("receipt_id")
    if not isinstance(rid, str) or not rid:
        return {"error": "missing required argument: receipt_id"}
    # The /api/verify endpoint accepts well-shaped ids; otherwise it 400s.
    safe = "".join(c for c in rid if c.isalnum() or c in ("_", "-"))[:64]
    if safe != rid:
        return {"error": "receipt_id contains characters not in the receipt-id alphabet"}
    result = _http("GET", f"/api/verify/{urllib.parse.quote(safe)}")
    if result.get("error"):
        return {
            "ok": False,
            "error": result.get("error"),
            "detail": result.get("body") or result.get("reason"),
        }
    checks = result.get("checks") or []
    all_ok = bool(checks) and all(c.get("ok") for c in checks)
    return {
        "ok": True,
        "receipt_id": result.get("receipt_id"),
        "anchored_hash_sha256": result.get("hash_hex"),
        "anchored_hash_sha512": result.get("sha512_hex"),
        "anchored_at_utc": result.get("created_at"),
        "btc_pinned_at_utc": result.get("btc_pinned_at"),
        **_bitcoin_state(result.get("btc_pinned_at")),
        "calendars_ok": result.get("calendars_ok"),
        "calendars_total": result.get("calendars_total"),
        "all_ots_proofs_valid": all_ok,
        "receipt_url": f"{BASE_URL}/r/{result.get('receipt_id', '')}",
    }


# ── edit-lineage verification ──────────────────────────────────────
# Walks the parent links of a folder anchor's edit lineage server-side
# (GET /api/verify_folder/<rid> per link) and re-runs the commitment
# checks LOCALLY with the same leaf/internal rules as _merkle_root —
# the server's `lineage` hints are never trusted without recomputing
# the reserved-leaf commitment. Reports anchor-time ordering only.

_RESERVED_PARENT_PATH = ".orphograph/parent"
_LINEAGE_NOTE = (
    "Verified anchor-time ordering of these anchors. This does not establish "
    "that any draft is a derivative of another, nor that no other versions exist."
)


def _lineage_leaf_hex(path: str, digest: bytes) -> str:
    """RFC-6962-style leaf: SHA-256(0x00 || path || 0x00 || digest) — the same
    rule _build_folder_manifest applies."""
    return hashlib.sha256(b"\x00" + path.encode("utf-8") + b"\x00" + digest).hexdigest()


def _lineage_examine_link(receipt: dict, manifest: dict) -> dict:
    """Recompute one link's commitment checks locally.

    Returns {"problems": [...], "parent_root": str|None, "parent_rid_hint": str|None}.
    Works with path-redacted manifests too: the reserved leaf is identified
    structurally, by testing the KNOWN reserved path against each leaf's
    (file_sha256_hex, leaf_hex) pair — no path field required.
    """
    problems: list[str] = []
    hash_hex = receipt.get("hash_hex")
    leaves = manifest.get("leaves")
    if not isinstance(hash_hex, str) or len(hash_hex) != 64:
        return {"problems": ["receipt hash_hex malformed"], "parent_root": None,
                "parent_rid_hint": None}
    if not isinstance(leaves, list) or not leaves:
        return {"problems": ["manifest has no leaves"], "parent_root": None,
                "parent_rid_hint": None}

    # Recompute the root from the leaf hashes (committed order) and, for any
    # leaf whose path is visible, re-derive the leaf from (path, file hash).
    leaf_bytes: list[bytes] = []
    reserved: list[dict] = []
    for leaf in leaves:
        if not isinstance(leaf, dict):
            problems.append("malformed leaf entry")
            continue
        leaf_hex = leaf.get("leaf_hex")
        file_hex = leaf.get("file_sha256_hex")
        try:
            lb = bytes.fromhex(leaf_hex) if isinstance(leaf_hex, str) else b""
            fb = bytes.fromhex(file_hex) if isinstance(file_hex, str) else b""
        except ValueError:
            lb, fb = b"", b""
        if len(lb) != 32:
            problems.append("leaf_hex is not 32 bytes of hex")
            continue
        leaf_bytes.append(lb)
        path = leaf.get("path")
        if isinstance(path, str) and len(fb) == 32:
            if _lineage_leaf_hex(path, fb) != leaf_hex:
                problems.append(f"leaf_hex does not derive from (path, file hash) for {path!r}")
        # Structural reserved-leaf detection: survives path redaction because
        # the reserved path is a known constant.
        if len(fb) == 32 and isinstance(file_hex, str) and file_hex == file_hex.lower() \
                and _lineage_leaf_hex(_RESERVED_PARENT_PATH, fb) == leaf_hex:
            reserved.append(leaf)
    if not problems and leaf_bytes:
        if _merkle_root(leaf_bytes).hex() != hash_hex:
            problems.append("recomputed manifest root does not match receipt hash_hex")

    parent_root: str | None = None
    parent_rid_hint: str | None = None
    hint = receipt.get("lineage")
    manifest_parent = manifest.get("parent")
    if len(reserved) > 1:
        problems.append("more than one reserved parent leaf")
    elif reserved:
        leaf = reserved[0]
        if leaf.get("size_bytes") != 0:
            problems.append("reserved parent leaf must have size_bytes 0")
        parent_root = leaf.get("file_sha256_hex")
        # Hints (receipt.lineage + manifest.parent) must agree with the
        # committed leaf — the leaf is the authority.
        if isinstance(hint, dict) and hint.get("parent_root") != parent_root:
            problems.append("receipt lineage.parent_root disagrees with the committed reserved leaf")
        if isinstance(manifest_parent, dict) and manifest_parent.get("root_hex") != parent_root:
            problems.append("manifest parent.root_hex disagrees with the committed reserved leaf")
        if isinstance(hint, dict) and isinstance(hint.get("parent_receipt_id"), str):
            parent_rid_hint = hint["parent_receipt_id"]
        elif isinstance(manifest_parent, dict) and isinstance(manifest_parent.get("receipt_id"), str):
            parent_rid_hint = manifest_parent["receipt_id"]
    else:
        if isinstance(hint, dict) and hint.get("committed"):
            problems.append("receipt claims committed lineage but no reserved parent leaf is in the manifest")
    return {"problems": problems, "parent_root": parent_root,
            "parent_rid_hint": parent_rid_hint}


def tool_verify_lineage(args: dict) -> dict:
    rid = args.get("receipt_id")
    if not isinstance(rid, str) or not rid:
        return {"error": "missing required argument: receipt_id"}
    safe = "".join(c for c in rid if c.isalnum() or c in ("_", "-"))[:64]
    if safe != rid:
        return {"error": "receipt_id contains characters not in the receipt-id alphabet"}
    try:
        max_depth = int(args.get("max_depth", 32))
    except (TypeError, ValueError):
        max_depth = 32
    max_depth = max(1, min(max_depth, 256))

    chain: list[dict] = []          # collected tip → downward
    seen: set[str] = set()
    broken_at: str | None = None
    depth_capped = False
    all_ok = True
    expected_root: str | None = None  # what the next receipt's root must equal
    current: str | None = safe
    while current is not None:
        if current in seen:
            broken_at = current
            all_ok = False
            break
        if len(chain) >= max_depth:
            depth_capped = True
            all_ok = False
            break
        seen.add(current)
        result = _http("GET", f"/api/verify_folder/{urllib.parse.quote(current)}")
        if result.get("error"):
            if not chain:
                return {"ok": False, "error": result.get("error"),
                        "detail": result.get("body") or result.get("reason")}
            broken_at = current
            all_ok = False
            break
        receipt = result.get("receipt") or {}
        manifest = result.get("manifest") or {}
        problems: list[str] = []
        # Never trust the hint that led us here: the fetched receipt must
        # anchor exactly the root the child committed.
        if expected_root is not None and receipt.get("hash_hex") != expected_root:
            problems.append(
                "parent hint led to a receipt whose anchored root does not "
                "match the committed parent root"
            )
        examined = _lineage_examine_link(receipt, manifest)
        problems.extend(examined["problems"])
        entry = {
            "receipt_id": receipt.get("receipt_id") or current,
            "root_hex": receipt.get("hash_hex"),
            "created_at": receipt.get("created_at"),
            "btc_pinned_at": receipt.get("btc_pinned_at"),
            "status": receipt.get("status", "pending"),
            "link": "committed" if examined["parent_root"] else "genesis",
            "checks_ok": not problems,
        }
        if problems:
            entry["problems"] = problems
            all_ok = False
        chain.append(entry)
        if problems:
            broken_at = current
            break
        if examined["parent_root"] is None:
            break  # genesis reached
        if not examined["parent_rid_hint"]:
            broken_at = current
            all_ok = False
            entry["problems"] = [
                "committed parent root has no discoverable receipt id "
                "(no lineage hint); walk cannot continue server-side"
            ]
            entry["checks_ok"] = False
            break
        expected_root = examined["parent_root"]
        current = examined["parent_rid_hint"]

    chain.reverse()  # report oldest anchor first
    out = {
        "ok": all_ok and broken_at is None and not depth_capped,
        "tip": safe,
        "chain": chain,
        "depth": len(chain),
        "broken_at": broken_at,
        "forks_seen": [],
        "note": _LINEAGE_NOTE,
    }
    if depth_capped:
        out["depth_capped"] = True
    return out


def tool_list_vault(args: dict) -> dict:
    if not API_KEY:
        return {
            "error": "ORPHO_API_KEY not set",
            "hint": (
                "list_vault requires an Orphograph API key. Issue one at "
                f"{BASE_URL}/account.html and add it to your MCP host config "
                "as the ORPHO_API_KEY environment variable."
            ),
        }
    limit = args.get("limit", 50)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 500))
    result = _http("GET", f"/api/me/anchors?limit={limit}")
    if result.get("error"):
        return {
            "ok": False,
            "error": result.get("error"),
            "detail": result.get("body") or result.get("reason"),
        }
    anchors = result.get("anchors") or []
    return {
        "ok": True,
        "anchor_count": len(anchors),
        "anchors": [
            {
                "receipt_id": a.get("receipt_id"),
                "created_at": a.get("created_at"),
                "client_label": a.get("client_label"),
                "calendars_ok": a.get("calendars_ok"),
                "calendars_total": a.get("calendars_total"),
                "btc_pinned_at": a.get("btc_pinned_at"),
                "receipt_url": f"{BASE_URL}/r/{a.get('receipt_id', '')}",
            }
            for a in anchors
        ],
        "has_more": bool(result.get("has_more")),
    }


# ── agent runs: one anchor for many steps, each step provable alone ──
# A run is anchored as a folder manifest whose paths are "step-NNNNNN" (the
# zero-padding keeps UTF-8 byte order equal to step order, which is the
# canonical leaf order). paths_public is set because the paths carry no
# content and the receiving side needs them to bind a step's digest to its
# leaf. Only digests leave the device.

_RUN_STEP_WIDTH = 6
_RUN_MAX_STEPS = 50_000  # the office's MAX_FOLDER_LEAVES
_HEX64 = frozenset("0123456789abcdef")


def _step_digest(step: Any) -> bytes:
    """A step is its output text, or {"sha256_hex": ...} for output already
    hashed by the caller (binary output, or text too large for a tool call)."""
    if isinstance(step, str):
        return hashlib.sha256(step.encode("utf-8")).digest()
    if isinstance(step, dict) and isinstance(step.get("sha256_hex"), str):
        h = step["sha256_hex"].strip().lower()
        if len(h) == 64 and set(h) <= _HEX64:
            return bytes.fromhex(h)
    raise ValueError("each step must be a string or {\"sha256_hex\": <64 hex chars>}")


def _build_run_manifest(steps: list) -> dict:
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list")
    if len(steps) > _RUN_MAX_STEPS:
        raise ValueError(f"at most {_RUN_MAX_STEPS} steps per run")
    leaves_meta, leaf_hashes = [], []
    for i, step in enumerate(steps, start=1):
        digest = _step_digest(step)
        path = f"step-{i:0{_RUN_STEP_WIDTH}d}"
        leaf = hashlib.sha256(b"\x00" + path.encode("utf-8") + b"\x00" + digest).digest()
        leaves_meta.append({"path": path, "file_sha256_hex": digest.hex(),
                            "leaf_hex": leaf.hex(),
                            "size_bytes": len(step.encode("utf-8")) if isinstance(step, str) else 0})
        leaf_hashes.append(leaf)
    return {"algorithm": _MERKLE_ALGORITHM, "version": _MERKLE_VERSION,
            "root_hex": _merkle_root(leaf_hashes).hex(), "leaves": leaves_meta}


def tool_anchor_run(args: dict) -> dict:
    """Anchor every step of an agent run under ONE receipt. Each step stays
    provable on its own: orphograph_verify_output(receipt_id, text) checks
    a single step without the others."""
    try:
        manifest = _build_run_manifest(args.get("steps"))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    body: dict = {"manifest": manifest, "paths_public": True}
    label = args.get("label")
    if isinstance(label, str) and label.strip():
        body["client_label"] = label.strip()[:200]
    log(f"anchor_run: {len(manifest['leaves'])} steps, "
        f"root {manifest['root_hex'][:12]}…, submitting digests …")
    result = _http("POST", "/api/anchor_folder", body)
    if result.get("error"):
        return {"ok": False, "error": result.get("error"),
                "detail": result.get("body") or result.get("reason")}
    refused = _no_calendar_accepted(result)
    if refused:
        return refused
    rid = result.get("receipt_id", "")
    return {
        "ok": True,
        "receipt_id": rid,
        "receipt_url": f"{BASE_URL}/r/{rid}" if rid else None,
        "root_hex": manifest["root_hex"],
        "step_count": len(manifest["leaves"]),
        "step_sha256_hex": [l["file_sha256_hex"] for l in manifest["leaves"]],
        **_bitcoin_state(result.get("btc_pinned_at")),
        "calendars_ok": result.get("calendars_ok"),
        "calendars_distinct_ok": result.get("calendars_distinct_ok"),
        "low_redundancy": result.get("low_redundancy", False),
        "note": ("No step text left this device: only one SHA-256 digest per "
                 "step and the Merkle root were transmitted. Any single step "
                 "can later be checked with orphograph_verify_output. The "
                 "digests and the step count are public on the receipt, so a "
                 "short or guessable step (\"approved\") can be confirmed by "
                 "anyone who guesses it; put a random nonce in such steps."),
    }


def _rebuild_root(leaves: list) -> tuple[bytes | None, list[str]]:
    """Recompute the root from the served leaves without trusting their
    leaf_hex: where a path is visible the leaf is recomputed from path and
    digest; where it is redacted only the served leaf_hex exists, and that
    leaf is reported as unbound. Returns (root, problems)."""
    problems: list[str] = []
    hashes: list[bytes] = []
    for i, leaf in enumerate(leaves):
        if not isinstance(leaf, dict):
            return None, [f"leaf {i} is not an object"]
        digest_hex, leaf_hex, path = leaf.get("file_sha256_hex"), leaf.get("leaf_hex"), leaf.get("path")
        try:
            if isinstance(path, str) and isinstance(digest_hex, str):
                h = hashlib.sha256(b"\x00" + path.encode("utf-8") + b"\x00"
                                   + bytes.fromhex(digest_hex)).digest()
                if isinstance(leaf_hex, str) and leaf_hex.lower() != h.hex():
                    problems.append(f"leaf {i}: served leaf_hex does not match its path and digest")
            elif isinstance(leaf_hex, str):
                h = bytes.fromhex(leaf_hex)
            else:
                return None, [f"leaf {i} carries neither a path+digest nor a leaf hash"]
        except ValueError:
            return None, [f"leaf {i} is not hex"]
        hashes.append(h)
    if not hashes:
        return None, ["manifest has no leaves"]
    return _merkle_root(hashes), problems


def tool_verify_output(args: dict) -> dict:
    """Check that an output is covered by a receipt, trusting only the
    anchored hash. For a single anchor: sha256(text) must equal it. For a run
    or folder anchor: the manifest is fetched, its root recomputed locally and
    required to equal the anchored hash, and the output's digest must sit in
    a leaf whose path binds it."""
    rid = args.get("receipt_id")
    if not isinstance(rid, str) or not rid:
        return {"error": "missing required argument: receipt_id"}
    safe = "".join(c for c in rid if c.isalnum() or c in ("_", "-"))[:64]
    if safe != rid:
        return {"error": "receipt_id contains characters not in the receipt-id alphabet"}
    text, given = args.get("text"), args.get("sha256_hex")
    if args.get("salt_hex") is not None and not (isinstance(text, str) and text):
        # The salt is mixed into the TEXT. With only a digest there is nothing
        # to mix it into; ignoring it would report a confident mismatch.
        return {"error": "salt_hex goes with text; for a salted anchor pass "
                         "the text and salt_hex, or the salted digest as sha256_hex"}
    if isinstance(text, str) and text:
        salt, salt_error = _parse_salt({"salt_hex": args.get("salt_hex")}
                                       if args.get("salt_hex") is not None else {})
        if salt_error:
            return {"error": salt_error}
        target = hashlib.sha256(salt + text.encode("utf-8")).hexdigest()
    elif isinstance(given, str) and len(given.strip()) == 64 and set(given.strip().lower()) <= _HEX64:
        target = given.strip().lower()
    else:
        return {"error": "pass the output as text, or its sha256_hex (64 hex chars)"}
    rec = _http("GET", f"/api/verify/{urllib.parse.quote(safe)}")
    if not isinstance(rec, dict):
        return {"ok": False, "error": "the office answered with an unexpected shape"}
    if rec.get("error"):
        return {"ok": False, "error": rec.get("error"),
                "detail": rec.get("body") or rec.get("reason")}
    anchored = rec.get("hash_hex")
    anchored = anchored.lower() if isinstance(anchored, str) else ""
    base = {"ok": True, "receipt_id": safe, "output_sha256_hex": target,
            "anchored_hash_sha256": anchored or None,
            "anchored_at_utc": rec.get("created_at"),
            "btc_pinned_at_utc": rec.get("btc_pinned_at"),
            "calendars_ok": rec.get("calendars_ok"),
            "receipt_url": f"{BASE_URL}/r/{safe}",
            "scope": ("Shows this exact output existed by the anchor time. It "
                      "does not show who produced it or that it is correct.")}
    if not anchored:
        return {**base, "match": False, "reason": "the receipt carries no anchored hash"}
    if anchored == target:
        return {**base, "match": True, "kind": "single"}
    folder = _http("GET", f"/api/verify_folder/{urllib.parse.quote(safe)}")
    if not isinstance(folder, dict):
        folder = {"error": "bad_shape"}
    if folder.get("error"):
        if folder.get("status") == 400:
            # The office answers 400 when the receipt is not a folder anchor:
            # a single anchor whose hash differs from the output's.
            return {**base, "match": False, "kind": "single",
                    "reason": "the output's hash is not the anchored hash"}
        # Anything else (network, 5xx, private 404) decided nothing.
        return {"ok": False, "receipt_id": safe,
                "error": "could not fetch the run's manifest; nothing was decided",
                "detail": folder.get("body") or folder.get("reason") or folder.get("error")}
    served = folder.get("manifest")
    leaves = served.get("leaves") if isinstance(served, dict) else None
    if not isinstance(leaves, list):
        leaves = []
    root, problems = _rebuild_root(leaves)
    if root is None or root.hex() != anchored or problems:
        return {**base, "match": False, "kind": "run",
                "reason": "the served manifest does not recompute to the anchored "
                          "root; do not trust it",
                "problems": problems or ["recomputed root differs from the anchored hash"]}
    hits = [l for l in leaves if isinstance(l, dict)
            and isinstance(l.get("file_sha256_hex"), str)
            and l["file_sha256_hex"].lower() == target]
    bound = [l.get("path") for l in hits if isinstance(l.get("path"), str)]
    if bound:
        return {**base, "match": True, "kind": "run", "matched_paths": bound,
                "leaf_count": len(leaves)}
    if hits:
        return {**base, "match": False, "kind": "run", "digest_listed": True,
                "reason": "the digest is listed, but the owner kept paths private, "
                          "so its leaf cannot be bound to it from here"}
    return {**base, "match": False, "kind": "run", "leaf_count": len(leaves),
            "reason": "no step in this run has the output's hash"}


# ── MCP protocol handling ──────────────────────────────────────────


TOOL_DEFINITIONS = [
    {
        "name": "orphograph_anchor_file",
        "description": (
            "Anchor a local file to the Bitcoin chain via Orphograph. "
            "Reads the file locally, computes SHA-256 and SHA-512 in-process, "
            "and transmits ONLY those fingerprints. The file body never "
            "leaves the device. Returns a receipt id, a receipt URL, and "
            "calendar attestation counts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file to anchor.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional human-readable label (max 200 chars). Stored on the receipt; do not include sensitive content.",
                },
                "c2pa_manifest_hash": {
                    "type": "string",
                    "description": "Optional SHA-256 hex of a C2PA manifest the receipt should reference (coexistence mode).",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "orphograph_anchor_folder",
        "description": (
            "Anchor a whole folder (a dataset) to the Bitcoin chain via "
            "Orphograph. Every file is hashed locally into an RFC-6962 Merkle "
            "tree; ONLY the manifest (relative paths + SHA-256 digests + the "
            "root) is transmitted — no file body leaves the device. Returns a "
            "receipt id and a hosted certificate URL; each file stays "
            "independently verifiable via an inclusion proof."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the folder (dataset bundle) to anchor.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional human-readable label (max 200 chars). Stored on the receipt; do not include sensitive content.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "orphograph_anchor_output",
        "description": (
            "Anchor a string of generated OUTPUT (an AI agent's result, a model "
            "response, a transcript, any text) to the Bitcoin chain — without "
            "writing a file first. Hashes the UTF-8 text in-process and transmits "
            "ONLY the SHA-256/512 fingerprints; the text never leaves the device. "
            "Use this to prove an exact output existed at a specific time. Returns "
            "a receipt id, a receipt URL, and calendar attestation counts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The output text to anchor. Hashed locally; not transmitted.",
                },
                "salt": {
                    "type": "boolean",
                    "description": (
                        "Anchor SHA-256(salt + text) with a fresh 32-byte random "
                        "salt, returned as salt_hex and never transmitted. Use it "
                        "for short or guessable output, whose plain fingerprint "
                        "anyone could confirm by guessing. Keep salt_hex: without "
                        "it the output cannot be re-verified."
                    ),
                },
                "salt_hex": {
                    "type": "string",
                    "description": "Your own 32-byte salt as 64 lowercase hex characters, instead of salt: true.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional human-readable label (max 200 chars). Stored on the receipt; do not include sensitive content.",
                },
                "c2pa_manifest_hash": {
                    "type": "string",
                    "description": "Optional SHA-256 hex of a C2PA manifest the receipt should reference (coexistence mode).",
                },
                "zk_proof": {
                    "type": "object",
                    "description": (
                        "Optional zero-knowledge execution proof, echoed on the "
                        "receipt as zk_provenance. SCOPE, which a relaying agent "
                        "should repeat: it proves a fixed, published hash-chain "
                        "procedure ran — NOT that a specific AI model ran. The "
                        "proving-key ceremony is development-grade and this is "
                        "not offered as a certified claim."
                    ),
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "orphograph_verify_receipt",
        "description": (
            "Look up an existing Orphograph receipt and return what the "
            "office recorded for it: the anchored hashes, the calendar "
            "attestation counts, and (when available) the time the pin to "
            "Bitcoin was OBSERVED. This reads the office's record — it does "
            "NOT independently verify the Bitcoin chain, and the observed "
            "pin time is not the block's own timestamp. For an independent "
            "chain check, run the OpenTimestamps client (`ots verify`) "
            "against the receipt's .ots files. Does not require an API key."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "receipt_id": {
                    "type": "string",
                    "description": "The receipt identifier issued at anchor time.",
                },
            },
            "required": ["receipt_id"],
        },
    },
    {
        "name": "orphograph_verify_lineage",
        "description": (
            "Walk the parent links of a receipt's edit lineage and verify each "
            "link. Reports anchor-time ordering only — it does not assess content "
            "similarity, authorship, or whether siblings exist."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "receipt_id": {
                    "type": "string",
                    "description": "tip receipt id",
                },
                "max_depth": {
                    "type": "integer",
                    "default": 32,
                    "description": "Maximum number of links to walk (1-256). Defaults to 32.",
                },
            },
            "required": ["receipt_id"],
        },
    },
    {
        "name": "orphograph_list_vault",
        "description": (
            "List the signed-in subscriber's anchored receipts. Requires "
            "ORPHO_API_KEY in the environment. The key is issued from "
            "https://orphograph.com/account.html."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of receipts to return (1-500). Defaults to 50.",
                    "minimum": 1,
                    "maximum": 500,
                },
            },
        },
    },
    {
        "name": "orphograph_anchor_run",
        "description": (
            "Anchor every step of an AI agent run under ONE Bitcoin-anchored "
            "receipt. Each step is hashed locally; only one SHA-256 digest per "
            "step and the Merkle root are transmitted, never the step text. "
            "Any single step stays provable on its own with "
            "orphograph_verify_output. Proves the steps existed by the anchor "
            "time, not who produced them or that they are correct. Step "
            "digests are public on the receipt: add a random nonce to short "
            "or guessable steps."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "The run's steps in order (max 50,000). Each is the step's output text, or {\"sha256_hex\": ...} for output you already hashed.",
                    "items": {"anyOf": [
                        {"type": "string"},
                        {"type": "object",
                         "properties": {"sha256_hex": {"type": "string"}},
                         "required": ["sha256_hex"]},
                    ]},
                    "minItems": 1,
                },
                "label": {
                    "type": "string",
                    "description": "Optional human-readable label (max 200 chars). Stored on the receipt; do not include sensitive content.",
                },
            },
            "required": ["steps"],
        },
    },
    {
        "name": "orphograph_verify_output",
        "description": (
            "Check whether an exact output is covered by a receipt, for the "
            "agent RECEIVING the output. Works for single anchors and for "
            "agent runs. Trusts only the anchored hash: for a run, the "
            "manifest is recomputed locally and must match it. Returns "
            "match true/false, the anchor time, and the reason when false."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "receipt_id": {"type": "string", "description": "The receipt id to check against."},
                "text": {"type": "string", "description": "The output text. Hashed locally; not transmitted."},
                "salt_hex": {"type": "string", "description": "The salt_hex returned when the output was anchored with salt; needed with text for a salted anchor."},
                "sha256_hex": {"type": "string", "description": "Alternatively, the output's SHA-256 (64 hex chars)."},
            },
            "required": ["receipt_id"],
        },
    },
]


def _reply(req_id: Any, result: dict | None = None, error: dict | None = None) -> dict:
    out: dict = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result if result is not None else {}
    return out


def _wrap_tool_result(payload: dict) -> dict:
    """MCP tool results are returned as a list of content blocks."""
    text = json.dumps(payload, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": bool(payload.get("error") and not payload.get("ok")),
    }


def handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _reply(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},
            },
            "serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            },
        })

    if method in ("notifications/initialized", "initialized"):
        return None  # notifications take no response

    if method == "ping":
        return _reply(req_id, {})

    if method == "tools/list":
        return _reply(req_id, {"tools": TOOL_DEFINITIONS})

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if name == "orphograph_anchor_file":
            return _reply(req_id, _wrap_tool_result(tool_anchor_file(args)))
        if name == "orphograph_anchor_folder":
            return _reply(req_id, _wrap_tool_result(tool_anchor_folder(args)))
        if name == "orphograph_anchor_output":
            return _reply(req_id, _wrap_tool_result(tool_anchor_output(args)))
        if name == "orphograph_verify_receipt":
            return _reply(req_id, _wrap_tool_result(tool_verify_receipt(args)))
        if name == "orphograph_verify_lineage":
            return _reply(req_id, _wrap_tool_result(tool_verify_lineage(args)))
        if name == "orphograph_anchor_run":
            return _reply(req_id, _wrap_tool_result(tool_anchor_run(args)))
        if name == "orphograph_verify_output":
            return _reply(req_id, _wrap_tool_result(tool_verify_output(args)))
        if name == "orphograph_list_vault":
            return _reply(req_id, _wrap_tool_result(tool_list_vault(args)))
        return _reply(req_id, error={"code": -32601, "message": f"unknown tool: {name}"})

    if method == "shutdown":
        return _reply(req_id, {})

    # Unrecognised method — JSON-RPC method-not-found.
    return _reply(req_id, error={"code": -32601, "message": f"method not found: {method}"})


def main() -> None:
    log(f"starting v{SERVER_VERSION} (base_url={BASE_URL}, api_key_set={bool(API_KEY)})")
    # Read newline-delimited JSON-RPC messages from stdin; emit responses on stdout.
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            log(f"bad_json on stdin: {e}")
            continue
        try:
            response = handle(msg)
        except Exception as e:  # noqa: BLE001 — never crash the server loop
            log(f"handler_error: {type(e).__name__}: {e}")
            response = _reply(msg.get("id"), error={"code": -32603, "message": "internal error"})
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
        if msg.get("method") == "shutdown":
            log("shutdown requested; exiting")
            return


if __name__ == "__main__":
    main()
