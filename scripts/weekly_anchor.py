#!/usr/bin/env python3
"""weekly_anchor.py — re-anchor key project artifacts on a schedule.

Run weekly via launchd. Each run produces fresh Bitcoin-anchored receipts
for LICENSE, the MCP server source, the brand assets, and the current
git HEAD — accumulating into a continuous, unfalsifiable chain of
authorship evidence for trademark, copyright, and patent disputes.

Output: appends a JSONL row per run to outbox/weekly_anchor_log.jsonl.
Stderr logs go to ~/Library/Logs/orphograph_weekly_anchor.log (set by
the launchd plist).

Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get("ORPHO_BASE_URL", "https://orphograph.com").rstrip("/")
API_KEY = os.environ.get("ORPHO_API_KEY", "").strip()
LOG_PATH = ROOT / "outbox" / "weekly_anchor_log.jsonl"

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; OrphographWeeklyAnchor/1.0)",
    "Accept-Encoding": "identity",
}
if API_KEY:
    HEADERS["X-Orpho-Api-Key"] = API_KEY

# Artifacts re-anchored on every run. Add new ones here as the project
# grows. The list deliberately includes both code (LICENSE, MCP server)
# and brand assets (seal, wordmark) — both are needed for distinct
# protection regimes.
ARTIFACTS = [
    "LICENSE",
    "mcp/orphograph_mcp.py",
    "mcp/README.md",
    "web/mcp.html",
    "web/seal.png",
    "web/seal.svg",
    "web/favicon.png",
    "web/index.html",
    "web/method/architecture.html",
    "web/vs/c2pa.html",
    "web/continuity.html",
    "web/roadmap.html",
    "CODEOWNERS",
    "docs/adr/0001-stdlib-only-server.md",
    "docs/adr/0002-mit-license-for-the-verifier.md",
    "docs/adr/0003-bitcoin-anchored-prior-art.md",
]


def hash_file(p: Path) -> tuple[str, str]:
    h256 = hashlib.sha256()
    h512 = hashlib.sha512()
    with p.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h256.update(chunk)
            h512.update(chunk)
    return h256.hexdigest(), h512.hexdigest()


def anchor(sha256: str, sha512: str, label: str) -> dict:
    body = json.dumps({
        "hash_hex": sha256,
        "sha512_hex": sha512,
        "client_label": label,
    }).encode("utf-8")
    headers = dict(HEADERS)
    # With a pack token the office's self-anchors are paid-tier: no 3/day
    # cap, and the receipts are not subject to free-tier pruning. Set
    # ORPHO_WEEKLY_PACK_TOKEN in the launchd plist to enable.
    pack_token = os.environ.get("ORPHO_WEEKLY_PACK_TOKEN", "").strip()
    if pack_token:
        headers["X-Pack-Token"] = pack_token
    req = urllib.request.Request(
        f"{BASE_URL}/api/anchor", data=body, method="POST", headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body_raw = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body_raw = ""
        return {"error": f"HTTP {e.code}", "body": body_raw}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def git_head() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return ""


def main() -> int:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = git_head()
    sys.stderr.write(f"[weekly_anchor] {run_ts} starting (git HEAD: {head[:12] or '(n/a)'})\n")

    # Rotation: the free tier allows 3 anchors/day/IP, so an unpaid run can
    # never cover the whole list. Persist a cursor and take the next slice
    # each week — every artifact gets re-anchored on a cycle instead of the
    # first three hogging every run while the rest 429 silently.
    state_path = LOG_PATH.parent / "weekly_anchor_state.json"
    cursor = 0
    try:
        cursor = int(json.loads(state_path.read_text()).get("cursor", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    batch_size = len(ARTIFACTS) if os.environ.get("ORPHO_WEEKLY_PACK_TOKEN", "").strip() else 3
    ordered = [ARTIFACTS[(cursor + i) % len(ARTIFACTS)] for i in range(len(ARTIFACTS))]
    todo = ordered[:batch_size]

    receipts: list[dict] = []
    for rel in todo:
        p = ROOT / rel
        if not p.is_file():
            sys.stderr.write(f"[weekly_anchor]   skip (missing): {rel}\n")
            continue
        try:
            sha256, sha512 = hash_file(p)
        except OSError as e:
            sys.stderr.write(f"[weekly_anchor]   skip ({e}): {rel}\n")
            continue
        label = f"weekly-{rel.replace('/', '-')[:60]}"
        result = anchor(sha256, sha512, label)
        rid = result.get("receipt_id", result.get("error", "?"))
        cal = result.get("calendars_ok", 0)
        sys.stderr.write(f"[weekly_anchor]   {rel}  →  {rid}  ({cal}/5)\n")
        receipts.append({
            "path": rel, "sha256": sha256, "label": label,
            "receipt_id": rid if not result.get("error") else None,
            "error": result.get("error"),
            "calendars_ok": cal,
        })
        time.sleep(3)  # be gentle on calendars + free-tier rate limiter

    # Anchor the git HEAD commit hash itself as a separate small artifact —
    # this is the strongest "the code on this date" claim.
    if head:
        head_sha256 = hashlib.sha256(head.encode("utf-8")).hexdigest()
        head_sha512 = hashlib.sha512(head.encode("utf-8")).hexdigest()
        result = anchor(head_sha256, head_sha512, f"weekly-git-head-{head[:12]}")
        rid = result.get("receipt_id", result.get("error", "?"))
        cal = result.get("calendars_ok", 0)
        sys.stderr.write(f"[weekly_anchor]   git-HEAD  →  {rid}  ({cal}/5)\n")
        receipts.append({
            "path": "(git HEAD)", "git_head": head,
            "sha256": head_sha256,
            "label": f"weekly-git-head-{head[:12]}",
            "receipt_id": rid if not result.get("error") else None,
            "error": result.get("error"),
            "calendars_ok": cal,
        })

    row = {"ts": run_ts, "receipts": receipts}
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")
    anchored_n = sum(1 for r in receipts if r.get("receipt_id"))
    try:
        state_path.write_text(json.dumps({"cursor": (cursor + anchored_n) % len(ARTIFACTS),
                                          "updated": run_ts}))
    except OSError:
        pass
    if anchored_n == 0:
        sys.stderr.write("[weekly_anchor] FAILED: zero anchors succeeded this run\n")
        return 1
    sys.stderr.write(f"[weekly_anchor] done; {len(receipts)} anchors logged to {LOG_PATH}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
