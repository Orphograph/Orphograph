"""test_pay_btc_same_origin.py — gate the same-origin /pay/btc.html surfaces.

Latent-security pass 2026-07-18: the static BTC pay page used to reach two
third-party hosts directly from the browser (a price oracle and an external
QR-image service). The strict CSP (connect-src 'self'; img-src 'self' data:)
blocked both, leaving the page degraded — and the external QR host, had the
CSP ever been relaxed, could have served a QR encoding a different payment
address. These tests pin the replacement:

  1. GET /api/btc/price     — same-origin proxy of the server-side
                              multi-oracle cache (btc_price).
  2. GET /api/btc/qr.svg    — server-rendered BIP-21 QR; the address is the
                              server-side PAY_BTC_ADDRESS constant, only a
                              bounded `sats` amount comes from the request.
  3. web/pay-btc.js         — regression guard: no third-party hosts remain.

Test classes mirror tests/test_founder_funnel_endpoint.py: evict app+sibling
modules so the next `import app` re-reads os.environ.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
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


def _get(base: str, path: str):
    req = urllib.request.Request(base + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, (e.read() if hasattr(e, "read") else b""), (
            dict(e.headers) if hasattr(e, "headers") else {}
        )


class TestBtcPriceEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_env = {k: os.environ.get(k) for k in (
            "ORPHO_DATA_DIR", "HOST", "PORT", "ORPHO_COOKIE_SECURE",
        )}
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

    def _seed_price(self, usd: float) -> None:
        import btc_price
        with btc_price._lock:
            btc_price._cache["price"] = usd
            btc_price._cache["source"] = "mempool"
            btc_price._cache["ts"] = time.time()

    def _clear_price(self) -> None:
        import btc_price
        btc_price._reset_cache_for_tests()

    def test_price_served_from_cache(self):
        self._seed_price(50_000.0)
        status, body, headers = _get(self._base, "/api/btc/price")
        self.assertEqual(status, 200)
        import json
        data = json.loads(body)
        self.assertEqual(data, {"usd": 50_000.0})

    def test_price_unavailable_returns_503(self):
        # Empty cache + all oracles failing → 0.0 → 503, matching the
        # existing order-creation behavior ("try again in a minute").
        self._clear_price()
        import btc_price

        # Patch the oracle fetch helper (NOT urllib globally — the test's
        # own HTTP client below uses the same urllib module).
        orig = btc_price._http_get_json
        btc_price._http_get_json = lambda url: None
        try:
            status, body, _ = _get(self._base, "/api/btc/price")
        finally:
            btc_price._http_get_json = orig
            self._clear_price()
        self.assertEqual(status, 503)

class TestPayBtcJsHasNoThirdPartyHosts(unittest.TestCase):
    """Regression guard: the pay page must stay same-origin-only.

    The strict CSP already blocks third-party fetches/images, so any
    reintroduced external host would silently break the page again —
    or worse, invite a CSP exception for a host that renders payment QRs.
    """

    def test_pay_btc_js_is_same_origin_only(self):
        src = (ROOT / "web" / "pay-btc.js").read_text(encoding="utf-8")
        for host in ("mempool.space", "qrserver", "https://"):
            self.assertNotIn(host, src, f"third-party reference '{host}' in pay-btc.js")

    def test_pay_btc_html_is_same_origin_only(self):
        html = (ROOT / "web" / "pay" / "btc.html").read_text(encoding="utf-8")
        for needle in ('src="http', "src='http", 'href="http://'):
            self.assertNotIn(needle, html, f"external resource '{needle}' in pay/btc.html")

    def test_removed_qr_route_stays_removed(self):
        """/api/btc/qr.svg was deleted 2026-08-18 with the page's QR.

        It is asserted here rather than merely deleted because the route
        carried the only server-side copy of the payment address; a
        reintroduction would recreate the drift hazard against the address
        hard-coded in pay-btc.js.
        """
        app_src = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("/api/btc/qr.svg", app_src)
        self.assertNotIn("PAY_BTC_ADDRESS", app_src)

    def test_wallet_deep_link_replaces_the_qr(self):
        """Removing the QR must not strand a phone with a bech32 address.

        Before 2026-08-18 this page had a Copy button and nothing else --
        buy.html had a bitcoin: deep link, this page did not. The QR was
        the only hand-off to a wallet app, so its removal ships the link.
        """
        html = (ROOT / "web" / "pay" / "btc.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "pay-btc.js").read_text(encoding="utf-8")
        self.assertIn('id="wallet-link"', html)
        self.assertIn('"bitcoin:"', js)


if __name__ == "__main__":
    unittest.main()
