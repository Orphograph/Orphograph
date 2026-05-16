#!/usr/bin/env python3
"""test_vault_filters.py — pin /api/me/anchors vault filter behavior:
q (hash prefix), label (substring), private (true/false), and cursor
pagination compose correctly with the filters.

Receipts are built directly via engine.anchor_hash with source=
"sub:<email_id>" so they are owned by a synthetic subscriber. The OTS
calendar submission is stubbed so no network is touched. Authentication
is exercised through auth.create_session + the Cookie header.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import urllib.error
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


def _start_test_server(data_dir: Path):
    os.environ["ORPHO_DATA_DIR"] = str(data_dir)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = "0"
    os.environ["ORPHO_COOKIE_SECURE"] = "0"
    os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
    for m in list(sys.modules.keys()):
        if m in _MODULES_TO_EVICT:
            sys.modules.pop(m, None)
    import app
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}", app


def _stop(server) -> None:
    server.shutdown()
    server.server_close()


class TestVaultFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_env = {k: os.environ.get(k) for k in (
            "ORPHO_DATA_DIR", "HOST", "PORT", "ORPHO_COOKIE_SECURE",
        )}
        cls._old_modules = {
            m: sys.modules[m] for m in list(sys.modules.keys())
            if m in _MODULES_TO_EVICT
        }
        cls._server, cls._base, cls._app = _start_test_server(Path(cls._tmp.name))
        import engine as engine_mod
        import auth as auth_mod
        import subscriptions as subs_mod
        cls.engine = engine_mod
        cls.auth = auth_mod
        cls.subscriptions = subs_mod

        # Monkey-patch is_active to short-circuit for our test email.
        cls.owner_email = "vault@example.com"
        cls._orig_is_active = subs_mod.is_active
        subs_mod.is_active = lambda email: email == cls.owner_email
        cls.owner_id = auth_mod.email_id(cls.owner_email)

        # Mint a session + capture the cookie header value.
        sid, _exp = auth_mod.create_session(cls.owner_email)
        cookie_name = auth_mod.cookie_name(False)  # dev / non-Secure name
        cls.cookie_header = f"{cookie_name}={sid}"

        # Stub OTS submission — no network.
        original_submit = engine_mod._submit
        engine_mod._submit = lambda cal, h: (False, "stubbed: no network")
        source = f"sub:{cls.owner_id}"

        try:
            # Receipts we will filter through. Build a mix of (hash prefix,
            # label, private). Use distinct hash prefixes so q filtering bites.
            # Receipt 1: hash prefix 'abc...', label 'invoice-Q1', public
            cls.r1 = cls._anchor(engine_mod, "abc-content-1", source,
                                 label="invoice-Q1", private=False)
            # Receipt 2: hash prefix 'abc...' (different second char), label
            # 'invoice-Q2', private
            cls.r2 = cls._anchor(engine_mod, "abc-content-2", source,
                                 label="invoice-Q2", private=True)
            # Receipt 3: hash prefix differs from 'abc', label 'photo-RAW',
            # public
            cls.r3 = cls._anchor(engine_mod, "ZZZ-content-3", source,
                                 label="photo-RAW", private=False)
            # Receipt 4: hash prefix differs from 'abc', label 'TEST-doc',
            # private
            cls.r4 = cls._anchor(engine_mod, "ZZZ-content-4", source,
                                 label="TEST-doc", private=True)
        finally:
            engine_mod._submit = original_submit

        # The engine stamps created_at at one-second resolution. To exercise
        # cursor pagination we need distinct timestamps; rewrite the on-disk
        # receipt.json files for r1/r2 to known, distinct values.
        cls._rewrite_created_at(cls.r1["receipt_id"], "2026-05-15T20:00:00+00:00")
        cls._rewrite_created_at(cls.r2["receipt_id"], "2026-05-15T20:00:01+00:00")
        # Refresh the in-memory rec dicts so tests see the new timestamps.
        cls.r1["created_at"] = "2026-05-15T20:00:00+00:00"
        cls.r2["created_at"] = "2026-05-15T20:00:01+00:00"

    @classmethod
    def _rewrite_created_at(cls, rid: str, new_ts: str) -> None:
        rfile = cls.engine.RECEIPTS_DIR / rid / "receipt.json"
        rec = json.loads(rfile.read_text())
        rec["created_at"] = new_ts
        rfile.write_text(json.dumps(rec))

    @classmethod
    def _anchor(cls, engine_mod, salt: str, source: str, label: str,
                private: bool) -> dict:
        # Use a salt that lets us shape the hash prefix predictably via brute
        # force is overkill. Instead we just use whatever digest comes out and
        # read it back from the response.
        digest = hashlib.sha256(salt.encode()).hexdigest()
        rec = engine_mod.anchor_hash(
            digest,
            client_label=label,
            source=source,
            private=private,
            owner_id=cls.owner_id if private else None,
        )
        return rec

    @classmethod
    def tearDownClass(cls):
        # Restore subscriptions.is_active
        try:
            cls.subscriptions.is_active = cls._orig_is_active
        except Exception:
            pass
        _stop(cls._server)
        cls._tmp.cleanup()
        for m in list(sys.modules.keys()):
            if m in _MODULES_TO_EVICT:
                sys.modules.pop(m, None)
        for m, mod in cls._old_modules.items():
            sys.modules[m] = mod
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # ── helpers ─────────────────────────────────────────────────────────

    def _get(self, path: str, with_cookie: bool = True):
        req = urllib.request.Request(self._base + path)
        if with_cookie:
            req.add_header("Cookie", self.cookie_header)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, {}

    def _ids(self, payload: dict) -> set[str]:
        return {a["receipt_id"] for a in payload.get("anchors", [])}

    # ── tests ───────────────────────────────────────────────────────────

    def test_unauthenticated_returns_401(self):
        status, data = self._get("/api/me/anchors", with_cookie=False)
        self.assertEqual(status, 401)
        self.assertEqual(data.get("error"), "not authenticated")

    def test_authenticated_lists_all_anchors(self):
        status, data = self._get("/api/me/anchors")
        self.assertEqual(status, 200)
        ids = self._ids(data)
        self.assertEqual(ids, {
            self.r1["receipt_id"], self.r2["receipt_id"],
            self.r3["receipt_id"], self.r4["receipt_id"],
        })

    def test_q_filters_case_insensitively_on_hash_prefix(self):
        # Pick the first 6 hex chars of r1's hash, uppercase it, and confirm
        # the filter still matches.
        prefix = self.r1["hash_hex"][:6].upper()
        status, data = self._get(f"/api/me/anchors?q={prefix}")
        self.assertEqual(status, 200)
        ids = self._ids(data)
        # r1 must be in. Nothing whose hash doesn't start with that prefix
        # should leak in.
        self.assertIn(self.r1["receipt_id"], ids)
        for a in data["anchors"]:
            self.assertTrue(
                a["hash_hex"].lower().startswith(prefix.lower()),
                f"non-matching hash leaked through q filter: {a['hash_hex']}",
            )

    def test_label_filters_case_insensitively(self):
        # 'INVOICE' should match 'invoice-Q1' + 'invoice-Q2'
        status, data = self._get("/api/me/anchors?label=INVOICE")
        self.assertEqual(status, 200)
        ids = self._ids(data)
        self.assertEqual(ids, {self.r1["receipt_id"], self.r2["receipt_id"]})

    def test_private_true_returns_only_private(self):
        status, data = self._get("/api/me/anchors?private=true")
        self.assertEqual(status, 200)
        ids = self._ids(data)
        self.assertEqual(ids, {self.r2["receipt_id"], self.r4["receipt_id"]})
        for a in data["anchors"]:
            self.assertTrue(a["private"])

    def test_private_false_returns_only_public(self):
        status, data = self._get("/api/me/anchors?private=false")
        self.assertEqual(status, 200)
        ids = self._ids(data)
        self.assertEqual(ids, {self.r1["receipt_id"], self.r3["receipt_id"]})
        for a in data["anchors"]:
            self.assertFalse(a["private"])

    def test_private_invalid_value_is_ignored(self):
        # Anything other than the literal 'true'/'false' is treated as "no
        # filter" — i.e. all rows come back, matching the no-param case.
        status, data = self._get("/api/me/anchors?private=banana")
        self.assertEqual(status, 200)
        ids = self._ids(data)
        self.assertEqual(ids, {
            self.r1["receipt_id"], self.r2["receipt_id"],
            self.r3["receipt_id"], self.r4["receipt_id"],
        })

    def test_filters_compose_q_and_label(self):
        # q=<r1 hash prefix> + label=invoice → only r1 (its label is invoice-Q1
        # and its hash starts with that prefix). r2's hash starts with a
        # different prefix even though its label contains 'invoice'.
        prefix = self.r1["hash_hex"][:8]
        path = f"/api/me/anchors?q={prefix}&label=invoice"
        status, data = self._get(path)
        self.assertEqual(status, 200)
        ids = self._ids(data)
        self.assertEqual(ids, {self.r1["receipt_id"]})

    def test_cursor_pagination_composes_with_filters(self):
        # Fetch all matching 'invoice' first, then use the older row's
        # created_at as the 'before' cursor and confirm only the row strictly
        # older than that timestamp comes back, and that the filter still
        # applies.
        status, data = self._get("/api/me/anchors?label=invoice")
        self.assertEqual(status, 200)
        rows = data["anchors"]
        self.assertEqual(len(rows), 2)
        # Rows are newest-first; cursor on the newer row should yield only the
        # older one.
        newer_created_at = rows[0]["created_at"]
        before_q = urllib.parse.quote(newer_created_at, safe="")
        status2, data2 = self._get(f"/api/me/anchors?label=invoice&before={before_q}")
        self.assertEqual(status2, 200)
        ids2 = self._ids(data2)
        # Exactly one row: the older 'invoice' row.
        self.assertEqual(len(ids2), 1)
        self.assertIn(rows[1]["receipt_id"], ids2)
        # And it must still be an invoice-labeled row (filter still applied).
        for a in data2["anchors"]:
            self.assertIn("invoice", (a.get("client_label") or "").lower())


if __name__ == "__main__":
    unittest.main()
