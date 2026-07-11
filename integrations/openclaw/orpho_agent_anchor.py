#!/usr/bin/env python3
"""orpho_agent_anchor.py — anchoring CLI for autonomous-agent workspaces.

Gives an agent (OpenClaw or any framework that can shell out) three verbs:

    anchor-file    anchor one file's bytes (skill file, config, artifact)
    anchor-memory  anchor a canonical manifest of the workspace's *.md
                   memory files, so later tampering with the agent's own
                   history is detectable
    anchor-text    anchor a short action record piped on stdin
    verify         check a receipt, optionally against a local file

A receipt proves the exact bytes existed at anchor time — nothing more.
It does not prove authorship, ownership, or that the action was correct.
File contents never leave the machine; only hashes are sent.

Usage:
    python3 orpho_agent_anchor.py anchor-file <path> [--label L]
    python3 orpho_agent_anchor.py anchor-memory <workspace_dir> [--label L]
    echo "sent invoice #42" | python3 orpho_agent_anchor.py anchor-text [--label L]
    python3 orpho_agent_anchor.py verify <receipt_id> [--file <path>]

Auth: --api-key / ORPHO_API_KEY, or --pack-token / ORPHO_PACK_TOKEN.
Receipts append to <workspace>/.orphograph/receipts.jsonl (or CWD for
anchor-file/anchor-text). Stdlib only.
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_BASE = "https://orphograph.com"
# Cloudflare 403s the default urllib UA (error 1010); keep a product UA
# aligned with the MCP server's outbound client.
USER_AGENT = "OrphographAgentAnchor/0.1 (+https://orphograph.com/integrations)"
HTTP_TIMEOUT_SEC = 15
CHUNK = 1024 * 1024
MEMORY_EXTS = (".md", ".markdown")
STATE_DIR_NAME = ".orphograph"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_file(path: str) -> tuple[str, str]:
    """Stream a file; return (sha256_hex, sha512_hex)."""
    h256, h512 = hashlib.sha256(), hashlib.sha512()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h256.update(chunk)
            h512.update(chunk)
    return h256.hexdigest(), h512.hexdigest()


def hash_text(text: str) -> tuple[str, str]:
    data = text.encode("utf-8")
    return hashlib.sha256(data).hexdigest(), hashlib.sha512(data).hexdigest()


def build_memory_manifest(workspace: str) -> str:
    """Canonical JSON manifest of every memory (*.md) file under workspace.

    Deterministic: POSIX relative paths, sorted, one sha256 per file.
    Hidden directories (including .orphograph) are skipped. Anchoring the
    manifest's hash commits to the exact state of the agent's memory.
    """
    entries = []
    for dirpath, dirnames, filenames in os.walk(workspace):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            if not fn.lower().endswith(MEMORY_EXTS) or fn.startswith("."):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, workspace).replace(os.sep, "/")
            sha256, _ = hash_file(full)
            entries.append({"path": rel, "sha256": sha256})
    manifest = {"version": 1, "kind": "agent-memory-manifest", "files": entries}
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"))


def post_anchor(base, sha256_hex, sha512_hex, label=None,
                api_key=None, pack_token=None) -> dict:
    payload = {"hash_hex": sha256_hex, "sha512_hex": sha512_hex}
    if label:
        payload["client_label"] = str(label)[:200]
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if api_key:
        headers["X-Orpho-Api-Key"] = api_key
    elif pack_token:
        headers["X-Pack-Token"] = pack_token
    req = urllib.request.Request(base.rstrip("/") + "/api/anchor",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as e:
        return {"error": "http_error", "status": e.code,
                "body": e.read().decode("utf-8", errors="replace")[:400]}
    except urllib.error.URLError as e:
        return {"error": "network_error", "reason": str(getattr(e, "reason", e))}


def get_verify(base, receipt_id: str) -> dict:
    url = base.rstrip("/") + "/api/verify/" + urllib.parse.quote(receipt_id)
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT,
               "Accept-Encoding": "identity"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as e:
        return {"error": "http_error", "status": e.code}
    except urllib.error.URLError as e:
        return {"error": "network_error", "reason": str(getattr(e, "reason", e))}


def append_receipt(state_dir: str, record: dict) -> None:
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, "receipts.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", default=os.environ.get("ORPHO_BASE_URL", DEFAULT_BASE))
    p.add_argument("--api-key", default=os.environ.get("ORPHO_API_KEY") or None)
    p.add_argument("--pack-token", default=os.environ.get("ORPHO_PACK_TOKEN") or None)
    p.add_argument("--label", default=None, help="client_label on the receipt (200 chars)")
    p.add_argument("--dry-run", action="store_true", help="hash only, no network")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("anchor-file"); sp.add_argument("path")
    sp = sub.add_parser("anchor-memory"); sp.add_argument("workspace")
    sub.add_parser("anchor-text")
    sp = sub.add_parser("verify"); sp.add_argument("receipt_id")
    sp.add_argument("--file", default=None)
    args = p.parse_args()
    if args.api_key and args.pack_token:
        p.error("use --api-key or --pack-token, not both")

    if args.cmd == "verify":
        result = get_verify(args.base, args.receipt_id)
        if args.file and "error" not in result:
            local256, _ = hash_file(args.file)
            result["local_sha256"] = local256
            result["local_match"] = (local256 == result.get("hash_hex")
                                     or local256 == result.get("sha256"))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if "error" not in result else 1

    if args.cmd == "anchor-file":
        sha256, sha512 = hash_file(args.path)
        subject, state_dir = args.path, os.path.join(os.getcwd(), STATE_DIR_NAME)
    elif args.cmd == "anchor-memory":
        manifest = build_memory_manifest(args.workspace)
        sha256, sha512 = hash_text(manifest)
        subject = f"memory-manifest:{args.workspace}"
        state_dir = os.path.join(args.workspace, STATE_DIR_NAME)
    else:  # anchor-text
        text = sys.stdin.read()
        if not text.strip():
            p.error("anchor-text: nothing on stdin")
        sha256, sha512 = hash_text(text)
        subject, state_dir = "action-text", os.path.join(os.getcwd(), STATE_DIR_NAME)

    if args.dry_run:
        print(json.dumps({"dry_run": True, "subject": subject, "sha256": sha256}))
        return 0

    result = post_anchor(args.base, sha256, sha512, label=args.label,
                         api_key=args.api_key, pack_token=args.pack_token)
    record = {"ts": utc_now_iso(), "subject": subject, "sha256": sha256,
              "response": result}
    append_receipt(state_dir, record)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
