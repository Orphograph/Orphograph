"""test_affiliate_redirect.py — /affiliate must redirect, not 404.

There is no standalone web/affiliate.html landing page; the affiliate /
referral details live on the signed-in account page. The /affiliate route
must therefore 302-redirect to /account instead of serving a missing
static file (which previously produced a 404).

Mirrors the ThreadingHTTPServer harness + no-follow redirect handler used by
tests/test_security_txt.py.
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


def _start_server(tmp_data_dir: Path) -> tuple[ThreadingHTTPServer, str]:
    os.environ["ORPHO_DATA_DIR"] = str(tmp_data_dir)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = "0"
    os.environ["ORPHO_COOKIE_SECURE"] = "0"
    # Make sure we pick up the freshly-edited app.py.
    for m in ("app",):
        sys.modules.pop(m, None)
    import app  # noqa: WPS433 — intentional late import after env setup
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):  # noqa: D401
        return None


class AffiliateRedirectCase(unittest.TestCase):
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

    def _open_no_redirect(self, path: str):
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            return opener.open(self.base + path)
        except urllib.error.HTTPError as e:
            return e

    def test_affiliate_redirects_to_account(self) -> None:
        resp = self._open_no_redirect("/affiliate")
        # Must be a 3xx redirect — never 404 (missing page) or 502 (crash).
        self.assertIn(resp.status, (301, 302, 303, 307, 308))
        self.assertNotIn(resp.status, (404, 502))
        self.assertEqual(resp.headers.get("Location", ""), "/account")

    def test_affiliate_trailing_slash_redirects_to_account(self) -> None:
        resp = self._open_no_redirect("/affiliate/")
        self.assertIn(resp.status, (301, 302, 303, 307, 308))
        self.assertNotIn(resp.status, (404, 502))
        self.assertEqual(resp.headers.get("Location", ""), "/account")


if __name__ == "__main__":
    unittest.main()
