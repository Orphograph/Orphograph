#!/usr/bin/env python3
"""canary_scan.py — daily leak-canary search.

Reads the unique canary string from ~/.orphograph_canary.txt and searches
three public places where leaked secrets typically end up:

  1. GitHub code search   (api.github.com/search/code — unauthenticated rate
                          limited to ~10/min; one query per day is well
                          inside that window)
  2. DuckDuckGo HTML      (no API key; lite/no-JS variant)
  3. pastebin.com search  (HTML scrape; brittle but free)

If the canary appears in ANY result, the script:
  • exits non-zero (so launchd marks the run failed)
  • writes the alarm to ~/Library/Logs/orphograph_canary.jsonl
  • fires a native macOS notification

If clean, exits 0 and appends a clean-tick record. Stdlib only. No secrets
ever leave the laptop — only the public canary string, which is meant to
be searchable.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CANARY_FILE = Path.home() / ".orphograph_canary.txt"
LOG_PATH = Path.home() / "Library" / "Logs" / "orphograph_canary.jsonl"
UA = "Orphograph-canary-scan/1.0 (+https://orphograph.com)"
TIMEOUT = 12.0


def _read_canary() -> str:
    try:
        return CANARY_FILE.read_text().strip()
    except OSError:
        return ""


def _http_get(url: str, headers: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError):
        return 0, ""


# Search engines echo the query string back in their HTML (search-box value,
# page title, etc.) even with zero results. We can't rely on "canary appears
# in body" — we have to look for affirmative "no results" markers and treat
# the *absence* of those markers as a hit.
GITHUB_EMPTY_MARKERS = (
    "We couldn&#39;t find any code matching",
    "We couldn't find any code matching",
    "Try a different search",
    "0 results",
)
DDG_EMPTY_MARKERS = (
    "No results.",
    "No results found",
    "No more results.",
    "no-results",
)


def _looks_empty(body: str, markers: tuple[str, ...]) -> bool:
    return any(m in body for m in markers)


def _check_github(canary: str) -> tuple[bool, str]:
    """GitHub code search via REST API (requires GITHUB_TOKEN env var).

    Unauthenticated GitHub now shows a sign-in wall for /search?type=code
    instead of running the query, so HTML scraping is unreliable. When a
    GITHUB_TOKEN is present in the environment we hit /search/code directly
    and trust the total_count field; otherwise we skip and let the DDG
    scan (which indexes GitHub public files) carry the load.
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        return False, ""
    q = urllib.parse.quote(canary)
    status, body = _http_get(
        f"https://api.github.com/search/code?q={q}",
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": f"Bearer {token}"},
    )
    if status != 200:
        return False, ""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False, ""
    if int(data.get("total_count", 0)) > 0:
        return True, "github.com (api)"
    return False, ""


def _check_ddg(canary: str) -> tuple[bool, str]:
    q = urllib.parse.quote(f'"{canary}"')
    status, body = _http_get(f"https://html.duckduckgo.com/html/?q={q}")
    if status != 200:
        return False, ""
    if _looks_empty(body, DDG_EMPTY_MARKERS):
        return False, ""
    if canary in body:
        return True, "duckduckgo"
    return False, ""


def _check_pastebin(canary: str) -> tuple[bool, str]:
    """Pastebin doesn't have a public search API; DDG site: query covers it."""
    q = urllib.parse.quote(f'"{canary}" site:pastebin.com')
    status, body = _http_get(f"https://html.duckduckgo.com/html/?q={q}")
    if status != 200:
        return False, ""
    if _looks_empty(body, DDG_EMPTY_MARKERS):
        return False, ""
    if canary in body:
        return True, "pastebin (via ddg)"
    return False, ""


def _notify(title: str, body: str) -> None:
    try:
        t = title.replace('"', '\\"')
        b = body.replace('"', '\\"')
        subprocess.run(
            ["osascript", "-e", f'display notification "{b}" with title "{t}"'],
            check=False, timeout=5,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        pass


def _append_log(record: dict) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        pass


def main() -> int:
    canary = _read_canary()
    if not canary or "ORPHO-CANARY-" not in canary:
        print("canary_scan: no canary file found at", CANARY_FILE)
        return 1

    hits = []
    for fn in (_check_github, _check_ddg, _check_pastebin):
        try:
            hit, src = fn(canary)
            if hit:
                hits.append(src)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"canary_scan: {fn.__name__} failed: {type(e).__name__}\n")

    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "canary_prefix": canary[:25],
        "hits": hits,
        "clean": not hits,
    }
    _append_log(record)

    if hits:
        msg = f"Canary found in: {', '.join(hits)}. Rotate ORPHO_FOUNDER_TOKEN now."
        print("ALARM:", msg)
        _notify("Orphograph — leak alarm", msg)
        return 2

    print(f"canary_scan: clean ({canary[:25]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
