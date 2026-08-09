#!/usr/bin/env python3
"""test_private_fails_closed.py — a privacy request we cannot grant must not publish.

DEFECT (2026-08-06 Stage 3e, mutation-vs-commitment sweep)
----------------------------------------------------------
Both anchor endpoints computed

    want_private = bool(payload.get("private", False)) and subscription_active

so a caller who explicitly asked for `private: true` and was not
subscription-authenticated got a PUBLIC receipt. No error. No warning. And on
the folder path the response body did not include `private` at all, so the
caller could not discover it even by looking.

Found live, on our own infrastructure: scripts/auto_anchor_repo.py sends
`"private": True` and its launchd job sets ORPHO_BASE_URL but no
ORPHO_API_KEY. Receipt EH6FYSiPfJhNub8c (2026-08-06 daily repo anchor) came
back `private: false`. Every one of those anchors published a 6402-leaf
manifest the script had asked to keep private. Leaf PATHS were still redacted
from non-owners, which bounds the damage, but the digests were not, and the
contract was broken either way.

Publishing is not undoable; retrying without `private` costs one line. So the
endpoints now fail closed: no anchor, no calendar submission, pack credit
refunded, and a 402 that says what happened.

These tests drive the real HTTP handler. The whole defect lived in the gap
between "the flag is computed correctly" and "the request does what the
caller asked", which is exactly what a unit test of the expression would have
missed.
"""
from __future__ import annotations

import hashlib
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

_POLLUTED = (
    "app", "engine", "auth", "rate_limit", "credits", "stats",
    "health", "subscriptions", "teams", "stripe_webhook",
    "mailer", "api_keys", "affiliate", "newsletter", "waitlist",
    "blog", "unsubscribe", "gdpr", "public_config",
    "receipt_export", "btc_price", "btc_payments", "stripe_api",
    "og_svg", "qrcode_svg", "badge_svg", "analytics",
    "support_tools", "onboarding", "referrals", "file_lock",
    "merkle", "lightning", "webhooks",
)


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
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _post(url: str, body: dict, headers: dict | None = None):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.getcode(), json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


class TestPrivateFailsClosed(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_modules = {m: sys.modules[m] for m in _POLLUTED if m in sys.modules}
        cls._old_env = {k: os.environ.get(k) for k in
                        ("ORPHO_DATA_DIR", "HOST", "PORT",
                         "ORPHO_COOKIE_SECURE", "RATE_LIMIT_PER_DAY")}
        cls._server, cls._base = _start_test_server(Path(cls._tmp.name))
        import engine as engine_mod
        import merkle as merkle_mod
        cls._original_submit = engine_mod._submit
        engine_mod._submit = lambda cal, h: (False, "stubbed: test mode")
        cls.engine = engine_mod
        cls.merkle = merkle_mod

    @classmethod
    def tearDownClass(cls):
        cls.engine._submit = cls._original_submit
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

    # ── helpers ──────────────────────────────────────────────────────────
    def _manifest(self, name: str):
        """Build a real manifest the way a client would: hash an actual
        folder, then emit the tree's own manifest. Hand-rolling the dict got
        the leaf_hex derivation wrong three times, which is itself a small
        argument for the manifest being tree-derived rather than assembled."""
        d = Path(self._tmp.name) / f"src-{name}"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(name)
        return self.merkle.MerkleTree.from_folder(d).manifest()

    # ── the defect ───────────────────────────────────────────────────────
    def test_anonymous_private_file_anchor_is_refused_not_published(self):
        code, body = _post(f"{self._base}/api/anchor",
                           {"hash_hex": "a" * 64, "private": True})
        self.assertEqual(code, 402,
                         f"an anonymous caller asked for a PRIVATE anchor and "
                         f"got {code}; anything 2xx means we published what "
                         f"they asked us to keep private. body={body}")
        self.assertFalse(body.get("private_granted"))
        self.assertIsNone(body.get("receipt_id"),
                          "a receipt was issued despite the refusal")

    def test_anonymous_private_folder_anchor_is_refused_not_published(self):
        code, body = _post(f"{self._base}/api/anchor_folder",
                           {"manifest": self._manifest("secret.csv"),
                            "private": True})
        self.assertEqual(code, 402, f"folder path published a private "
                                    f"request: {code} {body}")
        self.assertFalse(body.get("private_granted"))
        self.assertIsNone(body.get("receipt_id"))

    def test_refusal_costs_nothing_and_submits_nothing(self):
        """Fail-closed must not burn the caller's anchor. Nothing reaches the
        calendars either, since we return before engine.anchor_hash."""
        calls = []
        original = self.engine._submit
        self.engine._submit = lambda cal, h: (calls.append(h), (False, "x"))[1]
        try:
            _post(f"{self._base}/api/anchor",
                  {"hash_hex": "b" * 64, "private": True})
            _post(f"{self._base}/api/anchor_folder",
                  {"manifest": self._manifest("also-secret.csv"),
                   "private": True})
        finally:
            self.engine._submit = original
        self.assertEqual(calls, [],
                         "a refused private anchor still submitted to the "
                         "OpenTimestamps calendars")

    def test_refusal_refunds_the_pack_credit(self):
        """Distinct from 'submits nothing': a caller must not PAY for an
        anchor we declined to make. The two endpoints reach the refund by
        different routes — the file path through _reject_private, the folder
        path through the shared refunding responder — so both are asserted.
        """
        import credits
        for label, url, body in (
            ("file", "/api/anchor", {"hash_hex": "e" * 64, "private": True}),
            ("folder", "/api/anchor_folder",
             {"manifest": self._manifest("paid-secret.csv"), "private": True}),
        ):
            with self.subTest(endpoint=label):
                code_ = credits.new_claim_code()
                credits.add_credits(code_, "buyer@example.com", 3, "test")
                before = credits.balance(code_)
                status, payload = _post(f"{self._base}{url}", body,
                                        {"X-Pack-Token": code_})
                self.assertEqual(status, 402, payload)
                self.assertEqual(
                    credits.balance(code_), before,
                    f"{label}: the caller was charged a credit for an anchor "
                    f"that was refused and never created")

    # ── the silence, which was half the defect ───────────────────────────
    def test_folder_response_always_reports_the_privacy_state(self):
        """The folder response omitted `private` entirely, so a caller could
        not tell a granted request from a dropped one."""
        code, body = _post(f"{self._base}/api/anchor_folder",
                           {"manifest": self._manifest("public.txt")})
        self.assertEqual(code, 200, body)
        self.assertIn("private", body,
                      "folder anchor response does not report privacy state")
        self.assertFalse(body["private"])

    # ── the ordinary path must still work ────────────────────────────────
    def test_public_anchors_are_unaffected(self):
        code, body = _post(f"{self._base}/api/anchor", {"hash_hex": "c" * 64})
        self.assertEqual(code, 200, body)
        self.assertTrue(body.get("receipt_id"))

    def test_private_false_is_not_treated_as_a_request(self):
        code, body = _post(f"{self._base}/api/anchor",
                           {"hash_hex": "d" * 64, "private": False})
        self.assertEqual(code, 200, body)
        self.assertTrue(body.get("receipt_id"))


class TestAutoAnchorRefusesToPublishSilently(unittest.TestCase):
    """The script that tripped this must not paper over it."""

    SRC = (ROOT / "scripts" / "auto_anchor_repo.py").read_text()

    def test_it_no_longer_hardcodes_private_true(self):
        self.assertNotIn('"private": True,', self.SRC,
                         "private is hardcoded again; --allow-public cannot "
                         "turn it off")

    def test_it_has_an_explicit_opt_in_to_publish(self):
        self.assertIn("--allow-public", self.SRC)

    def test_it_exits_nonzero_when_privacy_is_refused(self):
        self.assertIn("return 3", self.SRC,
                      "a refused private anchor must be a loud failure, not "
                      "a silent fallback to publishing")

    def test_it_double_checks_the_server_response(self):
        """Belt and braces: if a future server build downgrades silently
        again, the script should catch it rather than trust the endpoint."""
        self.assertIn('payload.get("private") is False', self.SRC)

    def test_it_refuses_before_the_network_when_the_key_is_empty(self):
        """The plist ships ORPHO_AUTO_ANCHOR_KEY as an EMPTY placeholder and it
        was never filled, so the script ran anonymously for 78 days / 51 runs.
        An empty key must be fatal at the source, not survive to the server."""
        self.assertIn("ORPHO_AUTO_ANCHOR_KEY is empty", self.SRC)
        self.assertIn("not args.allow_public and not API_KEY", self.SRC)

    def test_the_remediation_names_the_correct_variable(self):
        """The var is ORPHO_AUTO_ANCHOR_KEY. ORPHO_API_KEY is a different
        thing and setting it fixes nothing — that wrong instruction was
        given once and must not be re-enshrined here."""
        self.assertIn("ORPHO_AUTO_ANCHOR_KEY", self.SRC)
        self.assertIn('os.environ.get("ORPHO_AUTO_ANCHOR_KEY"', self.SRC)

    def test_the_docstring_no_longer_asserts_the_anchor_is_private(self):
        head = self.SRC.split('"""')[1]
        self.assertNotIn("The anchor is marked\n``private``", head,
                         "docstring still states as fact a privacy property "
                         "the anchors did not have")


if __name__ == "__main__":
    unittest.main()


class TestFolderReceiptEmailOverTheWire(unittest.TestCase):
    """The folder receipt email, driven through /api/anchor_folder.

    The original regression test for this built a receipt dict with
    kind="folder" and handed it straight to mailer.send_receipt_email. That
    exercised the TEMPLATE and proved nothing about the request path — and the
    request path was broken: _handle_anchor_folder wrote kind/leaf_count to the
    receipt FILE but passed the un-updated in-memory record to the mailer, so
    the email took the single-file branch and told dataset customers to retain
    "the original file". Caught by the security review of this branch, not by
    the test that was supposed to cover it.

    This asserts on what the customer actually receives.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_modules = {m: sys.modules[m] for m in _POLLUTED if m in sys.modules}
        cls._old_env = {k: os.environ.get(k) for k in
                        ("ORPHO_DATA_DIR", "HOST", "PORT",
                         "ORPHO_COOKIE_SECURE", "RATE_LIMIT_PER_DAY")}
        cls._server, cls._base = _start_test_server(Path(cls._tmp.name))
        import engine as engine_mod
        import merkle as merkle_mod
        import mailer as mailer_mod
        cls._original_submit = engine_mod._submit
        engine_mod._submit = lambda cal, h: (False, "stubbed: test mode")
        cls.engine, cls.merkle, cls.mailer = engine_mod, merkle_mod, mailer_mod

    @classmethod
    def tearDownClass(cls):
        cls.engine._submit = cls._original_submit
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

    def setUp(self):
        self.sent = []
        self._real_send = self.mailer._send
        self.mailer._send = lambda to, subject, text, html=None, **kw: (
            self.sent.append({"to": to, "text": text}) or True)

    def tearDown(self):
        self.mailer._send = self._real_send

    def _folder_manifest(self, names):
        d = Path(self._tmp.name) / ("wire-" + "-".join(names))
        d.mkdir(parents=True, exist_ok=True)
        for n in names:
            (d / n).write_text(n)
        return self.merkle.MerkleTree.from_folder(d).manifest()

    def test_a_paid_folder_anchor_emails_manifest_guidance(self):
        import credits
        code_ = credits.new_claim_code()
        credits.add_credits(code_, "buyer@example.com", 3, "test")
        status, body = _post(
            f"{self._base}/api/anchor_folder",
            {"manifest": self._folder_manifest(["a.txt", "b.txt", "c.txt"]),
             "notify_email": "buyer@example.com"},
            {"X-Pack-Token": code_})
        self.assertEqual(status, 200, body)
        self.assertTrue(self.sent, "a paid folder anchor sent no receipt email")
        text = self.sent[-1]["text"]
        self.assertIn("manifest", text.lower(),
                      "the folder customer was not told to keep the manifest, "
                      "without which the root cannot be re-derived. The "
                      "in-memory record is missing kind='folder' again.")
        self.assertNotIn("the original file together", text,
                         "folder email took the single-file branch")
        self.assertIn("Merkle root", text)
        self.assertIn("3", text, "leaf count not reported")

    def test_the_response_and_the_record_agree_on_kind(self):
        status, body = _post(
            f"{self._base}/api/anchor_folder",
            {"manifest": self._folder_manifest(["x.txt"])})
        self.assertEqual(status, 200, body)
        self.assertEqual(body.get("kind"), "folder")
        stored = json.loads(
            (self.engine.RECEIPTS_DIR / body["receipt_id"] / "receipt.json"
             ).read_text())
        self.assertEqual(stored.get("kind"), "folder")
        self.assertEqual(stored.get("leaf_count"), 1)
