"""Cross-origin POST forgery is blocked by requiring a JSON content type.

Found 2026-08-19 by driving the real endpoint instead of reading the source.
The PR that shipped the demand instrument claimed the capture endpoint was
safe because "JSON content-type forces a preflight that isn't allowed", and an
independent reviewer marked the same reasoning "verified as fine". Both were
WRONG: nothing in the server inspected Content-Type at all.

    text/plain                        -> 200
    application/x-www-form-urlencoded -> 200
    multipart/form-data               -> 200

Those are the three enctypes a cross-origin HTML form can produce, and they
are CORS "simple requests" -- they reach the server with NO preflight. The
classic enctype="text/plain" trick yields a syntactically valid JSON body:

    <form action="https://orphograph.com/api/waitlist" method="POST"
          enctype="text/plain">
      <input name='{"email":"attacker@evil.com","interest":"agent_receipts","x":"'
             value='"}'>
    </form>

WHY IT MATTERS HERE, specifically. Session-authenticated endpoints were
already covered: the session cookie is SameSite=Lax, so it is not sent on a
cross-site POST. The exposed endpoints are the UNAUTHENTICATED ones, where
SameSite has nothing to act on -- and those are exactly the ones that feed the
numbers this project makes decisions from. /api/waitlist is the demand
instrument. /api/event is the funnel. /api/auth/email-link sends mail.
Forging them from visitors' browsers also borrows those visitors' IPs, which
is how the per-IP rate limits get walked around.

A MISSING Content-Type is also a simple request -- a cross-origin fetch with
an untyped Blob sends none -- so rejecting only the three form enctypes would
have left the hole open. The gate requires application/json.

Nothing legitimate breaks: every POST body in this server is JSON (nothing
parses form encoding), and the public API docs, the MCP server and both SDKs
all already send the header.
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
    saved = {m: sys.modules[m] for m in list(sys.modules) if m in _MODULES_TO_EVICT}
    for m in list(sys.modules):
        if m in _MODULES_TO_EVICT:
            sys.modules.pop(m, None)
    return saved


def _restore(saved: dict) -> None:
    for m in list(sys.modules):
        if m in _MODULES_TO_EVICT:
            sys.modules.pop(m, None)
    sys.modules.update(saved)


def _post(base: str, path: str, body: bytes, ctype: str | None):
    headers = {} if ctype is None else {"Content-Type": ctype}
    req = urllib.request.Request(base + path, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


class TestPostContentTypeGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._saved = _evict()
        cls._old = {k: os.environ.get(k) for k in
                    ("ORPHO_DATA_DIR", "HOST", "PORT", "ORPHO_COOKIE_SECURE")}
        os.environ["ORPHO_DATA_DIR"] = cls._tmp.name
        os.environ["HOST"] = "127.0.0.1"
        os.environ["PORT"] = "0"
        os.environ["ORPHO_COOKIE_SECURE"] = "0"
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        import app
        from http.server import ThreadingHTTPServer
        cls._app = app
        cls._server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls._base = f"http://127.0.0.1:{cls._server.server_address[1]}"
        threading.Thread(target=cls._server.serve_forever, daemon=True).start()
        cls._waitlist = Path(cls._tmp.name) / "waitlist.jsonl"

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._server.server_close()
        cls._tmp.cleanup()
        for k, v in cls._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _restore(cls._saved)

    # ── the forgery vector ────────────────────────────────────────────────
    SIMPLE_CTYPES = (
        "text/plain",
        "text/plain;charset=UTF-8",
        "application/x-www-form-urlencoded",
        "multipart/form-data; boundary=x",
        None,  # a cross-origin fetch with an untyped Blob sends no header
    )

    def test_simple_content_types_are_rejected(self):
        body = json.dumps({"email": "attacker@evil.com",
                           "interest": "agent_receipts"}).encode()
        for ctype in self.SIMPLE_CTYPES:
            with self.subTest(ctype=ctype):
                self.assertEqual(
                    _post(self._base, "/api/waitlist", body, ctype), 415,
                    f"{ctype!r} is a CORS simple content type: a cross-origin "
                    f"form could POST with it and no preflight")

    def test_nothing_forged_reached_storage(self):
        """The status code is not the claim. Read the ledger back."""
        for ctype in self.SIMPLE_CTYPES:
            _post(self._base, "/api/waitlist",
                  json.dumps({"email": "attacker@evil.com",
                              "interest": "agent_receipts"}).encode(), ctype)
        stored = self._waitlist.read_text() if self._waitlist.exists() else ""
        self.assertNotIn("attacker@evil.com", stored,
                         "a rejected request still wrote to the waitlist")

    def test_the_funnel_endpoint_is_covered_too(self):
        """/api/event feeds the numbers the demand decision is read from."""
        body = json.dumps({"event": "page_view", "page": "/"}).encode()
        self.assertEqual(_post(self._base, "/api/event", body, "text/plain"), 415)

    # ── nothing legitimate breaks ─────────────────────────────────────────
    def test_json_is_accepted_with_and_without_charset(self):
        for ctype in ("application/json", "application/json; charset=utf-8",
                      "APPLICATION/JSON"):
            with self.subTest(ctype=ctype):
                code = _post(self._base, "/api/event",
                             json.dumps({"event": "page_view", "page": "/"}).encode(),
                             ctype)
                self.assertNotEqual(code, 415, f"{ctype} must be accepted")

    def test_a_real_signup_still_lands(self):
        """NEGATIVE CONTROL for the whole file: if the gate rejected
        everything, every assertion above would pass vacuously."""
        code = _post(self._base, "/api/waitlist",
                     json.dumps({"email": "legit@example.com",
                                 "interest": "agent_receipts"}).encode(),
                     "application/json")
        self.assertEqual(code, 200)
        self.assertIn("legit@example.com", self._waitlist.read_text(),
                      "the gate is rejecting legitimate traffic too")

    def test_signature_verified_webhooks_are_exempt(self):
        """Third-party callers we do not control. Both verify a provider
        signature over the raw body, which is stronger than a content-type
        check, so they must not be forced to conform."""
        for path in self._app.Handler._CT_EXEMPT_POST_PATHS:
            with self.subTest(path=path):
                self.assertNotEqual(
                    _post(self._base, path, b"{}", "text/plain"), 415,
                    f"{path} is third-party and must stay exempt")

    def test_non_api_posts_are_untouched(self):
        self.assertNotEqual(_post(self._base, "/nope", b"x", "text/plain"), 415)

    def test_bodyless_posts_still_work(self):
        """Sign-out must keep working. account.js and statusbar.js POST with
        NO body and NO Content-Type; an unconditional requirement would have
        415'd every sign-out in production. Safe because the forgery vector
        needs a body, and these endpoints are session-authenticated anyway."""
        for path in ("/api/auth/signout", "/api/me/logout-all"):
            with self.subTest(path=path):
                self.assertNotEqual(_post(self._base, path, b"", None), 415,
                                    f"{path} is called bodyless by shipped clients")

    def test_a_body_without_a_declared_length_cannot_smuggle(self):
        """The exemption is scoped to genuinely empty requests: a declared
        body still has to be JSON."""
        self.assertEqual(
            _post(self._base, "/api/waitlist", b'{"email":"x@y.z"}', "text/plain"), 415)


if __name__ == "__main__":
    unittest.main()
