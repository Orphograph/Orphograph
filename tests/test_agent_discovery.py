#!/usr/bin/env python3
"""test_agent_discovery.py — pins for the agent-discoverability surface.

Drives the real ThreadingHTTPServer (harness conventions copied from
tests/test_lineage_endpoint.py: module evict/restore, temp ORPHO_DATA_DIR)
and asserts:

  * GET /llms.txt → 200 text/plain, mentions orphograph.com and carries the
    proves-WHEN framing (never authorship/originality).
  * GET /.well-known/mcp/server-card.json → 200 application/json, parses,
    and lists exactly the tool names defined in mcp/orphograph_mcp.py
    (mirrored, never invented).
  * The security headers on "/" (CSP included) are unchanged — the new
    routes are additive and must not regress the existing header set.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

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

# The exact security headers _security_headers() emits today. Pinned so an
# accidental edit alongside the discovery routes fails loudly.
EXPECTED_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def _mcp_tool_names() -> list[str]:
    """Load mcp/orphograph_mcp.py standalone and return its declared tools."""
    spec = importlib.util.spec_from_file_location(
        "orphograph_mcp_under_test", ROOT / "mcp" / "orphograph_mcp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [t["name"] for t in mod.TOOL_DEFINITIONS]


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


class TestAgentDiscovery(unittest.TestCase):
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

    def _get(self, path: str):
        resp = urllib.request.urlopen(f"{self._base}{path}", timeout=10)
        return resp.status, dict(resp.headers), resp.read()

    # -- /llms.txt --------------------------------------------------------

    def test_llms_txt_served_at_root(self):
        status, headers, body = self._get("/llms.txt")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/plain"),
                        headers["Content-Type"])
        text = body.decode("utf-8")
        self.assertIn("orphograph.com", text)
        # The honest-framing pin: proves WHEN, never authorship/originality.
        self.assertIn("proves WHEN, not who", text)
        self.assertIn("tamper-evident", text)

    def test_llms_txt_carries_security_headers(self):
        _, headers, _ = self._get("/llms.txt")
        self.assertEqual(headers.get("Content-Security-Policy"), EXPECTED_CSP)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

    # -- /.well-known/mcp/server-card.json --------------------------------

    def test_server_card_served_and_parses(self):
        status, headers, body = self._get("/.well-known/mcp/server-card.json")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"),
                        headers["Content-Type"])
        card = json.loads(body)
        self.assertEqual(card["name"], "io.github.Orphograph/orphograph")
        self.assertEqual(card["transport"], "stdio")
        self.assertEqual(card["homepage"], "https://orphograph.com")

    def test_server_card_mirrors_mcp_tools_exactly(self):
        _, _, body = self._get("/.well-known/mcp/server-card.json")
        card = json.loads(body)
        card_tools = [t["name"] for t in card["tools"]]
        self.assertEqual(card_tools, _mcp_tool_names())
        # Every tool carries a one-line description.
        for t in card["tools"]:
            self.assertTrue(t.get("description", "").strip(), t["name"])

    # -- no CSP / header regression on "/" ---------------------------------

    def test_homepage_security_headers_unchanged(self):
        status, headers, _ = self._get("/")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Security-Policy"), EXPECTED_CSP)
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Referrer-Policy"),
                         "strict-origin-when-cross-origin")
        self.assertEqual(headers.get("Strict-Transport-Security"),
                         "max-age=31536000; includeSubDomains")


if __name__ == "__main__":
    unittest.main()
