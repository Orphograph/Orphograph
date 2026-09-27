"""Every 429 the server sends is built in one place.

Nine handlers built their 429 by hand (status, Retry-After, JSON body, length,
security headers) and had already drifted: one sent `Cache-Control: no-store`,
eight did not, while every other JSON answer goes through _json_response,
which always does. Found 2026-09-27 (@checkout-429-helper, widened from the
two checkout handlers to the class).
"""
from __future__ import annotations

import json
from pathlib import Path

import _srv

APP = Path(__file__).resolve().parent.parent / "server" / "app.py"
HEADERS = {"Content-Type": "application/json"}


def _429s_outside_the_helper(src: str) -> list[str]:
    """Every literal 429 in the module that is not inside _send_rate_limited.
    Semantic (the AST's integer constants), so send_response(status) with
    status = 429, or _json_response(self, 429, ...), cannot slip past the way
    they slipped past a text count of "send_response(429)" (found in review)."""
    import ast
    tree = ast.parse(src)
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_send_rate_limited")
    inside = {id(n) for n in ast.walk(helper)}
    return [f"line {n.lineno}" for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and n.value == 429 and id(n) not in inside]


def test_one_place_builds_a_429():
    offenders = _429s_outside_the_helper(APP.read_text())
    assert offenders == [], f"a 429 is built outside _send_rate_limited: {offenders}"


def test_control_the_scan_sees_the_shapes_that_slipped_past_the_text_count():
    planted = (
        "def _send_rate_limited(h, r, p):\n    h.send_response(429)\n"
        "def signout(self):\n    status = 429\n    self.send_response(status)\n"
        "def waitlist(self):\n    _json_response(self, 429, {})\n"
    )
    assert _429s_outside_the_helper(planted) == ["line 4", "line 7"]


def test_a_throttled_checkout_says_when_to_retry_and_is_not_cached(tmp_path):
    for base in _srv.server_processes(tmp_path, stub_calendars=True, stub_stripe=True,
                                      STRIPE_SECRET_KEY="sk_test_not_a_real_key",
                                      CHECKOUT_RATE_PER_HOUR="1"):
        body = json.dumps({"plan": "no-such-plan", "email": "a@example.test"}).encode()
        first = _srv.request(base, "/api/stripe/checkout", "POST", body, HEADERS)
        assert first[0] == 400, first[0]  # control: the budget of 1 is spent on a real refusal
        status, raw, headers = _srv.request(base, "/api/stripe/checkout", "POST", body, HEADERS)
        assert status == 429, status
        payload = json.loads(raw)
        assert payload["error"] == "rate limit exceeded"
        assert headers.get("Retry-After") == str(payload["retry_after_seconds"]), headers
        assert headers.get("Cache-Control") == "no-store", headers
        assert headers.get("Strict-Transport-Security"), "security headers must still ride on it"


def test_a_throttled_waitlist_signup_says_when_to_retry(tmp_path):
    """One of the nine answers that went through _json_response with the wait
    only in the body: no Retry-After header for clients that follow it."""
    for base in _srv.server_processes(tmp_path, stub_calendars=True, RATE_LIMIT_PER_DAY="1"):
        body = json.dumps({"email": "wait@example.test", "interest": "capture"}).encode()
        _srv.request(base, "/api/waitlist", "POST", body, HEADERS)
        status, raw, headers = _srv.request(base, "/api/waitlist", "POST", body, HEADERS)
        assert status == 429, (status, raw)
        assert headers.get("Retry-After") == str(json.loads(raw)["retry_after_seconds"]), headers
        assert headers.get("Cache-Control") == "no-store"


def test_a_throttled_sign_out_still_signs_the_browser_out(tmp_path):
    """Sign-out built its 429 by hand (send_response(status)); through the
    helper it must keep clearing the cookie, which is what signs the browser
    out, and gain no-store."""
    for base in _srv.server_processes(tmp_path, stub_calendars=True):
        for i in range(40):
            status, raw, headers = _srv.request(base, "/api/auth/signout", "POST", b"{}", {
                **HEADERS, "Cookie": f"orpho_sid=junk-{i}"})
            if status == 429:
                break
        assert status == 429, "control: junk sign-outs from one address are throttled"
        assert "Max-Age=0" in (headers.get("Set-Cookie") or "") or "expires" in (
            headers.get("Set-Cookie") or "").lower(), headers.get("Set-Cookie")
        assert headers.get("Retry-After") == str(json.loads(raw)["retry_after_seconds"])
        assert headers.get("Cache-Control") == "no-store"
