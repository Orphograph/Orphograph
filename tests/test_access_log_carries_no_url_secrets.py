"""Secrets and addresses that travel in a URL must not reach the access log.

The access log writes the request line. It already truncates the client IP,
and it still wrote, in full:

  * `/a/<token>`                 a bearer sign-in token
  * `/api/pack/balance/<code>`   a claim code, which is what spends credits
  * `?e=<address>`, `?email=<address>`   a person's email address

The sign-in token became the urgent one on 2026-09-19. HEAD stopped spending
the token (a mail gateway's probe must not burn the person's link), which means
a probed link now sits in the log LIVE until the person clicks or it expires.
Before that change a logged token was always a dead one.

Drives a real server and reads the log file it actually wrote.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

import _srv

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("log_secrets")
    for base in _srv.server_processes(data_dir, stub_calendars=True):
        yield base, data_dir


def _mint_token(data_dir: Path, email: str) -> str:
    code = (
        "import os,sys;"
        f"os.environ['ORPHO_DATA_DIR']={str(data_dir)!r};"
        f"sys.path.insert(0,{str(REPO_ROOT / 'server')!r});"
        "import auth;"
        f"print(auth.issue_link_token({email!r})[0])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _log(data_dir: Path) -> str:
    logs = list(data_dir.glob("server-*.log"))
    assert len(logs) == 1, f"expected one server log, found {logs}"
    return logs[0].read_text(errors="replace")


def test_a_probed_sign_in_link_is_not_left_live_in_the_log(server):
    base, data_dir = server
    token = _mint_token(data_dir, "log-token@example.test")
    assert _srv.request(base, f"/a/{token}", method="HEAD", timeout=15)[0] == 303

    log = _log(data_dir)
    assert "HEAD /a/[redacted]" in log, "control: the request was not logged at all"
    assert token not in log, "a live sign-in token is sitting in the access log"

    # The link still works: redaction is about the log, not the request.
    status, _b, headers = _srv.request(base, f"/a/{token}?next=/pricing", timeout=15)
    assert status == 303 and "orpho_sid=" in headers.get("Set-Cookie", "")
    log = _log(data_dir)
    assert token not in log
    assert "GET /a/[redacted]?next=/pricing" in log, "the rest of the line must survive"


def test_claim_codes_and_addresses_are_not_logged(server):
    base, data_dir = server
    code = "pk_0123456789abcdef0123456789abcdef"
    _srv.request(base, f"/api/pack/balance/{code}", timeout=15)
    _srv.request(base, "/api/unsubscribe?e=log-address%40example.test", timeout=15)
    _srv.request(base, "/api/founder/customer?email=log-lookup%40example.test&x=1", timeout=15)

    log = _log(data_dir)
    assert "/api/pack/balance/[redacted]" in log and code not in log
    assert "/api/unsubscribe?e=[redacted]" in log
    assert "/api/founder/customer?email=[redacted]&x=1" in log, "only the value is removed"
    assert "log-address" not in log and "log-lookup" not in log


def test_ordinary_request_lines_are_left_alone(server):
    """The redaction must not eat paths that merely resemble the shapes."""
    base, data_dir = server
    for path in ("/api/health", "/about", "/pricing?ref=abc", "/lp/agent-receipts"):
        _srv.request(base, path, timeout=15)
    log = _log(data_dir)
    for line in ("GET /api/health ", "GET /about ", "GET /pricing?ref=abc ",
                 "GET /lp/agent-receipts "):
        assert line in log, f"{line!r} was altered or never logged"
    assert not re.search(r"GET /api/\[redacted\]", log)


def test_a_team_invite_code_in_a_share_link_does_not_reach_the_log(server):
    """The share link is `/team/join?code=<code>`; the code admits a person to
    the team, and every click wrote it to the access log."""
    base, data_dir = server
    code = "inv_LogCanary0123456789"
    _srv.request(base, f"/team/join?code={code}", timeout=15)
    _srv.request(base, "/team/join?plan=harmless-canary", timeout=15)
    text = _log(data_dir)
    assert "/team/join" in text, "control: the request reached the log at all"
    assert code not in text, "a team invite code was written to the access log"
    assert "plan=harmless-canary" in text, "the rule redacted a parameter it should not"
