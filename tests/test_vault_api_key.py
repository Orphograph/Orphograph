#!/usr/bin/env python3
"""test_vault_api_key.py — pin X-Orpho-Api-Key authentication on the vault
endpoints (/api/me/anchors, .zip, .csv).

The MCP server, SDKs, and CI integrations send only the API key header —
they have no cookie jar — so the vault endpoints must resolve the requester
from a valid key belonging to an active subscriber, with the session cookie
as fallback. Precedence mirrors /api/anchor.

Receipts are built directly via engine.anchor_hash with source=
"sub:<email_id>" so they are owned by a synthetic subscriber. The OTS
calendar submission is stubbed so no network is touched.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
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


class TestVaultApiKey(unittest.TestCase):
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
        import api_keys as keys_mod
        cls.engine = engine_mod
        cls.auth = auth_mod
        cls.subscriptions = subs_mod
        cls.api_keys = keys_mod

        cls.owner_email = "keyed@example.com"
        cls.other_email = "other@example.com"
        cls.lapsed_email = "lapsed@example.com"
        # Active for owner + other; lapsed subscriber has a key but no sub.
        cls._active_emails = {cls.owner_email, cls.other_email}
        cls._orig_is_active = subs_mod.is_active
        subs_mod.is_active = lambda email: email in cls._active_emails
        cls.owner_id = auth_mod.email_id(cls.owner_email)
        cls.other_id = auth_mod.email_id(cls.other_email)

        cls.owner_key = keys_mod.issue(cls.owner_email)
        cls.other_key = keys_mod.issue(cls.other_email)
        cls.lapsed_key = keys_mod.issue(cls.lapsed_email)

        sid, _exp = auth_mod.create_session(cls.owner_email)
        cookie_name = auth_mod.cookie_name(False)
        cls.cookie_header = f"{cookie_name}={sid}"

        original_submit = engine_mod._submit
        engine_mod._submit = lambda cal, h: (False, "stubbed: no network")
        try:
            cls.r_owner = cls._anchor(engine_mod, "owner-doc",
                                      f"sub:{cls.owner_id}")
            cls.r_other = cls._anchor(engine_mod, "other-doc",
                                      f"sub:{cls.other_id}")
            # Receipts anchored VIA the key carry the api:<key[:10]> source
            # tag — they must land in the same vault as session anchors.
            cls.r_owner_api = cls._anchor(engine_mod, "owner-keyed-doc",
                                          f"api:{cls.owner_key[:10]}")
            cls.r_other_api = cls._anchor(engine_mod, "other-keyed-doc",
                                          f"api:{cls.other_key[:10]}")
        finally:
            engine_mod._submit = original_submit

    @classmethod
    def _anchor(cls, engine_mod, salt: str, source: str) -> dict:
        digest = hashlib.sha256(salt.encode()).hexdigest()
        return engine_mod.anchor_hash(digest, source=source)

    @classmethod
    def tearDownClass(cls):
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

    def _get(self, path: str, api_key: str | None = None,
             cookie: bool = False):
        req = urllib.request.Request(self._base + path)
        if api_key is not None:
            req.add_header("X-Orpho-Api-Key", api_key)
        if cookie:
            req.add_header("Cookie", self.cookie_header)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def _receipt_ids(self, body: bytes) -> list[str]:
        return [a["receipt_id"] for a in json.loads(body)["anchors"]]

    # ── tests ───────────────────────────────────────────────────────────

    def test_valid_key_lists_own_vault(self):
        status, body, _ = self._get("/api/me/anchors", api_key=self.owner_key)
        self.assertEqual(status, 200)
        ids = self._receipt_ids(body)
        self.assertIn(self.r_owner["receipt_id"], ids)
        self.assertIn(self.r_owner_api["receipt_id"], ids)
        self.assertNotIn(self.r_other["receipt_id"], ids)
        self.assertNotIn(self.r_other_api["receipt_id"], ids)

    def test_key_scopes_to_its_owner(self):
        status, body, _ = self._get("/api/me/anchors", api_key=self.other_key)
        self.assertEqual(status, 200)
        ids = self._receipt_ids(body)
        self.assertIn(self.r_other["receipt_id"], ids)
        self.assertIn(self.r_other_api["receipt_id"], ids)
        self.assertNotIn(self.r_owner["receipt_id"], ids)
        self.assertNotIn(self.r_owner_api["receipt_id"], ids)

    def test_cookie_session_sees_keyed_receipts(self):
        # The dashboard (cookie auth) must show receipts anchored via the key.
        status, body, _ = self._get("/api/me/anchors", cookie=True)
        self.assertEqual(status, 200)
        self.assertIn(self.r_owner_api["receipt_id"], self._receipt_ids(body))

    def test_rotated_key_receipts_survive(self):
        # Receipts anchored under an old key stay in the vault after rotation.
        rot_email = "rotate@example.com"
        self._active_emails.add(rot_email)
        try:
            key1 = self.api_keys.issue(rot_email)
            original_submit = self.engine._submit
            self.engine._submit = lambda cal, h: (False, "stubbed: no network")
            try:
                rec = self._anchor(self.engine, "rotated-doc",
                                   f"api:{key1[:10]}")
            finally:
                self.engine._submit = original_submit
            key2 = self.api_keys.issue(rot_email)  # supersedes key1
            status, body, _ = self._get("/api/me/anchors", api_key=key2)
            self.assertEqual(status, 200)
            self.assertIn(rec["receipt_id"], self._receipt_ids(body))
        finally:
            self._active_emails.discard(rot_email)

    def test_bogus_key_is_401(self):
        status, _, _ = self._get("/api/me/anchors",
                                 api_key="orpho_totally_bogus")
        self.assertEqual(status, 401)

    def test_missing_key_and_cookie_is_401(self):
        status, _, _ = self._get("/api/me/anchors")
        self.assertEqual(status, 401)

    def test_lapsed_subscription_key_is_401(self):
        status, _, _ = self._get("/api/me/anchors", api_key=self.lapsed_key)
        self.assertEqual(status, 401)

    def test_revoked_key_is_401(self):
        victim = "revoked@example.com"
        orig = self.subscriptions.is_active
        self.subscriptions.is_active = lambda email: email in (
            self.owner_email, self.other_email, victim)
        try:
            key = self.api_keys.issue(victim)
            self.api_keys.revoke(victim)
            status, _, _ = self._get("/api/me/anchors", api_key=key)
            self.assertEqual(status, 401)
        finally:
            self.subscriptions.is_active = orig

    def test_cookie_session_still_works(self):
        status, body, _ = self._get("/api/me/anchors", cookie=True)
        self.assertEqual(status, 200)
        self.assertIn(self.r_owner["receipt_id"], self._receipt_ids(body))

    def test_bad_key_falls_back_to_cookie(self):
        # A stale/invalid key in the header must not lock out a valid session.
        status, body, _ = self._get("/api/me/anchors",
                                    api_key="orpho_stale", cookie=True)
        self.assertEqual(status, 200)
        self.assertIn(self.r_owner["receipt_id"], self._receipt_ids(body))

    def test_zip_export_with_key(self):
        status, body, headers = self._get("/api/me/anchors.zip",
                                          api_key=self.owner_key)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "application/zip")
        self.assertTrue(body.startswith(b"PK"))

    def test_zip_export_carries_folder_manifest(self):
        # A folder receipt's manifest.json is the only way a relying party
        # can recompute the anchored root from the files; the vault export
        # advertised it (sdk README) but only wrote receipt.json + *.ots.
        import io
        import zipfile
        rid = self.r_owner["receipt_id"]
        mpath = self.engine.RECEIPTS_DIR / rid / "manifest.json"
        mpath.write_text(json.dumps({"receipt_id": rid, "kind": "folder",
                                     "root_hex": self.r_owner["hash_hex"],
                                     "leaves": []}))
        # Renewal records ride along too (receipt_export.export_zip parity):
        # verify_renewal.py fails hard on a missing batch block.
        rdir = self.engine.RECEIPTS_DIR / rid / "renewal"
        rdir.mkdir(exist_ok=True)
        rpath = rdir / "001.json"
        rpath.write_text(json.dumps({"sequence": 1, "target": {"receipt_id": rid}}))
        try:
            status, body, _ = self._get("/api/me/anchors.zip",
                                        api_key=self.owner_key)
            self.assertEqual(status, 200)
            names = zipfile.ZipFile(io.BytesIO(body)).namelist()
            self.assertIn(f"{rid}/receipt.json", names)
            self.assertIn(f"{rid}/manifest.json", names)
            self.assertIn(f"{rid}/renewal/001.json", names)
        finally:
            mpath.unlink(missing_ok=True)
            rpath.unlink(missing_ok=True)

    def test_csv_export_with_key(self):
        status, body, _ = self._get("/api/me/anchors.csv",
                                    api_key=self.owner_key)
        self.assertEqual(status, 200)
        self.assertIn(self.r_owner["receipt_id"].encode(), body)


if __name__ == "__main__":
    unittest.main()
