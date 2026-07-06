#!/usr/bin/env python3
"""slo_monitor.py - local/public SLO evidence for Orphograph.

This is intentionally not a deployer and not a recovery bot. It does not use
GitHub, Fly, Stripe, Resend, or any vendor API. It answers a narrower question:
"Do we have dated evidence that the product promise is still healthy?"

Checks:
  - public root and /api/health respond and stay within latency budgets
  - public config toggles are not accidentally pausing core service
  - public stats endpoint returns its expected shape
  - local receipt data has no pending Bitcoin pins older than the SLA
  - local upgrade worker log is fresh when pending receipts exist

Outputs:
  - append-only JSONL: outbox/SLO_MONITOR.jsonl by default
  - optional markdown report: deploy/SLO_REPORTS/YYYY-MM-DDTHHMMSSZ.md

Exit codes:
  0  PASS, or WARN/SKIPPED only
  1  one or more FAIL findings
  2  monitor crashed before producing a usable run
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = os.environ.get("ORPHO_SLO_BASE_URL", "https://orphograph.com")
DEFAULT_OUTBOX = ROOT / "outbox"
DEFAULT_LOG_PATH = Path(os.environ.get("ORPHO_SLO_LOG", str(DEFAULT_OUTBOX / "SLO_MONITOR.jsonl")))
DEFAULT_REPORT_DIR = Path(os.environ.get("ORPHO_SLO_REPORT_DIR", str(ROOT / "deploy" / "SLO_REPORTS")))
DEFAULT_RECEIPTS_DIR = Path(os.environ.get("ORPHO_RECEIPTS_DIR", str(ROOT / "data" / "receipts")))
DEFAULT_UPGRADE_LOG = Path(os.environ.get("ORPHO_UPGRADE_LOG", str(ROOT / "data" / "upgrade_log.jsonl")))

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
SKIPPED = "SKIPPED"

HTTP_TIMEOUT_S = float(os.environ.get("ORPHO_SLO_HTTP_TIMEOUT", "10"))
ROOT_LATENCY_BUDGET_MS = float(os.environ.get("ORPHO_SLO_ROOT_LATENCY_MS", "2000"))
HEALTH_LATENCY_BUDGET_MS = float(os.environ.get("ORPHO_SLO_HEALTH_LATENCY_MS", "1000"))
PENDING_SLA_HOURS = float(os.environ.get("ORPHO_SLO_PENDING_HOURS", "36"))
UPGRADE_STALE_HOURS = float(os.environ.get("ORPHO_SLO_UPGRADE_STALE_HOURS", "6"))

# Same practical Cloudflare workaround used by morning_check.py: bot-shaped
# user agents are sometimes challenged before they reach the app.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


@dataclass
class Finding:
    name: str
    status: str
    summary: str
    latency_ms: float | None = None
    details: str = ""
    observed: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == FAIL


@dataclass
class MonitorConfig:
    base_url: str = DEFAULT_BASE_URL
    receipts_dir: Path = DEFAULT_RECEIPTS_DIR
    upgrade_log: Path = DEFAULT_UPGRADE_LOG
    log_path: Path = DEFAULT_LOG_PATH
    report_dir: Path = DEFAULT_REPORT_DIR
    pending_sla_hours: float = PENDING_SLA_HOURS
    upgrade_stale_hours: float = UPGRADE_STALE_HOURS
    root_latency_budget_ms: float = ROOT_LATENCY_BUDGET_MS
    health_latency_budget_ms: float = HEALTH_LATENCY_BUDGET_MS
    no_network: bool = False

    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(ts: Any) -> datetime | None:
    if not isinstance(ts, str) or not ts.strip():
        return None
    raw = ts.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _redact_receipt_id(value: Any) -> str:
    rid = str(value or "")
    if len(rid) <= 6:
        return rid or "unknown"
    return f"{rid[:6]}..."


def _http_get(url: str, timeout: float = HTTP_TIMEOUT_S) -> tuple[int, bytes, dict[str, str], float]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            elapsed_ms = (time.monotonic() - started) * 1000
            return int(getattr(resp, "status", 200) or 200), body, dict(resp.headers), elapsed_ms
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.monotonic() - started) * 1000
        body = b""
        try:
            body = e.read()
        except Exception:
            body = b""
        return int(getattr(e, "code", 0) or 0), body, dict(getattr(e, "headers", {}) or {}), elapsed_ms


def _json_from_body(body: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def check_site_root(cfg: MonitorConfig) -> Finding:
    if cfg.no_network:
        return Finding("site_root", SKIPPED, "network checks disabled")
    url = cfg.normalized_base_url() + "/"
    try:
        status, body, _, latency = _http_get(url)
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as e:
        return Finding("site_root", FAIL, f"GET / failed: {type(e).__name__}", details=str(e))
    if status != 200:
        return Finding("site_root", FAIL, f"GET / returned {status}", latency_ms=latency)
    observed = {"bytes": len(body), "budget_ms": cfg.root_latency_budget_ms}
    if latency > cfg.root_latency_budget_ms:
        return Finding(
            "site_root",
            WARN,
            f"GET / ok but slow: {latency:.0f}ms > {cfg.root_latency_budget_ms:.0f}ms",
            latency_ms=latency,
            observed=observed,
        )
    return Finding("site_root", PASS, f"GET / 200 in {latency:.0f}ms", latency_ms=latency, observed=observed)


def check_api_health(cfg: MonitorConfig) -> Finding:
    if cfg.no_network:
        return Finding("api_health", SKIPPED, "network checks disabled")
    url = cfg.normalized_base_url() + "/api/health"
    try:
        status, body, _, latency = _http_get(url)
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as e:
        return Finding("api_health", FAIL, f"/api/health failed: {type(e).__name__}", details=str(e))
    if status != 200:
        return Finding("api_health", FAIL, f"/api/health returned {status}", latency_ms=latency)
    data = _json_from_body(body)
    if not data:
        return Finding("api_health", FAIL, "/api/health returned non-object JSON", latency_ms=latency)
    observed = {
        "ok": data.get("ok"),
        "uptime_sec": data.get("uptime_sec"),
        "version": data.get("version"),
        "budget_ms": cfg.health_latency_budget_ms,
    }
    if data.get("ok") is not True:
        return Finding("api_health", FAIL, f"health.ok is {data.get('ok')!r}", latency_ms=latency, observed=observed)
    if latency > cfg.health_latency_budget_ms:
        return Finding(
            "api_health",
            WARN,
            f"/api/health ok but slow: {latency:.0f}ms > {cfg.health_latency_budget_ms:.0f}ms",
            latency_ms=latency,
            observed=observed,
        )
    return Finding("api_health", PASS, f"ok=true in {latency:.0f}ms", latency_ms=latency, observed=observed)


def check_public_config(cfg: MonitorConfig) -> Finding:
    if cfg.no_network:
        return Finding("public_config", SKIPPED, "network checks disabled")
    url = cfg.normalized_base_url() + "/api/config"
    try:
        status, body, _, latency = _http_get(url)
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as e:
        return Finding("public_config", FAIL, f"/api/config failed: {type(e).__name__}", details=str(e))
    if status != 200:
        return Finding("public_config", FAIL, f"/api/config returned {status}", latency_ms=latency)
    data = _json_from_body(body)
    if not data:
        return Finding("public_config", FAIL, "/api/config returned non-object JSON", latency_ms=latency)
    toggles = data.get("toggles") if isinstance(data.get("toggles"), dict) else {}
    observed = {"toggles": {k: bool(v) for k, v in toggles.items()}}
    hard_pauses = [k for k in ("maintenance_mode", "anchoring_disabled") if toggles.get(k)]
    if hard_pauses:
        return Finding("public_config", FAIL, f"core service pause active: {', '.join(hard_pauses)}", observed=observed)
    if toggles.get("checkout_disabled"):
        return Finding("public_config", WARN, "checkout_disabled is active", observed=observed)
    return Finding("public_config", PASS, "core toggles are normal", observed=observed)


def check_public_stats(cfg: MonitorConfig) -> Finding:
    if cfg.no_network:
        return Finding("public_stats", SKIPPED, "network checks disabled")
    url = cfg.normalized_base_url() + "/api/stats"
    try:
        status, body, _, latency = _http_get(url)
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as e:
        return Finding("public_stats", FAIL, f"/api/stats failed: {type(e).__name__}", details=str(e))
    if status != 200:
        return Finding("public_stats", FAIL, f"/api/stats returned {status}", latency_ms=latency)
    data = _json_from_body(body)
    if not data:
        return Finding("public_stats", FAIL, "/api/stats returned non-object JSON", latency_ms=latency)
    anchors = data.get("anchors") if isinstance(data.get("anchors"), dict) else {}
    if "total" not in anchors:
        return Finding("public_stats", FAIL, "/api/stats missing anchors.total", latency_ms=latency)
    observed = {
        "anchors_total": anchors.get("total"),
        "anchors_last_24h": anchors.get("last_24h"),
        "anchors_last_7d": anchors.get("last_7d"),
    }
    return Finding("public_stats", PASS, f"anchors_total={anchors.get('total')}", latency_ms=latency, observed=observed)


def scan_local_receipts(cfg: MonitorConfig, now: datetime | None = None) -> tuple[Finding, dict[str, Any]]:
    now = now or _utc_now()
    receipts_dir = cfg.receipts_dir
    summary: dict[str, Any] = {
        "receipts_dir": str(receipts_dir),
        "scanned": 0,
        "pending": 0,
        "partial": 0,
        "pinned": 0,
        "frozen": 0,
        "stale_pending": [],
    }
    if not receipts_dir.exists():
        return Finding("local_receipts", SKIPPED, f"{receipts_dir} does not exist", observed=summary), summary
    try:
        children = sorted(receipts_dir.iterdir())
    except OSError as e:
        return Finding("local_receipts", FAIL, f"could not list {receipts_dir}: {e}", observed=summary), summary
    for sub in children:
        if not sub.is_dir():
            continue
        rfile = sub / "receipt.json"
        if not rfile.exists():
            continue
        try:
            data = json.loads(rfile.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        summary["scanned"] += 1
        status = str(data.get("status", "pending") or "pending")
        if status in ("pending", "partial", "pinned"):
            summary[status] += 1
        if data.get("upgrade_frozen"):
            summary["frozen"] += 1
        if status != "pending":
            continue
        created = _parse_iso(data.get("created_at"))
        if created is None:
            continue
        age_h = (now - created).total_seconds() / 3600
        if age_h > cfg.pending_sla_hours:
            summary["stale_pending"].append({
                "receipt": _redact_receipt_id(data.get("receipt_id") or sub.name),
                "age_hours": round(age_h, 1),
                "frozen": bool(data.get("upgrade_frozen")),
                "stalls": int(data.get("upgrade_stalls", 0) or 0),
            })
    observed = {k: v for k, v in summary.items() if k != "stale_pending"}
    observed["stale_pending_count"] = len(summary["stale_pending"])
    if summary["stale_pending"]:
        examples = ", ".join(f"{r['receipt']} age={r['age_hours']}h" for r in summary["stale_pending"][:5])
        return Finding(
            "local_receipts",
            FAIL,
            f"{len(summary['stale_pending'])} pending receipt(s) older than {cfg.pending_sla_hours:.0f}h: {examples}",
            observed=observed,
            details=json.dumps(summary["stale_pending"], indent=2),
        ), summary
    return Finding(
        "local_receipts",
        PASS,
        f"scanned={summary['scanned']} pending={summary['pending']} stale=0",
        observed=observed,
    ), summary


def _last_jsonl_timestamp(path: Path) -> datetime | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        for key in ("ts", "timestamp", "checked_at"):
            parsed = _parse_iso(row.get(key))
            if parsed is not None:
                return parsed
    return None


def check_upgrade_log_freshness(
    cfg: MonitorConfig,
    receipt_summary: dict[str, Any],
    now: datetime | None = None,
) -> Finding:
    now = now or _utc_now()
    pending = int(receipt_summary.get("pending", 0) or 0)
    if pending <= 0:
        return Finding("upgrade_log", PASS, "no pending receipts require upgrade cadence")
    if not cfg.upgrade_log.exists():
        return Finding("upgrade_log", WARN, f"{cfg.upgrade_log} missing while {pending} receipt(s) are pending")
    last = _last_jsonl_timestamp(cfg.upgrade_log)
    if last is None:
        return Finding("upgrade_log", WARN, f"{cfg.upgrade_log} has no parseable timestamp")
    age_h = (now - last).total_seconds() / 3600
    observed = {"pending": pending, "last_run_at": _iso(last), "age_hours": round(age_h, 2)}
    if age_h > cfg.upgrade_stale_hours:
        return Finding(
            "upgrade_log",
            WARN,
            f"upgrade log is stale: {age_h:.1f}h > {cfg.upgrade_stale_hours:.0f}h with {pending} pending",
            observed=observed,
        )
    return Finding("upgrade_log", PASS, f"last upgrade run {age_h:.1f}h ago", observed=observed)


def run_checks(cfg: MonitorConfig, now: datetime | None = None) -> list[Finding]:
    now = now or _utc_now()
    findings = [
        check_site_root(cfg),
        check_api_health(cfg),
        check_public_config(cfg),
        check_public_stats(cfg),
    ]
    receipt_finding, receipt_summary = scan_local_receipts(cfg, now=now)
    findings.append(receipt_finding)
    findings.append(check_upgrade_log_freshness(cfg, receipt_summary, now=now))
    return findings


def summarize(findings: list[Finding], when: datetime | None = None) -> dict[str, Any]:
    when = when or _utc_now()
    counts = {PASS: 0, WARN: 0, FAIL: 0, SKIPPED: 0}
    for f in findings:
        counts[f.status] = counts.get(f.status, 0) + 1
    status = FAIL if counts[FAIL] else (WARN if counts[WARN] else PASS)
    return {
        "ts": _iso(when),
        "overall": status,
        "counts": counts,
        "findings": [
            {
                "name": f.name,
                "status": f.status,
                "summary": f.summary,
                "latency_ms": round(f.latency_ms, 1) if f.latency_ms is not None else None,
                "observed": f.observed,
            }
            for f in findings
        ],
    }


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")


def render_markdown(summary: dict[str, Any], findings: list[Finding]) -> str:
    lines: list[str] = []
    lines.append(f"# Orphograph SLO monitor - {summary['ts']}")
    lines.append("")
    lines.append(f"**Overall:** {summary['overall']}")
    counts = summary["counts"]
    lines.append(f"**Totals:** {counts[PASS]} PASS / {counts[WARN]} WARN / {counts[FAIL]} FAIL / {counts[SKIPPED]} SKIPPED")
    lines.append("")
    lines.append("## Findings")
    for f in findings:
        latency = f" ({f.latency_ms:.0f}ms)" if f.latency_ms is not None else ""
        lines.append(f"- **{f.status}** `{f.name}`{latency} - {f.summary}")
    detailed = [f for f in findings if f.details]
    if detailed:
        lines.append("")
        lines.append("## Details")
        for f in detailed:
            lines.append(f"### {f.name}")
            lines.append("```")
            lines.append(f.details)
            lines.append("```")
    lines.append("")
    lines.append("This report contains no customer emails, filenames, full receipt IDs, or secret values.")
    return "\n".join(lines) + "\n"


def write_report(report_dir: Path, summary: dict[str, Any], findings: list[Finding]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = summary["ts"].replace(":", "").replace("-", "")
    out = report_dir / f"{stamp}.md"
    out.write_text(render_markdown(summary, findings), encoding="utf-8")
    return out


def _config_from_args(argv: list[str] | None = None) -> tuple[MonitorConfig, argparse.Namespace]:
    parser = argparse.ArgumentParser(description="Generate Orphograph SLO evidence without GitHub or Fly access.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Public base URL to probe.")
    parser.add_argument("--receipts-dir", type=Path, default=DEFAULT_RECEIPTS_DIR, help="Local receipts directory.")
    parser.add_argument("--upgrade-log", type=Path, default=DEFAULT_UPGRADE_LOG, help="Local upgrade log JSONL.")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH, help="Append-only JSONL output path.")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help="Markdown report directory.")
    parser.add_argument("--pending-sla-hours", type=float, default=PENDING_SLA_HOURS, help="Max pending age before FAIL.")
    parser.add_argument("--upgrade-stale-hours", type=float, default=UPGRADE_STALE_HOURS, help="Max upgrade-log age before WARN.")
    parser.add_argument("--root-latency-ms", type=float, default=ROOT_LATENCY_BUDGET_MS, help="Root latency WARN budget.")
    parser.add_argument("--health-latency-ms", type=float, default=HEALTH_LATENCY_BUDGET_MS, help="Health latency WARN budget.")
    parser.add_argument("--no-network", action="store_true", help="Skip public HTTP probes and scan local state only.")
    parser.add_argument("--write-report", action="store_true", help="Write a markdown report in addition to JSONL.")
    parser.add_argument("--json", action="store_true", help="Print the JSON summary instead of a compact line.")
    parser.add_argument("--fail-on-warn", action="store_true", help="Exit 1 when WARN findings are present.")
    args = parser.parse_args(argv)
    cfg = MonitorConfig(
        base_url=args.base_url,
        receipts_dir=args.receipts_dir,
        upgrade_log=args.upgrade_log,
        log_path=args.log,
        report_dir=args.report_dir,
        pending_sla_hours=args.pending_sla_hours,
        upgrade_stale_hours=args.upgrade_stale_hours,
        root_latency_budget_ms=args.root_latency_ms,
        health_latency_budget_ms=args.health_latency_ms,
        no_network=args.no_network,
    )
    return cfg, args


def main(argv: list[str] | None = None) -> int:
    try:
        cfg, args = _config_from_args(argv)
        when = _utc_now()
        findings = run_checks(cfg, now=when)
        summary = summarize(findings, when=when)
        append_jsonl(cfg.log_path, summary)
        report_path = write_report(cfg.report_dir, summary, findings) if args.write_report else None
        if args.json:
            payload = dict(summary)
            if report_path:
                payload["report_path"] = str(report_path)
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            msg = (
                f"{summary['overall']} "
                f"pass={summary['counts'][PASS]} warn={summary['counts'][WARN]} "
                f"fail={summary['counts'][FAIL]} skipped={summary['counts'][SKIPPED]}"
            )
            if report_path:
                msg += f" report={report_path}"
            print(msg)
        if summary["counts"][FAIL] > 0:
            return 1
        if args.fail_on_warn and summary["counts"][WARN] > 0:
            return 1
        return 0
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"slo_monitor crashed: {type(e).__name__}: {e}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
