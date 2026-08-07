#!/usr/bin/env python3
"""test_head_method.py — HEAD must be GET without a body (RFC 9110 §9.3.2).

Regression pin for the defect found 2026-08-07 via Cloudflare Security
Insights. `do_HEAD` answered 501 for every path except /api/event, and the
501 error page carried none of `_security_headers()`. Consequences, none of
which are visible from a browser:

  * Cloudflare's scanner probes with HEAD, saw no Strict-Transport-Security,
    and raised "Domains without HSTS" for orphograph.com AND www — while GET
    was serving `max-age=31536000; includeSubDomains` correctly all along.
  * The same scanner raised "Security.txt not configured" for a site that
    serves a valid security.txt with 200 on GET.
  * Link checkers, uptime monitors and CDN prefetch all HEAD first.

So: one method bug manufactured three external security findings. The
assertions below are written against what a scanner actually receives —
status AND headers AND the absence of a body — because a test that only
checked `!= 501` would pass while the scanner still failed.

Harness conventions copied from tests/test_agent_discovery.py.
"""
from __future__ import annotations

import http.client
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

_POLLUTED = (
    "app", "engine", "auth", "rate_limit", "credits", "stats",
    "health", "subscriptions", "teams", "stripe_webhook",
    "mailer", "api_keys", "affiliate", "newsletter", "waitlist",
    "blog", "unsubscribe", "gdpr", "public_config",
    "receipt_export", "btc_price", "btc_payments", "stripe_api",
    "og_svg", "qrcode_svg", "badge_svg", "analytics",
    "support_tools", "onboarding", "referrals", "file_lock",
    "merkle",
)

# Headers a security scanner reads. Every one of these must survive a HEAD.
SCANNER_HEADERS = (
    "Strict-Transport-Security",
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
)

# Paths a scanner or link checker probes. Mix of static, generated and API.
PROBED_PATHS = ("/", "/.well-known/security.txt", "/llms.txt", "/api/health")


def _start_test_server(data_dir: Path):
    os.environ["ORPHO_DATA_DIR"] = str(data_dir)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = "0"
    os.environ["ORPHO_COOKIE_SECURE"] = "0"
    os.environ["RATE_LIMIT_PER_DAY"] = "100000"
    os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    for m in _POLLUTED:
        sys.modules.pop(m, None)
    import app
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


class TestHeadMethod(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_modules = {m: sys.modules[m] for m in _POLLUTED if m in sys.modules}
        cls._old_env = {
            k: os.environ.get(k)
            for k in ("ORPHO_DATA_DIR", "HOST", "PORT", "ORPHO_COOKIE_SECURE",
                      "RATE_LIMIT_PER_DAY")
        }
        cls._server, cls._base = _start_test_server(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._server.server_close()
        cls._tmp.cleanup()
        for m in _POLLUTED:
            sys.modules.pop(m, None)
        for m, mod in cls._old_modules.items():
            sys.modules[m] = mod
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # -- helpers ----------------------------------------------------------

    def _head(self, path: str):
        """Raw HEAD via http.client — urllib hides the empty-body question."""
        parts = urlsplit(self._base)
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
        try:
            conn.request("HEAD", path)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def _get(self, path: str):
        req = urllib.request.Request(f"{self._base}{path}")
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:      # 404s are legitimate answers
            return exc.code, dict(exc.headers), exc.read()

    # -- the defect itself ------------------------------------------------

    def test_head_is_not_501(self):
        """The literal regression: HEAD used to be 501 everywhere."""
        for path in PROBED_PATHS:
            with self.subTest(path=path):
                status, _, _ = self._head(path)
                self.assertNotEqual(
                    status, 501,
                    f"HEAD {path} returned 501 Not Implemented — this is the "
                    "bug that made Cloudflare report missing HSTS/security.txt")

    def test_head_status_matches_get(self):
        for path in PROBED_PATHS:
            with self.subTest(path=path):
                head_status, _, _ = self._head(path)
                get_status, _, _ = self._get(path)
                self.assertEqual(head_status, get_status,
                                 f"HEAD {path} and GET {path} disagree on status")

    def test_head_carries_the_headers_a_scanner_reads(self):
        """The assertion that actually closes the Cloudflare findings.

        A status-only check would pass while the scanner still failed, so
        assert on the headers the scanner is looking for.
        """
        for path in PROBED_PATHS:
            for header in SCANNER_HEADERS:
                with self.subTest(path=path, header=header):
                    _, head_headers, _ = self._head(path)
                    _, get_headers, _ = self._get(path)
                    self.assertIn(header, head_headers,
                                  f"HEAD {path} dropped {header}")
                    self.assertEqual(
                        head_headers[header], get_headers.get(header),
                        f"HEAD {path} sent a different {header} than GET")

    def test_head_hsts_value_is_the_one_cloudflare_wants(self):
        _, headers, _ = self._head("/")
        self.assertEqual(headers.get("Strict-Transport-Security"),
                         "max-age=31536000; includeSubDomains")

    def test_head_sends_no_body(self):
        """RFC 9110 §9.3.2: the response MUST NOT include content."""
        for path in PROBED_PATHS:
            with self.subTest(path=path):
                _, _, body = self._head(path)
                self.assertEqual(body, b"",
                                 f"HEAD {path} returned a body of {len(body)} bytes")

    def test_head_content_length_matches_get(self):
        """Content-Length describes what GET *would* send, per RFC."""
        for path in PROBED_PATHS:
            with self.subTest(path=path):
                _, head_headers, _ = self._head(path)
                _, get_headers, get_body = self._get(path)
                if "Content-Length" not in get_headers:
                    self.skipTest(f"GET {path} is chunked/streamed")
                self.assertEqual(head_headers.get("Content-Length"),
                                 get_headers.get("Content-Length"),
                                 f"HEAD {path} misreports Content-Length")
                self.assertEqual(int(get_headers["Content-Length"]), len(get_body))

    # -- security.txt, the surface Cloudflare specifically flagged ---------

    def test_security_txt_is_reachable_by_a_scanner(self):
        status, headers, _ = self._head("/.well-known/security.txt")
        self.assertEqual(status, 200)
        self.assertIn("Strict-Transport-Security", headers)

    # -- /api/event keeps its deliberate 405 ------------------------------

    def test_event_endpoint_still_405s_on_head(self):
        """This one path is intentionally method-restricted; don't regress it."""
        status, headers, body = self._head("/api/event")
        self.assertEqual(status, 405)
        self.assertEqual(headers.get("Allow"), "POST")
        self.assertEqual(body, b"")

    # -- OPTIONS, same defect class ---------------------------------------

    def test_options_advertises_methods_instead_of_501(self):
        parts = urlsplit(self._base)
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
        try:
            conn.request("OPTIONS", "/")
            resp = conn.getresponse()
            resp.read()
            self.assertNotEqual(resp.status, 501)
            allow = resp.getheader("Allow") or ""
            self.assertIn("GET", allow)
            self.assertIn("HEAD", allow)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
