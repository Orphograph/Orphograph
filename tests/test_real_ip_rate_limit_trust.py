"""test_real_ip_rate_limit_trust.py

What a client-supplied IP header can and cannot do to rate limiting
(audit 2026-08-25, backlog item B: "Fly-Client-IP forgeability").

MEASURED, with a real server and the real limiter:

  ORPHO_TRUST_PROXY_HEADERS=0  (the default)
      burn the limit, then rotate Fly-Client-IP or X-Forwarded-For
      -> still 429. Client headers cannot bypass. This is the invariant
         this file exists to hold.

  ORPHO_TRUST_PROXY_HEADERS=1  (what PRODUCTION sets, per app.py:196)
      burn the limit, then rotate either header
      -> 200, 200, 200. Each forged value mints a FRESH bucket.

The second result is not automatically a vulnerability, and this module does
not claim it is. Production runs client -> Cloudflare -> Fly, and app.py:160
asserts that Fly sets `Fly-Client-IP` at its edge "unlike X-Forwarded-For, the
client cannot forge it". IF that holds, a forged header never reaches the
origin and the bypass is unreachable.

THAT ASSERTION IS UNVERIFIED ANYWHERE IN THIS REPOSITORY. It is a comment, not
a test, and confirming it needs either `fly ssh` or a write against production
— both founder-gated. So this file pins what CAN be checked here, and names
the gap instead of quietly assuming the comment is right.

The trust_proxy=0 case is the load-bearing one: it proves the limiter buckets
on something the client does not control whenever the platform guarantee is
absent, which is the property that must never regress.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request

import pytest

import _srv

LIMIT = 3


def _anchor(base: str, tag: str, headers: dict | None = None) -> int:
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(
        base + "/api/anchor",
        data=json.dumps({"hash_hex": hashlib.sha256(tag.encode()).hexdigest()}).encode(),
        headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def _burn(base: str, prefix: str) -> list[int]:
    return [_anchor(base, f"{prefix}-burn-{i}") for i in range(LIMIT + 2)]


# FUNCTION scope, deliberately. The rate limiter is per-server state, so a
# module-scoped server lets the first test spend the budget the next one needs
# to assert on — the second and third tests then measure an exhausted limiter
# rather than the property they name. Observed 2026-08-25.
@pytest.fixture
def untrusted(tmp_path_factory):
    yield from _srv.server_processes(tmp_path_factory.mktemp("rl_untrusted"),
        RATE_LIMIT_PER_DAY=str(LIMIT), ORPHO_TRUST_PROXY_HEADERS="0", stub_calendars=True)


@pytest.fixture
def trusted(tmp_path_factory):
    yield from _srv.server_processes(tmp_path_factory.mktemp("rl_trusted"),
        RATE_LIMIT_PER_DAY=str(LIMIT), ORPHO_TRUST_PROXY_HEADERS="1", stub_calendars=True)


@pytest.mark.parametrize("header", ["Fly-Client-IP", "X-Forwarded-For"])
def test_client_headers_cannot_bypass_the_limit_without_a_trusted_proxy(untrusted, header):
    """THE INVARIANT. With no platform guarantee, a client-supplied header must
    never mint a fresh rate-limit bucket — the limiter buckets on the socket
    peer, which the client does not control."""
    burned = _burn(untrusted, f"untrusted-{header}")
    assert 429 in burned, f"the limit never engaged; this test proves nothing: {burned}"
    forged = [_anchor(untrusted, f"untrusted-{header}-f{i}", {header: f"203.0.113.{i}"})
              for i in range(LIMIT)]
    assert all(c == 429 for c in forged), (
        f"{header} bypassed the rate limit with ORPHO_TRUST_PROXY_HEADERS=0: {forged}"
    )


@pytest.mark.parametrize("header", ["Fly-Client-IP", "X-Forwarded-For"])
def test_with_trust_enabled_the_header_IS_the_identity(trusted, header):
    """Documents the measured production-flag behaviour rather than asserting a
    verdict on it. With trust enabled the header IS the rate-limit identity, so
    a forged value mints a fresh bucket. Safe ONLY while the edge overwrites
    the header — app.py:160 asserts Fly does, and nothing here can confirm it.

    If this ever starts FAILING, the platform guarantee has been made real in
    code (peer validation, an allowlist) and the docstring above should be
    rewritten rather than the test deleted."""
    burned = _burn(trusted, f"trusted-{header}")
    assert 429 in burned, f"the limit never engaged: {burned}"
    forged = [_anchor(trusted, f"trusted-{header}-f{i}", {header: f"203.0.113.{i}"})
              for i in range(LIMIT)]
    assert 200 in forged, (
        "expected the trusted-proxy path to treat the header as identity; if "
        "this now refuses, peer validation was added — update the docstring"
    )


def test_the_limiter_actually_engages(untrusted):
    """NEGATIVE CONTROL. If the limit never fired, every assertion above would
    be comparing 200s to 200s and proving nothing."""
    codes = _burn(untrusted, "control")
    assert codes[:LIMIT] == [200] * LIMIT, codes
    assert codes[LIMIT] == 429, codes
