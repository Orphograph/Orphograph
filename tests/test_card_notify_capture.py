"""test_card_notify_capture.py — card-buyer email capture while card checkout is down.

Funnel-hygiene pass. When /api/config reports stripe.card_charges_enabled=false
the homepage card CTAs stay hidden and card-only buyers used to bounce silently.
Each paid tier card now carries a notify form (hidden by default in the static
HTML) that checkout-cta.js reveals ONLY while the flag is false, POSTing the
email to the EXISTING /api/waitlist endpoint with the tier encoded as
`interest` (card_pack / card_pack50 / card_personal).

Covers:
  - static markup: forms present and `hidden` by default on both homepages;
  - checkout-cta.js: reveal/hide keyed on card_charges_enabled (same
    mechanism as the card buttons), wired to /api/waitlist;
  - endpoint happy path: row lands in waitlist.jsonl with {ts, email, tier};
  - per-IP rate limit on the capture endpoint (429 once exhausted);
  - waitlist.add preserves the card_* tier values (not coerced to "other").

Server-thread harness mirrors tests/test_founder_token_bruteforce.py.
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
    saved = {m: sys.modules[m] for m in list(sys.modules.keys()) if m in _MODULES_TO_EVICT}
    for m in list(sys.modules.keys()):
        if m in _MODULES_TO_EVICT:
            sys.modules.pop(m, None)
    return saved


def _restore(saved: dict) -> None:
    for m in list(sys.modules.keys()):
        if m in _MODULES_TO_EVICT:
            sys.modules.pop(m, None)
    for m, mod in saved.items():
        sys.modules[m] = mod


def _post_json(base: str, path: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base + path, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, (e.read() if hasattr(e, "read") else b"")


class TestStaticMarkup(unittest.TestCase):
    """Both homepage documents ship the notify forms hidden by default —
    a visitor with card checkout LIVE (or JS off) never sees them."""

    # Homepage-lean-v2 (2026-07-23): the full tiers/checkout/notify block moved
    # off the homepage onto the dedicated /pricing page (web/pricing.html). The
    # homepage now carries only a compact one-line pricing strip → /pricing.
    NOTIFY_IDS = {
        "web/pricing.html": ("notify-pack", "notify-pack50", "notify-personal"),
        "web/v2/index.html": ("notify-pack", "notify-personal"),
    }

    def test_forms_present_and_hidden_by_default(self):
        for rel, ids in self.NOTIFY_IDS.items():
            html = (ROOT / rel).read_text()
            for fid in ids:
                with self.subTest(page=rel, form=fid):
                    marker = f'id="{fid}"'
                    self.assertIn(marker, html, f"{rel} lost the {fid} form")
                    # The form tag itself must carry `hidden` so it is
                    # invisible until checkout-cta.js decides otherwise.
                    tag = html[html.index(marker) - 200:html.index(marker) + 200]
                    self.assertIn("hidden", tag, f"{fid} in {rel} is not hidden by default")

    def test_no_inline_style_or_script_in_notify_block(self):
        """Strict CSP: the notify block must not introduce inline style= or
        onsubmit= handlers — behavior lives in checkout-cta.js only."""
        for rel in self.NOTIFY_IDS:
            html = (ROOT / rel).read_text()
            start = html.index('class="card-notify"')
            block = html[start:start + 800]
            self.assertNotIn("style=", block)
            self.assertNotIn("onsubmit", block)
            self.assertNotIn("onclick", block)

    def test_checkout_cta_js_gates_on_card_charges_flag(self):
        """The reveal/hide mechanism is the SAME flag that lights the card
        buttons: card_charges_enabled. Flipping it true must hide the forms."""
        js = (ROOT / "web" / "checkout-cta.js").read_text()
        self.assertIn("card_charges_enabled === true", js)
        # Notify forms follow the negation of that flag.
        for fid, tier in (("notify-pack", "card_pack"),
                          ("notify-pack50", "card_pack50"),
                          ("notify-personal", "card_personal")):
            self.assertIn(f'wireNotify("{fid}", "{tier}", !ok)', js)
        # Hidden state is driven by the flag: shown only when !ok.
        self.assertIn("form.hidden = !show", js)
        # Capture posts to the existing waitlist endpoint — no new API surface.
        self.assertIn('"/api/waitlist"', js)

    def test_cache_busted_asset_versions(self):
        """Changed assets must carry bumped ?v= or Cloudflare serves stale 24h."""
        # Homepage-lean-v2: checkout-cta.js + the tiers block moved to /pricing.
        # 2026-08-05: index.css -> v=21 on EVERY page that loads it (mobile
        # sleekness pass + the [hidden] fix). Versions had drifted per page
        # (v=16/19/20), which defeats the bump: a page left behind keeps
        # serving stale CSS for up to 24h from the CDN. The loop below is the
        # drift guard — it is the part that actually prevents a recurrence.
        index = (ROOT / "web" / "index.html").read_text()
        self.assertIn("/index.css?v=21", index)
        pricing = (ROOT / "web" / "pricing.html").read_text()
        self.assertIn("/checkout-cta.js?v=4", pricing)
        self.assertIn("/index.css?v=21", pricing)
        # No page may still reference a stale index.css version.
        for page in (ROOT / "web").rglob("*.html"):
            body = page.read_text()
            if "/index.css?v=" in body:
                self.assertIn("/index.css?v=21", body,
                              f"{page.name} loads a stale index.css version")
        v2 = (ROOT / "web" / "v2" / "index.html").read_text()
        self.assertIn("/checkout-cta.js?v=4", v2)
        self.assertIn("/v2/style.css?v=8", v2)


class TestWaitlistCardTiers(unittest.TestCase):
    def test_card_tiers_are_allowed_interests(self):
        saved = _evict()
        tmp = tempfile.TemporaryDirectory()
        old = os.environ.get("ORPHO_WAITLIST")
        os.environ["ORPHO_WAITLIST"] = str(Path(tmp.name) / "waitlist.jsonl")
        try:
            import waitlist
            for tier in ("card_pack", "card_pack50", "card_personal"):
                self.assertIn(tier, waitlist.ALLOWED_INTERESTS)
                self.assertTrue(waitlist.add(f"buyer+{tier}@example.com", tier))
            rows = [json.loads(l) for l in
                    Path(os.environ["ORPHO_WAITLIST"]).read_text().splitlines()]
            self.assertEqual([r["interest"] for r in rows],
                             ["card_pack", "card_pack50", "card_personal"])
            for r in rows:
                self.assertIn("@example.com", r["email"])
                self.assertTrue(r["ts"])  # ISO timestamp recorded
        finally:
            tmp.cleanup()
            if old is None:
                os.environ.pop("ORPHO_WAITLIST", None)
            else:
                os.environ["ORPHO_WAITLIST"] = old
            _restore(saved)


class TestCaptureEndpoint(unittest.TestCase):
    """HTTP-level: happy path + rate limit on the reused /api/waitlist."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_env = {k: os.environ.get(k) for k in (
            "ORPHO_DATA_DIR", "ORPHO_WAITLIST", "HOST", "PORT",
            "ORPHO_COOKIE_SECURE", "RATE_LIMIT_PER_DAY",
        )}
        os.environ["ORPHO_DATA_DIR"] = cls._tmp.name
        os.environ["ORPHO_WAITLIST"] = str(Path(cls._tmp.name) / "waitlist.jsonl")
        os.environ["HOST"] = "127.0.0.1"
        os.environ["PORT"] = "0"
        os.environ["ORPHO_COOKIE_SECURE"] = "0"
        os.environ["RATE_LIMIT_PER_DAY"] = "3"
        cls._saved = _evict()
        import app
        from http.server import ThreadingHTTPServer
        cls._app = app
        cls._server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls._base = f"http://127.0.0.1:{cls._server.server_address[1]}"
        threading.Thread(target=cls._server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._server.server_close()
        cls._tmp.cleanup()
        _restore(cls._saved)
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        # Fresh per-IP bucket per test — the limiter is process-global.
        self._app._anchor_limiter._buckets.clear()
        p = Path(os.environ["ORPHO_WAITLIST"])
        if p.exists():
            p.unlink()

    def _rows(self):
        p = Path(os.environ["ORPHO_WAITLIST"])
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    def test_happy_path_records_email_tier_ts(self):
        status, body = _post_json(self._base, "/api/waitlist",
                                  {"email": "buyer@example.com", "interest": "card_pack50"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body).get("ok"))
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["email"], "buyer@example.com")
        self.assertEqual(rows[0]["interest"], "card_pack50")
        self.assertTrue(rows[0]["ts"])

    def test_invalid_email_accepted_but_not_recorded(self):
        # Endpoint answers 200 either way (no address-validity oracle) but
        # writes nothing for garbage input.
        status, _ = _post_json(self._base, "/api/waitlist",
                               {"email": "not-an-email", "interest": "card_pack"})
        self.assertEqual(status, 200)
        self.assertEqual(self._rows(), [])

    def test_rate_limited_after_capacity(self):
        for i in range(3):  # RATE_LIMIT_PER_DAY=3 shared with the waitlist key
            status, _ = _post_json(self._base, "/api/waitlist",
                                   {"email": f"b{i}@example.com", "interest": "card_pack"})
            self.assertEqual(status, 200, f"request {i} should pass")
        status, body = _post_json(self._base, "/api/waitlist",
                                  {"email": "b4@example.com", "interest": "card_pack"})
        self.assertEqual(status, 429)
        self.assertIn("retry_after_seconds", json.loads(body))
        self.assertEqual(len(self._rows()), 3, "429'd request must not be recorded")


if __name__ == "__main__":
    unittest.main()
