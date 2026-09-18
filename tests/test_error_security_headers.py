"""Error responses must carry the same security headers as every other response.

Found 2026-09-12 by a production sweep of every dispatched route: responses
produced by `send_error()` — unknown paths, GET on POST-only API routes, an
invalid magic-link token — reached clients with no Strict-Transport-Security,
Content-Security-Policy, X-Content-Type-Options, X-Frame-Options or
Referrer-Policy. `BaseHTTPRequestHandler.send_error` never calls
`_security_headers`, and app.py calls it 35 times. Same scanner-visible class
as the 2026-08-07 HEAD/501 incident, where a response without HSTS made an
external scanner report the whole site as missing it.

Driven against a real server process through the shared `_srv` helper, so the
headers asserted are the bytes a client receives.
"""
from __future__ import annotations

import pytest

import _srv

SECURITY_HEADERS = (
    "Strict-Transport-Security",
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
)

# Each of these is answered by send_error() on master at 2ce02e1.
ERROR_PATHS = (
    ("/definitely-not-a-page-xyz", 404),   # static fallthrough, no such file
    ("/api/anchor", 404),                  # POST-only route asked with GET
    ("/a/not!a!token", 400),               # magic-link token fails TOKEN_RE
)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    yield from _srv.server_processes(tmp_path_factory.mktemp("error-headers"))


def _request(base: str, method: str, path: str):
    status, _body, headers = _srv.request(base, path, method)
    return status, headers.items()   # items(), not a dict: a repeat must stay visible


@pytest.mark.parametrize("method", ("GET", "HEAD"))
@pytest.mark.parametrize("path,expected", ERROR_PATHS)
def test_error_responses_carry_security_headers(base, method, path, expected):
    status, headers = _request(base, method, path)
    assert status == expected
    names = {k.lower() for k, _ in headers}
    missing = [h for h in SECURITY_HEADERS if h.lower() not in names]
    assert missing == [], f"{method} {path} -> {status} without {missing}"


@pytest.mark.parametrize("method", ("GET", "HEAD"))
@pytest.mark.parametrize("path", ("/api/health", "/definitely-not-a-page-xyz"))
def test_security_headers_are_sent_at_most_once(base, method, path):
    # The fix must not double-send on paths that already call _security_headers.
    _status, headers = _request(base, method, path)
    for h in SECURITY_HEADERS:
        count = sum(1 for k, _ in headers if k.lower() == h.lower())
        assert count <= 1, f"{method} {path}: {h} sent {count} times"
