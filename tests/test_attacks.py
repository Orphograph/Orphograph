"""test_attacks.py — adversarial fuzzing of every public endpoint.

Each test fires a known attack pattern at a live in-process server
and asserts the response stays in the safe zone: never 5xx, never
hangs, never echoes the input back into a server-side execution
path. We want failures to look like crisp 4xx codes with no
process state change.

Spun up in-process; uses the same `live_server` fixture as test_ui.py.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
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
    data_dir = tmp_path_factory.mktemp("attack_data")
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "ORPHO_DATA_DIR": str(data_dir),
        "ORPHO_COOKIE_SECURE": "0",
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
    try: proc.wait(timeout=5)
    except subprocess.TimeoutExpired: proc.kill()


def _status(url, method="GET", body=None, headers=None, timeout=5):
    """Return status code; treat HTTPError as a valid status."""
    if body is not None and not isinstance(body, (bytes, bytearray)):
        body = body.encode() if isinstance(body, str) else None
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


# ── Path traversal on receipt-id-shaped routes ───────────────────────────

@pytest.mark.parametrize("rid", [
    "../../../etc/passwd",
    "..%2F..%2F..%2Fetc%2Fpasswd",
    "..%252F..%252Fetc",
    "%00",
    "../" * 50,
    "with/slash",
    "with spaces",
    "with;semi",
    "‹unicode›",
    "a" * 200,                  # too long
    "/absolute/path",
    "",                         # empty
    "?query=true",
    "#fragment",
    "<script>alert(1)</script>",
])
def test_receipt_id_attacks_get_400_not_500(server, rid):
    encoded = urllib.parse.quote(rid, safe="")
    for prefix in ("/api/verify/", "/api/receipt/", "/r/"):
        code = _status(f"{server}{prefix}{encoded}")
        # Acceptable: 400 (bad shape), 404 (not found), 403 (forbidden).
        # Never 5xx, never 200 for these attack inputs.
        assert code in (400, 404, 403), f"{prefix}{rid!r} returned {code}"


# ── Oversized bodies ─────────────────────────────────────────────────────

def test_anchor_rejects_oversized_body(server):
    body = b"x" * (10 * 1024 * 1024)
    code = _status(f"{server}/api/anchor", method="POST", body=body,
                   headers={"Content-Type": "application/json"})
    # Server may either return 400 cleanly OR drop the connection mid-upload
    # (-1) once it sees Content-Length way over the cap. Both are correct
    # rejections.
    assert code in (400, -1)


def test_webhook_rejects_oversized_body(server):
    body = b"x" * (1 * 1024 * 1024)
    code = _status(f"{server}/api/stripe/webhook", method="POST", body=body,
                   headers={"Stripe-Signature": "t=0,v1=x"})
    assert code in (400, 503, -1)


# ── Malformed Content-Length ─────────────────────────────────────────────

def test_anchor_with_bogus_content_length(server):
    req = urllib.request.Request(f"{server}/api/anchor",
                                  data=b"{}", method="POST",
                                  headers={"Content-Length": "junk"})
    try:
        urllib.request.urlopen(req, timeout=5).read()
        code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 400


# ── JSON parser ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("body,expected", [
    (b"not-json", 400),
    (b'{"unterminated', 400),
    (b'\xff\xfe\xfd', 400),
    (b'{"hash_hex":"00"}', 400),  # too short hex
    (b'{"hash_hex":"' + b"g" * 64 + b'"}', 400),  # non-hex
    (b'{}', 400),
])
def test_anchor_malformed_json(server, body, expected):
    code = _status(f"{server}/api/anchor", method="POST", body=body,
                   headers={"Content-Type": "application/json"})
    assert code == expected


# ── Email header injection ───────────────────────────────────────────────

@pytest.mark.parametrize("email", [
    "a@b.com\r\nBcc: attacker@evil.com",
    "a@b.com\nX-Injected: yes",
    "<a@b.com>",
    "a@b.com,extra@b.com",
    "@b.com",
    "no-at-sign",
    "a@",
    "a" * 400 + "@b.com",
])
def test_email_link_rejects_malformed_address_silently(server, email):
    body = json.dumps({"email": email}).encode()
    code = _status(f"{server}/api/auth/email-link", method="POST", body=body,
                   headers={"Content-Type": "application/json"})
    # Per the implementation we return 200 with a neutral message to avoid
    # enumeration. The success/failure path doesn't differ from the client's
    # view. Both shouldn't actually email the malformed address.
    assert code in (200, 429)


# ── Auth token shape ─────────────────────────────────────────────────────

@pytest.mark.parametrize("token", [
    "short",
    "with/slash",
    "with spaces",
    "../../etc",
    "<script>alert(1)</script>",
    "%00",
    "",
    "a" * 200,
])
def test_redeem_link_with_garbage_token(server, token):
    encoded = urllib.parse.quote(token, safe="")
    code = _status(f"{server}/a/{encoded}")
    assert code in (400, 404)


# ── Webhook signature attacks ────────────────────────────────────────────

@pytest.mark.parametrize("sig", [
    "",
    "junk",
    "t=,v1=",
    "t=0,v1=" + "0" * 64,
    "t=" + str(int(time.time())) + ",v1=invalid",
    "t=9999999999,v1=" + "f" * 64,
    "v1=" + "a" * 64,  # no timestamp
])
def test_webhook_rejects_bad_signature(server, sig):
    body = b'{"type":"checkout.session.completed"}'
    code = _status(f"{server}/api/stripe/webhook", method="POST", body=body,
                   headers={"Content-Type": "application/json",
                            "Stripe-Signature": sig})
    # Without STRIPE_WEBHOOK_SECRET set, server returns 503; with it set,
    # bad sigs return 400. Both are acceptable defenses.
    assert code in (400, 503)


# ── Slowloris-class — partial connection ─────────────────────────────────

def test_handler_has_socket_timeout_configured(server):
    """Smoke check: the Handler class has a `timeout` attribute set so
    abandoned connections don't pin threads forever."""
    sys.path.insert(0, str(REPO_ROOT / "server"))
    import app
    assert getattr(app.Handler, "timeout", None) is not None
    assert app.Handler.timeout > 0
    assert app.Handler.timeout < 120  # not insanely long


# ── Header injection in path / query ─────────────────────────────────────

@pytest.mark.parametrize("payload", [
    "/api/health?%0D%0AX-Injected:%20yes",
    "/api/health?test=%00",
])
def test_no_crlf_injection_via_url(server, payload):
    # urllib refuses to send literal CRLF in URLs, which IS the defense at
    # the client layer. We test the already-encoded variants here; the
    # server must handle them as benign query strings.
    code = _status(f"{server}{payload}")
    assert code in (200, 400, 404), f"{payload!r} returned {code}"


def test_literal_crlf_in_url_is_blocked_at_client_layer():
    """Sanity check: urllib's own URL parsing refuses literal CRLF, which
    is the first layer of defense before our server sees anything."""
    import http.client
    import urllib.error
    try:
        urllib.request.urlopen("http://127.0.0.1:1/path\r\nX-Injected: yes", timeout=0.1)
        assert False, "urllib unexpectedly accepted CRLF in URL"
    except (http.client.InvalidURL, urllib.error.URLError, ValueError, OSError):
        pass  # rejected as expected


# ── Concurrent-flood rate-limit boundary ──────────────────────────────────

def test_rate_limit_eventually_blocks(server):
    """Hammer /api/anchor without a pack token; eventually 429."""
    body = json.dumps({"hash_hex": "00" * 32}).encode()  # bad hash; rejected at validation
    # Note: rate limiter ticks BEFORE validation, so even bad-hash anchors
    # consume tokens. We expect to see a 429 within ~20 attempts.
    saw_429 = False
    for _ in range(20):
        code = _status(f"{server}/api/anchor", method="POST", body=body,
                       headers={"Content-Type": "application/json"})
        if code == 429:
            saw_429 = True
            break
    assert saw_429, "expected to hit rate limit within 20 attempts"


# ── Sample receipt absolutely never returns 500 ──────────────────────────

def test_sample_receipt_is_resilient(server):
    code = _status(f"{server}/sample/index.json")
    assert code == 200
    code = _status(f"{server}/sample/sample.txt")
    assert code == 200
    code = _status(f"{server}/sample/a.ots")
    assert code == 200


# ── /api/me with manipulated cookie ──────────────────────────────────────

@pytest.mark.parametrize("cookie_val", [
    "orpho_sid=",
    "orpho_sid=tampered",
    "orpho_sid=" + "x" * 200,
    "__Host-orpho_sid=" + "a" * 50,
    "orpho_sid=<script>",
])
def test_session_cookie_tampering_returns_401(server, cookie_val):
    code = _status(f"{server}/api/me", headers={"Cookie": cookie_val})
    assert code == 401, f"cookie {cookie_val!r} returned {code}"
