#!/usr/bin/env python3
"""anchor.py — anchor a file (or hash) to Bitcoin via orphograph.com.

Privacy-by-construction:
  - If given a file: SHA-256 is computed LOCALLY. Only the 32-byte hash
    leaves the machine. The file's bytes are never uploaded.
  - Filename is included ONLY if --label is passed (opt-in).

Usage:
    anchor.py <file-or-hash> [--label LABEL] [--api-key KEY] [--endpoint URL]

Exit codes:
    0  receipt created (5/5 calendars OK)
    1  receipt created but some calendars failed
    2  bad input (file not found, invalid hash)
    3  network / rate-limit error
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
ENDPOINT = os.environ.get("ORPHOGRAPH_ENDPOINT", "https://orphograph.com")
HTTP_TIMEOUT = 30


def is_hash_arg(s: str) -> bool:
    return bool(HEX64.match(s.strip()))


def hash_file(path: Path) -> tuple[str, str]:
    """Return (sha256_hex, sha512_hex). Streams the file in 4MB chunks."""
    s256, s512 = hashlib.sha256(), hashlib.sha512()
    with path.open("rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            s256.update(chunk)
            s512.update(chunk)
    return s256.hexdigest(), s512.hexdigest()


def post_anchor(endpoint: str, hash_hex: str, sha512_hex: str | None,
                label: str, api_key: str) -> tuple[int, dict]:
    body = {"hash_hex": hash_hex, "client_label": label}
    if sha512_hex:
        body["sha512_hex"] = sha512_hex
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "orphograph-skill/0.1"}
    if api_key:
        headers["X-Orpho-Api-Key"] = api_key
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/api/anchor",
        data=data, method="POST", headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": str(e)}
    except (urllib.error.URLError, OSError) as e:
        return 0, {"error": str(e)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("target", help="path to a file, OR a 64-char hex SHA-256")
    p.add_argument("--label", default="",
                   help="optional filename/label stored alongside the hash (default: off — privacy)")
    p.add_argument("--api-key", default=os.environ.get("ORPHOGRAPH_API_KEY", ""),
                   help="orphograph API key (or set $ORPHOGRAPH_API_KEY); without it, uses free tier")
    p.add_argument("--endpoint", default=ENDPOINT,
                   help=f"orphograph endpoint (default: {ENDPOINT})")
    p.add_argument("--json", action="store_true", help="output JSON only (for scripting)")
    args = p.parse_args()

    # Determine if target is a file or a pre-computed hash.
    sha512_hex = None
    if is_hash_arg(args.target):
        sha256_hex = args.target.strip().lower()
    else:
        path = Path(args.target).expanduser()
        if not path.exists() or not path.is_file():
            print(f"error: not a file and not a 64-char hex hash: {args.target}", file=sys.stderr)
            return 2
        try:
            sha256_hex, sha512_hex = hash_file(path)
        except OSError as e:
            print(f"error reading file: {e}", file=sys.stderr)
            return 2

    code, data = post_anchor(args.endpoint, sha256_hex, sha512_hex,
                             args.label, args.api_key)
    if code == 0:
        print(f"network error: {data.get('error', 'unknown')}", file=sys.stderr)
        return 3
    if code == 429:
        print("rate limit reached on the free tier.", file=sys.stderr)
        print(f"  buy a Writer Pack (10 anchors / $19): {args.endpoint}/buy.html",
              file=sys.stderr)
        return 3
    if code >= 400:
        print(f"server returned {code}: {data}", file=sys.stderr)
        return 3

    rid = data.get("receipt_id", "")
    receipt_url = f"{args.endpoint.rstrip('/')}/r/{rid}"
    cals_ok = data.get("calendars_ok", 0)
    cals_total = data.get("calendars_total", 0)

    if args.json:
        print(json.dumps({
            "ok": True,
            "receipt_id": rid,
            "receipt_url": receipt_url,
            "calendars_ok": cals_ok,
            "calendars_total": cals_total,
            "sha256": sha256_hex,
            "sha512": sha512_hex,
        }, indent=2))
    else:
        print("Anchored.")
        print(f"  Receipt:   {rid}")
        print(f"  SHA-256:   {sha256_hex}")
        print(f"  Calendars: {cals_ok}/{cals_total} confirmed")
        print(f"  View:      {receipt_url}")
        print()
        print("Save the receipt JSON for offline verification:")
        print(f"  curl {args.endpoint.rstrip('/')}/api/receipt/{rid} > {rid}.receipt.json")
        print()
        print("Bitcoin block pin completes within ~1 hour. The receipt page")
        print("will then link to mempool.space and blockstream.info for")
        print("independent third-party verification.")

    return 0 if cals_ok == cals_total else 1


if __name__ == "__main__":
    sys.exit(main())
