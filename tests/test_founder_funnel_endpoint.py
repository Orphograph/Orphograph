"""test_founder_funnel_endpoint.py — gate /api/founder/funnel.

The endpoint reads data/events.jsonl, filters the 4 funnel events, and
returns per-day counts + 30-day totals + stage conversion rates. Gated by
the shared-secret X-Orpho-Founder header that must match the
ORPHO_FOUNDER_TOKEN env var.

Test classes mirror tests/test_admin_toggles.py: evict app+sibling modules
so the next `import app` re-reads os.environ.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
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


def _get(base: str, path: str, headers: dict | None = None):
    req = urllib.request.Request(base + path, method="GET")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, (e.read() if hasattr(e, "read") else b""), (
            dict(e.headers) if hasattr(e, "headers") else {}
        )


def _stub_events(data_dir: Path) -> None:
    """Plant a few funnel events plus one non-funnel event for filtering."""
    events_path = data_dir.parent / "data" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    lines = []
    for offset, ev in [
        (0, "drop_zone_visible"),
        (0, "drop_zone_visible"),
        (0, "file_anchored"),
        (1, "checkout_clicked"),
        (1, "checkout_returned_success"),
        (1, "some_other_event"),  # must be filtered out
    ]:
        ts = (now - timedelta(hours=offset)).isoformat()
        lines.append(json.dumps({"event": ev, "ts": ts}))
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# The endpoint reads data/events.jsonl relative to the SERVER source tree,
# not relative to ORPHO_DATA_DIR. Make sure we land the stub there.
def _events_path_from_app() -> Path:
    return ROOT / "data" / "events.jsonl"


class TestFunnelEndpointTokenUnset(unittest.TestCase):
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

    def test_no_token_returns_404(self):
        status, _, _ = _get(self._base, "/api/founder/funnel")
        self.assertEqual(status, 404)

    def test_no_token_with_any_header_still_404(self):
        status, _, _ = _get(self._base, "/api/founder/funnel",
                            headers={"X-Orpho-Founder": "anything"})
        self.assertEqual(status, 404)


class TestFunnelEndpointTokenSet(unittest.TestCase):
    TOKEN = "test-funnel-token-xyz789"
    _stub_events_backup: bytes | None = None
    _stub_existed = False

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_env = {k: os.environ.get(k) for k in (
            "ORPHO_FOUNDER_TOKEN", "ORPHO_DATA_DIR",
            "HOST", "PORT", "ORPHO_COOKIE_SECURE",
        )}
        os.environ["ORPHO_FOUNDER_TOKEN"] = cls.TOKEN

        # Back up / install stub events.jsonl at the path the endpoint reads.
        events = _events_path_from_app()
        cls._stub_existed = events.exists()
        if cls._stub_existed:
            cls._stub_events_backup = events.read_bytes()
        else:
            events.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        stub_lines = []
        for offset, ev in [
            (0, "drop_zone_visible"),
            (0, "drop_zone_visible"),
            (0, "file_anchored"),
            (1, "checkout_clicked"),
            (1, "checkout_returned_success"),
            (1, "some_other_event"),
        ]:
            ts = (now - timedelta(hours=offset)).isoformat()
            stub_lines.append(json.dumps({"event": ev, "ts": ts}))
        events.write_text("\n".join(stub_lines) + "\n", encoding="utf-8")

        cls._saved = _evict()
        cls._server, cls._base, cls._app = _start(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        _stop(cls._server)
        cls._tmp.cleanup()
        _restore(cls._saved)
        # Restore the original events.jsonl (or remove the stub).
        events = _events_path_from_app()
        if cls._stub_existed and cls._stub_events_backup is not None:
            events.write_bytes(cls._stub_events_backup)
        else:
            try:
                events.unlink()
            except FileNotFoundError:
                pass
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_wrong_token_returns_404(self):
        status, _, _ = _get(self._base, "/api/founder/funnel",
                            headers={"X-Orpho-Founder": "WRONG"})
        self.assertEqual(status, 404)

    def test_missing_header_returns_404(self):
        status, _, _ = _get(self._base, "/api/founder/funnel")
        self.assertEqual(status, 404)

    def test_correct_token_returns_funnel_json(self):
        status, body, _ = _get(self._base, "/api/founder/funnel",
                               headers={"X-Orpho-Founder": self.TOKEN})
        self.assertEqual(status, 200)
        data = json.loads(body)
        # Shape contract: per the _handle_founder_funnel implementation.
        self.assertIn("timestamp", data)
        self.assertIn("totals_30d", data)
        self.assertIn("rates_30d_pct", data)
        self.assertIn("events_scanned", data)
        self.assertIn("series_by_day", data)

        totals = data["totals_30d"]
        for ev in ("drop_zone_visible", "file_anchored",
                   "checkout_clicked", "checkout_returned_success"):
            self.assertIn(ev, totals)
            self.assertIsInstance(totals[ev], int)

        # Stub planted 2 + 1 + 1 + 1 of the four funnel events; non-funnel
        # event must be filtered out.
        self.assertGreaterEqual(totals["drop_zone_visible"], 2)
        self.assertGreaterEqual(totals["file_anchored"], 1)
        self.assertGreaterEqual(totals["checkout_clicked"], 1)
        self.assertGreaterEqual(totals["checkout_returned_success"], 1)

        rates = data["rates_30d_pct"]
        for k in ("visible_to_anchored", "anchored_to_checkout",
                  "checkout_to_paid", "visible_to_paid"):
            self.assertIn(k, rates)
            self.assertIsInstance(rates[k], (int, float))

        self.assertIsInstance(data["series_by_day"], list)


if __name__ == "__main__":
    unittest.main()


class LpCtaEventTests(unittest.TestCase):
    """lp_cta_clicked is a whitelisted funnel event and the LP wires it up."""

    def test_lp_cta_clicked_in_whitelist(self):
        import app
        self.assertIn("lp_cta_clicked", app.FUNNEL_EVENTS)

    def test_agent_receipts_lp_loads_cta_binder(self):
        html = (ROOT / "web" / "lp" / "agent-receipts.html").read_text()
        self.assertIn('src="/assets/lp-cta.js', html)
        self.assertIn('src="/assets/event.js', html)

    def test_lp_cta_binder_asset_exists_and_calls_orpho_event(self):
        js = (ROOT / "web" / "assets" / "lp-cta.js").read_text()
        self.assertIn('orphoEvent("lp_cta_clicked")', js)
