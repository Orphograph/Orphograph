#!/usr/bin/env python3
"""weekly_anchor.py — re-anchor key project artifacts on a schedule.

Run weekly via launchd. Each run commits every tracked artifact — LICENSE,
the MCP server source, the brand assets, the ADRs — plus the current git
HEAD into a SINGLE folder-Merkle receipt, anchored to Bitcoin via
``/api/anchor_folder``. One anchor per run covers the whole set, so the run
fits inside the free-tier 3/day cap without a subscription, and every
artifact gets a Bitcoin-anchored inclusion proof against one root instead
of a separate receipt each.

Why one receipt, not N: the old version issued one ``/api/anchor`` call per
artifact. On the free tier only 3/day succeed, so most artifacts 429'd every
week and a cursor rotated which three got through. A folder-Merkle commits
all of them in one request — the root is what lands on Bitcoin, and an
inclusion proof (served from the persisted manifest) proves any single
artifact belongs to that dated root.

Output: appends a JSONL row per run to outbox/weekly_anchor_log.jsonl.

Stdlib only. The RFC 6962 tree is built with the repo's own canonical
``server/merkle.py`` (itself stdlib-only) so the root computed here is
byte-identical to what the server recomputes and verifies.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get("ORPHO_BASE_URL", "https://orphograph.com").rstrip("/")
API_KEY = os.environ.get("ORPHO_API_KEY", "").strip()
LOG_PATH = ROOT / "outbox" / "weekly_anchor_log.jsonl"

# Canonical RFC 6962 Merkle implementation — the same module the server uses
# to recompute and verify the root. Importing it (rather than reimplementing)
# is what guarantees the root_hex we submit matches the server's, so the
# anchor never 400s on a root mismatch. server/merkle.py is stdlib-only.
sys.path.insert(0, str(ROOT / "server"))
import merkle  # noqa: E402

HEADERS = {
    "Content-Type": "application/json",
    # Plain, self-identifying UA — not a browser spoof. Cloudflare blocks the
    # DEFAULT python-urllib UA, so we set an explicit one; we do NOT pretend
    # to be a browser.
    "User-Agent": "OrphographWeeklyAnchor/2.0",
    "Accept-Encoding": "identity",
}
if API_KEY:
    HEADERS["X-Orpho-Api-Key"] = API_KEY

# Artifacts committed on every run. Add new ones here as the project grows.
# The list deliberately mixes code (LICENSE, MCP server), brand assets (seal,
# wordmark), and prior-art docs (ADRs) — each needed for a distinct protection
# regime. Missing paths are skipped with a note, never fatal.
ARTIFACTS = [
    "LICENSE",
    "mcp/orphograph_mcp.py",
    "mcp/manifest.json",
    "mcp/server.json",
    "mcp/Dockerfile",
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

# Synthetic leaf path for the git HEAD commit. It is not a file on disk; the
# manifest commits (path, sha256) pairs, and the server never requires a leaf
# to correspond to a real file — so the current commit hash rides inside the
# same root as the artifacts. The "@" prefix marks it as synthetic metadata.
GIT_HEAD_LEAF_PATH = "@meta/git-HEAD"


def _hash_file(p: Path) -> tuple[bytes, int]:
    """Streaming SHA-256 of a file. Returns (digest_bytes, size)."""
    h = hashlib.sha256()
    size = 0
    with p.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.digest(), size


def git_head() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return ""


def collect_leaves(root: Path, artifacts: list[str], head: str) -> list[dict]:
    """Hash every existing artifact (+ the git HEAD) into leaf metadata.

    Returns a list of {path, file_sha256_hex, size_bytes} dicts, sorted by the
    UTF-8 byte order of the path — the canonical leaf order. Missing files are
    skipped. Pure (no network); this is the unit-tested seam.
    """
    raw: list[tuple[str, bytes, int]] = []
    for rel in artifacts:
        p = root / rel
        if not p.is_file():
            sys.stderr.write(f"[weekly_anchor]   skip (missing): {rel}\n")
            continue
        try:
            digest, size = _hash_file(p)
        except OSError as e:
            sys.stderr.write(f"[weekly_anchor]   skip ({e}): {rel}\n")
            continue
        raw.append((rel, digest, size))
    if head:
        head_digest = hashlib.sha256(head.encode("utf-8")).digest()
        raw.append((GIT_HEAD_LEAF_PATH, head_digest, len(head.encode("utf-8"))))
    raw.sort(key=lambda e: e[0].encode("utf-8"))
    return [
        {"path": rel, "file_sha256_hex": digest.hex(), "size_bytes": size}
        for rel, digest, size in raw
    ]


def build_manifest(leaves_in: list[dict]) -> dict:
    """Assemble an orphograph-merkle-v1-rfc6962 manifest from leaf metadata.

    Uses the canonical merkle module so the root is byte-identical to the
    server's recomputation. Self-verifies via MerkleTree.from_manifest before
    returning, so a malformed manifest fails here (loud, local) rather than as
    a server 400. Pure (no network).
    """
    if not leaves_in:
        raise ValueError("no leaves to anchor (all artifacts missing?)")
    leaf_hashes: list[bytes] = []
    leaves_meta: list[dict] = []
    for entry in leaves_in:
        digest = bytes.fromhex(entry["file_sha256_hex"])
        leaf = merkle._leaf_hash(entry["path"], digest)
        leaf_hashes.append(leaf)
        leaves_meta.append({
            "path": entry["path"],
            "file_sha256_hex": entry["file_sha256_hex"],
            "leaf_hex": leaf.hex(),
            "size_bytes": int(entry["size_bytes"]),
        })
    levels = merkle._build_levels(leaf_hashes)
    manifest = {
        "algorithm": merkle.ALGORITHM,
        "version": merkle.VERSION,
        "root_hex": levels[-1][0].hex(),
        "leaves": leaves_meta,
    }
    # Belt and suspenders: reconstruct from our own manifest. Raises if the
    # leaves do not commit to root_hex — the exact check the server runs.
    merkle.MerkleTree.from_manifest(manifest)
    return manifest


def anchor_folder(manifest: dict, label: str) -> dict:
    """POST the manifest to /api/anchor_folder. Returns the parsed response."""
    body = json.dumps({"manifest": manifest, "client_label": label}).encode("utf-8")
    headers = dict(HEADERS)
    pack_token = os.environ.get("ORPHO_WEEKLY_PACK_TOKEN", "").strip()
    if pack_token:
        headers["X-Pack-Token"] = pack_token
    req = urllib.request.Request(
        f"{BASE_URL}/api/anchor_folder", data=body, method="POST", headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body_raw = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body_raw = ""
        return {"error": f"HTTP {e.code}", "body": body_raw}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def main() -> int:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = git_head()
    sys.stderr.write(
        f"[weekly_anchor] {run_ts} starting (git HEAD: {head[:12] or '(n/a)'})\n"
    )

    leaves = collect_leaves(ROOT, ARTIFACTS, head)
    if not leaves:
        sys.stderr.write("[weekly_anchor] FAILED: no artifacts to anchor\n")
        return 1
    manifest = build_manifest(leaves)
    label = f"weekly-{run_ts[:10]}-{len(leaves)}-artifacts"

    result = anchor_folder(manifest, label)
    rid = result.get("receipt_id")
    err = result.get("error")
    cal_ok = result.get("calendars_ok", 0)
    cal_total = result.get("calendars_total", 0)
    if rid:
        sys.stderr.write(
            f"[weekly_anchor]   folder root {manifest['root_hex'][:16]}… "
            f"→ {rid}  ({cal_ok}/{cal_total} calendars, {len(leaves)} leaves)\n"
        )
    else:
        sys.stderr.write(
            f"[weekly_anchor]   folder anchor FAILED → {err} "
            f"{result.get('body','')}\n"
        )

    row = {
        "ts": run_ts,
        "mode": "folder",
        "root_hex": manifest["root_hex"],
        "receipt_id": rid,
        "error": err,
        "calendars_ok": cal_ok,
        "calendars_total": cal_total,
        "git_head": head or None,
        "leaves": [{"path": lf["path"], "sha256": lf["file_sha256_hex"]} for lf in leaves],
    }
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")

    if not rid:
        sys.stderr.write("[weekly_anchor] FAILED: anchor did not succeed\n")
        return 1
    sys.stderr.write(
        f"[weekly_anchor] done; 1 folder receipt over {len(leaves)} artifacts "
        f"logged to {LOG_PATH}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
