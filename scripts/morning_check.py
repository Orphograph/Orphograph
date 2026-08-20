#!/usr/bin/env python3
"""morning_check.py — login-trigger health / revenue / feedback snapshot.

Runs at every macOS login (and every four hours thereafter) so the founder
sees, the moment they open the laptop, whether anything needs attention:

  1. Website          — orphograph.com responds 200, /api/health is healthy
  2. Paying customers — MRR, active subscriber count, churned-this-month
  3. Customer feedback — pending refund requests, recent support events

Reads ORPHO_FOUNDER_TOKEN from ~/.orphograph_secrets.env (mode 0600) and
calls /api/founder/morning-summary in one round trip. If the token is
absent the script degrades gracefully to public /api/health only.

Output:
  • One concise line to stdout (consumed by the calling Terminal or log)
  • A native macOS notification banner (osascript)
  • An append-only JSONL record at ~/Library/Logs/orphograph_morning_check.jsonl

Stdlib only. Safe to run from launchd, cron, or by hand. No secrets are
printed or logged. The script always exits 0 unless the website is fully
unreachable (exit 2) — that exit code is reserved so launchd users can
chain a louder alert if they want one.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROD_BASE = "https://orphograph.com"
SECRETS_PATH = Path.home() / ".orphograph_secrets.env"
LOG_PATH = Path.home() / "Library" / "Logs" / "orphograph_morning_check.jsonl"
TIMEOUT_S = 8.0
# Cloudflare's Bot Fight Mode rejects bot-shaped User-Agents (curl/, python-,
# anything advertising itself as automation) at the TLS handshake on the
# orange-cloud proxy. A real-browser UA gets through reliably; a "compatible;
# bot" UA was passing then started failing intermittently with SSL handshake
# timeouts. Settled on a stable Safari/macOS UA that matches the actual host
# the script runs on.
USER_AGENT = (
    "Orphograph-morning-check/1.0 (+https://orphograph.com)"
)
RETRIES = 3


def _read_founder_token() -> str:
    """Pull ORPHO_FOUNDER_TOKEN from ~/.orphograph_secrets.env if present."""
    env = os.environ.get("ORPHO_FOUNDER_TOKEN", "").strip()
    if env:
        return env
    if not SECRETS_PATH.exists():
        return ""
    try:
        for raw in SECRETS_PATH.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("ORPHO_FOUNDER_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _fetch(path: str, token: str = "") -> tuple[int, dict | None, float]:
    """GET <prod>/<path> with retries; return (status, parsed_json_or_None, rtt_ms).

    Cloudflare Bot Fight Mode causes intermittent SSL handshake timeouts even
    with a browser-shaped UA — first request after an idle period often gets
    challenged. Three attempts is enough to ride through that without making
    the check feel slow on the happy path.
    """
    import time
    last_err = ""
    t_overall = datetime.now()
    for attempt in range(RETRIES):
        req = urllib.request.Request(PROD_BASE + path, headers={"User-Agent": USER_AGENT})
        if token:
            req.add_header("X-Orpho-Founder", token)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                body = r.read()
                rtt = (datetime.now() - t_overall).total_seconds() * 1000
                try:
                    return r.status, json.loads(body.decode("utf-8")), rtt
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return r.status, None, rtt
        except urllib.error.HTTPError as e:
            rtt = (datetime.now() - t_overall).total_seconds() * 1000
            return e.code, None, rtt
        except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as e:
            last_err = type(e).__name__
            if attempt < RETRIES - 1:
                time.sleep(1.0 + attempt)  # 1s, 2s
    _ = last_err
    return 0, None, 0.0


def _notify(title: str, body: str) -> None:
    """macOS native notification. Silent if osascript is unavailable."""
    try:
        # Escape double quotes for AppleScript literals.
        t = title.replace('"', '\\"')
        b = body.replace('"', '\\"')
        subprocess.run(
            ["osascript", "-e", f'display notification "{b}" with title "{t}"'],
            check=False,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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


def _fmt_money(v) -> str:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


def main() -> int:
    token = _read_founder_token()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 1. Public health probe — always works, even with no token.
    health_status, health_json, health_rtt = _fetch("/api/health")
    site_ok = health_status == 200 and bool(health_json) and bool(health_json.get("ok"))

    record: dict = {
        "ts": now,
        "site_ok": site_ok,
        "health_http": health_status,
        "health_rtt_ms": round(health_rtt, 1),
    }

    revenue = None
    feedback = None
    summary_http = None
    if token:
        summary_http, summary_json, _ = _fetch("/api/founder/morning-summary", token=token)
        if summary_http == 200 and isinstance(summary_json, dict):
            revenue = summary_json.get("revenue") or {}
            feedback = summary_json.get("feedback") or {}
            record["summary_ok"] = True
            record["revenue"] = revenue
            record["feedback"] = feedback
        else:
            record["summary_ok"] = False
            record["summary_http"] = summary_http
    else:
        record["summary_ok"] = False
        record["summary_http"] = "no_token"

    _append_log(record)

    # Compose one human line for stdout + one for notification.
    if not site_ok:
        line = f"[orphograph] SITE DOWN — /api/health returned {health_status or 'no-response'}"
        print(line)
        _notify("Orphograph — site down", line)
        return 2

    counts = (health_json or {}).get("counts") or {}
    receipts = counts.get("receipts_on_disk", counts.get("anchors", "?"))
    last = (health_json or {}).get("last") or {}
    last_anchor = last.get("anchor_at") or "—"
    line_bits = [f"orphograph.com OK ({round(health_rtt)}ms)",
                 f"receipts={receipts}",
                 f"last_anchor={last_anchor}"]

    if revenue and not revenue.get("error"):
        mrr = _fmt_money(revenue.get("mrr"))
        active = ((revenue.get("customers") or {}).get("active", "?"))
        churn = revenue.get("churn_rate")
        churn_s = f"{round(float(churn) * 100, 1)}%" if isinstance(churn, (int, float)) else "—"
        line_bits.append(f"MRR={mrr} active={active} churn={churn_s}")

    alert_bits = []
    if feedback:
        pending = feedback.get("refund_requests_pending", 0) or 0
        today = feedback.get("refund_requests_today", 0) or 0
        events24 = feedback.get("recent_events_24h", 0) or 0
        line_bits.append(f"refunds_pending={pending} today={today} events24h={events24}")
        if pending > 0:
            alert_bits.append(f"{pending} refund request(s) waiting")
        if today > 0:
            alert_bits.append(f"{today} filed today")
    elif not token:
        line_bits.append("no founder token — public probe only")

    line = " | ".join(line_bits)
    print(line)

    # Notify only when something needs attention. Silent on green days.
    if alert_bits:
        _notify("Orphograph — attention", " · ".join(alert_bits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
