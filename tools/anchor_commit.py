#!/usr/bin/env python3
"""anchor_commit.py — Bitcoin-anchored git commit / release attestation.

Anchor a git commit's identity to the Bitcoin chain via orphograph.com, so you
hold an independent, vendor-neutral proof that a specific commit (and therefore
the exact tree it points at) existed at or before a Bitcoin block. Complements
commit signing (which proves *who*, not *when-independently*) and supply-chain
attestation — no central timestamp authority, verifiable by anyone against the
chain with the standalone verifier.

What is anchored: the SHA-256 of the commit's canonical descriptor
    "orphograph-commit-anchor\ncommit <full-sha>\ntree <tree-sha>\n"
The commit SHA already content-addresses the whole commit (tree + parents +
message + author), so this binds the exact repository state at that commit. The
descriptor is reproducible, so anyone can re-derive the hash and verify the
receipt — no Orphograph dependency.

The receipt is written to `.orphograph/commits/<sha>.json` in the repo and,
with --git-notes, attached to the commit via `git notes` (ref orphograph) so the
attestation travels with the repository.

Privacy: only the SHA-256/512 of the descriptor is transmitted. In a public repo
the commit SHA is already public; in a private repo nothing about the content
leaves the machine beyond that derived hash.

Stdlib only. Usage:
    python3 tools/anchor_commit.py                      # anchor HEAD
    python3 tools/anchor_commit.py --ref v1.0.0         # anchor a tag/ref
    python3 tools/anchor_commit.py --git-notes          # also attach via git notes
    python3 tools/anchor_commit.py --dry-run            # show the hash, anchor nothing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ENDPOINT = "https://orphograph.com"
HTTP_TIMEOUT_SEC = 30
USER_AGENT = "orphograph-anchor-commit/0.1 (stdlib)"
GIT_NOTES_REF = "orphograph"


def _git(*args: str) -> str:
    """Run a git command, returning stripped stdout. Raises on failure."""
    out = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def resolve_commit(ref: str = "HEAD") -> tuple[str, str, str]:
    """Return (commit_sha, tree_sha, subject) for `ref`."""
    sha = _git("rev-parse", ref)
    tree = _git("rev-parse", f"{ref}^{{tree}}")
    subject = _git("show", "-s", "--format=%s", ref)
    return sha, tree, subject


def commit_descriptor(commit_sha: str, tree_sha: str) -> str:
    """The canonical, reproducible string that gets hashed + anchored."""
    return f"orphograph-commit-anchor\ncommit {commit_sha}\ntree {tree_sha}\n"


def descriptor_hashes(commit_sha: str, tree_sha: str) -> tuple[str, str]:
    raw = commit_descriptor(commit_sha, tree_sha).encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), hashlib.sha512(raw).hexdigest()


def anchor_hash(endpoint: str, hash_hex: str, sha512_hex: str,
                label: str, api_key: str) -> tuple[bool, dict]:
    body = {"hash_hex": hash_hex, "sha512_hex": sha512_hex, "client_label": label}
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["X-Orpho-Api-Key"] = api_key
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/api/anchor", data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return True, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode())
        except Exception:
            d = {"error": str(e)}
        d["status_code"] = e.code
        return False, d
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return False, {"error": f"{type(e).__name__}: {e}"}


def _store_receipt(repo_root: Path, commit_sha: str, record: dict) -> Path:
    out_dir = repo_root / ".orphograph" / "commits"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{commit_sha}.json"
    path.write_text(json.dumps(record, indent=2))
    return path


def _attach_git_note(commit_sha: str, record: dict) -> bool:
    try:
        _git("notes", f"--ref={GIT_NOTES_REF}", "add", "-f",
             "-m", json.dumps(record, separators=(",", ":")), commit_sha)
        return True
    except (subprocess.CalledProcessError, OSError):
        return False


def anchor_commit(ref: str = "HEAD", *, endpoint: str = DEFAULT_ENDPOINT,
                  api_key: str = "", label: bool = True, git_notes: bool = False,
                  dry_run: bool = False, repo_root: Path | None = None,
                  resolve_fn=resolve_commit, anchor_fn=anchor_hash) -> dict:
    """Anchor `ref`'s commit identity. Returns a result dict. Pure-ish: the git
    resolver + anchor client are injectable so this is unit-testable offline."""
    commit_sha, tree_sha, subject = resolve_fn(ref)
    sha256_hex, sha512_hex = descriptor_hashes(commit_sha, tree_sha)
    short = commit_sha[:12]
    if dry_run:
        return {"ok": True, "dry_run": True, "commit": commit_sha, "tree": tree_sha,
                "descriptor_sha256": sha256_hex, "subject": subject}
    client_label = f"commit {short}" if label else ""
    ok, resp = anchor_fn(endpoint, sha256_hex, sha512_hex, client_label, api_key)
    if not ok:
        return {"ok": False, "commit": commit_sha, "error": resp.get("error", resp),
                "detail": resp.get("status_code")}
    rid = resp.get("receipt_id", "")
    record = {
        "commit": commit_sha,
        "tree": tree_sha,
        "subject": subject,
        "descriptor_sha256": sha256_hex,
        "receipt_id": rid,
        "receipt_url": f"{endpoint.rstrip('/')}/r/{rid}" if rid else "",
        "anchored_at": resp.get("created_at"),
        "calendars_ok": resp.get("calendars_ok"),
        "anchored_by": "orphograph-anchor-commit/0.1",
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    result = {"ok": True, **record}
    if repo_root is not None:
        try:
            result["receipt_path"] = str(_store_receipt(repo_root, commit_sha, record))
        except OSError as e:
            result["receipt_path_error"] = f"{type(e).__name__}: {e}"
    if git_notes:
        result["git_note_attached"] = _attach_git_note(commit_sha, record)
    return result


def main() -> int:
    p = argparse.ArgumentParser(description="Bitcoin-anchor a git commit's identity")
    p.add_argument("--ref", default="HEAD", help="commit/tag/ref to anchor (default HEAD)")
    p.add_argument("--endpoint", default=os.environ.get("ORPHO_ENDPOINT", DEFAULT_ENDPOINT))
    p.add_argument("--api-key", default=os.environ.get("ORPHOGRAPH_API_KEY", ""))
    p.add_argument("--no-label", action="store_true", help="don't put the commit id on the receipt")
    p.add_argument("--git-notes", action="store_true", help="also attach the receipt via `git notes`")
    p.add_argument("--dry-run", action="store_true", help="print the hash; anchor nothing")
    args = p.parse_args()
    try:
        repo_root = Path(_git("rev-parse", "--show-toplevel"))
    except (subprocess.CalledProcessError, OSError):
        print(json.dumps({"ok": False, "error": "not a git repository"}))
        return 2
    try:
        result = anchor_commit(
            args.ref, endpoint=args.endpoint, api_key=args.api_key,
            label=not args.no_label, git_notes=args.git_notes, dry_run=args.dry_run,
            repo_root=None if args.dry_run else repo_root)
    except subprocess.CalledProcessError as e:
        print(json.dumps({"ok": False, "error": f"git failed: {e}"}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
