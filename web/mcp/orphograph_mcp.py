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

- orphograph_verify_receipt(receipt_id)
    GET /api/verify/<id>. Return the verification result — including
    calendar counts, btc_pinned_at if available, and the receipt URL
    for human-readable view.

- orphograph_list_vault(limit?)
    GET /api/me/anchors with X-Orpho-Api-Key header. Returns the
    signed-in subscriber's anchored receipts. Requires ORPHO_API_KEY
    to be set in the environment, otherwise the server returns a
    clean error directing the user to /account.html for a key.

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
SERVER_VERSION = "0.1.0"


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


def _http(method: str, path: str, body: dict | None = None) -> dict:
    """JSON request to BASE_URL. Returns the parsed response body or {error: ...}."""
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = dict(COMMON_HEADERS)
    if API_KEY:
        headers["X-Orpho-Api-Key"] = API_KEY
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
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
        return {"error": "http_error", "status": e.code, "body": body_raw}
    except urllib.error.URLError as e:
        return {"error": "network_error", "reason": str(getattr(e, "reason", e))}
    except TimeoutError:
        return {"error": "timeout"}


# ── tools ──────────────────────────────────────────────────────────


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
    result = _http("POST", "/api/anchor", payload)
    if result.get("error"):
        return {
            "ok": False,
            "error": result.get("error"),
            "detail": result.get("body") or result.get("reason"),
        }
    rid = result.get("receipt_id", "")
    return {
        "ok": True,
        "receipt_id": rid,
        "receipt_url": f"{BASE_URL}/r/{rid}" if rid else None,
        "sha256_hex": sha256_hex,
        "sha512_hex": sha512_hex,
        "calendars_ok": result.get("calendars_ok"),
        "calendars_total": result.get("calendars_total"),
        "low_redundancy": result.get("low_redundancy", False),
        "pack_consumed": result.get("pack_consumed", False),
        "pack_remaining": result.get("pack_remaining", 0),
        "subscription_active": result.get("subscription_active", False),
        "size_bytes": size,
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
    rid = result.get("receipt_id", "")
    return {
        "ok": True,
        "receipt_id": rid,
        "certificate_url": f"{BASE_URL}/certificate/{rid}" if rid else None,
        "root_hex": manifest["root_hex"],
        "file_count": len(manifest["leaves"]),
        "size_bytes": total,
        "note": ("No file body left this device — only the manifest (relative "
                 "paths + SHA-256 digests + the Merkle root) was transmitted."),
    }


def hash_text(text: str) -> tuple[str, str, int]:
    """SHA-256 + SHA-512 of a UTF-8 string. Returns (sha256_hex, sha512_hex, byte_len)."""
    raw = text.encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), hashlib.sha512(raw).hexdigest(), len(raw)


def tool_anchor_output(args: dict) -> dict:
    """Anchor a string of generated output (e.g. an AI agent's result, a model
    response, a transcript) directly — without writing it to a file first. The
    text is hashed in-process; ONLY the SHA-256/512 fingerprints are transmitted,
    so the output itself never leaves the device. Proves "this exact output
    existed at time T" — re-verify later by hashing the identical text."""
    text = args.get("text")
    if not isinstance(text, str) or not text:
        return {"error": "missing required argument: text"}
    sha256_hex, sha512_hex, size = hash_text(text)
    payload: dict = {"hash_hex": sha256_hex, "sha512_hex": sha512_hex}
    label = args.get("label")
    if isinstance(label, str) and label.strip():
        payload["client_label"] = label.strip()[:200]
    c2pa_hash = args.get("c2pa_manifest_hash")
    if isinstance(c2pa_hash, str) and c2pa_hash.strip():
        payload["c2pa_manifest_hash"] = c2pa_hash.strip().lower()
    log(f"anchor_output: hashing {size} bytes of output, submitting hash …")
    result = _http("POST", "/api/anchor", payload)
    if result.get("error"):
        return {
            "ok": False,
            "error": result.get("error"),
            "detail": result.get("body") or result.get("reason"),
        }
    rid = result.get("receipt_id", "")
    return {
        "ok": True,
        "receipt_id": rid,
        "receipt_url": f"{BASE_URL}/r/{rid}" if rid else None,
        "sha256_hex": sha256_hex,
        "sha512_hex": sha512_hex,
        "calendars_ok": result.get("calendars_ok"),
        "calendars_total": result.get("calendars_total"),
        "low_redundancy": result.get("low_redundancy", False),
        "pack_consumed": result.get("pack_consumed", False),
        "pack_remaining": result.get("pack_remaining", 0),
        "subscription_active": result.get("subscription_active", False),
        "size_bytes": size,
        "note": (
            "The output text did not leave this device — only the SHA-256 and "
            "SHA-512 fingerprints were transmitted. To re-verify, hash the exact "
            "same text and compare to the receipt's anchored hash."
        ),
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
        "calendars_ok": result.get("calendars_ok"),
        "calendars_total": result.get("calendars_total"),
        "all_ots_proofs_valid": all_ok,
        "receipt_url": f"{BASE_URL}/r/{result.get('receipt_id', '')}",
    }


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
                "label": {
                    "type": "string",
                    "description": "Optional human-readable label (max 200 chars). Stored on the receipt; do not include sensitive content.",
                },
                "c2pa_manifest_hash": {
                    "type": "string",
                    "description": "Optional SHA-256 hex of a C2PA manifest the receipt should reference (coexistence mode).",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "orphograph_verify_receipt",
        "description": (
            "Verify an existing Orphograph receipt against the calendars "
            "and Bitcoin chain. Returns the anchored hashes, the calendar "
            "attestation counts, and (when available) the Bitcoin block "
            "commitment time. Does not require an API key."
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
