#!/usr/bin/env python3
"""orphograph_watchdog — production liveness probe + auto-recovery.

Probes orphograph.com and /api/health every invocation (launchd schedules
the cadence). On unhealthy, parses `fly status -a orphograph --json` and
starts or restarts the app machine.

Stdlib only. Re-entrant. CLIENT-only: never modifies the production
server; only invokes the fly CLI on behalf of the founder using their
existing credentials.

Exit codes:
  0  prod healthy, no action
  1  prod unhealthy, recovery attempted
  2  uncovered failure (fly CLI missing, JSON parse error, etc.)

Logs each probe to ~/Hydroboro/logs/orphograph_watchdog.jsonl (no PII;
HTTP status codes only). After 3 consecutive failures within 10 minutes
attempts a Telegram notification via ~/.claude/notifier.py if importable,
otherwise writes ~/Hydroboro/logs/orphograph_watchdog_ALERT.txt.
"""
from __future__ import annotations

import datetime as _dt
import hashlib  # noqa: F401  (kept for forward use / required by spec)
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

FLY_BIN = "/opt/homebrew/bin/fly"
APP_NAME = "orphograph"

PROBE_URLS = (
    "https://orphograph.com/",
    "https://orphograph.com/api/health",
)
PROBE_TIMEOUT_S = 15
TOTAL_DEADLINE_S = 30

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

LOG_DIR = os.path.expanduser("~/Hydroboro/logs")
LOG_PATH = os.path.join(LOG_DIR, "orphograph_watchdog.jsonl")
ALERT_PATH = os.path.join(LOG_DIR, "orphograph_watchdog_ALERT.txt")

ALERT_WINDOW_S = 600  # 10 minutes
ALERT_THRESHOLD = 3


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _utc_now_iso() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _ensure_log_dir() -> None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except OSError:
        pass


def _probe_one(url: str, timeout: int = PROBE_TIMEOUT_S) -> int:
    """Return HTTP status code, or 0 on transport failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as e:
        return int(getattr(e, "code", 0) or 0)
    except Exception:
        return 0


def probe_prod() -> tuple[bool, dict]:
    """Probe all PROBE_URLS within TOTAL_DEADLINE_S. Healthy iff all 200."""
    start = time.monotonic()
    codes: dict[str, int] = {}
    for url in PROBE_URLS:
        remaining = TOTAL_DEADLINE_S - (time.monotonic() - start)
        if remaining <= 0:
            codes[url] = 0
            continue
        timeout = min(PROBE_TIMEOUT_S, max(1, int(remaining)))
        codes[url] = _probe_one(url, timeout=timeout)
    healthy = all(c == 200 for c in codes.values())
    return healthy, codes


def _fly_status() -> dict | None:
    try:
        proc = subprocess.run(
            [FLY_BIN, "status", "-a", APP_NAME, "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None


def _pick_app_machine(status: dict) -> dict | None:
    """Find the primary app machine in fly status JSON.

    fly JSON shape varies a bit; we look at "Machines" (a list) and prefer
    process_group == "app" if present, otherwise the first machine.
    """
    machines = (
        status.get("Machines")
        or status.get("machines")
        or []
    )
    if not isinstance(machines, list) or not machines:
        return None
    for m in machines:
        cfg = m.get("config") or {}
        pg = cfg.get("process_group") or m.get("process_group") or ""
        if str(pg).lower() == "app":
            return m
    return machines[0]


def _machine_state(m: dict) -> str:
    return str(m.get("state") or m.get("State") or "").lower()


def _machine_id(m: dict) -> str:
    return str(m.get("id") or m.get("ID") or "")


def _fly_run(args: list[str]) -> int:
    try:
        proc = subprocess.run(
            [FLY_BIN, *args],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return int(proc.returncode)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 127


def attempt_recovery(codes: dict) -> str:
    """Inspect fly status and start/restart the app machine. Return action tag."""
    status = _fly_status()
    if status is None:
        return "fly_unavailable"
    machine = _pick_app_machine(status)
    if machine is None:
        return "no_machine_found"
    state = _machine_state(machine)
    mid = _machine_id(machine)
    if not mid:
        return "no_machine_id"
    if state in ("stopped", "failed", "unhealthy"):
        rc = _fly_run(["machine", "start", mid])
        return f"machine_start:{mid}:rc{rc}"
    if state == "started":
        rc = _fly_run(["machine", "restart", mid, "--skip-health-checks"])
        return f"machine_restart:{mid}:rc{rc}"
    return f"no_action:state={state}"


# --------------------------------------------------------------------------- #
# Logging + alerting
# --------------------------------------------------------------------------- #


def append_log(entry: dict) -> None:
    _ensure_log_dir()
    line = json.dumps(entry, sort_keys=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _recent_failures(window_s: int = ALERT_WINDOW_S) -> int:
    """Count consecutive trailing UNHEALTHY entries within window_s."""
    if not os.path.exists(LOG_PATH):
        return 0
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return 0
    cutoff = _dt.datetime.utcnow() - _dt.timedelta(seconds=window_s)
    consec = 0
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            break
        ts = row.get("timestamp_utc") or ""
        try:
            t = _dt.datetime.strptime(ts.rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            break
        if t < cutoff:
            break
        if row.get("status") == "UNHEALTHY":
            consec += 1
        else:
            break
    return consec


def _try_telegram(text: str) -> bool:
    """Try ~/.claude/notifier.py. Return True if delivered."""
    notifier_dir = os.path.expanduser("~/.claude")
    notifier_file = os.path.join(notifier_dir, "notifier.py")
    if not os.path.exists(notifier_file):
        return False
    added = False
    if notifier_dir not in sys.path:
        sys.path.insert(0, notifier_dir)
        added = True
    try:
        import importlib
        mod = importlib.import_module("notifier")
        fn = getattr(mod, "notify", None) or getattr(mod, "send", None)
        if not callable(fn):
            return False
        try:
            result = fn(text)
            return bool(result) if result is not None else True
        except Exception:
            return False
    except Exception:
        return False
    finally:
        if added:
            try:
                sys.path.remove(notifier_dir)
            except ValueError:
                pass


def raise_alert(codes: dict, action: str) -> str:
    text = (
        "[orphograph watchdog] prod UNHEALTHY for "
        f"{ALERT_THRESHOLD} consecutive checks. codes={codes} action={action}"
    )
    if _try_telegram(text):
        return "telegram"
    _ensure_log_dir()
    try:
        with open(ALERT_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{_utc_now_iso()} {text}\n")
        return "file"
    except OSError:
        return "none"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def run_once() -> int:
    healthy, codes = probe_prod()
    entry: dict = {
        "timestamp_utc": _utc_now_iso(),
        "status": "HEALTHY" if healthy else "UNHEALTHY",
        "response_codes": codes,
        "action_taken": None,
    }
    if healthy:
        append_log(entry)
        return 0

    # Unhealthy path.
    try:
        action = attempt_recovery(codes)
    except Exception as exc:  # noqa: BLE001 (defensive: never crash watchdog)
        entry["action_taken"] = f"exception:{type(exc).__name__}"
        append_log(entry)
        return 2

    entry["action_taken"] = action
    append_log(entry)

    # Alert path: count UNHEALTHY entries including the one we just wrote.
    if _recent_failures() >= ALERT_THRESHOLD:
        alert_route = raise_alert(codes, action)
        append_log(
            {
                "timestamp_utc": _utc_now_iso(),
                "status": "ALERT",
                "response_codes": codes,
                "action_taken": f"alert:{alert_route}",
            }
        )

    if action.startswith("machine_start") or action.startswith("machine_restart"):
        return 1
    return 2


def main(argv: list[str] | None = None) -> int:
    try:
        return run_once()
    except Exception:  # noqa: BLE001
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
