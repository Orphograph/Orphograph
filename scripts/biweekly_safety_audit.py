#!/usr/bin/env python3
"""biweekly_safety_audit.py — re-audit the safety class closed in the 2026-05-18 premortem.

Runs every 2 weeks via launchd (see scripts/com.orphograph.audit.plist.template),
or manually for ad-hoc checks. Writes a markdown report to
deploy/AUDIT_REPORTS/YYYY-MM-DD.md. Pure stdlib. Read-only.

Exit codes:
    0  all checks PASS (or SKIPPED with documented reason)
    1  one or more checks FAIL

Hard guarantees:
    * No real Stripe / NOWPayments / Resend API calls
    * No secret values written to the report
    * Idempotent — safe to re-run

Manual run:
    python3 scripts/biweekly_safety_audit.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# --------------------------------------------------------------------- paths

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "deploy" / "AUDIT_REPORTS"
WEB_INDEX = ROOT / "web" / "index.html"
DATA_RECEIPTS = ROOT / "data" / "receipts"

BASE_URL = "https://orphograph.com"
GENESIS_RECEIPT_ID = "o3WGD22T4UwqfCrb"
PYTEST_BASELINE = 381
HTTP_TIMEOUT = 15

# Mirrors server/engine.py CALENDARS as of 2026-05-18. Hard-coded so the audit
# is decoupled from a server import (server may not be importable in any env).
CALENDARS = [
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://alice.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
    "https://btc.calendar.catallaxy.com",
]

# Files whose appearance in `git log --all` would indicate a secret leak.
# Sourced from deploy/HMAC_SECRET_AUDIT_2026_05_18.md.
SECRET_FILES = [
    "data/.hmac_secret",
    "data/auth_sessions.jsonl",
    "data/auth_tokens.jsonl",
    "data/btc_address.txt",
    "data/cold_wallet_address.txt",
]

SECRET_ENV_KEYS = [
    "STRIPE_SECRET_KEY",
    "RESEND_API_KEY",
    "NOWPAYMENTS_API_KEY",
    "BTC_RECEIVE_ADDRESS",
]

# --------------------------------------------------------------- result types

PASS = "PASS"
FAIL = "FAIL"
SKIPPED = "SKIPPED"


class Finding:
    __slots__ = ("section", "status", "summary", "details")

    def __init__(self, section: str, status: str, summary: str, details: str = "") -> None:
        self.section = section
        self.status = status
        self.summary = summary
        self.details = details

    @property
    def failed(self) -> bool:
        return self.status == FAIL


# ---------------------------------------------------------------- http utils


def _http_get(url: str, timeout: int = HTTP_TIMEOUT) -> tuple[int, bytes, dict[str, str]]:
    req = urllib.request.Request(url, headers={"User-Agent": "orphograph-audit/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read(), dict(resp.headers)


def _head(url: str, timeout: int = 8) -> int:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "orphograph-audit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        # HTTP-level response (4xx/5xx). Host is reachable; treat as reachable.
        return e.code
    except (urllib.error.URLError, socket.timeout, OSError):
        return 0


# --------------------------------------------------------------- check funcs


def check_site_reachable() -> Finding:
    section = "1. Site reachability"
    try:
        status, _, _ = _http_get(BASE_URL + "/")
        if status != 200:
            return Finding(section, FAIL, f"HTTPS GET {BASE_URL}/ returned {status}")
    except Exception as e:  # noqa: BLE001
        return Finding(section, FAIL, f"HTTPS GET failed: {type(e).__name__}: {e}")

    # TLS cert: fetch peer cert via raw ssl
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection(("orphograph.com", 443), timeout=HTTP_TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname="orphograph.com") as ssock:
                cert = ssock.getpeercert()
        not_after = cert.get("notAfter")
        if not not_after:
            return Finding(section, FAIL, "TLS cert has no notAfter field")
        # Format: 'May 17 12:00:00 2027 GMT'
        exp = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days_left = (exp - datetime.now(timezone.utc)).days
        if days_left < 30:
            return Finding(
                section,
                FAIL,
                f"TLS cert expires in {days_left}d (< 30d threshold). notAfter={not_after}",
            )
        return Finding(section, PASS, f"200 OK; TLS cert valid for {days_left}d")
    except Exception as e:  # noqa: BLE001
        return Finding(section, FAIL, f"TLS check failed: {type(e).__name__}: {e}")


def check_api_health() -> Finding:
    section = "2. API health"
    try:
        status, body, _ = _http_get(BASE_URL + "/api/health")
        if status != 200:
            return Finding(section, FAIL, f"/api/health returned {status}")
        data = json.loads(body.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return Finding(section, FAIL, f"/api/health request failed: {type(e).__name__}: {e}")

    if not data.get("ok"):
        return Finding(section, FAIL, f"health.ok is falsy: {data.get('ok')!r}")
    if not isinstance(data.get("uptime_sec"), int) or data["uptime_sec"] <= 0:
        return Finding(section, FAIL, f"uptime_sec invalid: {data.get('uptime_sec')!r}")

    # If ACTIVE_PROBES is set on the server, calendar entries will have a real
    # 'reachable' bool. Otherwise 'reachable' is None / 'checked' False.
    cals = data.get("calendars", [])
    unreachable_active = [c for c in cals if c.get("reachable") is False]
    if unreachable_active:
        names = ", ".join(c.get("url", "?") for c in unreachable_active)
        return Finding(
            section,
            FAIL,
            f"{len(unreachable_active)} calendar(s) reachable=false: {names}",
        )
    return Finding(section, PASS, f"ok=true, uptime={data['uptime_sec']}s")


def check_css_cache_key() -> Finding:
    section = "3. CSS cache key freshness"
    try:
        local_html = WEB_INDEX.read_text(encoding="utf-8")
    except OSError as e:
        return Finding(section, FAIL, f"could not read local web/index.html: {e}")
    m_local = re.search(r'/index\.css\?v=(\d+)', local_html)
    if not m_local:
        return Finding(section, FAIL, "no /index.css?v=N in local web/index.html")
    local_v = int(m_local.group(1))

    try:
        status, body, _ = _http_get(BASE_URL + "/")
        if status != 200:
            return Finding(section, FAIL, f"GET / returned {status}")
        prod_html = body.decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return Finding(section, FAIL, f"GET / failed: {type(e).__name__}: {e}")

    m_prod = re.search(r'/index\.css\?v=(\d+)', prod_html)
    if not m_prod:
        return Finding(section, FAIL, "no /index.css?v=N in production HTML")
    prod_v = int(m_prod.group(1))

    if local_v - prod_v > 1:
        return Finding(
            section,
            FAIL,
            f"production v={prod_v} is {local_v - prod_v} versions behind local v={local_v}",
        )
    return Finding(section, PASS, f"local v={local_v}, prod v={prod_v}")


def check_genesis_receipt() -> Finding:
    section = "4. Genesis receipt status"
    url = f"{BASE_URL}/api/receipt/{GENESIS_RECEIPT_ID}"
    try:
        status, body, _ = _http_get(url)
        if status != 200:
            return Finding(section, FAIL, f"{url} returned {status}")
        data = json.loads(body.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return Finding(section, FAIL, f"request failed: {type(e).__name__}: {e}")

    rstatus = data.get("status")
    if rstatus not in ("pinned", "partial"):
        return Finding(
            section,
            FAIL,
            f"genesis receipt status={rstatus!r} (expected pinned or partial); 28h+ pending is a regression",
        )
    return Finding(section, PASS, f"status={rstatus}")


def check_calendars_reachable() -> Finding:
    section = "5. OTS calendar reachability"
    unreachable: list[str] = []
    for url in CALENDARS:
        code = _head(url)
        if code == 0:
            unreachable.append(url)
    if len(unreachable) > 1:
        return Finding(
            section,
            FAIL,
            f"{len(unreachable)}/{len(CALENDARS)} calendars unreachable: {', '.join(unreachable)}",
        )
    if unreachable:
        return Finding(
            section,
            PASS,
            f"1 calendar unreachable (within tolerance): {unreachable[0]}",
        )
    return Finding(section, PASS, f"all {len(CALENDARS)} calendars reachable")


def check_kill_switch_state() -> Finding:
    section = "6. Kill-switch state"
    try:
        status, body, _ = _http_get(BASE_URL + "/api/config")
        if status != 200:
            return Finding(section, FAIL, f"/api/config returned {status}")
        data = json.loads(body.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return Finding(section, FAIL, f"request failed: {type(e).__name__}: {e}")

    toggles = data.get("toggles") or {}
    flipped = [k for k in ("maintenance_mode", "checkout_disabled", "anchoring_disabled") if toggles.get(k)]
    if flipped:
        return Finding(section, FAIL, f"toggles in non-default state: {', '.join(flipped)}")
    return Finding(section, PASS, "all toggles in default off position")


def check_hmac_secret_history() -> Finding:
    section = "7. HMAC secret git history"
    if not (ROOT / ".git").exists():
        return Finding(section, SKIPPED, "not a git working tree")
    leaks: list[str] = []
    for path in SECRET_FILES:
        try:
            out = subprocess.run(
                ["git", "log", "--all", "--full-history", "--oneline", "--", path],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as e:
            return Finding(section, SKIPPED, f"git unavailable: {e}")
        if out.stdout.strip():
            # count lines only — never echo the commit subjects in case they contain secrets
            n = len(out.stdout.strip().splitlines())
            leaks.append(f"{path} ({n} commit(s))")
    if leaks:
        return Finding(section, FAIL, f"secret files in git history: {'; '.join(leaks)}")
    return Finding(section, PASS, f"all {len(SECRET_FILES)} secret files: 0 commits")


def check_receipts_pending() -> Finding:
    section = "8. Receipts stuck pending >24h (local)"
    if not DATA_RECEIPTS.exists():
        return Finding(section, SKIPPED, "no local data/receipts/ — needs prod data, out of scope")
    now = datetime.now(timezone.utc)
    stuck: list[str] = []
    scanned = 0
    for sub in DATA_RECEIPTS.iterdir():
        if not sub.is_dir():
            continue
        rfile = sub / "receipt.json"
        if not rfile.exists():
            continue
        scanned += 1
        try:
            data = json.loads(rfile.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") != "pending":
            continue
        created = data.get("created_at")
        if not created:
            continue
        try:
            ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        age_h = (now - ts).total_seconds() / 3600
        if age_h > 24:
            # Receipt IDs are non-PII but redact anyway to be safe
            rid = data.get("receipt_id", sub.name)
            stuck.append(f"{rid[:6]}… age={age_h:.1f}h")
    if stuck:
        return Finding(section, FAIL, f"{len(stuck)} pending >24h: {'; '.join(stuck)}")
    return Finding(section, PASS, f"scanned {scanned} receipts, 0 stuck pending")


def check_stripe_reconcile() -> Finding:
    section = "9. Stripe-credits ledger drift"
    if not os.environ.get("STRIPE_SECRET_KEY"):
        return Finding(section, SKIPPED, "STRIPE_SECRET_KEY absent")
    script = ROOT / "scripts" / "reconcile_stripe_ledger.py"
    if not script.exists():
        return Finding(section, SKIPPED, "scripts/reconcile_stripe_ledger.py not present")
    try:
        out = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.SubprocessError as e:
        return Finding(section, FAIL, f"could not run reconcile: {e}")
    last = (out.stdout.strip().splitlines() or [""])[-1]
    if out.returncode == 0:
        return Finding(section, PASS, f"reconcile exit=0; last line: {last[:120]}")
    return Finding(
        section,
        FAIL,
        f"reconcile exit={out.returncode}; last line: {last[:160]}",
    )


def check_fly_memory() -> Finding:
    section = "10. Fly memory headroom"
    fly = shutil.which("fly") or shutil.which("flyctl")
    if not fly:
        return Finding(section, SKIPPED, "fly CLI not in PATH")
    try:
        out = subprocess.run(
            [fly, "status", "--app", "orphograph", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.SubprocessError as e:
        return Finding(section, SKIPPED, f"fly invocation failed: {e}")
    if out.returncode != 0:
        return Finding(section, SKIPPED, f"fly status exit={out.returncode}: {out.stderr.strip()[:120]}")
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError as e:
        return Finding(section, FAIL, f"could not parse fly json: {e}")

    # The fly status --json shape contains a Machines array (key varies across
    # versions). Walk to find any 'memory_mb' or guest.memory_mb.
    mem_mb = _extract_machine_memory(data)
    if mem_mb is None:
        return Finding(section, SKIPPED, "could not locate machine memory in fly json output")
    if mem_mb <= 256:
        return Finding(section, FAIL, f"machine memory={mem_mb}MB (smallest tier)")
    return Finding(section, PASS, f"machine memory={mem_mb}MB")


def _extract_machine_memory(data: object) -> int | None:
    """Walk the fly status --json shape looking for a memory_mb value."""
    stack: list[object] = [data]
    found: list[int] = []
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for k, v in item.items():
                if k in ("memory_mb",) and isinstance(v, int):
                    found.append(v)
                elif k == "guest" and isinstance(v, dict) and isinstance(v.get("memory_mb"), int):
                    found.append(v["memory_mb"])
                else:
                    stack.append(v)
        elif isinstance(item, list):
            stack.extend(item)
    if not found:
        return None
    return min(found)


def check_test_suite() -> Finding:
    section = "11. Test suite green"
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:anchorpy", "-q", "--tb=no"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        return Finding(section, SKIPPED, f"pytest not available: {e}")
    output = out.stdout + "\n" + out.stderr
    m = re.search(r'(\d+)\s+passed', output)
    if not m:
        last = (out.stdout.strip().splitlines() or [""])[-1]
        return Finding(section, FAIL, f"pytest exit={out.returncode}; no 'N passed' line. last: {last[:160]}")
    n = int(m.group(1))
    failed_m = re.search(r'(\d+)\s+failed', output)
    failed_n = int(failed_m.group(1)) if failed_m else 0
    if failed_n > 0:
        return Finding(section, FAIL, f"{failed_n} failed, {n} passed")
    if n < PYTEST_BASELINE:
        return Finding(
            section,
            FAIL,
            f"{n} passed (below baseline {PYTEST_BASELINE}); tests may have been deleted",
        )
    return Finding(section, PASS, f"{n} passed (baseline {PYTEST_BASELINE})")


_KEY_PATTERNS = {
    "STRIPE_SECRET_KEY": re.compile(r'STRIPE_SECRET_KEY\s*=\s*["\']?(sk_(?:live|test)_[A-Za-z0-9]{8,})'),
    "RESEND_API_KEY": re.compile(r'RESEND_API_KEY\s*=\s*["\']?(re_[A-Za-z0-9_]{8,})'),
    "NOWPAYMENTS_API_KEY": re.compile(r'NOWPAYMENTS_API_KEY\s*=\s*["\']?([A-Z0-9]{20,})'),
    "BTC_RECEIVE_ADDRESS": re.compile(
        r'BTC_RECEIVE_ADDRESS\s*=\s*["\']?(bc1[0-9a-z]{20,}|[13][A-HJ-NP-Za-km-z1-9]{25,34})'
    ),
}


def check_inline_secrets() -> Finding:
    section = "12. Inline secrets in working tree"
    hits: list[str] = []
    scan_dirs = [ROOT / "web", ROOT / "server"]
    for base in scan_dirs:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in (".py", ".js", ".ts", ".html", ".css", ".json", ".env"):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for name, pat in _KEY_PATTERNS.items():
                if pat.search(content):
                    rel = path.relative_to(ROOT)
                    # Do NOT include the captured value — record only file + key name
                    hits.append(f"{name} in {rel}")
    if hits:
        return Finding(section, FAIL, f"{len(hits)} inline secret hit(s)", "\n".join(hits))
    return Finding(section, PASS, f"scanned {len(scan_dirs)} dirs for {len(_KEY_PATTERNS)} key patterns; no hits")


# --------------------------------------------------------------- driver

CHECKS: list[Callable[[], Finding]] = [
    check_site_reachable,
    check_api_health,
    check_css_cache_key,
    check_genesis_receipt,
    check_calendars_reachable,
    check_kill_switch_state,
    check_hmac_secret_history,
    check_receipts_pending,
    check_stripe_reconcile,
    check_fly_memory,
    check_test_suite,
    check_inline_secrets,
]


def run_all() -> list[Finding]:
    findings: list[Finding] = []
    for fn in CHECKS:
        try:
            findings.append(fn())
        except Exception as e:  # noqa: BLE001 — never let a check crash the agent
            findings.append(
                Finding(
                    fn.__name__,
                    FAIL,
                    f"check crashed: {type(e).__name__}: {e}",
                )
            )
    return findings


def render_report(findings: list[Finding], when: datetime) -> str:
    fails = [f for f in findings if f.status == FAIL]
    skipped = [f for f in findings if f.status == SKIPPED]
    passed = [f for f in findings if f.status == PASS]
    lines: list[str] = []
    lines.append(f"# Orphograph biweekly safety audit — {when.date().isoformat()}")
    lines.append("")
    lines.append(f"Generated: `{when.isoformat(timespec='seconds')}`")
    lines.append("")
    lines.append(f"**Totals:** {len(passed)} PASS · {len(fails)} FAIL · {len(skipped)} SKIPPED · {len(findings)} total")
    lines.append("")
    if fails:
        lines.append("## Findings (FAIL)")
        for f in fails:
            lines.append(f"- **{f.section}** — {f.summary}")
        lines.append("")
    else:
        lines.append("## Findings (FAIL)")
        lines.append("None.")
        lines.append("")
    lines.append("## Section detail")
    for f in findings:
        lines.append("")
        lines.append(f"### {f.section} — {f.status}")
        lines.append("")
        lines.append(f.summary)
        if f.details:
            lines.append("")
            lines.append("```")
            lines.append(f.details)
            lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Re-run manually: `python3 scripts/biweekly_safety_audit.py`")
    lines.append("")
    return "\n".join(lines)


def write_report(text: str, when: datetime, reports_dir: Path | None = None) -> Path:
    reports_dir = reports_dir or REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"{when.date().isoformat()}.md"
    out.write_text(text, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    when = datetime.now(timezone.utc)
    findings = run_all()
    text = render_report(findings, when)
    path = write_report(text, when)
    fail_count = sum(1 for f in findings if f.failed)
    print(f"audit complete: {len(findings)} checks, {fail_count} FAIL → {path}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
