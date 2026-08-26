"""_srv.py — ONE correct way to spin a server in a test.

Written 2026-08-25 after three separate defects traced to eleven hand-copied
server fixtures that had each drifted:

  * PORT REUSE RACE. `_free_port()` binds :0 and closes, so calling it twice
    can hand back the SAME port and the second server fails to bind. Twenty-one
    test files carry a copy of that helper. Fix: reserve every port at once,
    holding the sockets, and release them together.

  * STARTUP DEADLINE TOO SHORT. Copies used 10s or 15s. With eleven fixtures
    the suite times out under load, which reads as a product failure. Fix: one
    deadline, tuned in one place.

  * STDERR THROWN AWAY — the expensive one. Seven copies pass
    `stderr=subprocess.DEVNULL`, so a server that CRASHES on boot reports only
    "server did not start". A real crash-on-boot race in _seed_sample_receipt
    (FileExistsError when two processes share ORPHO_DATA_DIR) hid behind that
    message until the output was captured by hand. Fix: always capture, and
    put the server's own last words in the failure message.

Use `spin(tmp_path, n=1)` and get back bases you can hit. Anything a specific
test needs beyond this belongs in that test, not in another copy of this.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP = REPO_ROOT / "server" / "app.py"

STARTUP_TIMEOUT_SEC = 45
_TAIL_CHARS = 1500


def reserve_ports(n: int) -> list[int]:
    """N distinct free ports. Reserved together so two calls cannot collide."""
    holders = [socket.socket() for _ in range(n)]
    try:
        for h in holders:
            h.bind(("127.0.0.1", 0))
        ports = [h.getsockname()[1] for h in holders]
    finally:
        for h in holders:
            h.close()
    assert len(set(ports)) == n, f"port collision: {ports}"
    return ports


def base_env(data_dir: str | os.PathLike, port: int, **extra: str) -> dict:
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "ORPHO_DATA_DIR": str(data_dir),
        "ORPHO_COOKIE_SECURE": "0",
        "ORPHO_OFFLINE_CALENDARS": "1",
        # Default generous: a rate-limited response is the LIMITER's verdict,
        # not the handler's, and a test that cannot tell them apart is vacuous.
        "RATE_LIMIT_PER_DAY": "100000",
        **extra,
    }
    env.pop("RESEND_API_KEY", None)
    return env


def spin(data_dir: str | os.PathLike, n: int = 1, **env_extra: str):
    """Start n server processes on one data dir. Yields (bases, procs, logs).

    Caller is responsible for stopping them; `server_processes` below does it.
    """
    ports = reserve_ports(n)
    procs, bases, logs = [], [], []
    for port in ports:
        log_path = Path(data_dir) / f"server-{port}.log"
        lf = open(log_path, "w")
        logs.append((log_path, lf))
        procs.append(subprocess.Popen(
            [sys.executable, str(APP)],
            env=base_env(data_dir, port, **env_extra),
            stdout=lf, stderr=subprocess.STDOUT,   # never DEVNULL — see docstring
        ))
        bases.append(f"http://127.0.0.1:{port}")
    return bases, procs, logs


def _tail(log_path: Path) -> str:
    try:
        return log_path.read_text(errors="replace")[-_TAIL_CHARS:]
    except OSError:
        return "(no server log)"


def wait_ready(bases, procs, logs) -> None:
    """Block until every server answers /api/health, or fail with its OUTPUT."""
    for base, proc, (log_path, _lf) in zip(bases, procs, logs):
        deadline = time.time() + STARTUP_TIMEOUT_SEC   # per server, not shared
        while time.time() < deadline:
            if proc.poll() is not None:
                break                                   # died — stop waiting
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1).read()
                break
            except Exception:
                time.sleep(0.2)
        else:
            _kill_all(procs, logs)
            pytest.fail(f"{base} did not start within {STARTUP_TIMEOUT_SEC}s\n"
                        f"--- server output ---\n{_tail(log_path)}")
        if proc.poll() is not None:
            _kill_all(procs, logs)
            pytest.fail(f"{base} EXITED during startup (code {proc.returncode})\n"
                        f"--- server output ---\n{_tail(log_path)}")


def _kill_all(procs, logs) -> None:
    for p in procs:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    for _path, lf in logs:
        try:
            lf.close()
        except Exception:
            pass


def server_processes(data_dir, n: int = 1, **env_extra: str):
    """Context-manager-ish generator for a pytest fixture:

        @pytest.fixture(scope="module")
        def server(tmp_path_factory):
            yield from _srv.server_processes(tmp_path_factory.mktemp("x"))
    """
    bases, procs, logs = spin(data_dir, n=n, **env_extra)
    wait_ready(bases, procs, logs)
    try:
        yield bases[0] if n == 1 else bases
    finally:
        _kill_all(procs, logs)
