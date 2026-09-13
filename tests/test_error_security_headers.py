"""Error responses must carry the same security headers as every other response.

Found 2026-09-12 by a production sweep of every dispatched route: responses
produced by `send_error()` — unknown paths, GET on POST-only API routes, an
invalid magic-link token — reached clients with no Strict-Transport-Security,
Content-Security-Policy, X-Content-Type-Options, X-Frame-Options or
Referrer-Policy. `BaseHTTPRequestHandler.send_error` never calls
`_security_headers`, and app.py calls it 35 times. Same scanner-visible class
as the 2026-08-07 HEAD/501 incident, where a response without HSTS made an
external scanner report the whole site as missing it.
"""
from __future__ import annotations

import http.client
import os
import sys
import tempfile
import threading
import unittest
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

SECURITY_HEADERS = (
    "Strict-Transport-Security",
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
)

# Each of these is answered by send_error() on master at dfd9088.
ERROR_PATHS = {
    "/definitely-not-a-page-xyz": 404,   # static fallthrough, no such file
    "/api/anchor": 404,                  # POST-only route asked with GET
    "/a/not!a!token": 400,               # magic-link token fails TOKEN_RE
}


def _start_test_server(data_dir: Path):
    os.environ["ORPHO_DATA_DIR"] = str(data_dir)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = "0"
    os.environ["ORPHO_COOKIE_SECURE"] = "0"
    os.environ["RATE_LIMIT_PER_DAY"] = "100000"
    for m in _POLLUTED:
        sys.modules.pop(m, None)
    import app
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class TestErrorResponseSecurityHeaders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_modules = {m: sys.modules[m] for m in _POLLUTED if m in sys.modules}
        cls._old_env = {k: os.environ.get(k) for k in
                        ("ORPHO_DATA_DIR", "HOST", "PORT", "ORPHO_COOKIE_SECURE", "RATE_LIMIT_PER_DAY")}
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

    def _request(self, method: str, path: str):
        parts = urlsplit(self._base)
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
        try:
            conn.request(method, path)
            resp = conn.getresponse()
            resp.read()
            return resp.status, resp.getheaders()
        finally:
            conn.close()

    def test_error_responses_carry_security_headers_on_get_and_head(self):
        for path, expected in ERROR_PATHS.items():
            for method in ("GET", "HEAD"):
                with self.subTest(method=method, path=path):
                    status, headers = self._request(method, path)
                    self.assertEqual(status, expected)
                    names = {k.lower() for k, _ in headers}
                    missing = [h for h in SECURITY_HEADERS if h.lower() not in names]
                    self.assertEqual(missing, [], f"{method} {path} -> {status} without {missing}")

    def test_security_headers_are_sent_once_on_error_and_normal_responses(self):
        # The fix must not double-send on paths that already call _security_headers.
        for path in ("/api/health", "/definitely-not-a-page-xyz"):
            for method in ("GET", "HEAD"):
                with self.subTest(method=method, path=path):
                    _status, headers = self._request(method, path)
                    for h in SECURITY_HEADERS:
                        count = sum(1 for k, _ in headers if k.lower() == h.lower())
                        self.assertLessEqual(count, 1, f"{method} {path}: {h} sent {count} times")


if __name__ == "__main__":
    unittest.main()
