#!/usr/bin/env python3
"""Anchor release artifacts via the Orphograph API from CI.

Reads configuration from environment variables (set by action.yml):

    ORPHO_PATHS          space-separated glob patterns (default "dist/*")
    ORPHO_API_KEY        optional subscription API key   -> X-Orpho-Api-Key
    ORPHO_PACK_TOKEN     optional prepaid pack token     -> X-Pack-Token
    ORPHO_BASE_URL       API base URL (default https://orphograph.com)
    ORPHO_FAIL_ON_ERROR  "true" to exit non-zero on any anchoring failure

For each matched file it computes SHA-256 + SHA-512 locally (the file
itself never leaves the runner), POSTs the hashes to /api/anchor, and
collects {file, sha256, receipt_id, receipt_url}.

Side effects:
    - writes orphograph-receipts.json in the workspace (cwd)
    - appends a markdown table to $GITHUB_STEP_SUMMARY when set
    - writes a `receipts` output to $GITHUB_OUTPUT when set

Secrets are only ever read from the environment, never from argv, so
they cannot appear in process listings or shell traces.

Honesty note: a receipt proves the hashed bytes existed at anchoring
time. It does not prove authorship, ownership, or legal validity.

stdlib only; no third-party dependencies.
"""

import glob
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

CLIENT_LABEL = "github-action"
MAX_ATTEMPTS = 2          # one retry on transient failure, never a loop
RETRY_SLEEP_SEC = 5
REQUEST_TIMEOUT_SEC = 30
INTER_FILE_PAUSE_SEC = 1  # gentle pacing between anchors
CHUNK = 1024 * 1024

FREE_TIER_HINT = (
    "Hit the free-tier rate limit (3 anchors/day/IP). GitHub-hosted "
    "runners share IP addresses, so the free tier is often already "
    "exhausted by other users. To anchor reliably from CI, pass an API "
    "key (api_key) or prepaid pack token (pack_token) as a secret."
)


def log(msg: str) -> None:
    print(msg, flush=True)


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def hash_file(path: str) -> tuple:
    """Return (sha256_hex, sha512_hex) of a file, streamed."""
    h256 = hashlib.sha256()
    h512 = hashlib.sha512()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            h256.update(block)
            h512.update(block)
    return h256.hexdigest(), h512.hexdigest()


def build_headers() -> dict:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "orphograph-github-action/1.0",
    }
    api_key = os.environ.get("ORPHO_API_KEY", "").strip()
    pack_token = os.environ.get("ORPHO_PACK_TOKEN", "").strip()
    if api_key:
        headers["X-Orpho-Api-Key"] = api_key
    elif pack_token:
        headers["X-Pack-Token"] = pack_token
    return headers


def anchor_one(base_url: str, headers: dict, sha256_hex: str, sha512_hex: str):
    """POST one anchor request. Returns (receipt_dict_or_None, fatal_reason_or_None).

    fatal_reason is a short string when anchoring cannot proceed at all
    (rate limit exhausted, service paused) so the caller can stop early.
    """
    payload = json.dumps({
        "hash_hex": sha256_hex,
        "sha512_hex": sha512_hex,
        "client_label": CLIENT_LABEL,
    }).encode("utf-8")
    url = base_url.rstrip("/") + "/api/anchor"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return body, None
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:500]
            except Exception:
                pass
            if exc.code == 429:
                # Rate limited: do NOT retry-hammer the API. Stop here.
                retry_after = ""
                try:
                    retry_after = str(json.loads(detail).get("retry_after_seconds", ""))
                except Exception:
                    pass
                reason = "rate_limited"
                if retry_after:
                    reason += " (retry_after_seconds=%s)" % retry_after
                return None, reason
            if exc.code == 503:
                return None, "anchoring_paused (HTTP 503)"
            log("::warning::anchor HTTP %d (attempt %d/%d): %s"
                % (exc.code, attempt, MAX_ATTEMPTS, detail))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log("::warning::anchor request failed (attempt %d/%d): %s"
                % (attempt, MAX_ATTEMPTS, exc))
        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_SLEEP_SEC)
    return None, "request_failed"


def append_step_summary(rows: list, errors: list) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_path:
        return
    lines = ["", "## Orphograph anchoring", ""]
    if rows:
        lines += [
            "| File | SHA-256 | Receipt |",
            "| --- | --- | --- |",
        ]
        for r in rows:
            lines.append("| %s | `%s...` | [%s](%s) |" % (
                r["file"], r["sha256"][:16], r["receipt_id"], r["receipt_url"]))
        lines += [
            "",
            "Each receipt proves these exact bytes existed at anchoring time "
            "(Bitcoin-anchored via OpenTimestamps). It does not prove "
            "authorship, ownership, or legal validity.",
        ]
    if errors:
        lines += ["", "**Not anchored:**", ""]
        lines += ["- %s" % e for e in errors]
    lines.append("")
    try:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    except OSError as exc:
        log("::warning::could not write step summary: %s" % exc)


def write_output(rows: list) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    try:
        with open(output_path, "a", encoding="utf-8") as fh:
            # Compact single-line JSON is safe for the name=value form.
            fh.write("receipts=%s\n" % json.dumps(rows, separators=(",", ":")))
    except OSError as exc:
        log("::warning::could not write GITHUB_OUTPUT: %s" % exc)


def main() -> int:
    base_url = os.environ.get("ORPHO_BASE_URL", "").strip() or "https://orphograph.com"
    patterns = (os.environ.get("ORPHO_PATHS", "").strip() or "dist/*").split()
    fail_on_error = env_flag("ORPHO_FAIL_ON_ERROR")
    headers = build_headers()
    authed = "X-Orpho-Api-Key" in headers or "X-Pack-Token" in headers

    files = []
    for pattern in patterns:
        for path in sorted(glob.glob(pattern, recursive=True)):
            if os.path.isfile(path) and path not in files:
                files.append(path)

    if not files:
        log("::warning::no files matched pattern(s): %s — nothing to anchor."
            % " ".join(patterns))
        write_output([])
        return 1 if fail_on_error else 0

    log("Anchoring %d file(s) via %s (auth: %s)"
        % (len(files), base_url, "yes" if authed else "no, free tier"))

    rows = []
    errors = []
    stopped_reason = None

    for i, path in enumerate(files):
        try:
            sha256_hex, sha512_hex = hash_file(path)
        except OSError as exc:
            errors.append("%s: unreadable (%s)" % (path, exc))
            continue

        body, fatal = anchor_one(base_url, headers, sha256_hex, sha512_hex)
        if fatal is not None:
            errors.append("%s: %s" % (path, fatal))
            if fatal.startswith("rate_limited") or fatal.startswith("anchoring_paused"):
                # Skip remaining files: retrying them would just spam the API.
                remaining = files[i + 1:]
                if remaining:
                    errors.append("skipped %d remaining file(s): %s"
                                  % (len(remaining), ", ".join(remaining)))
                stopped_reason = fatal
                break
            continue
        if body is None or "receipt_id" not in body:
            errors.append("%s: unexpected API response" % path)
            continue

        receipt_id = str(body["receipt_id"])
        row = {
            "file": path,
            "sha256": sha256_hex,
            "receipt_id": receipt_id,
            "receipt_url": "https://orphograph.com/r/" + receipt_id,
        }
        rows.append(row)
        log("anchored %s -> %s (calendars %s/%s)" % (
            path, row["receipt_url"],
            body.get("calendars_ok", "?"), body.get("calendars_total", "?")))
        if i + 1 < len(files):
            time.sleep(INTER_FILE_PAUSE_SEC)

    # Always write results, even partial ones.
    try:
        with open("orphograph-receipts.json", "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2)
            fh.write("\n")
        log("wrote orphograph-receipts.json (%d receipt(s))" % len(rows))
    except OSError as exc:
        errors.append("could not write orphograph-receipts.json: %s" % exc)

    append_step_summary(rows, errors)
    write_output(rows)

    if stopped_reason and stopped_reason.startswith("rate_limited") and not authed:
        log("::notice::" + FREE_TIER_HINT)

    if errors:
        for e in errors:
            log("::warning::" + e)
        if fail_on_error:
            log("::error::anchoring incomplete and fail_on_error is set")
            return 1
        log("anchoring incomplete; continuing because fail_on_error=false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
