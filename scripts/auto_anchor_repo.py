#!/usr/bin/env python3
"""auto_anchor_repo.py — daily folder anchor of the meaningful repo state.

Hashes the current state of all in-scope repo files into a single
Merkle root via :mod:`server.merkle`, then submits that root as a
folder anchor to the production endpoint at
``https://orphograph.com/api/anchor_folder``. The anchor is marked
``private`` so paths and metadata are owner-gated behind the founder's
session / API key.

Every successful anchor appends one JSON line to
``outbox/AUTO_ANCHOR_HISTORY.jsonl`` with just enough metadata
(receipt_id, root_hex, calendars_ok, anchored_at_utc, git_sha) to
reconstruct provenance later — no leaf paths, no receipt body.

Stdlib only. MIT licensed.

Exit codes:
    0  success
    1  network failure (cannot reach the server)
    2  API rejection / non-2xx from the server
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import merkle  # noqa: E402  (sys.path adjustment is required first)

BASE_URL = os.environ.get("ORPHO_BASE_URL", "https://orphograph.com").rstrip("/")
API_KEY = os.environ.get("ORPHO_AUTO_ANCHOR_KEY", "").strip()
HISTORY_PATH = ROOT / "outbox" / "AUTO_ANCHOR_HISTORY.jsonl"

# Browser-shaped UA so the production CDN doesn't classify the daemon as
# a bot. The label tag identifies the source for log analysis.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15 OrphographAutoAnchor/1.0"
)

# Folder-walk exclusion list — same shape as compliance_scan, but with
# outbox/ also excluded because that directory is the founder's private
# scratch space and should be anchored selectively, not automatically.
EXCLUDE_PATTERNS: tuple[str, ...] = (
    ".git/*",
    "node_modules/*",
    "sdk-node/node_modules/*",
    "sdk-node/dist/*",
    "__pycache__/*",
    "*.pyc",
    "data/*",
    "receipts/*",
    "*.png",
    "*.jpg",
    "*.ico",
    "*.pdf",
    "*.zip",
    "*.ots",
    "outbox/*",
)


def git_short_sha(root: Path) -> str:
    """Return the short git SHA at HEAD, or an empty string on failure."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def build_manifest(root: Path) -> dict:
    """Walk the repo and produce a folder-anchor manifest."""
    tree = merkle.MerkleTree.from_folder(root, exclude=list(EXCLUDE_PATTERNS))
    return tree.manifest()


def post_anchor(manifest: dict, client_label: str, base_url: str, api_key: str) -> tuple[int, dict]:
    """POST the manifest to the folder-anchor endpoint.

    Returns ``(status_code, response_json_or_error_dict)``.
    Raises ``urllib.error.URLError`` on network failure (the caller maps
    that to exit code 1).
    """
    body = json.dumps({
        "manifest": manifest,
        "client_label": client_label,
        "private": True,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "identity",
    }
    if api_key:
        headers["X-Orpho-Api-Key"] = api_key
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/anchor_folder",
        data=body,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = resp.getcode()
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        return e.code, {"error": f"HTTP {e.code}", "body": err_body[:500]}
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, {"error": "non-JSON response", "body": raw[:500]}


def append_history(history_path: Path, row: dict) -> None:
    """Append a single JSON line to the history file (atomic enough for daily use)."""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT), help="repo root to anchor")
    parser.add_argument("--base-url", default=BASE_URL, help="API base URL")
    parser.add_argument("--quiet", action="store_true", help="suppress stdout")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    git_sha = git_short_sha(root)
    client_label = f"{git_sha or 'no-git'} auto-anchor"

    try:
        manifest = build_manifest(root)
    except ValueError as e:
        if not args.quiet:
            sys.stderr.write(f"[auto_anchor] manifest build failed: {e}\n")
        return 2

    try:
        status, payload = post_anchor(manifest, client_label, args.base_url, API_KEY)
    except urllib.error.URLError as e:
        if not args.quiet:
            sys.stderr.write(f"[auto_anchor] network error: {e}\n")
        return 1
    except TimeoutError as e:
        if not args.quiet:
            sys.stderr.write(f"[auto_anchor] timeout: {e}\n")
        return 1

    if status < 200 or status >= 300:
        if not args.quiet:
            sys.stderr.write(f"[auto_anchor] API rejection: {status} {payload.get('error','?')}\n")
        return 2

    receipt_id = payload.get("receipt_id", "")
    root_hex = payload.get("root_hex", "")
    calendars_ok = payload.get("calendars_ok", 0)

    row = {
        "receipt_id": receipt_id,
        "root_hex": root_hex,
        "calendars_ok": calendars_ok,
        "anchored_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": git_sha,
    }
    append_history(HISTORY_PATH, row)

    if not args.quiet:
        sys.stdout.write(
            f"[auto_anchor] receipt={receipt_id} root={root_hex[:16]} "
            f"calendars={calendars_ok}/5 git={git_sha or '(n/a)'}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
