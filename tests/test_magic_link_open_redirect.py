"""test_magic_link_open_redirect.py

The magic-link landing page must never redirect off-site
(VERIFIED DEFECT found and fixed 2026-08-25 auditing session/auth).

/a/<token> redeems a one-time login token, sets the session cookie, and then
redirects to `?next=`. The whitelist was:

    next_raw.startswith("/") and not next_raw.startswith("//")
    and "\\n" not in next_raw and "\\r" not in next_raw and len(next_raw) < 200

That is not sufficient, because a browser normalises the value BEFORE
resolving it. Three payloads bypassed it against a live server:

    ?next=/%5Cevil.example      -> Location: /\\evil.example    -> //evil.example
    ?next=/%09//evil.example    -> Location: /<TAB>//evil...    -> //evil.example
    ?next=/./%5C/evil.example   -> Location: /\\/evil.example   -> //evil.example

Browsers convert "\\" to "/" and strip tab/CR/LF, so each resolves to a
protocol-relative URL — a cross-origin redirect from a trusted domain,
immediately after the victim has been authenticated. Root cause: a literal
`//` test cannot see a second leading slash that only appears AFTER browser
normalisation.

The fix does not try to enumerate every normalisation. It allows only the
conservative shape a real landing path has — no backslash, no control
characters or space, exactly one leading slash — and falls back to /account.

These cases are pinned because the payload class is large and easy to
half-fix: any future edit that reintroduces a literal-prefix test will fail
here.
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

# (label, raw ?next= value, must_be_rejected)
CASES = [
    ("backslash",            "/%5Cevil.example",         True),
    ("tab_then_double",      "/%09//evil.example",       True),
    ("dotslash_backslash",   "/./%5C/evil.example",      True),
    ("protocol_relative",    "//evil.example",           True),
    ("absolute_https",       "https://evil.example",     True),
    ("absolute_scheme_less", "https:evil.example",       True),
    ("crlf_injection",       "/%0d%0aX-Injected:%201",   True),
    ("backslash_double",     "/%5C%5Cevil.example",      True),
    ("newline_then_double",  "/%0a//evil.example",       True),
    ("space_then_double",    "/%20//evil.example",       True),
    ("legit_account",        "/account",                 False),
    ("legit_fragment",       "/%23drop",                 False),
    ("legit_plain",          "/pricing",                 False),
]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("openredirect_data")
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1",
           "ORPHO_DATA_DIR": str(data_dir), "ORPHO_COOKIE_SECURE": "0",
           "RATE_LIMIT_PER_DAY": "100000", "ORPHO_OFFLINE_CALENDARS": "1"}
    env.pop("RESEND_API_KEY", None)
    proc = subprocess.Popen([sys.executable, str(REPO_ROOT / "server" / "app.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
    yield base, str(data_dir)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _mint_token(data_dir: str, label: str) -> str:
    """Issue a real one-time link token against the server's own ledger."""
    code = (
        "import os,sys;"
        f"os.environ['ORPHO_DATA_DIR']={data_dir!r};"
        f"sys.path.insert(0,{str(REPO_ROOT / 'server')!r});"
        "import auth;"
        f"print(auth.issue_link_token({label!r})[0])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def _location(base: str, token: str, nxt: str) -> str:
    opener = urllib.request.build_opener(_NoRedirect)
    url = f"{base}/a/{token}?next={nxt}"
    try:
        r = opener.open(url, timeout=15)
        return r.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        return e.headers.get("Location", "")


@pytest.mark.parametrize("label,nxt,must_reject", CASES, ids=[c[0] for c in CASES])
def test_next_param_cannot_leave_the_site(server, label, nxt, must_reject):
    base, data_dir = server
    token = _mint_token(data_dir, f"{label}@example.test")
    loc = _location(base, token, nxt)

    assert loc, f"{label}: no Location header — the token was not redeemed"
    # Whatever survives must be same-site under BROWSER normalisation, not
    # merely under a literal string test.
    normalised = "".join(c for c in loc if ord(c) > 0x20 and ord(c) != 0x7F).replace("\\", "/")
    assert normalised.startswith("/"), f"{label}: Location left the site: {loc!r}"
    assert not normalised.startswith("//"), (
        f"{label}: OPEN REDIRECT — Location {loc!r} normalises to {normalised!r}, "
        "which a browser resolves cross-origin"
    )
    if must_reject:
        assert loc == "/account", f"{label}: hostile value survived as {loc!r}"
    else:
        assert loc != "/account" or nxt == "/account", (
            f"{label}: a legitimate path was over-blocked ({nxt!r} -> {loc!r})"
        )


def test_a_rejected_next_still_signs_the_user_in(server):
    """The fallback must not break login. A hostile ?next= sends the user to
    /account WITH a session cookie — refusing the redirect target is not a
    reason to refuse the authentication."""
    base, data_dir = server
    token = _mint_token(data_dir, "still-signs-in@example.test")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        r = opener.open(f"{base}/a/{token}?next=/%5Cevil.example", timeout=15)
        headers = r.headers
    except urllib.error.HTTPError as e:
        headers = e.headers
    assert headers.get("Location") == "/account"
    cookie = headers.get("Set-Cookie", "")
    assert "orpho_sid=" in cookie, f"no session cookie issued: {cookie!r}"
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie, cookie
