"""test_money_surface_hardening_2026_05_29.py

Regression tests for the 2026-05-29 money-surface hardening pass (Tier 2/3):
  - /api/btc/claim no longer 500s (was NameError: _truncate_ip/_client_ip
    never existed; the real helper is _client_key()).
  - /api/me/affiliate/payout fails CLOSED with 503 instead of 500
    (handler was dispatched but never defined → AttributeError).
  - Rate-limit client-IP bucketing can no longer be bypassed by rotating a
    client-supplied X-Forwarded-For (leftmost token was attacker-controlled).
"""
from __future__ import annotations

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


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    port = _free_port()
    data_dir = tmp_path_factory.mktemp("hardening_data")
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "ORPHO_DATA_DIR": str(data_dir),
        "ORPHO_COOKIE_SECURE": "0",
        "RATE_LIMIT_PER_DAY": "100000",   # don't trip limits during these probes
    }
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "server" / "app.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _post(url, body=b"{}", headers=None, timeout=5):
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=body, method="POST", headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


# --------------------------------------------------------------- btc/claim crash

def test_btc_claim_does_not_500(server):
    """Before the fix, the source_ip kwarg evaluated _truncate_ip(self._client_ip())
    — both undefined — so every reaching POST crashed with a 500. Now it must
    degrade to a crisp 4xx/5xx-but-not-500 response."""
    # Valid JSON, missing fields → submit() should reject cleanly, NOT NameError.
    code = _post(f"{server}/api/btc/claim", body=b'{"email":"x@example.com"}')
    assert code != 500, "btc/claim must not 500 (NameError regression)"
    assert code in (200, 400, 403, 503), f"unexpected status {code}"


def test_btc_claim_empty_body_does_not_500(server):
    code = _post(f"{server}/api/btc/claim", body=b"{}")
    assert code != 500
    assert code in (200, 400, 403, 503)


# --------------------------------------------------------- affiliate payout glue

def test_affiliate_payout_fails_closed_not_500(server):
    """Dispatched-but-undefined handler used to AttributeError → 500. Now it
    fails closed with a clear 503 and never auto-grants value."""
    code = _post(f"{server}/api/me/affiliate/payout",
                 body=b'{"method":"credits"}')
    assert code != 500, "affiliate payout must not 500 (missing-handler regression)"
    assert code == 503


# --------------------------------------------------------- XFF bypass (pure unit)

def test_resolve_peer_ip_ignores_spoofed_leftmost_xff():
    """Rotating the LEFTMOST X-Forwarded-For must NOT change the bucket key:
    the rightmost (proxy-appended) entry is authoritative."""
    import app
    key_a = app._resolve_peer_ip("", "1.2.3.4, 9.9.9.9", "10.0.0.1", True)
    key_b = app._resolve_peer_ip("", "5.6.7.8, 9.9.9.9", "10.0.0.1", True)
    assert key_a == key_b == "9.9.9.9"


def test_resolve_peer_ip_prefers_platform_real_ip_header():
    import app
    chosen = app._resolve_peer_ip("8.8.8.8", "1.2.3.4, 9.9.9.9", "10.0.0.1", True)
    assert chosen == "8.8.8.8"


def test_resolve_peer_ip_untrusted_ignores_headers():
    """With no trusted proxy, client-supplied headers must never override the
    real socket peer — else any client spoofs an unlimited number of buckets."""
    import app
    chosen = app._resolve_peer_ip("8.8.8.8", "1.2.3.4", "10.0.0.1", False)
    assert chosen == "10.0.0.1"


def test_resolve_peer_ip_falls_back_to_peer_when_no_headers():
    import app
    assert app._resolve_peer_ip("", "", "10.0.0.1", True) == "10.0.0.1"
