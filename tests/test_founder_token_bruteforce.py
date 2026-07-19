"""test_founder_token_bruteforce.py — failures-only lockout on /api/founder/*.

Latent-security pass 2026-07-18. The founder token is a static shared secret
(ORPHO_FOUNDER_TOKEN) with no expiry and no per-request session; before this
change an attacker could guess X-Orpho-Founder values without bound. The
shared gate (app.Handler._founder_authorized) now:

  - never consumes quota on a CORRECT token (founder sees zero change);
  - consumes one _founder_fail_limiter token per FAILED guess (keyed by
    truncated client IP);
  - once the bucket is empty, refuses without even running the compare —
    still answering 404, indistinguishable from a wrong token.

Test classes mirror tests/test_founder_funnel_endpoint.py: evict app+sibling
modules so the next `import app` re-reads os.environ.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))


_MODULES_TO_EVICT = (
    "app", "engine", "auth", "rate_limit", "credits", "stats",
    "health", "subscriptions", "teams", "stripe_webhook",
    "mailer", "api_keys", "affiliate", "newsletter", "waitlist",
    "blog", "unsubscribe", "gdpr", "public_config",
    "receipt_export", "btc_price", "btc_payments", "stripe_api",
    "og_svg", "qrcode_svg", "badge_svg", "analytics",
    "support_tools", "onboarding", "referrals", "file_lock",
)

TOKEN = "correct-horse-battery-staple"


def _evict() -> dict:
    saved = {m: sys.modules[m] for m in list(sys.modules.keys()) if m in _MODULES_TO_EVICT}
    for m in list(sys.modules.keys()):
        if m in _MODULES_TO_EVICT:
            sys.modules.pop(m, None)
    return saved


def _restore(saved: dict) -> None:
    for m in list(sys.modules.keys()):
        if m in _MODULES_TO_EVICT:
            sys.modules.pop(m, None)
    for m, mod in saved.items():
        sys.modules[m] = mod


def _start(data_dir: Path):
    os.environ["ORPHO_DATA_DIR"] = str(data_dir)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = "0"
    os.environ["ORPHO_COOKIE_SECURE"] = "0"
    os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    import app
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}", app


def _stop(server) -> None:
    server.shutdown()
    server.server_close()


def _get(base: str, path: str, headers: dict | None = None):
    req = urllib.request.Request(base + path, method="GET")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, (e.read() if hasattr(e, "read") else b"")


class TestFounderBruteforceLockout(unittest.TestCase):
    ENDPOINT = "/api/founder/admin/toggles"  # cheapest gated endpoint

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_env = {k: os.environ.get(k) for k in (
            "ORPHO_FOUNDER_TOKEN", "ORPHO_DATA_DIR",
            "HOST", "PORT", "ORPHO_COOKIE_SECURE",
        )}
        os.environ["ORPHO_FOUNDER_TOKEN"] = TOKEN
        cls._saved = _evict()
        cls._server, cls._base, cls._app = _start(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        _stop(cls._server)
        cls._tmp.cleanup()
        _restore(cls._saved)
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        # Fresh bucket per test — the limiter is process-global in-memory.
        self._app._founder_fail_limiter._buckets.clear()

    def test_correct_token_ok_wrong_token_404(self):
        status, _ = _get(self._base, self.ENDPOINT, {"X-Orpho-Founder": TOKEN})
        self.assertEqual(status, 200)
        status, _ = _get(self._base, self.ENDPOINT, {"X-Orpho-Founder": "nope"})
        self.assertEqual(status, 404)
        status, _ = _get(self._base, self.ENDPOINT)  # header absent
        self.assertEqual(status, 404)

    def test_success_never_consumes_quota(self):
        # Far more correct-token requests than the failure capacity —
        # all must succeed, proving success never burns the bucket.
        n = int(self._app.FOUNDER_FAIL_CAPACITY) + 5
        for i in range(n):
            status, _ = _get(self._base, self.ENDPOINT, {"X-Orpho-Founder": TOKEN})
            self.assertEqual(status, 200, f"request {i} throttled")

    def test_failures_below_capacity_do_not_lock_out_founder(self):
        for _ in range(int(self._app.FOUNDER_FAIL_CAPACITY) - 1):
            status, _ = _get(self._base, self.ENDPOINT, {"X-Orpho-Founder": "guess"})
            self.assertEqual(status, 404)
        status, _ = _get(self._base, self.ENDPOINT, {"X-Orpho-Founder": TOKEN})
        self.assertEqual(status, 200)

    def test_exhausted_bucket_locks_out_with_identical_404(self):
        for _ in range(int(self._app.FOUNDER_FAIL_CAPACITY)):
            status, _ = _get(self._base, self.ENDPOINT, {"X-Orpho-Founder": "guess"})
            self.assertEqual(status, 404)
        # Bucket now empty: even the CORRECT token answers 404 (the gate
        # refuses to compare), and further guesses stay 404 — no status
        # change ever reveals that a lockout exists.
        status, _ = _get(self._base, self.ENDPOINT, {"X-Orpho-Founder": TOKEN})
        self.assertEqual(status, 404)
        status, _ = _get(self._base, self.ENDPOINT, {"X-Orpho-Founder": "guess2"})
        self.assertEqual(status, 404)

    def test_lockout_applies_across_all_founder_endpoints(self):
        for _ in range(int(self._app.FOUNDER_FAIL_CAPACITY)):
            _get(self._base, self.ENDPOINT, {"X-Orpho-Founder": "guess"})
        for path in ("/api/founder/metrics", "/api/founder/funnel",
                     "/api/founder/morning-summary"):
            status, _ = _get(self._base, path, {"X-Orpho-Founder": TOKEN})
            self.assertEqual(status, 404, f"{path} should share the lockout")


class TestFounderGlobalLockout(unittest.TestCase):
    """Funnel-hygiene pass: second, all-IPs failure counter + lockout logging.

    The per-IP bucket alone lets an attacker rotating source addresses mint a
    fresh guess budget per /24. The global bucket bounds total guesses across
    the fleet. Trust-proxy mode is enabled here so tests can rotate the
    platform real-IP header (Fly-Client-IP) to simulate distributed sources.
    """

    ENDPOINT = "/api/founder/admin/toggles"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_env = {k: os.environ.get(k) for k in (
            "ORPHO_FOUNDER_TOKEN", "ORPHO_DATA_DIR", "HOST", "PORT",
            "ORPHO_COOKIE_SECURE", "ORPHO_TRUST_PROXY_HEADERS",
        )}
        os.environ["ORPHO_FOUNDER_TOKEN"] = TOKEN
        os.environ["ORPHO_TRUST_PROXY_HEADERS"] = "1"
        cls._saved = _evict()
        cls._server, cls._base, cls._app = _start(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        _stop(cls._server)
        cls._tmp.cleanup()
        _restore(cls._saved)
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        # Small, fresh buckets per test so lockout triggers fast. The handler
        # resolves the module globals at call time, so swapping is safe.
        self._app._founder_fail_limiter = self._app.TokenBucket(3, 3 / 900.0)
        self._app._founder_fail_global_limiter = self._app.TokenBucket(5, 5 / 900.0)

    def _fail_from(self, ip: str):
        return _get(self._base, self.ENDPOINT,
                    {"X-Orpho-Founder": "guess", "Fly-Client-IP": ip})

    def test_rotating_ips_hits_global_lockout_and_logs(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            # 5 failures from 5 different /24s — each far below the per-IP cap.
            for i in range(5):
                status, _ = self._fail_from(f"10.{i}.{i}.1")
                self.assertEqual(status, 404)
            # Global bucket now empty: even the CORRECT token from a brand-new
            # IP answers 404 — the rotation strategy is dead.
            status, _ = _get(self._base, self.ENDPOINT,
                             {"X-Orpho-Founder": TOKEN, "Fly-Client-IP": "172.16.9.9"})
            self.assertEqual(status, 404)
        self.assertIn("GLOBAL lockout engaged", err.getvalue())

    def test_global_lockout_expires_with_refill(self):
        for i in range(5):
            self._fail_from(f"10.{i}.{i}.1")
        status, _ = _get(self._base, self.ENDPOINT,
                         {"X-Orpho-Founder": TOKEN, "Fly-Client-IP": "172.16.9.9"})
        self.assertEqual(status, 404, "lockout should be engaged")
        # Simulate the lockout window elapsing: rewind the bucket's clock a
        # full refill period. peek() then sees a refilled bucket.
        import time as _time
        g = self._app._founder_fail_global_limiter
        key = self._app._FOUNDER_GLOBAL_FAIL_KEY
        tokens, _last = g._buckets[key]
        g._buckets[key] = (tokens, _time.time() - 900)
        status, _ = _get(self._base, self.ENDPOINT,
                         {"X-Orpho-Founder": TOKEN, "Fly-Client-IP": "172.16.9.9"})
        self.assertEqual(status, 200, "founder access must self-heal after the window")

    def test_per_ip_lockout_logs_and_expires(self):
        import contextlib
        import io
        import time as _time
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for _ in range(3):  # per-IP capacity swapped to 3 in setUp
                status, _ = self._fail_from("10.99.99.1")
                self.assertEqual(status, 404)
        self.assertIn("per-IP lockout engaged", err.getvalue())
        # Locked: correct token from the SAME /24 refuses...
        status, _ = _get(self._base, self.ENDPOINT,
                         {"X-Orpho-Founder": TOKEN, "Fly-Client-IP": "10.99.99.2"})
        self.assertEqual(status, 404)
        # ...until the window elapses (rewind the per-IP bucket's clock).
        lim = self._app._founder_fail_limiter
        key = "founder-fail:" + self._app.truncate_ip("10.99.99.1")
        tokens, _last = lim._buckets[key]
        lim._buckets[key] = (tokens, _time.time() - 900)
        status, _ = _get(self._base, self.ENDPOINT,
                         {"X-Orpho-Founder": TOKEN, "Fly-Client-IP": "10.99.99.2"})
        self.assertEqual(status, 200)


class TestFounderTokenUnset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_env = {k: os.environ.get(k) for k in (
            "ORPHO_FOUNDER_TOKEN", "ORPHO_DATA_DIR",
            "HOST", "PORT", "ORPHO_COOKIE_SECURE",
        )}
        os.environ.pop("ORPHO_FOUNDER_TOKEN", None)
        cls._saved = _evict()
        cls._server, cls._base, cls._app = _start(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        _stop(cls._server)
        cls._tmp.cleanup()
        _restore(cls._saved)
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_endpoints_are_404_when_token_unset(self):
        for path in ("/api/founder/admin/toggles", "/api/founder/metrics"):
            status, _ = _get(self._base, path, {"X-Orpho-Founder": "anything"})
            self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
