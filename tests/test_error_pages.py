"""test_error_pages.py — branded send_error() pages.

The stdlib default "Error response" template is unbranded and offers no
path back into the site. The Handler overrides error_message_format so a
mistyped URL (e.g. /lp/agent-receipt for /lp/agent-receipts) lands on an
Orphograph-styled page with links onward.

Pins:
  - file-level: the template is branded, links back to / and /lp/, and is
    CSP-clean (external stylesheets only, no inline style).
  - server-level: a nonexistent path returns 404 with the branded body,
    not the stdlib template.
"""
from __future__ import annotations

import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


# ── file-level pins ────────────────────────────────────────────────────

def _template() -> str:
    import app  # noqa: WPS433

    return app.Handler.error_message_format


def test_error_template_is_branded() -> None:
    tpl = _template()
    assert "orphograph" in tpl, "error page must carry the brand"
    assert 'href="/"' in tpl, "error page must link back to home"
    assert 'href="/lp/"' in tpl, "error page must link to the guides index"


def test_error_template_keeps_stdlib_placeholders() -> None:
    """send_error() substitutes these; dropping them breaks the render."""
    tpl = _template()
    assert "%(code)d" in tpl
    assert "%(message)s" in tpl


def test_error_template_is_csp_clean() -> None:
    """style-src 'self': inline styles would be silently dropped."""
    tpl = _template()
    assert "<style" not in tpl, "no inline <style> blocks on the error page"
    assert 'style="' not in tpl, "no inline style= attributes on the error page"
    assert "<link rel=" in tpl and "stylesheet" in tpl, "external stylesheet expected"


def test_error_template_noindex() -> None:
    """Error pages must not enter the search index."""
    assert 'name="robots" content="noindex"' in _template()


# ── server-level pins ──────────────────────────────────────────────────

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


class _ErrorPageCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import tempfile

        cls._tmp = tempfile.TemporaryDirectory()
        cls.server, cls.base = _start_server(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def _get_error(self, path: str) -> tuple[int, str, str]:
        try:
            resp = urllib.request.urlopen(self.base + path)
        except urllib.error.HTTPError as e:
            resp = e
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, resp.headers.get("Content-Type", ""), body

    def test_typo_lp_route_renders_branded_404(self) -> None:
        """/lp/agent-receipt (singular typo) → branded 404, not stdlib page."""
        status, ctype, body = self._get_error("/lp/agent-receipt")
        self.assertEqual(status, 404)
        self.assertTrue(ctype.startswith("text/html"), f"got {ctype!r}")
        self.assertIn("orphograph", body)
        self.assertIn('href="/lp/"', body)
        self.assertNotIn("Error code explanation", body, "stdlib template leaked through")

    def test_unknown_path_renders_branded_404(self) -> None:
        status, _ctype, body = self._get_error("/definitely-not-a-page-xyz")
        self.assertEqual(status, 404)
        self.assertIn("orphograph", body)
        self.assertIn('href="/"', body)


if __name__ == "__main__":
    unittest.main()
