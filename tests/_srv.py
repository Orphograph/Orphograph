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

import http.client
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP = REPO_ROOT / "server" / "app.py"
TEST_SERVER = REPO_ROOT / "tests" / "_run_server.py"

STARTUP_TIMEOUT_SEC = 45
_TAIL_CHARS = 1500

# base URL -> log path of the server spun on it, so a request that finds the
# server gone can say what the server said last.
_LOG_BY_BASE: dict[str, Path] = {}


class ServerGone(RuntimeError):
    """A spun server stopped answering. The message carries its log tail."""


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
        # NO OFFLINE-CALENDAR KNOB EXISTS. This fixture used to set
        # ORPHO_OFFLINE_CALENDARS=1, which NOTHING in the product reads
        # (verified 2026-08-26 across 161 shipped files). engine.CALENDARS is
        # a hardcoded list of five real OpenTimestamps URLs with no env
        # override, so any test that anchors through this fixture SUBMITS
        # OVER THE NETWORK to third-party public calendars: about 3s per
        # anchor, and flaky whenever they are slow. That is a live
        # constraint on the suite, not a setting. Adding an override touches
        # DOCTRINE.md's five-calendar code invariant, so it is a founder
        # decision, not a test-side fix.
        # Default generous: a rate-limited response is the LIMITER's verdict,
        # not the handler's, and a test that cannot tell them apart is vacuous.
        "RATE_LIMIT_PER_DAY": "100000",
        **extra,
    }
    env.pop("RESEND_API_KEY", None)
    return env


def spin(data_dir: str | os.PathLike, n: int = 1, *,
         stub_calendars: bool = False, fail_calendars: str = "",
         **env_extra: str):
    """Start n server processes on one data dir. Yields (bases, procs, logs).

    Caller is responsible for stopping them; `server_processes` below does it.

    `fail_calendars` (only meaningful with `stub_calendars`) is a
    comma-separated list of short calendar tokens — "a", "b", "alice",
    "finney", "btc" — the stub refuses. Default "" keeps every existing
    caller's behaviour: all five accept.
    """
    ports = reserve_ports(n)
    procs, bases, logs = [], [], []
    for port in ports:
        log_path = Path(data_dir) / f"server-{port}.log"
        lf = open(log_path, "w")
        logs.append((log_path, lf))
        command = [sys.executable, str(APP)]
        if stub_calendars:
            # A test-harness process patch, not a product environment knob.
            # The handler, request parsing, engine persistence and response
            # serialization remain real; only third-party calendar I/O is
            # replaced with a valid deterministic acceptance body.
            command = [sys.executable, str(TEST_SERVER), "--stub-calendars"]
            if fail_calendars:
                command += ["--fail-calendars", fail_calendars]
        elif fail_calendars:
            raise ValueError("fail_calendars requires stub_calendars=True — "
                             "the real calendars cannot be shaped")
        procs.append(subprocess.Popen(
            command,
            env=base_env(data_dir, port, **env_extra),
            stdout=lf, stderr=subprocess.STDOUT,   # never DEVNULL — see docstring
        ))
        bases.append(f"http://127.0.0.1:{port}")
        _LOG_BY_BASE[bases[-1]] = log_path
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
    gone = {path for path, _lf in logs}
    for base in [b for b, path in _LOG_BY_BASE.items() if path in gone]:
        del _LOG_BY_BASE[base]          # a reused port must not inherit this log
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


def server_processes(data_dir, n: int = 1, *,
                     stub_calendars: bool = False, fail_calendars: str = "",
                     **env_extra: str):
    """Context-manager-ish generator for a pytest fixture:

        @pytest.fixture(scope="module")
        def server(tmp_path_factory):
            yield from _srv.server_processes(tmp_path_factory.mktemp("x"))
    """
    bases, procs, logs = spin(
        data_dir, n=n, stub_calendars=stub_calendars,
        fail_calendars=fail_calendars, **env_extra)
    wait_ready(bases, procs, logs)
    try:
        yield bases[0] if n == 1 else bases
    finally:
        _kill_all(procs, logs)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def request(base: str, path: str, method: str = "GET", body: bytes | None = None,
            headers: dict | None = None,
            timeout: float = 10) -> tuple[int, bytes, http.client.HTTPMessage]:
    """Exactly one round-trip against a spun server: (status, body, headers).
    3xx/4xx/5xx come back as sent — redirects are never followed, so a POST
    is never replayed as a GET against its Location. Headers come back as the
    HTTPMessage itself: `.get()` is case-insensitive and `.get_all()` /
    `.items()` keep a header the server sent twice, which a dict would hide.
    A server spun by `spin()` that has stopped answering — refused, reset or
    timed out — raises ServerGone with its last output instead of a bare
    connection error."""
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers or {})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers
    except (OSError, http.client.BadStatusLine, http.client.IncompleteRead) as e:
        # OSError covers URLError (refused), ConnectionError (reset) and
        # TimeoutError (wedged). NOT HTTPException as a whole: InvalidURL is
        # raised client-side before anything is sent, and is the test's bug.
        log_path = _LOG_BY_BASE.get(base)
        if log_path is None:
            raise
        raise ServerGone(f"{method} {base}{path} got no answer ({e!r})\n"
                         f"--- server output ---\n{_tail(log_path)}") from e


def raw_request(base: str, target: str, method: str = "GET", *,
                line: str | None = None, headers: str = "",
                timeout: float = 15) -> bytes:
    """Send one request line EXACTLY as written and return the raw response.

    urllib normalises the target, so it cannot send `//a/<token>`, `/./a/…` or
    the absolute form `http://host/a/<token>`, and those are shapes the server
    and its access log both see from real clients. `line` replaces the whole
    request line (a one-word line, a control character). `headers` is extra
    header lines, each ending in CRLF, sent as latin-1: a char 0x80-0xFF goes
    out as that one byte, which urllib refuses to send. A server that has
    stopped answering raises ServerGone with its output, like request()."""
    import socket
    from urllib.parse import urlparse
    u = urlparse(base)
    first = line if line is not None else f"{method} {target} HTTP/1.1"
    chunks = []
    try:
        with socket.create_connection((u.hostname, u.port), timeout=timeout) as s:
            s.sendall(f"{first}\r\nHost: {u.netloc}\r\n{headers}"
                      "User-Agent: uptime-check/1.0\r\nConnection: close\r\n\r\n"
                      .encode("latin-1"))
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
    except OSError as e:
        log_path = _LOG_BY_BASE.get(base)
        if log_path is None:
            raise
        raise ServerGone(f"{first!r} to {base} got no answer ({e!r})\n"
                         f"--- server output ---\n{_tail(log_path)}") from e
    return b"".join(chunks)


def _json_object(raw: bytes) -> dict:
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        return {"_raw": raw.decode("utf-8", "replace")}
    return parsed


def ok_json(status: int, rec: dict) -> dict:
    """The reply was a 200 carrying a JSON object. An empty or non-JSON body
    arrives as {"_raw": ...}; without this, `assert "field" not in rec` passes
    over a page that never rendered."""
    assert status == 200, (status, rec)
    assert "_raw" not in rec, f"200 without a JSON object: {rec['_raw'][:200]!r}"
    return rec


def get_json(base: str, path: str, headers: dict | None = None,
             timeout: float = 10) -> tuple[int, dict]:
    code, raw, _ = request(base, path, headers=headers, timeout=timeout)
    return code, _json_object(raw)


def post_json(base: str, path: str, payload: dict, headers: dict | None = None,
              timeout: float = 10) -> tuple[int, dict]:
    """POST a JSON body. The reply comes back as the object the server sent;
    anything that is not a JSON object comes back as {"_raw": ...}."""
    h = {"Content-Type": "application/json", **(headers or {})}
    code, raw, _ = request(base, path, "POST", json.dumps(payload).encode(), h,
                           timeout=timeout)
    return code, _json_object(raw)


def anchor(base: str, payload: dict, headers: dict | None = None,
           timeout: float = 10) -> tuple[int, dict]:
    return post_json(base, "/api/anchor", payload, headers, timeout)
