#!/usr/bin/env python3
"""xrpl_pay.py — receive-only XRP Ledger payment rail for credit packs (RLUSD).

Stdlib only. OFF unless ORPHO_XRPL_ADDRESS is set. The server only ever knows
the merchant's PUBLIC classic address: there is no seed, no key and no signing
anywhere in this module, and nothing here can send a payment. XRPL is a
payment rail only; anchoring stays Bitcoin (see docs/XRPL_RAIL.md).

How a pack is bought:
  1. create_invoice(plan, email) quotes the pack's USD price in RLUSD (1:1,
     no FX oracle), picks a fresh 32-bit DestinationTag no stored invoice has
     ever used, and stores the invoice under the data dir. 60 minutes to pay.
  2. The buyer sends RLUSD to the merchant address with that tag.
  3. check_invoice(id) asks public XRPL nodes. account_tx on one node finds
     candidate payments; every candidate is then re-read with `tx` on EVERY
     configured node, and nothing is decided unless ORPHO_XRPL_MIN_AGREE
     distinct hosts (default 2) return the same answer. A node is untrusted.
  4. A payment credits the invoice ONLY if: validated ledger; Payment;
     Destination is ours; DestinationTag is the invoice's; tesSUCCESS; and
     meta.delivered_amount (never Amount/DeliverMax: partial payments) is
     RLUSD of the configured issuer, worth at least the quote.
  5. Crediting is the NOWPayments webhook's path, step for step: a claim
     code from credits.new_claim_code, credits.add_credits, a processed-
     events ledger with a per-invoice mint marker and a per-tx-hash marker,
     a cross-check of the credit ledger itself, all inside a thread lock plus
     a cross-process file lock; the claim-code email goes out after the
     lock is released.

Underpayment -> `underpaid`, no credit. A payment closed after the invoice
expired -> recorded, flagged for manual review, no credit. Any node failure
-> UNAVAILABLE and no state change: "could not read" is never "unpaid".

Public-node use follows tools/xrpl_anchor/NODES.md (the testnet anchor tool's
policy): one honest User-Agent that no env var can change; a refusal (HTTP
401/403/429, slowDown/tooBusy, "rate limit") blocks that node for the life
of the process with no retry; light traffic (one account_tx scan of at most
MAX_PAGES pages, one `tx` per node per candidate, at most one check per
invoice every MIN_CHECK_INTERVAL seconds). The quorum is the one
tools/xrpl_anchor/xrpl_verify.py uses: any dissent or disagreement is not a
pass, and too few confirmations is UNAVAILABLE. That tool is not in the
production image (the Dockerfile ships server/ only) and must stay a
standalone script, so its rules are restated here and
tests/test_xrpl_pay.py pins them to the tool's.

Env:
  ORPHO_XRPL_ADDRESS         merchant classic r-address (arms the rail)
  ORPHO_XRPL_RLUSD_ISSUER    RLUSD issuer r-address (required when armed)
  ORPHO_XRPL_RLUSD_CURRENCY  40-hex currency code (default: "RLUSD" in hex)
  ORPHO_XRPL_RPC             comma-separated JSON-RPC node URLs
                             (default https://xrplcluster.com/)
  ORPHO_XRPL_MIN_AGREE       distinct nodes that must agree (default 2)
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import credits
import mailer
import nowpayments_api
import request_mode
from file_lock import locked

# ---------------------------------------------------------------- constants

DEFAULT_RPC = "https://xrplcluster.com/"
DEFAULT_MIN_AGREE = 2
INVOICE_TTL_SEC = 60 * 60
# Late payments are still looked for (and flagged) for a day after expiry;
# after that a GET answers from the store without asking any node.
LATE_WATCH_SEC = 24 * 60 * 60
# A payment that closed this long before the invoice existed is not for it.
PRE_CREATE_SLACK_SEC = 60
MIN_CHECK_INTERVAL = 10.0
MAX_PAGES = 3
PAGE_LIMIT = 100
# Unprocessed candidates confirmed per check; the rest wait for the next poll
# (a counted hash is never re-read), so one request stays bounded.
MAX_CANDIDATES = 5
TIMEOUT = 10                  # seconds per request; not configurable
MAX_BODY = 2 * 1024 * 1024
# One honest, named identity, a constant: no flag, env var or rotation can
# change it (tools/xrpl_anchor/NODES.md). No browser token.
USER_AGENT = "orphograph-xrpl-pay/0 (+https://github.com/Orphograph/Orphograph)"
REFUSED_STATUS = (401, 403, 429)
RIPPLE_EPOCH = 946684800      # 2000-01-01T00:00:00Z, XRPL `date` origin
TICKER = "RLUSD"

_B58 = "rpshnaf39wBUDNEGHJKLM4PQRST7VWXYZ2bcdeCg65jkm8oFqi1tuvAxyz"
# Account ids 0 and 1: no key can sign for them (black holes).
_BLACK_HOLES = (bytes(20), bytes(19) + b"\x01")
_HEX40 = re.compile(r"^[0-9A-F]{40}$")
_TX_HASH = re.compile(r"^[0-9A-F]{64}$")
INVOICE_ID_RE = re.compile(r"^xr_[A-Za-z0-9_-]{8,32}$")
_LOOPBACK = ("127.0.0.1", "localhost", "::1")

# --------------------------------------------------------------- data paths

ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    return Path(os.environ.get(
        "ORPHO_DATA_DIR",
        str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))


def invoices_path() -> Path:
    return _data_dir() / "xrpl_invoices.jsonl"


def processed_path() -> Path:
    return _data_dir() / "xrpl_processed_events.jsonl"


def _mint_lock_path() -> Path:
    p = processed_path()
    return p.with_suffix(p.suffix + ".lock")


def _invoice_lock_path() -> Path:
    p = invoices_path()
    return p.with_suffix(p.suffix + ".lock")


_mint_lock = threading.Lock()
_invoice_lock = threading.Lock()
_check_lock = threading.Lock()
_last_check: dict[str, float] = {}

# ---------------------------------------------------------- address codec


def _b58decode(s: str) -> bytes | None:
    n = 0
    for ch in s:
        i = _B58.find(ch)
        if i < 0:
            return None
        n = n * 58 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip(_B58[0]))) + raw


def _b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return _B58[0] * (len(raw) - len(raw.lstrip(b"\0"))) + out


def _checksum(b: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()[:4]


def decode_classic_address(address: str) -> bytes | None:
    """The 20-byte account id of a classic r-address, or None when it is not
    one: wrong alphabet or length, wrong version byte, bad checksum."""
    if not isinstance(address, str) or not 25 <= len(address) <= 35 or address[0] != "r":
        return None
    raw = _b58decode(address)
    if raw is None or len(raw) != 25 or raw[0] != 0:
        return None
    if _checksum(raw[:21]) != raw[21:]:
        return None
    return raw[1:21]


def encode_classic_address(account_id: bytes) -> str:
    if len(account_id) != 20:
        raise ValueError("account id must be 20 bytes")
    body = b"\0" + account_id
    return _b58encode(body + _checksum(body))


def address_problem(name: str, address: str) -> str | None:
    """Why `address` cannot be used as `name`, or None. Names the env var,
    never the value (the value is public, but the message is for /health
    counting and logs, and stays uniform with x402's)."""
    if not address:
        return f"{name} is not set"
    account = decode_classic_address(address)
    if account is None:
        return f"{name} is not a valid classic XRPL address (alphabet, length or checksum)"
    if account in _BLACK_HOLES:
        return f"{name} is a black-hole account no key can sign for"
    return None

# ------------------------------------------------------------------ config


def address() -> str:
    return os.environ.get("ORPHO_XRPL_ADDRESS", "").strip()


def issuer() -> str:
    return os.environ.get("ORPHO_XRPL_RLUSD_ISSUER", "").strip()


def default_currency() -> str:
    """The 160-bit nonstandard currency code for the ticker: its ASCII bytes,
    zero-padded to 20 bytes, as 40 upper-case hex characters."""
    return TICKER.encode("ascii").hex().upper().ljust(40, "0")


def currency() -> str:
    return (os.environ.get("ORPHO_XRPL_RLUSD_CURRENCY", "").strip().upper()
            or default_currency())


def nodes() -> list[str]:
    raw = os.environ.get("ORPHO_XRPL_RPC", "").strip() or DEFAULT_RPC
    return distinct_nodes([n.strip() for n in raw.split(",") if n.strip()])


def min_agree_raw() -> str:
    return os.environ.get("ORPHO_XRPL_MIN_AGREE", "").strip()


def min_agree() -> int:
    try:
        return int(min_agree_raw() or DEFAULT_MIN_AGREE)
    except ValueError:
        return DEFAULT_MIN_AGREE


def distinct_nodes(urls):
    """Drop repeats of the same host, so one node cannot cast two votes.
    Same rule as tools/xrpl_anchor/xrpl_verify.distinct_nodes."""
    seen, out = set(), []
    for n in urls:
        host = (urllib.parse.urlsplit(n).hostname or n).lower()
        if host not in seen:
            seen.add(host)
            out.append(n)
    return out


def node_url_problem(url: str) -> str | None:
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 — raises ValueError on a bad port
    except ValueError:
        return "ORPHO_XRPL_RPC has an unparseable node URL"
    if not host:
        return "ORPHO_XRPL_RPC has a node URL with no host"
    if parts.username or parts.password:
        return "ORPHO_XRPL_RPC node URLs must not carry credentials"
    if parts.scheme == "https":
        return None
    # Plain http only to this machine (a node or a test double on loopback).
    if parts.scheme == "http" and host in _LOOPBACK:
        return None
    return "ORPHO_XRPL_RPC node URLs must be https"


def is_set() -> bool:
    """The founder turned the rail on (an address is set). Not the same as
    armed: a set rail with a config problem stays unarmed."""
    return bool(address())


def config_problems() -> list[str]:
    """Why the rail must NOT be armed, or [] when it may be. Messages name
    env vars only. Empty when the rail is not set at all (nothing to fix)."""
    if not is_set():
        return []
    out = []
    why = address_problem("ORPHO_XRPL_ADDRESS", address())
    if why:
        out.append(why)
    why = address_problem("ORPHO_XRPL_RLUSD_ISSUER", issuer())
    if why:
        out.append(why)
    elif issuer() == address():
        out.append("ORPHO_XRPL_RLUSD_ISSUER is the merchant address itself")
    cur = currency()
    if not _HEX40.match(cur):
        out.append("ORPHO_XRPL_RLUSD_CURRENCY must be 40 hex characters")
    elif cur.startswith("00"):
        out.append("ORPHO_XRPL_RLUSD_CURRENCY must be a nonstandard (non-00) code")
    ns = nodes()
    for n in ns:
        why = node_url_problem(n)
        if why:
            out.append(why)
            break
    raw = min_agree_raw()
    if raw and not raw.isdigit():
        out.append("ORPHO_XRPL_MIN_AGREE must be a whole number")
    elif not 1 <= min_agree() <= len(ns):
        out.append("ORPHO_XRPL_MIN_AGREE needs that many distinct ORPHO_XRPL_RPC hosts "
                   f"({len(ns)} configured)")
    return out


def armed() -> bool:
    """True iff a payment can be invoiced and checked right now."""
    return is_set() and not config_problems()


def health_state() -> dict:
    """For /api/health. Counts only: the reasons name env vars and are for
    the founder (`python3 -c "import xrpl_pay; print(xrpl_pay.config_problems())"`)."""
    on = armed()
    return {"armed": on,
            "config_problems": len(config_problems()),
            "min_agree": min_agree() if on else None,
            "nodes": len(nodes()) if on else None,
            "nodes_blocked": len(BLOCKED)}

# ------------------------------------------------------------ node access


class Unavailable(Exception):
    """A node could not be read, or the nodes did not settle the question."""


class Blocked(Unavailable):
    """The node's operator refused the request. Stop; never retry it."""


# Nodes that refused us, for the life of this process. A deploy (restart)
# is the only thing that asks them again; there is no rotation around it.
BLOCKED: set[str] = set()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A node never gets to send us somewhere else, same host or not."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code,
                                     "redirect refused", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urlopen(req, timeout):
    return _OPENER.open(req, timeout=timeout)


def _refusal(node: str, why: str) -> Blocked:
    if node not in BLOCKED:
        BLOCKED.add(node)
        sys.stderr.write(f"[xrpl_pay] node operator refused ({why}); "
                         f"node blocked until restart: {node}\n")
    return Blocked(f"{node}: operator refused ({why})")


def rpc(node: str, method: str, params: dict) -> dict:
    """One JSON-RPC call to one node: its `result` object. Raises Unavailable
    (or Blocked) for anything else; never retries."""
    if node in BLOCKED:
        raise Blocked(f"{node}: operator refused earlier; not asked again")
    body = json.dumps({"method": method,
                       "params": [{**params, "api_version": 2}]}).encode()
    req = urllib.request.Request(node, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    try:
        with _urlopen(req, TIMEOUT) as r:
            raw = r.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise Unavailable(f"{node}: response over {MAX_BODY} bytes")
        if b"rate limit" in raw.lower():
            raise _refusal(node, "rate limit")
        parsed = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in REFUSED_STATUS:
            raise _refusal(node, f"HTTP {e.code}") from e
        raise Unavailable(f"{node}: HTTP {e.code}") from e
    except (OSError, http.client.HTTPException, ValueError, RecursionError) as e:
        raise Unavailable(f"{node}: {type(e).__name__}") from e
    res = parsed.get("result") if isinstance(parsed, dict) else None
    if not isinstance(res, dict):
        raise Unavailable(f"{node}: no result object")
    if res.get("error") in ("slowDown", "tooBusy"):
        raise _refusal(node, str(res["error"]))
    return res

# --------------------------------------------------- reading a transaction


def _close_unix(tx: dict, holder: dict) -> int | None:
    date = tx.get("date", holder.get("date"))
    if type(date) is int and date >= 0:
        return date + RIPPLE_EPOCH
    iso = holder.get("close_time_iso")
    if isinstance(iso, str):
        try:
            dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        return int(dt.timestamp())
    return None


def normalize(holder) -> dict | None:
    """One transaction as a node gave it (an account_tx entry or a `tx`
    result, API v2 or v1) -> {hash, validated, tx, meta, ledger_index,
    ledger_hash, close_unix}; None for a shape that is not a transaction."""
    if not isinstance(holder, dict):
        return None
    tx = holder.get("tx_json")
    if tx is None:
        tx = holder.get("tx", holder)          # v1: account_tx nests "tx"; `tx` does not
    meta = holder.get("meta")
    if not isinstance(tx, dict) or not isinstance(meta, dict):
        return None
    h = holder.get("hash") or tx.get("hash")
    if not isinstance(h, str) or not _TX_HASH.match(h.upper()):
        return None
    li = holder.get("ledger_index", tx.get("ledger_index"))
    return {"hash": h.upper(),
            "validated": holder.get("validated") is True,
            "tx": tx, "meta": meta,
            "ledger_index": li if type(li) is int else None,
            "ledger_hash": holder.get("ledger_hash") if isinstance(holder.get("ledger_hash"), str) else None,
            "close_unix": _close_unix(tx, holder)}


def is_candidate(norm: dict | None, invoice: dict) -> bool:
    """A Payment to our address carrying this invoice's tag, whatever its
    result or validation (those are judged after the quorum re-read)."""
    if not norm:
        return False
    tx = norm["tx"]
    tag = tx.get("DestinationTag")
    return (tx.get("TransactionType") == "Payment"
            and tx.get("Destination") == invoice["destination"]
            and type(tag) is int and tag == invoice["destination_tag"])


def _decimal(v) -> Decimal | None:
    if not isinstance(v, str) or len(v) > 64:
        return None
    try:
        d = Decimal(v)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


# Verdicts, one per transaction:
#   NOT_OURS     not a Payment to this address and tag (never recorded)
#   PENDING      not in a validated ledger yet (looked at again later)
#   FAILED       validated but not tesSUCCESS (tecPATH_DRY, ...): nothing moved
#   WRONG_ASSET  delivered_amount is not RLUSD of our issuer (XRP, another
#                IOU, an issuer impostor) or not readable: manual review
#   TOO_EARLY    closed before the invoice existed: not for it
#   LATE         a full payment that closed after expiry: manual review
#   UNDERPAID    RLUSD, but delivered less than the quote
#   PAID         RLUSD of our issuer, delivered >= quote, before expiry
def classify(norm: dict | None, invoice: dict) -> tuple[str, dict]:
    """Pure: one normalized transaction against one invoice."""
    if not is_candidate(norm, invoice):
        return "NOT_OURS", {}
    detail = {"hash": norm["hash"], "ledger_index": norm["ledger_index"],
              "close_unix": norm["close_unix"]}
    if not norm["validated"]:
        return "PENDING", detail
    result = norm["meta"].get("TransactionResult")
    if result != "tesSUCCESS":
        return "FAILED", {**detail, "result": str(result)[:32]}
    # delivered_amount ONLY. Amount (DeliverMax in API v2) is what the sender
    # offered; with tfPartialPayment the ledger may deliver far less.
    delivered = norm["meta"].get("delivered_amount")
    if not isinstance(delivered, dict):
        # A string is XRP drops; "unavailable" is a pre-2014 ledger.
        return "WRONG_ASSET", {**detail, "reason": "delivered_amount is not an issued currency"}
    cur = delivered.get("currency")
    if not isinstance(cur, str) or cur.upper() != invoice["currency"]:
        return "WRONG_ASSET", {**detail, "reason": "currency is not RLUSD"}
    if delivered.get("issuer") != invoice["issuer"]:
        return "WRONG_ASSET", {**detail, "reason": "issuer is not the RLUSD issuer"}
    value = _decimal(delivered.get("value"))
    if value is None or value <= 0:
        return "WRONG_ASSET", {**detail, "reason": "delivered value unreadable"}
    detail["value"] = str(value.normalize())
    close = norm["close_unix"]
    if close is None:
        # Cannot tell when it landed, so cannot tell it was in time.
        return "LATE", {**detail, "reason": "no close time"}
    if close < invoice["created_at"] - PRE_CREATE_SLACK_SEC:
        return "TOO_EARLY", detail
    if value < Decimal(invoice["amount"]):
        return "UNDERPAID", detail
    if close > invoice["expires_at"]:
        return "LATE", detail
    return "PAID", detail

# --------------------------------------------------------------- quorum


def _vote(node: str, tx_hash: str, invoice: dict) -> dict:
    try:
        res = rpc(node, "tx", {"transaction": tx_hash, "binary": False})
    except Blocked:
        return {"node": node, "verdict": "UNAVAILABLE", "reason": "operator refused"}
    except Unavailable as e:
        return {"node": node, "verdict": "UNAVAILABLE", "reason": str(e)}
    if res.get("error") == "txnNotFound":
        return {"node": node, "verdict": "UNAVAILABLE", "reason": "not in this node's history"}
    if res.get("status") not in (None, "success") or "error" in res:
        return {"node": node, "verdict": "UNAVAILABLE", "reason": "node error"}
    norm = normalize(res)
    if norm is None:
        return {"node": node, "verdict": "UNAVAILABLE", "reason": "malformed transaction"}
    if norm["hash"] != tx_hash:
        return {"node": node, "verdict": "DISSENT", "reason": "node returned another transaction"}
    verdict, detail = classify(norm, invoice)
    return {"node": node, "verdict": verdict, "ledger_hash": norm["ledger_hash"],
            **detail, "hash": tx_hash}


_AGREE_ON = ("verdict", "hash", "ledger_index", "close_unix", "value")


def confirm(tx_hash: str, invoice: dict, node_list: list[str], need: int, vote=None) -> dict:
    """Re-read one candidate on every distinct node and combine, the way
    xrpl_verify.verify does: a node that returns a different transaction,
    or answers that differ, is not a pass; fewer than `need` agreeing
    answers is Unavailable. PENDING needs no quorum (nothing is decided)."""
    vote = vote or _vote
    distinct = distinct_nodes(node_list)
    # One `tx` per distinct node, asked side by side (as xrpl_verify does),
    # so a slow node costs one timeout, not one per node.
    with ThreadPoolExecutor(max_workers=max(1, len(distinct))) as pool:
        per = list(pool.map(lambda n: vote(n, tx_hash, invoice), distinct))
    answers = [p for p in per if p["verdict"] not in ("UNAVAILABLE", "DISSENT")]
    if any(p["verdict"] == "DISSENT" for p in per):
        raise Unavailable(f"{tx_hash}: a node returned another transaction")
    keys = {tuple(json.dumps(p.get(k)) for k in _AGREE_ON) for p in answers}
    ledger_hashes = {p["ledger_hash"] for p in answers if p.get("ledger_hash")}
    if len(keys) > 1 or len(ledger_hashes) > 1:
        raise Unavailable(f"{tx_hash}: nodes disagree")
    if answers and answers[0]["verdict"] == "PENDING":
        return answers[0]
    if len(answers) < need:
        raise Unavailable(f"{tx_hash}: {len(answers)} of {need} required nodes confirmed")
    return {**answers[0], "agreeing_nodes": len(answers)}


def discover(invoice: dict, node_list: list[str]) -> list[str]:
    """Candidate tx hashes for this invoice from the first node whose
    account_tx scan completes. Newest first, at most MAX_PAGES pages; the
    scan stops at transactions older than the invoice. Raises Unavailable
    when no node completes it."""
    errors = []
    for node in node_list:
        try:
            return _scan(node, invoice)
        except Unavailable as e:
            errors.append(str(e))
    raise Unavailable("; ".join(errors) or "no node configured")


def _scan(node: str, invoice: dict) -> list[str]:
    found: list[str] = []
    marker = None
    floor = invoice["created_at"] - PRE_CREATE_SLACK_SEC
    for _page in range(MAX_PAGES):
        params = {"account": invoice["destination"], "ledger_index_min": -1,
                  "ledger_index_max": -1, "limit": PAGE_LIMIT, "forward": False,
                  "binary": False}
        if marker is not None:
            params["marker"] = marker
        res = rpc(node, "account_tx", params)
        if res.get("status") not in (None, "success") or "error" in res:
            raise Unavailable(f"{node}: account_tx error")
        txs = res.get("transactions")
        if not isinstance(txs, list):
            raise Unavailable(f"{node}: account_tx without a transaction list")
        older = False
        for entry in txs:
            norm = normalize(entry)
            if norm and norm["close_unix"] is not None and norm["close_unix"] < floor:
                older = True
                continue
            if is_candidate(norm, invoice) and norm["hash"] not in found:
                found.append(norm["hash"])
        marker = res.get("marker")
        if older or marker is None:
            return found
    return found

# ------------------------------------------------------------ the store


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _rows(path: Path):
    if not path.exists():
        return
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue        # a torn line from a crash mid-append
            if isinstance(row, dict):
                yield row


def _load_invoices() -> dict[str, dict]:
    """Every invoice, latest snapshot per id (rows are full snapshots)."""
    out: dict[str, dict] = {}
    for row in _rows(invoices_path()):
        iid = row.get("invoice_id")
        if isinstance(iid, str):
            out[iid] = row
    return out


def get_invoice(invoice_id: str) -> dict | None:
    if not isinstance(invoice_id, str) or not INVOICE_ID_RE.match(invoice_id):
        return None
    return _load_invoices().get(invoice_id)


def _write_invoice(record: dict) -> None:
    with locked(invoices_path(), mode="a", exclusive=True) as f:
        f.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")


def _processed() -> set[str]:
    return {r["event_id"] for r in _rows(processed_path())
            if isinstance(r.get("event_id"), str)}


def _mark_processed(event_id: str, result: dict) -> None:
    with locked(processed_path(), mode="a", exclusive=True) as f:
        f.write(json.dumps({"ts": _iso(_now()), "event_id": event_id,
                            "result": result}, separators=(",", ":")) + "\n")


def plans() -> dict:
    """The one pack ladder (nowpayments_api.PLANS): same prices, same credits."""
    return nowpayments_api.PLANS


def _new_tag(used: set[int]) -> int:
    # 1..2^32-1: some wallets treat tag 0 as "no tag".
    for _ in range(64):
        tag = 1 + secrets.randbelow(2**32 - 1)
        if tag not in used:
            return tag
    raise RuntimeError("could not find an unused destination tag")


def create_invoice(plan: str, email: str) -> dict:
    """Store and return a new invoice for `plan`. Raises ValueError for an
    unknown plan and RuntimeError when the rail is not armed."""
    if not armed():
        raise RuntimeError("xrpl rail not armed")
    meta = plans().get(plan)
    if meta is None:
        raise ValueError("unknown plan")
    now = int(_now())
    with _invoice_lock, locked(_invoice_lock_path(), exclusive=True):
        stored = _load_invoices()
        # Every tag ever issued, expired ones included: a late payment to an
        # old tag must never be read as paying a new invoice.
        used = {r.get("destination_tag") for r in stored.values()}
        record = {
            "invoice_id": "xr_" + secrets.token_urlsafe(12),
            "plan": plan,
            "credits": int(meta["credit_count"]),
            "amount": str(Decimal(str(meta["price_usd"]))),
            "currency": currency(),
            "issuer": issuer(),
            "destination": address(),
            "destination_tag": _new_tag(used),
            "created_at": now,
            "expires_at": now + INVOICE_TTL_SEC,
            "email": email,
            "state": "awaiting_payment",
            "credited": False,
            "manual_review": False,
            "payments": [],
        }
        _write_invoice(record)
    return record


def public_view(record: dict, verification: str, reason: str = "") -> dict:
    """What the unauthenticated status route may show. NEVER the email or a
    claim code: payment instructions (public on the ledger anyway), state,
    and the credit count."""
    state = record["state"]
    if state == "awaiting_payment" and _now() > record["expires_at"]:
        state = "expired"
    out = {
        "ok": True,
        "invoice_id": record["invoice_id"],
        "plan": record["plan"],
        "status": state,
        "credited": bool(record["credited"]),
        "credits": record["credits"] if record["credited"] else None,
        "manual_review": bool(record["manual_review"]),
        "expires_at": _iso(record["expires_at"]),
        "pay": pay_instructions(record),
        "verification": verification,
    }
    if reason:
        out["verification_reason"] = reason
    return out


def pay_instructions(record: dict) -> dict:
    return {
        "network": "xrpl",
        "destination": record["destination"],
        "destination_tag": record["destination_tag"],
        "amount": {"currency": record["currency"], "issuer": record["issuer"],
                   "value": record["amount"]},
        "ticker": TICKER,
        "note": ("Send exactly this RLUSD amount with this destination tag. "
                 "A payment without the tag, in another asset, or after the "
                 "invoice expires is not credited automatically."),
    }

# ---------------------------------------------------------------- crediting


def find_mint(invoice_id: str) -> dict | None:
    return credits.find_xrpl_mint(invoice_id)


def _apply(invoice_id: str, confirmed: list[dict]) -> tuple[dict, list[tuple]]:
    """Record quorum-confirmed answers and credit at most once. Returns the
    updated record and the claim emails to send AFTER the lock is released.
    Every money mutation happens inside both locks (the NOWPayments order)."""
    emails: list[tuple] = []
    with _mint_lock, locked(_mint_lock_path(), exclusive=True):
        record = _load_invoices().get(invoice_id)
        if record is None:
            raise KeyError(invoice_id)
        done = _processed()
        changed = False
        mint_marker = f"mint:{invoice_id}"
        minted_already = mint_marker in done or find_mint(invoice_id) is not None
        if minted_already and not record["credited"]:
            # A crash lost the snapshot after the ledger row: heal it.
            record.update(state="paid", credited=True)
            changed = True
            if mint_marker not in done:
                _mark_processed(mint_marker, {"healed": True})
                done.add(mint_marker)
        for c in sorted(confirmed, key=lambda c: (c.get("ledger_index") or 0, c["hash"])):
            verdict, h = c["verdict"], c["hash"]
            tx_marker = f"tx:{h}"
            # Exactly once per transaction hash, across ALL invoices: a
            # replayed or re-reported hash is never looked at twice.
            if verdict in ("PENDING", "NOT_OURS") or tx_marker in done:
                continue
            entry = {"hash": h, "verdict": verdict, "ledger_index": c.get("ledger_index"),
                     "value": c.get("value"), "close_unix": c.get("close_unix")}
            if verdict == "PAID" and not record["credited"]:
                claim_code = credits.new_claim_code()
                credits.add_credits(claim_code=claim_code, email=record["email"],
                                    amount=record["credits"],
                                    source=f"xrpl:{h}:{invoice_id}")
                record.update(state="paid", credited=True)
                result = {"ok": True, "claim_code_minted": True, "plan": record["plan"],
                          "credits": record["credits"], "invoice_id": invoice_id}
                _mark_processed(mint_marker, result)
                done.add(mint_marker)
                emails.append((record["email"], claim_code, record["credits"]))
                entry["credited"] = True
            elif verdict == "PAID":
                entry["verdict"] = "EXTRA_PAYMENT"      # credited already: refund by hand
                record["manual_review"] = True
            elif verdict == "LATE":
                record["manual_review"] = True
                if not record["credited"]:
                    record["state"] = "late_payment"
            elif verdict == "UNDERPAID":
                if not record["credited"] and record["state"] == "awaiting_payment":
                    record["state"] = "underpaid"
                record["manual_review"] = True
            elif verdict == "WRONG_ASSET":
                record["manual_review"] = True
            # FAILED / TOO_EARLY: nothing moved to this invoice; the marker
            # alone keeps the hash from being re-read.
            if verdict in ("FAILED", "TOO_EARLY"):
                _mark_processed(tx_marker, {"invoice_id": invoice_id, "verdict": verdict})
                done.add(tx_marker)
                continue
            record["payments"].append(entry)
            _mark_processed(tx_marker, {"invoice_id": invoice_id, "verdict": entry["verdict"]})
            done.add(tx_marker)
            changed = True
            if entry["verdict"] != "PAID":
                sys.stderr.write(f"[xrpl_pay] invoice {invoice_id}: {entry['verdict']} "
                                 f"tx={h} — no credit, manual_review={record['manual_review']}\n")
        if changed:
            _write_invoice(record)
    return record, emails


def check_invoice(invoice_id: str, *, force: bool = False) -> dict:
    """Look the invoice up on the ledger (bounded, throttled) and return
    {"record", "verification", "reason", "minted"}. verification is
    "checked", "unavailable" (state untouched) or "skipped" (final, out of
    the watch window, throttled, or a HEAD request)."""
    record = get_invoice(invoice_id)
    if record is None:
        raise KeyError(invoice_id)
    now = _now()
    if record["credited"]:
        return {"record": record, "verification": "skipped", "reason": "credited", "minted": False}
    if now > record["expires_at"] + LATE_WATCH_SEC:
        return {"record": record, "verification": "skipped", "reason": "past watch window",
                "minted": False}
    if request_mode.is_safe_method():
        # HEAD is read-only: no node is asked and nothing is written.
        return {"record": record, "verification": "skipped", "reason": "head", "minted": False}
    with _check_lock:
        last = _last_check.get(invoice_id)
        mono = time.monotonic()
        if not force and last is not None and mono - last < MIN_CHECK_INTERVAL:
            return {"record": record, "verification": "skipped", "reason": "throttled",
                    "minted": False}
        _last_check[invoice_id] = mono
        if len(_last_check) > 4096:
            _last_check.clear()
            _last_check[invoice_id] = mono
    node_list, need = nodes(), min_agree()
    done = _processed()
    try:
        hashes = [h for h in discover(record, node_list)
                  if f"tx:{h}" not in done][:MAX_CANDIDATES]
        confirmed = [confirm(h, record, node_list, need) for h in hashes]
    except Unavailable as e:
        sys.stderr.write(f"[xrpl_pay] invoice {invoice_id}: UNAVAILABLE ({e})\n")
        return {"record": record, "verification": "unavailable",
                "reason": "XRPL nodes could not confirm; nothing was changed",
                "minted": False}
    if not confirmed:
        return {"record": record, "verification": "checked", "reason": "", "minted": False}
    record, emails = _apply(invoice_id, confirmed)
    for to, code, count in emails:
        sent = mailer.send_pack_claim_email(to, code, count)
        sys.stderr.write(f"[xrpl_pay] minted claim_code invoice={invoice_id} "
                         f"credits={count} email_sent={sent}\n")
    return {"record": record, "verification": "checked", "reason": "", "minted": bool(emails)}
