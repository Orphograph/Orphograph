"""test_security_txt.py — RFC 9116 security.txt + JSON-LD structured data pins.

Verifies:
  - web/.well-known/security.txt exists, parses, and carries the required
    RFC 9116 fields (Contact, Expires, Canonical) with a future Expires.
  - The HTTP server serves /.well-known/security.txt as text/plain.
  - /security.txt 301-redirects to /.well-known/security.txt (RFC 9116 §3).
  - web/index.html carries a JSON-LD <script> block whose @graph contains
    Organization + WebSite + Service nodes.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
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

WEB = ROOT / "web"
SECURITY_TXT = WEB / ".well-known" / "security.txt"
INDEX_HTML = WEB / "index.html"


# ── file-level pins ────────────────────────────────────────────────────

def _parse_security_txt(body: str) -> dict[str, list[str]]:
    """Tiny RFC 9116 field parser: returns {field: [value, ...]}."""
    out: dict[str, list[str]] = {}
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        out.setdefault(field.strip(), []).append(value.strip())
    return out


def test_security_txt_file_exists_and_parses() -> None:
    """The file must exist and carry the RFC 9116 mandatory fields."""
    assert SECURITY_TXT.is_file(), f"missing: {SECURITY_TXT}"
    body = SECURITY_TXT.read_text(encoding="utf-8")
    fields = _parse_security_txt(body)

    # RFC 9116 mandatory fields.
    assert "Contact" in fields and fields["Contact"], "Contact field required"
    assert "Expires" in fields and fields["Expires"], "Expires field required"
    assert "Canonical" in fields and fields["Canonical"], "Canonical field required"

    # Canonical must point at the well-known path.
    assert any(
        c.endswith("/.well-known/security.txt") for c in fields["Canonical"]
    ), f"Canonical must reference /.well-known/security.txt: {fields['Canonical']}"

    # Expires must parse as ISO 8601 and be in the future.
    exp_raw = fields["Expires"][0]
    # Python <3.11 doesn't accept the trailing 'Z'; normalize.
    exp_norm = exp_raw.replace("Z", "+00:00")
    exp_dt = _dt.datetime.fromisoformat(exp_norm)
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=_dt.timezone.utc)
    now = _dt.datetime.now(_dt.timezone.utc)
    assert exp_dt > now, f"Expires must be in the future, got {exp_raw} (now={now.isoformat()})"


# ── server-level pins ──────────────────────────────────────────────────

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


class _ServerCase(unittest.TestCase):
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

    def test_security_txt_served_with_correct_content_type(self) -> None:
        with urllib.request.urlopen(self.base + "/.well-known/security.txt") as resp:
            self.assertEqual(resp.status, 200)
            ctype = resp.headers.get("Content-Type", "")
            self.assertTrue(
                ctype.startswith("text/plain"),
                f"expected text/plain, got {ctype!r}",
            )
            body = resp.read().decode("utf-8")
            self.assertIn("Contact:", body)
            self.assertIn("Canonical:", body)

    def test_security_txt_short_url_redirects(self) -> None:
        # Disable urllib's automatic redirect-following so we can inspect the 301.
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):  # noqa: D401
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        try:
            resp = opener.open(self.base + "/security.txt")
        except urllib.error.HTTPError as e:  # type: ignore[name-defined]  # noqa: F821
            resp = e
        self.assertEqual(resp.status, 301)
        loc = resp.headers.get("Location", "")
        self.assertEqual(loc, "/.well-known/security.txt")


# Need urllib.error in scope for the redirect-handler except clause.
import urllib.error  # noqa: E402


# ── JSON-LD pin ────────────────────────────────────────────────────────

_JSONLD_RE = re.compile(
    r'<script\s+type="application/ld\+json"\s*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def test_jsonld_present_on_homepage() -> None:
    """Homepage must carry a JSON-LD block with Org + WebSite + Service nodes."""
    body = INDEX_HTML.read_text(encoding="utf-8")
    match = _JSONLD_RE.search(body)
    assert match is not None, "no <script type=\"application/ld+json\"> block found"
    payload = json.loads(match.group(1))
    assert payload.get("@context") == "https://schema.org"
    graph = payload.get("@graph")
    assert isinstance(graph, list) and graph, "@graph must be a non-empty list"
    types = {node.get("@type") for node in graph if isinstance(node, dict)}
    for expected in ("Organization", "WebSite", "Service"):
        assert expected in types, f"@graph missing {expected} (saw {sorted(types)})"


if __name__ == "__main__":
    unittest.main()
