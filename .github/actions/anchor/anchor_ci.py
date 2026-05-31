#!/usr/bin/env python3
"""anchor_ci.py — self-contained CI anchorer for the Orphograph composite action.

Hashes a file OR a string locally (SHA-256 + SHA-512) and anchors ONLY the
fingerprint to Bitcoin via orphograph.com/api/anchor — the bytes never leave the
runner. Reads inputs from environment variables (set by action.yml, which avoids
shell-injection), prints the receipt, and writes `receipt-id` / `receipt-url` to
$GITHUB_OUTPUT so downstream steps can use them.

Stdlib only. Self-contained so the action needs no pip install.

Env inputs:
  ORPHO_FILE        path to a file to anchor (mutually exclusive with ORPHO_TEXT)
  ORPHO_TEXT        a string to anchor (e.g. an SBOM digest, release notes, a sha)
  ORPHO_LABEL       optional receipt label
  ORPHOGRAPH_API_KEY  optional API key (free tier if omitted)
  ORPHO_ENDPOINT    default https://orphograph.com
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "https://orphograph.com"
USER_AGENT = "orphograph-ci-action/0.1 (stdlib)"
_CHUNK = 4 * 1024 * 1024


def hashes_of_file(path: str) -> tuple[str, str]:
    s256, s512 = hashlib.sha256(), hashlib.sha512()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            s256.update(chunk)
            s512.update(chunk)
    return s256.hexdigest(), s512.hexdigest()


def hashes_of_text(text: str) -> tuple[str, str]:
    raw = text.encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), hashlib.sha512(raw).hexdigest()


def anchor(endpoint: str, sha256: str, sha512: str, label: str, api_key: str,
           _transport=None) -> tuple[bool, dict]:
    body = {"hash_hex": sha256, "sha512_hex": sha512}
    if label:
        body["client_label"] = label[:200]
    if _transport is not None:
        return _transport(endpoint, body, api_key)
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["X-Orpho-Api-Key"] = api_key
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/api/anchor", data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode())
        except Exception:
            d = {"error": f"HTTP {e.code}"}
        d["status_code"] = e.code
        return False, d
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return False, {"error": f"{type(e).__name__}: {e}"}


def write_github_output(pairs: dict) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    try:
        with open(out, "a") as f:
            for k, v in pairs.items():
                f.write(f"{k}={v}\n")
    except OSError:
        pass


def run(env: dict, anchor_fn=anchor) -> tuple[int, dict]:
    """Pure-ish entrypoint (env in, (exit_code, result) out) so it's testable."""
    file_in = (env.get("ORPHO_FILE") or "").strip()
    text_in = env.get("ORPHO_TEXT") or ""
    label = (env.get("ORPHO_LABEL") or "").strip()
    api_key = (env.get("ORPHOGRAPH_API_KEY") or "").strip()
    endpoint = (env.get("ORPHO_ENDPOINT") or DEFAULT_ENDPOINT).strip()
    if file_in and text_in:
        return 2, {"ok": False, "error": "provide ORPHO_FILE or ORPHO_TEXT, not both"}
    if file_in:
        if not os.path.isfile(file_in):
            return 2, {"ok": False, "error": f"not a file: {file_in}"}
        s256, s512 = hashes_of_file(file_in)
    elif text_in:
        s256, s512 = hashes_of_text(text_in)
    else:
        return 2, {"ok": False, "error": "nothing to anchor: set ORPHO_FILE or ORPHO_TEXT"}
    ok, resp = anchor_fn(endpoint, s256, s512, label, api_key)
    if not ok:
        return 1, {"ok": False, "error": resp.get("error", resp), "sha256": s256}
    rid = resp.get("receipt_id", "")
    return 0, {
        "ok": True, "receipt_id": rid,
        "receipt_url": f"{endpoint.rstrip('/')}/r/{rid}" if rid else "",
        "sha256": s256, "calendars_ok": resp.get("calendars_ok"),
    }


def main() -> int:
    code, result = run(os.environ)
    print(json.dumps(result, indent=2))
    if result.get("ok"):
        write_github_output({"receipt-id": result["receipt_id"],
                             "receipt-url": result["receipt_url"]})
    return code


if __name__ == "__main__":
    sys.exit(main())
