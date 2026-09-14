#!/usr/bin/env python3
"""all_endpoints_probe.py — comprehensive endpoint probe replacing ad-hoc curl.

Hits every public endpoint and a representative subset of authenticated
endpoints, reporting pass/fail for each. Designed for:

  - Pre-deploy smoke checks ("does prod still respond on every route?")
  - Post-deploy validation ("did the latest push break anything?")
  - CI integration (exit 0 on all-pass, exit 1 on any fail)

Stdlib only. No dependency on pytest or requests.

Usage:
    python3 scripts/all_endpoints_probe.py [--server URL] [--json] [--verbose] [--allow-writes]

Only GET/HEAD probes run by default. --allow-writes explicitly opts into
POST probes, including a real waitlist signup.

Returns exit 0 if every probe passes, exit 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# Honest, identifying User-Agent. urllib's default `Python-urllib/3.x` is denied by
# Cloudflare's bot rules (403 "error code: 1010"), so without this every probe
# fails before reaching the server. Never a browser-spoofing string.
USER_AGENT = "Orphograph-endpoint-probe/1.0 (+https://orphograph.com)"


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
    # Percent-encode the path: http.client refuses raw spaces/control characters
    # (InvalidURL), which aborted the whole run at the invalid-receipt probe.
    # Already-encoded sequences and query syntax are kept as written.
    url = server.rstrip("/") + urllib.parse.quote(probe.path, safe="/?=&%@:+,;")
    data = None
    headers = dict(probe.headers)
    headers.setdefault("User-Agent", USER_AGENT)
    if probe.body is not None:
        data = json.dumps(probe.body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=probe.method, headers=headers)
    t0 = datetime.now()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read()
            # Keep urllib's HTTPMessage: header names are case-insensitive in HTTP,
            # and behind Cloudflare they arrive lowercase. A plain dict made every
            # `"X-Frame-Options" in headers` check fail on a page that served it.
            resp_headers = resp.headers
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read() if hasattr(e, "read") else b""
        resp_headers = e.headers if getattr(e, "headers", None) is not None else {}
    except (urllib.error.URLError, OSError, ConnectionError, ValueError) as e:
        # ValueError covers a request that cannot be built (http.client.InvalidURL):
        # one bad probe is a failed result, never a crash that hides every other result.
        elapsed = int((datetime.now() - t0).total_seconds() * 1000)
        return Result(probe.name, False, 0, f"request failed: {type(e).__name__}", elapsed)
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


def check_sample_receipt(status: int, headers: dict, body: bytes) -> tuple[bool, str]:
    try:
        receipt = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return False, "not receipt JSON"
    if not isinstance(receipt, dict):
        return False, "not a receipt object"
    ok = (receipt.get("receipt_id") == SAMPLE["receipt_id"]
          and receipt.get("hash_hex") == SAMPLE["hash_hex"]
          and receipt.get("found") is True)
    return ok, "canonical receipt matches" if ok else "canonical receipt mismatch"


SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "web/sample/receipt.json").read_text())
SAMPLE_ID = SAMPLE["receipt_id"]


PROBES = [
    Probe("Canonical receipt", "GET", f"/api/receipt/{SAMPLE_ID}", check=check_sample_receipt),
    Probe("Canonical badge", "GET", f"/api/badge/{SAMPLE_ID}.svg",
          check=check_html_contains("<svg", SAMPLE_ID)),
    Probe("Canonical certificate", "GET", f"/certificate/{SAMPLE_ID}",
          check=check_html_contains("Provenance Certificate", SAMPLE_ID)),
    Probe("Canonical receipt page", "GET", f"/r/{SAMPLE_ID}",
          check=check_html_contains("Orphograph Receipt", SAMPLE_ID)),
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
    Probe("About page",
          "GET", "/about.html"),
    Probe("Sitemap XML",
          "GET", "/sitemap.xml",
          check=check_html_contains("<urlset")),
    Probe("Robots.txt",
          "GET", "/robots.txt"),
    Probe("Favicon",
          "GET", "/favicon.png"),
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
    ap.add_argument("--allow-writes", action="store_true",
                    help="also run write probes (including a real waitlist signup)")
    args = ap.parse_args()

    results: list[Result] = []
    skipped = []
    for probe in PROBES:
        if not args.allow_writes and probe.method.upper() not in {"GET", "HEAD"}:
            skipped.append(probe.name)
            continue
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
            "skipped_write_probes": skipped,
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
    if skipped:
        print(f"Skipped {len(skipped)} write probes; opt in with --allow-writes.")
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
