"""test_ab_home.py — the cream-vs-dark homepage experiment split.

Pins the five behaviors that make the experiment safe to run in production:
off-by-default, sticky assignment, bot exclusion, no-store on both arms, and
the server-side ledger. Env is read per-request by _serve_ab_home, so one
server instance covers every case.

Mirrors the ThreadingHTTPServer harness used by tests/test_affiliate_redirect.py.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) TestBrowser/1.0"
BOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
DARK_MARKER = "/v2/style.css"
CREAM_MARKER = "/index.css"


def _start_server(tmp_data_dir: Path) -> tuple[ThreadingHTTPServer, str]:
    os.environ["ORPHO_DATA_DIR"] = str(tmp_data_dir)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = "0"
    os.environ["ORPHO_COOKIE_SECURE"] = "0"
    for m in ("app",):
        sys.modules.pop(m, None)
    import app  # noqa: WPS433 — intentional late import after env setup
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


class ABHomeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        cls._tmp = tempfile.TemporaryDirectory()
        cls.server, cls.base = _start_server(Path(cls._tmp.name))
        cls.log = Path(cls._tmp.name) / "ab_home.jsonl"

    @classmethod
    def tearDownClass(cls) -> None:
        os.environ.pop("ORPHO_AB_HOME", None)
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def _get(self, path: str, ua: str = BROWSER_UA, cookie: str | None = None):
        req = urllib.request.Request(self.base + path, headers={"User-Agent": ua})
        if cookie:
            req.add_header("Cookie", cookie)
        return urllib.request.urlopen(req, timeout=10)

    def _log_lines(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text().splitlines() if x.strip()]

    def test_off_by_default(self) -> None:
        os.environ.pop("ORPHO_AB_HOME", None)
        resp = self._get("/")
        self.assertEqual(resp.status, 200)
        body = resp.read().decode("utf-8")
        self.assertIn(CREAM_MARKER, body)
        self.assertNotIn("orpho_ab_home", resp.headers.get("Set-Cookie", "") or "")

    def test_forced_dark_assignment_and_headers(self) -> None:
        os.environ["ORPHO_AB_HOME"] = "1.0"
        resp = self._get("/")
        body = resp.read().decode("utf-8")
        self.assertIn(DARK_MARKER, body)
        set_cookie = resp.headers.get("Set-Cookie", "") or ""
        self.assertIn("orpho_ab_home=dark", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        self.assertIn("Cookie", resp.headers.get("Vary", "") or "")
        views = [r for r in self._log_lines() if r["event"] == "home_view" and r["variant"] == "dark"]
        self.assertTrue(views and views[-1].get("new") is True)

    def test_sticky_cookie_wins_over_fraction(self) -> None:
        os.environ["ORPHO_AB_HOME"] = "1.0"  # would force dark for NEW visitors
        resp = self._get("/", cookie="orpho_ab_home=cream")
        body = resp.read().decode("utf-8")
        self.assertIn(CREAM_MARKER, body)
        self.assertNotIn("orpho_ab_home", resp.headers.get("Set-Cookie", "") or "")

    def test_bots_always_get_cream_and_no_cookie(self) -> None:
        os.environ["ORPHO_AB_HOME"] = "1.0"
        resp = self._get("/", ua=BOT_UA)
        body = resp.read().decode("utf-8")
        self.assertIn(CREAM_MARKER, body)
        self.assertNotIn("orpho_ab_home", resp.headers.get("Set-Cookie", "") or "")

    def test_checkout_view_attributed_to_arm(self) -> None:
        os.environ["ORPHO_AB_HOME"] = "1.0"
        before = len([r for r in self._log_lines() if r["event"] == "checkout_view"])
        resp = self._get("/pay/crypto?plan=writer_pack", cookie="orpho_ab_home=dark")
        self.assertEqual(resp.status, 200)
        rows = [r for r in self._log_lines() if r["event"] == "checkout_view"]
        self.assertEqual(len(rows), before + 1)
        self.assertEqual(rows[-1]["variant"], "dark")


if __name__ == "__main__":
    unittest.main()
