"""Tests for the trap-cleanup behavior of the loopback e2e harness.

Regression guard for the bug fixed 2026-06-21: the harness started the server
with `( cd "$WT" && ... python3 server/app.py ... & echo $! )`, so `$!` captured
the *subshell* pid. Killing it left python orphaned (and the port bound). The
fix backgrounds python DIRECTLY so `$!` is the server's real pid, with a
`trap cleanup EXIT INT TERM` that does TERM -> wait -> KILL escalation and
`rm -rf` the temp dir.

These tests prove that pattern:
  1. kills the backgrounded pid on NORMAL exit and removes the temp dir;
  2. kills it and cleans up when the harness is INTERRUPTED (SIGTERM);
  3. captures the REAL child pid (not an orphaning subshell);
  4. (static) the committed harness still uses the safe pattern.

No network, no real server boot — tests 1-3 drive a minimal harness that
reproduces the exact cleanup idiom with a bounded `sleep` child (self-exits in
20s as a backstop so a failed assertion can never leak a process).
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARNESS = REPO / "tests" / "manual" / "logout_all_e2e.sh"

# Minimal harness reproducing the FIXED cleanup idiom: background a long-lived
# child DIRECTLY, capture $! (the real pid), trap cleanup on EXIT/INT/TERM.
# Writes "<pid> <datadir>" to $1; mode $2 = "exit" (return now) or "wait" (block
# until signalled). Bounded 20s sleeper = safety backstop against leaks.
_FIXED_HARNESS = r'''#!/usr/bin/env bash
set -u
REPORT="$1"
MODE="${2:-exit}"
DATA=$(mktemp -d "${TMPDIR:-/tmp}/trapclean.XXXXXX")
python3 -c "import time; time.sleep(20)" &
SRVPID=$!
cleanup() {
  if [ -n "${SRVPID:-}" ] && kill -0 "$SRVPID" 2>/dev/null; then
    pkill -P "$SRVPID" 2>/dev/null
    kill -TERM "$SRVPID" 2>/dev/null
    wait "$SRVPID" 2>/dev/null
    kill -0 "$SRVPID" 2>/dev/null && kill -KILL "$SRVPID" 2>/dev/null
  fi
  rm -rf "$DATA"
}
trap cleanup EXIT INT TERM
printf '%s %s\n' "$SRVPID" "$DATA" > "$REPORT"
if [ "$MODE" = "wait" ]; then
  : > "$REPORT.ready"
  wait "$SRVPID"
fi
'''


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    return not _pid_alive(pid)


def _wait_file(path: Path, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return path.exists()


def _write_fixed_harness(tmp_path) -> Path:
    h = tmp_path / "fixed_harness.sh"
    h.write_text(_FIXED_HARNESS)
    h.chmod(0o755)
    return h


def test_cleanup_kills_backgrounded_pid_on_normal_exit(tmp_path):
    h = _write_fixed_harness(tmp_path)
    report = tmp_path / "report.txt"
    subprocess.run(["bash", str(h), str(report), "exit"], check=True, timeout=30)
    pid_str, data_dir = report.read_text().split()
    pid = int(pid_str)
    # EXIT trap must have killed the child (no orphan) ...
    assert _wait_dead(pid), f"backgrounded pid {pid} survived normal exit — orphaned!"
    # ... and removed the temp dir.
    assert not Path(data_dir).exists(), f"temp dir {data_dir} not cleaned up on exit"


def test_cleanup_fires_on_sigterm(tmp_path):
    h = _write_fixed_harness(tmp_path)
    report = tmp_path / "report.txt"
    proc = subprocess.Popen(["bash", str(h), str(report), "wait"])
    child = None
    data_dir = None
    try:
        assert _wait_file(Path(str(report) + ".ready")), "harness never became ready"
        pid_str, data_dir = report.read_text().split()
        child = int(pid_str)
        assert _pid_alive(child), "child should be alive before signal"
        proc.send_signal(signal.SIGTERM)          # interrupt the harness
        proc.wait(timeout=15)
        assert _wait_dead(child), f"child {child} survived harness SIGTERM — trap didn't fire"
        assert not Path(data_dir).exists(), f"temp dir {data_dir} not cleaned on signal"
    finally:
        if proc.poll() is None:
            proc.kill()
        if child is not None and _pid_alive(child):
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_captured_pid_is_the_real_child_not_a_subshell(tmp_path):
    """Original bug: $! was a subshell, so the recorded pid's command was a
    shell, not python. Assert the recorded pid IS the python child."""
    h = _write_fixed_harness(tmp_path)
    report = tmp_path / "report.txt"
    proc = subprocess.Popen(["bash", str(h), str(report), "wait"])
    child = None
    try:
        assert _wait_file(Path(str(report) + ".ready")), "harness never became ready"
        pid_str, _ = report.read_text().split()
        child = int(pid_str)
        cmd = subprocess.run(
            ["ps", "-p", pid_str, "-o", "command="],
            capture_output=True, text=True,
        ).stdout.lower()
        assert "python" in cmd, (
            f"captured pid {child} is not the python child (ps: {cmd!r}) — subshell regression"
        )
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        if child is not None and _pid_alive(child):
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_committed_harness_uses_safe_cleanup_pattern():
    """Static guard on the real harness: must background python DIRECTLY (so $!
    is the server pid) and trap cleanup on EXIT/INT/TERM. Fails if anyone
    reverts to the orphaning `( cd && python3 server/app.py & )` subshell."""
    assert HARNESS.exists(), f"missing harness: {HARNESS}"
    src = HARNESS.read_text()
    assert "SRVPID=$!" in src, "harness must capture the backgrounded pid in $!"
    assert "trap cleanup EXIT INT TERM" in src, "harness must trap cleanup on EXIT/INT/TERM"
    # Must NOT launch the server inside a `( ... & )` subshell (the bug).
    assert not re.search(r"\(\s*cd[^\n]*python3 server/app\.py[^\n]*&", src), \
        "harness re-introduced the orphaning subshell-background pattern"
    # Must background `python3 server/app.py` directly, then capture $! next line.
    assert re.search(r"python3 server/app\.py[^\n]*&\s*\nSRVPID=\$!", src), \
        "harness must background `python3 server/app.py` directly and capture $! on the next line"
