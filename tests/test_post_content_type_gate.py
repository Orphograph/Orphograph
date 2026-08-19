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
        # STRIPE_WEBHOOK_SECRET is popped below; save it too or the deletion
        # outlives this class and a later stripe test silently takes the
        # "not configured" branch.
        cls._old = {k: os.environ.get(k) for k in
                    ("ORPHO_DATA_DIR", "HOST", "PORT", "ORPHO_COOKIE_SECURE",
                     "STRIPE_WEBHOOK_SECRET")}
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

    def test_rfc8058_one_click_unsubscribe_still_works(self):
        """Gmail / Yahoo / Microsoft POST here with
        `Content-Type: application/x-www-form-urlencoded` and a body of
        `List-Unsubscribe=One-Click`. That is the SPEC -- not a client we can
        change -- and mailer.py advertises it via List-Unsubscribe-Post on
        every non-transactional send.

        The first version of this gate answered 415, so the opt-out vanished
        and the mail kept going: a deliverability and bulk-sender-compliance
        failure that no existing test covered, which is why the suite stayed
        green through it.
        """
        status = _post(self._base, "/api/unsubscribe?e=optout%40example.com",
                       b"List-Unsubscribe=One-Click",
                       "application/x-www-form-urlencoded")
        self.assertNotEqual(status, 415,
                            "one-click unsubscribe is form-encoded BY SPEC; "
                            "gating it silently loses the opt-out")
        # The status is not the claim. The failure mode being guarded is a
        # LOST opt-out, so read the suppression ledger back.
        suppressions = Path(self._tmp.name) / "suppressions.jsonl"
        self.assertTrue(suppressions.exists(),
                        "no suppression ledger written -- the opt-out was lost")
        self.assertIn("optout@example.com", suppressions.read_text(),
                      "the request succeeded but the address was never "
                      "suppressed; the mail would keep going")

    def test_signature_verified_webhooks_are_exempt(self):
        """Third-party callers we do not control. Both verify a provider
        signature over the raw body, which is stronger than a content-type
        check, so they must not be forced to conform."""
        for path in self._app.Handler._CT_EXEMPT_POST_PATHS:
            with self.subTest(path=path):
                self.assertNotEqual(
                    _post(self._base, path, b"{}", "text/plain"), 415,
                    f"{path} is third-party and must stay exempt")

    def test_no_cors_policy_grants_a_cross_origin_post(self):
        """The gate's whole premise. application/json is not a CORS simple
        content type, so a forged cross-origin POST needs a preflight -- and
        this asserts nothing here answers one for POST.

        CORS IS granted on four paths (/api/verify/*, /api/verify_folder/*,
        /api/badge/*, /api/inclusion_proof) because the embeddable badge needs
        it, but only for `GET, OPTIONS` and with no Access-Control-Allow-Headers.
        If any of that ever widens to POST, this gate stops protecting anything
        and this test is the thing that says so.
        """
        src = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
        self.assertIn('"Access-Control-Allow-Methods", "GET, OPTIONS"', src,
                      "CORS methods changed -- if POST was added, the "
                      "content-type gate no longer blocks cross-origin forgery")
        self.assertNotIn("Access-Control-Allow-Headers", src,
                         "allowing request headers cross-origin would let a "
                         "content-type preflight succeed")

    def test_exempt_list_does_not_match_by_prefix(self):
        """An exemption list must not widen on its own."""
        self.assertEqual(
            _post(self._base, "/api/stripe/webhookEVIL", b'{"x":1}', "text/plain"),
            415, "a prefix of an exempt path must not inherit its exemption")

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

    def test_a_declared_body_still_has_to_be_json(self):
        """The bodyless exemption is scoped to genuinely empty requests."""
        self.assertEqual(
            _post(self._base, "/api/waitlist", b'{"email":"x@y.z"}', "text/plain"), 415)

    def test_chunked_body_skips_the_gate_but_still_cannot_smuggle(self):
        """The genuinely undeclared case is Transfer-Encoding: chunked, which
        has no Content-Length and therefore takes the bodyless branch.

        It is NOT rejected by the gate, and that is safe only because of an
        invariant: every handler sizes its body via _read_content_length and
        reads nothing it did not size, so a chunked POST is answered 400 and
        writes nothing. This test pins the consequence with a real chunked
        request, so if a handler ever learns to stream one, the safety this
        rests on stops being silent.
        """
        import http.client
        host, port = self._base.rsplit(":", 1)
        conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=5)
        body = json.dumps({"email": "chunked@evil.com",
                           "interest": "agent_receipts"}).encode()
        conn.putrequest("POST", "/api/waitlist")
        conn.putheader("Content-Type", "text/plain")
        conn.putheader("Transfer-Encoding", "chunked")
        conn.endheaders()
        conn.send(b"%x\r\n" % len(body) + body + b"\r\n0\r\n\r\n")
        status = conn.getresponse().status
        conn.close()
        self.assertNotEqual(status, 200, "a chunked forgery must not succeed")
        stored = self._waitlist.read_text() if self._waitlist.exists() else ""
        self.assertNotIn("chunked@evil.com", stored,
                         "a chunked body reached storage -- the invariant that "
                         "handlers only read what Content-Length sized is broken")


if __name__ == "__main__":
    unittest.main()
