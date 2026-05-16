#!/usr/bin/env python3
"""all_endpoints_probe.py — comprehensive endpoint probe replacing ad-hoc curl.

Hits every public endpoint and a representative subset of authenticated
endpoints, reporting pass/fail for each. Designed for:

  - Pre-deploy smoke checks ("does prod still respond on every route?")
  - Post-deploy validation ("did the latest push break anything?")
  - CI integration (exit 0 on all-pass, exit 1 on any fail)

Stdlib only. No dependency on pytest or requests.

Usage:
    python3 scripts/all_endpoints_probe.py [--server URL] [--json] [--verbose]

Returns exit 0 if every probe passes, exit 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional


@dataclass
class Probe:
    name: str
    method: str
    path: str
    body: Optional[dict] = None
    headers: dict = field(default_factory=dict)
    # check(status, headers, body_bytes) -> (ok: bool, detail: str)
    check: Optional[Callable] = None
    # Endpoints in this list are expected to 404 / 401 / 402 when unauthenticated;
    # the probe passes if the status is in expected_status (or in the 2xx range
    # otherwise).
    expected_status: tuple[int, ...] = (200,)


@dataclass
class Result:
    name: str
    ok: bool
    status: int
    detail: str
    elapsed_ms: int


def hit(server: str, probe: Probe, timeout: float = 10.0) -> Result:
    url = server.rstrip("/") + probe.path
    data = None
    headers = dict(probe.headers)
    if probe.body is not None:
        data = json.dumps(probe.body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=probe.method, headers=headers)
    t0 = datetime.now()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read()
            resp_headers = dict(resp.headers)
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read() if hasattr(e, "read") else b""
        resp_headers = dict(e.headers) if hasattr(e, "headers") else {}
    except (urllib.error.URLError, OSError, ConnectionError) as e:
        elapsed = int((datetime.now() - t0).total_seconds() * 1000)
        return Result(probe.name, False, 0, f"connection error: {type(e).__name__}", elapsed)
    elapsed = int((datetime.now() - t0).total_seconds() * 1000)

    ok = status in probe.expected_status
    detail = f"HTTP {status}"
    if probe.check:
        try:
            check_ok, check_detail = probe.check(status, resp_headers, body)
            ok = ok and check_ok
            detail = f"{detail} · {check_detail}"
        except Exception as e:
            ok = False
            detail = f"{detail} · check exception: {e}"
    return Result(probe.name, ok, status, detail, elapsed)


def check_json_has(*keys: str) -> Callable:
    def _check(status: int, headers: dict, body: bytes) -> tuple[bool, str]:
        try:
            obj = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False, "not JSON"
        missing = [k for k in keys if k not in obj]
        if missing:
            return False, f"missing keys: {missing}"
        return True, f"keys present: {list(keys)}"
    return _check


def check_html_contains(*needles: str) -> Callable:
    def _check(status: int, headers: dict, body: bytes) -> tuple[bool, str]:
        text = body.decode("utf-8", errors="replace")
        missing = [n for n in needles if n not in text]
        if missing:
            return False, f"missing: {missing}"
        return True, "HTML markers present"
    return _check


def check_security_headers(status: int, headers: dict, body: bytes) -> tuple[bool, str]:
    required = ("Content-Security-Policy", "X-Content-Type-Options",
                "X-Frame-Options", "Strict-Transport-Security")
    missing = [h for h in required if h not in headers]
    if missing:
        return False, f"missing headers: {missing}"
    return True, "security headers OK"


def check_no_secrets(status: int, headers: dict, body: bytes) -> tuple[bool, str]:
    """Verify no Stripe/Resend secret-key prefixes leak in any response."""
    text = body.decode("utf-8", errors="replace")
    secrets = ("sk_live_", "sk_test_", "whsec_", "re_live_")
    found = [s for s in secrets if s in text]
    if found:
        return False, f"SECRET LEAK: {found}"
    return True, "no secret prefixes leaked"


PROBES = [
    Probe("Landing page",
          "GET", "/",
          check=check_html_contains("Orphograph", "Bitcoin")),
    Probe("Privacy Policy",
          "GET", "/privacy.html",
          check=check_html_contains("Privacy")),
    Probe("Terms of Service",
          "GET", "/terms.html",
          check=check_html_contains("Terms")),
    Probe("API docs",
          "GET", "/docs/api.html",
          check=check_html_contains("/api/anchor", "/api/receipt")),
    Probe("Verify page",
          "GET", "/verify/",
          check=check_html_contains("verify")),
    Probe("Stats page",
          "GET", "/stats.html"),
    Probe("Status page",
          "GET", "/status.html"),
    Probe("Press kit",
          "GET", "/press.html"),
    Probe("Compare page",
          "GET", "/compare.html"),
    Probe("About page",
          "GET", "/about.html"),
    Probe("Sitemap XML",
          "GET", "/sitemap.xml",
          check=check_html_contains("<urlset")),
    Probe("Robots.txt",
          "GET", "/robots.txt"),
    Probe("Favicon",
          "GET", "/favicon.svg"),
    # JSON APIs
    Probe("Health",
          "GET", "/api/health",
          check=check_json_has("version", "uptime_sec")),
    Probe("Stats",
          "GET", "/api/stats",
          check=check_json_has("anchors", "calendars")),
    Probe("Public config",
          "GET", "/api/config",
          check=check_json_has("stripe", "pricing", "toggles", "features")),
    # Auth-gated endpoints — expect 401 (not 5xx) when unauthenticated
    Probe("Account (unauth)",
          "GET", "/api/me",
          expected_status=(401,)),
    Probe("Anchors list (unauth)",
          "GET", "/api/me/anchors",
          expected_status=(401,)),
    Probe("Vault ZIP (unauth)",
          "GET", "/api/me/anchors.zip",
          expected_status=(401,)),
    Probe("Cancel sub (unauth)",
          "POST", "/api/me/cancel-subscription",
          body={},
          expected_status=(401,)),
    # Founder-only endpoints — should 404 (not 5xx) without token
    Probe("Founder metrics (unauth)",
          "GET", "/api/founder/metrics",
          expected_status=(404,)),
    Probe("Founder customer (unauth)",
          "GET", "/api/founder/customer?email=test@example.com",
          expected_status=(404,)),
    Probe("Admin toggles (unauth)",
          "GET", "/api/founder/admin/toggles",
          expected_status=(404,)),
    # Stripe webhook unsigned → 503 (fail closed) or 200 (probe-accept mode)
    Probe("Stripe webhook (unsigned)",
          "POST", "/api/stripe/webhook",
          body={"id": "evt_probe", "type": "ping"},
          expected_status=(400, 503, 200)),
    # Receipt verification with invalid id — 400
    Probe("Receipt (invalid id)",
          "GET", "/api/receipt/this is not a valid id",
          expected_status=(400, 404)),
    # Anchor without body — 400
    Probe("Anchor (no body)",
          "POST", "/api/anchor",
          body={},
          expected_status=(400, 429)),
    # Sanity: no secrets in landing
    Probe("Landing — no secret leak",
          "GET", "/",
          check=check_no_secrets),
    Probe("API docs — no secret leak",
          "GET", "/docs/api.html",
          check=check_no_secrets),
    # Security headers on landing
    Probe("Security headers — landing",
          "GET", "/",
          check=check_security_headers),
    # Newsletter signup (rate-limited but should accept)
    Probe("Waitlist signup",
          "POST", "/api/waitlist",
          body={"email": "probe@example.com", "interest": "personal"}),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", default="http://127.0.0.1:8989",
                    help="server URL (default: http://127.0.0.1:8989)")
    ap.add_argument("--json", action="store_true",
                    help="emit JSON report instead of text")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="print details for every probe, not just failures")
    args = ap.parse_args()

    results: list[Result] = []
    for probe in PROBES:
        r = hit(args.server, probe)
        results.append(r)

    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed

    if args.json:
        out = {
            "server": args.server,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "results": [
                {
                    "name": r.name,
                    "ok": r.ok,
                    "status": r.status,
                    "detail": r.detail,
                    "elapsed_ms": r.elapsed_ms,
                }
                for r in results
            ],
        }
        print(json.dumps(out, indent=2))
        return 0 if failed == 0 else 1

    print(f"All-endpoints probe — {args.server}")
    print(f"Probes: {len(results)}  Passed: {passed}  Failed: {failed}")
    print()
    for r in results:
        mark = "✓" if r.ok else "✗"
        line = f"  {mark} {r.name:<35} {r.detail}  [{r.elapsed_ms}ms]"
        if not r.ok or args.verbose:
            print(line)
    print()
    if failed:
        print(f"FAIL: {failed} probe(s) failed.")
        return 1
    print("PASS: all probes returned expected status / content.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
