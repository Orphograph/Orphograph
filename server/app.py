#!/usr/bin/env python3
"""app.py — stdlib HTTP server for orphograph.

Endpoints:
    GET  /                       — serve web/index.html
    GET  /<asset>                — serve any file under web/
    POST /api/anchor             — body: {"hash_hex": "<64 hex>", "client_label": "optional"}
    GET  /api/receipt/<id>       — return receipt JSON
    GET  /api/verify/<id>        — re-check the receipt locally
    GET  /api/health             — liveness
    GET  /api/stats              — public marketing metrics (counts only, no PII)

Loopback only by default (127.0.0.1). Override with HOST env var for testing.
"""
from __future__ import annotations

import csv
import io
import json
import math
import mimetypes
mimetypes.add_type("font/woff2", ".woff2")  # serve self-hosted fonts with correct type (X-Content-Type-Options: nosniff is set)
import os
import posixpath
import re
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote_plus

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402
import acceptance_hook  # noqa: E402
import affiliate  # noqa: E402
import analytics  # noqa: E402
import api_keys  # noqa: E402
import blog  # noqa: E402
import og_svg  # noqa: E402
import public_config  # noqa: E402
import badge_svg  # noqa: E402
import credits  # noqa: E402
import lightning  # noqa: E402
import auth  # noqa: E402
import gdpr  # noqa: E402
import health  # noqa: E402
import mailer  # noqa: E402
import merkle  # noqa: E402
# Optional module — additive signature feature. MUST NOT crash app startup if
# its Ed25519 backend is missing in the build. Set to None on failure so
# dependent code paths feature-flag themselves off.
try:
    import manifest_signature  # noqa: E402
except Exception as _e:  # noqa: BLE001
    sys.stderr.write(f"[startup] manifest_signature unavailable: {_e}\n")
    manifest_signature = None  # type: ignore[assignment]
import stats  # noqa: E402
import stripe_api  # noqa: E402
import stripe_webhook  # noqa: E402
import nowpayments_api  # noqa: E402
import nowpayments_webhook  # noqa: E402
import subscriptions  # noqa: E402
import teams  # noqa: E402
import unsubscribe  # noqa: E402
import waitlist  # noqa: E402
import webhooks  # noqa: E402
# Optional module — vertical landing pages. MUST NOT crash app startup if a
# YAML backend is missing in the build. None disables /verticals/* routes.
try:
    import verticals  # noqa: E402
except Exception as _e:  # noqa: BLE001
    sys.stderr.write(f"[startup] verticals unavailable: {_e}\n")
    verticals = None  # type: ignore[assignment]
try:
    import payout_monitor  # noqa: E402
except ImportError:  # pragma: no cover
    payout_monitor = None  # type: ignore
from http.cookies import SimpleCookie  # noqa: E402
from rate_limit import TokenBucket, truncate_ip  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8989"))

MAX_BODY_BYTES = 4096  # we only accept tiny JSON payloads
MAX_WEBHOOK_BODY_BYTES = 256 * 1024
REQUEST_TIMEOUT_SEC = 30
MAX_BATCH_BODY_BYTES = 64 * 1024
MAX_BATCH_ITEMS = 50
# Folder-anchor manifests carry one entry per file. 8 MiB ≈ 50K files at
# ~150 bytes/leaf (path + 64-hex file digest + 64-hex leaf hex + size).
# Larger folders are a v2 problem (paginated/chunked upload).
MAX_FOLDER_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_FOLDER_LEAVES = 50_000
RECEIPT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # secrets.token_urlsafe(12) shape


def _lineage_section_html(rid: str, lineage) -> str:
    """Server-rendered "Version history" section for /r/<rid> (edit-lineage).

    Returns "" unless the receipt carries a committed lineage block whose
    fields re-validate against the receipt-id / lowercase-hex alphabets.
    Defense in depth: the values were validated at anchor time, but they are
    being placed into HTML here, so re-check before injection — both
    alphabets are HTML-inert, so no escaping pass is needed (same argument
    as the {{RECEIPT_ID}} substitution below). CSP-safe: markup only, no
    inline style or script; all styling lives in web/receipt.css.
    """
    if not isinstance(lineage, dict) or not lineage.get("committed"):
        return ""
    parent_id = lineage.get("parent_receipt_id")
    parent_root = lineage.get("parent_root")
    if not isinstance(parent_id, str) or not RECEIPT_ID_RE.match(parent_id):
        return ""
    if not engine._is_hex(parent_root, 64):
        return ""
    parent_found = (engine.RECEIPTS_DIR / parent_id / "receipt.json").exists()
    if parent_found:
        # /r/<id> is the canonical receipt URL for both kinds — folder
        # parents forward to their /certificate view client-side.
        parent_node = f'<a class="mono lineage-id" href="/r/{parent_id}">{parent_id}</a>'
        parent_note = ""
    else:
        parent_node = f'<span class="mono lineage-id">{parent_id}</span>'
        parent_note = (
            '  <p class="muted small lineage-missing">The parent receipt does not '
            "resolve on this server; its committed root is shown above.</p>\n"
        )
    root_short = f"{parent_root[:10]}&hellip;{parent_root[-6:]}"
    return (
        '<section id="version-history" class="version-history needs-record" '
        'aria-label="Version history">\n'
        "  <h2>Version history</h2>\n"
        '  <p class="lineage-chain">\n'
        '    <span class="lineage-hop"><span class="lineage-hop-label">This version</span>'
        f'<span class="mono lineage-id">{rid}</span></span>\n'
        '    <span class="lineage-arrow" aria-hidden="true">&#8594;</span>\n'
        '    <span class="lineage-hop"><span class="lineage-hop-label">Parent</span>'
        f"{parent_node}</span>\n"
        "  </p>\n"
        '  <p class="muted small lineage-root-line">Parent anchored root '
        f'<code class="mono lineage-root" title="{parent_root}">{root_short}</code></p>\n'
        f"{parent_note}"
        '  <p class="muted small lineage-scope">Lineage shows only that this '
        "version&#8217;s anchored root committed to the parent&#8217;s anchored root. "
        "It does not show what changed, when the edit happened, or who made it.</p>\n"
        "</section>"
    )
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
EMAIL_RE = re.compile(r"^[^@\s,]{1,64}@[^@\s,]{1,255}$")


def _utf8_encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
COOKIE_SECURE = os.environ.get("ORPHO_COOKIE_SECURE", "1") != "0"
TRUST_PROXY_HEADERS = os.environ.get("ORPHO_TRUST_PROXY_HEADERS", "0") == "1"
# Platform-set real-client-IP header. Fly.io sets `Fly-Client-IP` to the true
# connecting IP at its edge; unlike X-Forwarded-For, the client cannot forge it.
# Used in preference to XFF for rate-limit bucketing so the limiter can't be
# bypassed by rotating a client-supplied XFF value.
REAL_IP_HEADER = os.environ.get("ORPHO_REAL_IP_HEADER", "Fly-Client-IP")
# CDN-set real-client-IP header. Cloudflare sets `CF-Connecting-IP` to the true
# visitor IP at ITS edge. This matters because the site is fronted by
# Cloudflare: the connection Fly.io sees originates from a Cloudflare egress
# node, so BOTH the socket peer AND `Fly-Client-IP` are Cloudflare-owned
# addresses, not the visitor's. Cloudflare rotates egress per request, so one
# visitor's successive events land in several different /24s. Used for the
# funnel-analytics ip_trunc field only — see `_resolve_analytics_ip`.
CDN_CLIENT_IP_HEADER = os.environ.get("ORPHO_CDN_CLIENT_IP_HEADER", "CF-Connecting-IP")


def _resolve_analytics_ip(cdn_ip_value: str, xff_value: str, peer_addr: str,
                          trust_proxy: bool) -> tuple[str, str]:
    """Pick the visitor IP for funnel analytics. Pure (no I/O) for testing.

    Returns (ip, source) where source is one of "cf" | "xff" | "socket" so
    every logged row self-declares where its address came from. Precedence:

      1. CF-Connecting-IP  — Cloudflare's true-visitor header ("cf")
      2. first (leftmost) X-Forwarded-For entry — the original client as
         recorded by the first proxy in the chain ("xff")
      3. the socket peer address — last resort ("socket")

    Deliberately DIFFERENT from `_resolve_peer_ip`, which serves rate-limit
    bucketing and therefore takes the RIGHTMOST XFF entry (the leftmost token
    is client-supplied and rotating it would mint unlimited fresh limiter
    buckets). Analytics wants the most accurate available *identity* rather
    than the least forgeable one, so it reads leftmost. The tradeoff is
    accepted knowingly: these values are advisory counts, never an
    authorization or throttling input.

    The `trust_proxy` gate is inherited from `_resolve_peer_ip` — with no
    trusted proxy in front, client-supplied headers are ignored entirely and
    the row records ("socket"). Production sets ORPHO_TRUST_PROXY_HEADERS=1.

    Caller truncates; this function never returns a truncated value.
    """
    peer = peer_addr or ""
    if not trust_proxy:
        return peer, "socket"
    cdn = (cdn_ip_value or "").strip()
    if cdn:
        return cdn, "cf"
    parts = [p.strip() for p in (xff_value or "").split(",") if p.strip()]
    if parts:
        return parts[0], "xff"
    return peer, "socket"


def _resolve_peer_ip(real_ip_value: str, xff_value: str, peer_addr: str,
                     trust_proxy: bool) -> str:
    """Pick the client IP for rate-limit bucketing. Pure (no I/O) for testing.

    When behind a trusted proxy, prefer the platform real-IP header
    (Fly-Client-IP) which the client cannot forge. Otherwise fall back to the
    RIGHTMOST X-Forwarded-For entry: proxies APPEND the true connecting IP to
    the right of any client-supplied value, so the leftmost token is
    attacker-controlled and using it would let a client rotate XFF to mint
    unlimited fresh rate-limit buckets. With no trusted proxy, only the real
    socket peer counts (client-supplied headers must never bypass limits).
    """
    peer = peer_addr or ""
    if not trust_proxy:
        return peer
    real = (real_ip_value or "").strip()
    if real:
        return real
    parts = [p.strip() for p in (xff_value or "").split(",") if p.strip()]
    return parts[-1] if parts else peer
ALLOW_UNSIGNED_WEBHOOK_PROBE = os.environ.get("ORPHO_ALLOW_UNSIGNED_WEBHOOK_PROBE", "0") == "1"
ALLOWED_STATIC_SUFFIXES = {
    ".html", ".css", ".js", ".svg", ".png", ".ico", ".webmanifest",
    ".json", ".txt", ".ots",  # sample receipt assets under web/sample/
    ".py", ".md", ".tar", ".gz",  # self-hosted OSS verifier under web/verify/
    ".zip",  # offline verifier kit under web/dist/orphograph-verify.zip
    ".xml",  # sitemap.xml for SEO discoverability
    ".woff2",  # self-hosted OFL fonts under web/fonts/ (Fraunces / Spectral / IBM Plex Mono)
}

# 10 anchors/hour/IP by default; refills at 10/3600 = ~0.00278 tokens/sec
# Free-tier rate limit, server-clock-enforced.
#
# Default: 3 anchors per 24h rolling window per IP-prefix bucket.
# Implementation: token bucket with capacity=3 tokens and refill=3/86400 per
# second. The user gets 3 anchors per UTC day on average; a fresh token drops
# in roughly every 8 hours. Because the server uses its own monotonic clock,
# clients cannot bypass the limit by changing their device's time zone or
# system clock — the bucket lives on the server, not the browser.
#
# Operators can override by setting RATE_LIMIT_PER_DAY (preferred) or
# RATE_LIMIT_PER_HOUR (legacy; multiplied by 24 if set).
_legacy_per_hour = os.environ.get("RATE_LIMIT_PER_HOUR")
if _legacy_per_hour and not os.environ.get("RATE_LIMIT_PER_DAY"):
    _per_day_default = str(int(_legacy_per_hour) * 24)
else:
    _per_day_default = "3"
ANCHOR_RATE_CAPACITY = int(os.environ.get("RATE_LIMIT_PER_DAY", _per_day_default))
ANCHOR_RATE_REFILL = ANCHOR_RATE_CAPACITY / 86400.0
ANCHOR_RATE_WINDOW_LABEL = "24h"
# Durability threshold for the `low_redundancy` flag. Counted in DISTINCT
# upstream calendars (engine.distinct_calendars), NOT in server
# acknowledgements: a.pool and b.pool are aggregators for alice and bob, so
# a.pool + alice + b.pool is three acknowledgements resting on two calendars
# under one operator. Default 3 is unchanged in value and stricter in meaning
# — with four distinct calendars of which two (alice, bob) share an operator,
# any three distinct calendars necessarily span at least two operators.
# Maximum meaningful value is engine.CALENDARS_DISTINCT_TOTAL (4); a higher
# setting flags every receipt.
MIN_CALENDARS_OK = int(os.environ.get("MIN_CALENDARS_OK", "3"))
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
NOWPAYMENTS_IPN_SECRET = os.environ.get("NOWPAYMENTS_IPN_SECRET", "")
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
RATE_LIMIT_SNAPSHOT = Path(os.environ.get(
    "ORPHO_RATE_LIMIT_SNAPSHOT", str(DATA_DIR / "rate_limit_state.json")
))
# Admin operational toggles (set to "1" to enable)
ORPHO_MAINTENANCE_MODE = os.environ.get("ORPHO_MAINTENANCE_MODE", "0") == "1"
ORPHO_DISABLE_CHECKOUT = os.environ.get("ORPHO_DISABLE_CHECKOUT", "0") == "1"
ORPHO_DISABLE_ANCHORING = os.environ.get("ORPHO_DISABLE_ANCHORING", "0") == "1"

_anchor_limiter = TokenBucket(
    ANCHOR_RATE_CAPACITY,
    ANCHOR_RATE_REFILL,
    snapshot_path=RATE_LIMIT_SNAPSHOT,
)

# /api/anchor_folder reads and re-hashes up to 8 MB / 50,000 leaves before it
# can tell a valid manifest from a bad one, and validation now comes before
# any charge, so the charge can no longer be what bounds that work. Two
# things do. A rejected manifest spends a per-address budget of its own (a
# valid one spends nothing here), checked before the body is read. And one
# address has one folder request in flight at a time: one 8 MB manifest
# peaks near 15 MB on a 512 MB machine, and 25 concurrent corrupt manifests
# from one fresh address reached 369 MB before this bound. Per address, not
# a global slot count, so a slow upload can only hold up its own sender.
FOLDER_REJECT_CAPACITY = 10
FOLDER_REJECT_REFILL = 10 / 3600.0  # then one every 6 minutes
_folder_reject_limiter = TokenBucket(FOLDER_REJECT_CAPACITY, FOLDER_REJECT_REFILL)
_folder_in_flight: set[str] = set()
_folder_in_flight_lock = threading.Lock()

# Read-only status lookups the buyer's own page makes after paying:
# web/pay/success.js polls /api/nowpayments/order/<id> up to 6 times, and
# web/buy.js asks /api/stripe/session once per load. Both used to draw on
# _anchor_limiter, whose budget is 3 per day per IP prefix, so the 4th poll
# answered 429 for ~8 hours and a payment credited after the 3rd poll never
# showed as confirmed (measured in production 2026-09-22: 200,200,200,429).
# The ids are unguessable, so this bucket only has to stop a tight loop, not
# ration a day. In-memory: a restart refilling it is harmless.
# Keyed per IP prefix (a /24), so buyers behind one shared mobile NAT share it:
# 60 covers ten success pages polling 6 times each.
STATUS_RATE_CAPACITY = 60
STATUS_RATE_REFILL = 60 / 3600.0  # burst 60, then one a minute
_status_limiter = TokenBucket(STATUS_RATE_CAPACITY, STATUS_RATE_REFILL)
# /api/stripe/session makes one live Stripe read per well-formed id, and the
# account's Stripe read limit (100/s) is shared with checkout and the webhook.
# A buyer loads the page a handful of times, so the burst stays small: at 5,
# twenty prefixes are needed to reach Stripe's per-second limit even briefly
# (the old 3/day bucket needed 34). No ceiling shared across callers: one
# would let a few prefixes lock every buyer out.
SESSION_LOOKUP_CAPACITY = 5
SESSION_LOOKUP_REFILL = 5 / 3600.0
_session_lookup_limiter = TokenBucket(SESSION_LOOKUP_CAPACITY, SESSION_LOOKUP_REFILL)

# Funnel-event limiter: 60 events / IP / minute. Separate bucket so noisy
# analytics traffic can't burn the anchor-rate budget (and vice versa).
# Cookieless: keyed by truncated IP only. In-memory only (no snapshot) —
# analytics is best-effort and a restart resetting the bucket is fine.
EVENT_RATE_CAPACITY = 60
EVENT_RATE_REFILL = 60 / 60.0  # 1 token/sec refill, burst 60
_event_limiter = TokenBucket(EVENT_RATE_CAPACITY, EVENT_RATE_REFILL)

# Founder-token brute-force limiter (2026-07-18 latent-security pass).
# Counts FAILED X-Orpho-Founder guesses per truncated client IP; a correct
# token never consumes, so the legitimate founder sees zero behavior change.
# After 20 failed guesses the gate stops comparing entirely (still answers
# 404, identical to a wrong token) until the bucket refills — 1 attempt
# every 3 minutes. In-memory like _event_limiter: a restart resetting the
# quota is acceptable for a defense-in-depth control.
FOUNDER_FAIL_CAPACITY = 20
FOUNDER_FAIL_REFILL = 20 / 3600.0  # full recovery in 1 hour
_founder_fail_limiter = TokenBucket(FOUNDER_FAIL_CAPACITY, FOUNDER_FAIL_REFILL)

# Global (all-IPs) founder-failure counter (funnel-hygiene pass). The per-IP
# bucket above is keyed on truncated client IP, so an attacker rotating source
# addresses mints a fresh 20-guess budget per /24. This second bucket counts
# every failed guess regardless of origin: once FOUNDER_GLOBAL_FAIL_CAPACITY
# failures accumulate across the whole fleet, the gate refuses (identical 404)
# until refill. Same failures-only semantics — a correct token never consumes,
# so the founder is only impacted while an active spray is underway.
FOUNDER_GLOBAL_FAIL_CAPACITY = 100
FOUNDER_GLOBAL_FAIL_REFILL = 100 / 3600.0  # full recovery in 1 hour
_founder_fail_global_limiter = TokenBucket(FOUNDER_GLOBAL_FAIL_CAPACITY, FOUNDER_GLOBAL_FAIL_REFILL)
_FOUNDER_GLOBAL_FAIL_KEY = "founder-fail:GLOBAL"

# Allowlist for the funnel events the client actually emits. Any value outside
# this set is rejected with 400. This set MUST stay in sync with every
# track()/orphoEvent() call in web/**/*.js — tests/test_funnel_event_whitelist.py
# greps the client JS and fails if a client-emitted name is missing here (that
# gap is exactly what silently dropped the homepage funnel before this fix).
# The stored row is always {ts, event, page, ip_trunc}; the event name is a
# fixed vocabulary label, so adding names here cannot widen what is persisted.
FUNNEL_EVENTS = frozenset({
    # page + section visibility
    "page_view",           # any page load (homepage, LP); page= distinguishes
    "drop_zone_visible",   # #drop scrolled into view (homepage)
    # anchor funnel
    "anchor_start",        # user began an anchor (cream/app.js homepage)
    "anchor_done",         # anchor completed (cream/app.js homepage)
    "file_anchored",       # anchor completed (dark/v2.js homepage)
    # purchase / pricing intent
    "buy_pack_click",      # clicked buy-pack CTA
    "buy_personal_click",  # clicked buy-personal CTA
    "billing_toggle",      # toggled monthly/annual pricing
    "pack_waitlist_join",  # joined a pack waitlist
    # /lp/agent-receipts demand instrument (2026-08-19). BOTH outcomes are
    # listed: a submit that never lands is the one event a demand meter must
    # not silently drop, because a missing failure reads as absent interest.
    "lp_notify_submit",    # waitlist capture accepted on the agent-receipts LP
    "lp_notify_error",     # waitlist capture attempted and failed
    "checkout_clicked",    # checkout initiated (v2)
    "checkout_error",      # checkout attempt errored (v2) — funnel-loss signal
    "checkout_returned_success",  # returned from Stripe success
    # engagement / secondary CTAs
    "try_sample_click",    # clicked "try a sample"
    "verify_sample_click", # clicked "verify sample"
    "share_link_click",    # clicked a share link
    "lp_cta_clicked",      # landing-page CTA click
    # scroll-depth telemetry (native, cookieless — see web/assets/scroll-depth.js)
    "scroll_25",           # visitor scrolled past 25% of the page (fires once)
    "scroll_50",           # visitor scrolled past 50% of the page (fires once)
    "scroll_75",           # visitor scrolled past 75% of the page (fires once)
    "scroll_100",          # visitor reached the bottom of the page (fires once)
})
FUNNEL_EVENT_FIELDS = frozenset({"event", "page"})
FUNNEL_EVENTS_PATH = DATA_DIR / "events.jsonl"
MAX_EVENT_PAGE_LEN = 256

# ── homepage A/B experiment (cream "/" vs dark "/v2" document) ──────────────
# ORPHO_AB_HOME = fraction of first-time human visitors who get the dark
# document served AT "/" (0 = experiment off). Read per-request so ops can
# flip it via env without code changes and tests can toggle it. Assignment is
# sticky via a 1-bit HttpOnly cookie; bots always get cream (SEO stability).
# Measurement is entirely server-side into data/ab_home.jsonl — the funnel
# collector's privacy contract (no cookies recorded) stays untouched.
AB_HOME_COOKIE = "orpho_ab_home"
AB_LOG_PATH = DATA_DIR / "ab_home.jsonl"
# The ledger shares the data volume with receipts and the credit ledger, and
# any client can forge the arm cookie, so it stops growing here.
AB_LOG_MAX_BYTES = 16 * 1024 * 1024
# The checkout page itself. Its .css/.js, redirect hops and 404 neighbours are
# not a person reaching checkout.
AB_CHECKOUT_PATHS = frozenset({"/pay/crypto", "/pay/crypto/"})
_AB_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|bingpreview|facebookexternalhit|twitterbot|"
    r"linkedinbot|whatsapp|telegram|lighthouse|headless|python-urllib|"
    r"python-requests|curl|wget",
    re.I,
)


def _subscription_active_for(email: str | None) -> bool:
    """True if `email` has an active subscription directly OR via team membership.

    Team members inherit their team owner's subscription benefits. This single
    helper centralizes the inheritance so endpoints stay readable.
    """
    if not email:
        return False
    if subscriptions.is_active(email):
        return True
    owner = teams.owner_email_for(email)
    if owner and owner != email and subscriptions.is_active(owner):
        return True
    return False


class _HeadBodySuppressor:
    """Forward the header block to the real socket, swallow the body.

    Used only by do_HEAD. `suppress` flips to True the moment end_headers()
    has flushed, so status line and headers reach the client verbatim and
    every subsequent write (the body GET would have sent) is dropped. The
    reported byte count stays truthful to the caller so writers that check
    it — shutil.copyfileobj and friends — behave normally.
    """

    def __init__(self, wfile) -> None:
        self._wfile = wfile
        self.suppress = False

    def write(self, data):
        if self.suppress:
            return len(data) if data is not None else 0
        return self._wfile.write(data)

    def __getattr__(self, name):
        return getattr(self._wfile, name)


def _security_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
    handler.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    # Deny powerful browser features the site never uses. clipboard-write is
    # deliberately NOT in this deny list: the copy-to-clipboard buttons on the
    # receipt/account/pay pages depend on navigator.clipboard (defaults to
    # self-allowed). camera/microphone/geolocation/usb/payment are unused by any
    # page (verified: no getUserMedia / BarcodeDetector / geolocation in web/).
    handler.send_header(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), usb=(), payment=(), "
        "accelerometer=(), gyroscope=(), magnetometer=()",
    )
    handler.send_header(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    # CORS: /api/verify/* and the embeddable badge SVG are intentionally
    # cross-origin-readable. Both surface only public-receipt fields that
    # are already publicly reachable via /r/<id> and /api/badge/<id>.svg,
    # so adding Access-Control-Allow-Origin: * does not change the
    # disclosure surface. Required for the embeddable widget at
    # /badge.html to function on third-party sites.
    try:
        rpath = getattr(handler, "path", "") or ""
        if (
            rpath.startswith("/api/verify/")
            or rpath.startswith("/api/verify_folder/")
            or rpath.startswith("/api/badge/")
            or rpath == "/api/inclusion_proof"
        ):
            handler.send_header("Access-Control-Allow-Origin", "*")
            handler.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            handler.send_header("Access-Control-Max-Age", "86400")
    except Exception:
        pass


def _read_content_length(handler: BaseHTTPRequestHandler) -> int:
    raw = handler.headers.get("Content-Length", "0") or "0"
    try:
        n = int(raw)
    except ValueError:
        return -1
    return n if n >= 0 else -1


# ── compression + caching helpers ──────────────────────────────────────

_COMPRESSIBLE_PREFIXES = (
    "text/", "application/json", "application/xml",
    "application/atom+xml", "application/javascript",
    "image/svg+xml",
)
_COMPRESS_THRESHOLD_BYTES = 512


def _client_accepts_gzip(handler: BaseHTTPRequestHandler) -> bool:
    enc = handler.headers.get("Accept-Encoding", "")
    return "gzip" in (token.strip().split(";")[0] for token in enc.split(","))


def _maybe_compress(
    handler: BaseHTTPRequestHandler,
    body: bytes,
    content_type: str,
) -> tuple[bytes, str | None]:
    """If the client accepts gzip and the payload is large enough + compressible,
    return (gzipped_body, "gzip"). Otherwise return (body, None)."""
    if len(body) < _COMPRESS_THRESHOLD_BYTES:
        return body, None
    if not any(content_type.startswith(p) for p in _COMPRESSIBLE_PREFIXES):
        return body, None
    if not _client_accepts_gzip(handler):
        return body, None
    import gzip
    compressed = gzip.compress(body, compresslevel=6)
    # If gzip didn't actually shrink it (rare for tiny payloads), skip.
    if len(compressed) >= len(body):
        return body, None
    return compressed, "gzip"


def _weak_etag(*, mtime: float, size: int) -> str:
    """Weak ETag from (mtime, size) — good enough for static files we serve."""
    import hashlib
    h = hashlib.sha256(f"{mtime}:{size}".encode()).hexdigest()[:16]
    return f'W/"{h}"'


def _reject_private(handler: BaseHTTPRequestHandler, pack_consumed: bool,
                    pack_token: str | None) -> None:
    """Decline an anchor that asked for `private` we cannot grant.

    Both anchor endpoints used to compute `private and subscription_active`,
    which SILENTLY PUBLISHED a receipt the caller had explicitly asked to keep
    private. No error, no warning, and on the folder path the response did not
    even report the resulting privacy state, so the caller could not tell.

    Found live: the founder's own daily repo anchor sends `"private": True`
    (scripts/auto_anchor_repo.py) and its launchd job passes no API key, so
    every one of those anchors has been public.

    Publishing is not undoable. Retrying without `private` costs the caller
    one line. So this fails closed: no anchor is created, any pack credit is
    refunded, and the response says exactly what happened and how to proceed.
    """
    if pack_consumed and pack_token:
        credits.refund_credit(pack_token)
    _json_response(handler, 402, {
        "error": "private anchors require an active subscription",
        "detail": (
            "This request asked for private: true, and this caller is not "
            "subscription-authenticated. Rather than publish a receipt you "
            "asked to keep private, no anchor was created and nothing was "
            "submitted to the calendars."
        ),
        "how_to_proceed": (
            "Authenticate with an active subscription (session cookie or "
            "X-Orpho-Api-Key), or resend without `private` to anchor publicly."
        ),
        "private_requested": True,
        "private_granted": False,
        "credit_refunded": bool(pack_consumed),
    })


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, indent=2).encode("utf-8")
    ctype = "application/json; charset=utf-8"
    body, enc = _maybe_compress(handler, body, ctype)
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    if enc:
        handler.send_header("Content-Encoding", enc)
        handler.send_header("Vary", "Accept-Encoding")
    _security_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def _send_html(handler: BaseHTTPRequestHandler, status: int, html_body: str) -> None:
    body = html_body.encode("utf-8")
    ctype = "text/html; charset=utf-8"
    body, enc = _maybe_compress(handler, body, ctype)
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "public, max-age=300")
    if enc:
        handler.send_header("Content-Encoding", enc)
        handler.send_header("Vary", "Accept-Encoding")
    _security_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def _send_xml(handler: BaseHTTPRequestHandler, status: int, xml_body: str, *, content_type: str = "application/xml; charset=utf-8") -> None:
    body = xml_body.encode("utf-8")
    body, enc = _maybe_compress(handler, body, content_type)
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "public, max-age=600")
    if enc:
        handler.send_header("Content-Encoding", enc)
        handler.send_header("Vary", "Accept-Encoding")
    _security_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def _sitemap_lastmod(loc: str) -> str:
    """Honest <lastmod>: the served file's mtime, YYYY-MM-DD (UTC).

    Pages resolve to their .html sibling, directory paths to index.html;
    anything unresolvable falls back to the homepage's mtime.
    """
    from datetime import datetime, timezone
    rel = loc.lstrip("/")
    cand = WEB_DIR / (rel + "index.html") if loc.endswith("/") else WEB_DIR / rel
    if not cand.is_file():
        html = WEB_DIR / (rel + ".html")
        cand = html if html.is_file() else WEB_DIR / "index.html"
    ts = cand.stat().st_mtime
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _build_sitemap() -> str:
    site = os.environ.get("SITE_URL", "https://orphograph.com").rstrip("/")
    # Canonical public-URL set — reconciled 2026-07-03 with the (previously
    # drifted) static web/sitemap.xml; a test pins the two in lockstep.
    # Lockstep between lists is not coverage: /pricing and 12 other indexable
    # pages were missing from all of them at once. The list is now also checked
    # against the pages themselves (test_sitemap_covers_every_indexable_page).
    urls: list[tuple[str, str]] = [
        ("/", "1.0"),
        ("/verify/", "0.9"),
        ("/pricing", "0.9"),
        ("/blog/", "0.8"),
        ("/learn", "0.8"),
        ("/dataset-provenance", "0.8"),
        ("/integrations", "0.7"),
        ("/accept", "0.7"),
        ("/standing-record", "0.5"),
        ("/lp/", "0.8"),
        ("/about", "0.7"),
        ("/about-the-office", "0.5"),
        ("/faq", "0.7"),
        ("/writers", "0.7"),
        ("/what-is-this", "0.6"),
        ("/anchor-output", "0.6"),
        ("/verify-js", "0.7"),
        ("/lp/prove-photo-pre-ai", "0.7"),
        ("/lp/bitcoin-timestamp-file", "0.7"),
        ("/lp/c2pa-alternative", "0.7"),
        ("/lp/opentimestamps-explained", "0.7"),
        ("/lp/wedding-photographer-proof", "0.7"),
        ("/lp/manuscript-priority-date", "0.7"),
        ("/lp/screenshot-evidence-timestamp", "0.7"),
        ("/lp/ai-image-detector-vs-provenance", "0.7"),
        ("/lp/eu-ai-act-training-data", "0.7"),
        ("/lp/agent-receipts", "0.7"),
        ("/method", "0.6"),
        ("/method/architecture", "0.6"),
        ("/method/bitcoin-attestation", "0.6"),
        ("/method/evidence-law", "0.6"),
        ("/method/folder-merkle", "0.6"),
        ("/method/legal-recognition", "0.6"),
        ("/method/the-mit-verifier-annotated", "0.6"),
        ("/method/whitepaper", "0.6"),
        ("/method/why-filenames-are-not-stored", "0.6"),
        ("/docs", "0.7"),
        ("/docs/agents", "0.6"),
        ("/docs/api", "0.6"),
        ("/docs/webhooks", "0.6"),
        ("/docs/cli", "0.6"),
        ("/docs/sdk", "0.6"),
        ("/docs/quickstart", "0.6"),
        ("/docs/install", "0.6"),
        ("/docs/verify", "0.6"),
        ("/stats", "0.6"),
        ("/gift", "0.6"),
        ("/status", "0.5"),
        ("/security", "0.5"),
        ("/continuity", "0.5"),
        ("/ios", "0.5"),
        ("/mcp", "0.5"),
        ("/badge", "0.5"),
        ("/badge-demo", "0.4"),
        ("/press", "0.4"),
        ("/press-kit", "0.4"),
        ("/roadmap", "0.4"),
        ("/changelog", "0.4"),
        ("/access", "0.6"),
        ("/pack", "0.6"),
        ("/construction/", "0.4"),
        ("/listings/", "0.4"),
        ("/matters/", "0.4"),
        ("/workpapers/", "0.4"),
        ("/blog/atom.xml", "0.4"),
        ("/blog/rss.xml", "0.4"),
        ("/signin", "0.3"),
        ("/recover", "0.3"),
        ("/terms", "0.3"),
        ("/privacy", "0.3"),
        ("/legal/", "0.3"),
        ("/legal/aup", "0.3"),
        ("/legal/contact-policy", "0.3"),
        ("/legal/dmca", "0.3"),
        ("/legal/trademark", "0.3"),
        ("/.well-known/security.txt", "0.3"),
        ("/humans.txt", "0.3"),
        ("/sitemap-image.xml", "0.3"),
        ("/press-kit/orphograph-press-kit.zip", "0.3"),
    ]
    # Blog posts publish through two paths — the md router (blog.list_posts)
    # and static long-form HTML files under web/blog/ — and either alone
    # under-lists the sitemap (7 static-only posts were missing before the
    # reconciliation). Union both, deduped, deterministic order.
    blog_slugs = {post["slug"] for post in blog.list_posts()}
    blog_dir = WEB_DIR / "blog"
    if blog_dir.is_dir():
        blog_slugs.update(f.stem for f in blog_dir.glob("*.html") if f.stem != "index")
    for slug in sorted(blog_slugs):
        urls.append((f"/blog/{slug}", "0.7"))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, prio in urls:
        # Emit clean URLs (the canonical form) — strip .html defensively so a
        # future list entry can never reintroduce a .html loc.
        loc = path[:-5] if path.endswith(".html") else path
        lines += [
            "  <url>",
            f"    <loc>{site}{loc}</loc>",
            f"    <lastmod>{_sitemap_lastmod(loc)}</lastmod>",
            f"    <priority>{prio}</priority>",
            "  </url>",
        ]
    lines.append("</urlset>")
    return "\n".join(lines)


def _ascii_word(value: str, extra: str = "_") -> bool:
    """True when every char is an ASCII letter or digit, or one of `extra`.

    str.isalnum() alone accepts any Unicode letter or digit (é, ٣, fullwidth),
    so an id that passed it could still be unsendable in an upstream URL: the
    HTTP client raised before connecting and the handler dropped the socket
    with no response instead of answering 400."""
    return value.isascii() and all(c.isalnum() or c in extra for c in value)


def _json_str(payload: dict, key: str) -> str:
    """payload[key] when it is a string, else "". A JSON body is the client's
    to shape: `{"email": 1}` is valid JSON, and `.strip()` on it raised out of
    the handler, which dropped the connection instead of answering 400.
    Handlers first reject a present field of the wrong type with
    _wrong_type_field, so "" here only ever means absent or null."""
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _wrong_type_field(payload: dict, *keys: str) -> str | None:
    """The first of `keys` present with a non-string, non-null value. Such a
    request answers 400: treating it as absent made {"team_name": 1} create a
    team called "My Team"."""
    for key in keys:
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            return key
    return None


def _ab_fraction() -> float:
    """ORPHO_AB_HOME as a fraction in [0, 1]; 0 (experiment off) when unset or
    unreadable. float() accepts "nan", "inf" and "1e400"; unclamped, the
    homepage split then raised on int(fraction * 10_000) for every visitor."""
    try:
        value = float(os.environ.get("ORPHO_AB_HOME", "0") or 0)
    except ValueError:
        return 0.0
    return min(1.0, max(0.0, value)) if math.isfinite(value) else 0.0


def _ab_counts_this_visitor(handler: BaseHTTPRequestHandler) -> bool:
    """A cookie-attributed A/B write is allowed: the experiment runs and the
    client is not a bot the split never assigns. The arm cookie is forgeable,
    so without this every cookie-bearing request wrote a row."""
    return _ab_fraction() > 0 and not _AB_BOT_RE.search(handler.headers.get("User-Agent", ""))


_AB_CAP_NOTICE = {"sent": False}


def _ab_log(event: str, variant: str, extra: dict | None = None) -> None:
    """Append one experiment record. Best-effort: analytics must never break serving."""
    try:
        from datetime import datetime, timezone
        if AB_LOG_PATH.exists() and AB_LOG_PATH.stat().st_size >= AB_LOG_MAX_BYTES:
            if not _AB_CAP_NOTICE["sent"]:
                _AB_CAP_NOTICE["sent"] = True
                sys.stderr.write(f"[ab] {AB_LOG_PATH.name} reached {AB_LOG_MAX_BYTES} bytes; "
                                 "experiment rows are no longer recorded\n")
            return
        # v2: one row per page view. Rows before it counted the page's own
        # .css/.js and redirect hops too, and were written with the test off.
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "event": event, "variant": variant, "v": 2}
        if extra:
            rec.update(extra)
        with open(AB_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _ab_cookie_variant(handler: BaseHTTPRequestHandler) -> str | None:
    """The visitor's experiment arm, or None when unassigned/invalid."""
    try:
        jar = SimpleCookie()
        jar.load(handler.headers.get("Cookie", ""))
        morsel = jar.get(AB_HOME_COOKIE)
        value = morsel.value if morsel else ""
        return value if value in ("cream", "dark") else None
    except Exception:
        return None


def _serve_ab_home(handler: BaseHTTPRequestHandler) -> bool:
    """Cream-vs-dark homepage split at "/". Returns True when this function
    wrote the response; False → caller falls through to the normal cream
    homepage (experiment off, bot traffic, or unreadable variant document).
    Both arms are served with no-store so shared caches can't bleed arms.
    """
    fraction = _ab_fraction()
    if fraction <= 0:
        return False
    if _AB_BOT_RE.search(handler.headers.get("User-Agent", "")):
        return False
    variant = _ab_cookie_variant(handler)
    newly_assigned = variant is None
    if newly_assigned and handler._is_head():
        # A cookieless HEAD is a probe, not a visitor: no arm, no view, no
        # cookie. It is described with the plain homepage, as bots get. It
        # still carries the headers that make GET uncacheable across arms,
        # so a cache or link checker revalidating from HEAD does not learn a
        # single-representation story that GET contradicts. (Content-Length
        # is the plain page's: which arm a later GET draws is not knowable.)
        # A returning visitor's HEAD is described from their arm below.
        try:
            plain = (WEB_DIR / "index.html").read_bytes()
        except OSError:
            return False
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(plain)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Vary", "Cookie")
        _security_headers(handler)
        handler.end_headers()
        handler.wfile.write(plain)
        return True
    if newly_assigned:
        variant = "dark" if secrets.randbelow(10_000) < int(fraction * 10_000) else "cream"
    # Dark is now the canonical homepage (served as the static index.html); the
    # "cream" arm serves the archived index-cream.html so a re-enabled A/B still
    # compares the cream design against the live dark one.
    doc = WEB_DIR / ("v2/index.html" if variant == "dark" else "index.html")
    try:
        body = doc.read_bytes()
    except OSError:
        return False
    # Log before any response bytes go out, so a client that has received
    # the body can rely on the view record existing (mirrors checkout_view).
    if not handler._is_head():
        _ab_log("home_view", variant, {"new": newly_assigned})
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Vary", "Cookie")
    if newly_assigned:
        cookie = f"{AB_HOME_COOKIE}={variant}; Max-Age=2592000; Path=/; SameSite=Lax; HttpOnly"
        if COOKIE_SECURE:
            cookie += "; Secure"
        handler.send_header("Set-Cookie", cookie)
    _security_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)
    return True


# Files that live under web/ but must never be publicly reachable.
# web/** ships wholesale, so anything dropped in there is served by default —
# internal design mockups and superseded pages included. Audited 2026-07-26:
# /_mockups/{A_pure,B_broadsheet,C_instrument} and /index-legacy were all
# returning 200 to the open internet, carrying claim wording that contradicts
# the canonical pages.
#
# Matched against the path with any .html suffix stripped, because pages are
# served at clean extensionless URLs — /index-legacy and /index-legacy.html are
# the same document and both must 404.
_PRIVATE_PATH_PREFIXES = ("_mockups/",)
_PRIVATE_PATH_EXACT = frozenset({"index-legacy"})


# Withdrawn pages, matched by PREFIX so every path under the withdrawn
# subtree answers Gone -- not just the handful of URLs that were ever
# directly linked. A hand-typed tuple of three exact strings per withdrawal
# (the original form of this check, for /inspection on 2026-09-18 and
# /practice on 2026-09-19) missed the clean-URL siblings _serve_static
# resolves for any subdirectory: /<prefix>/index (extensionless) and
# /<prefix>/index.css (or any other asset that lived beside index.html)
# fell through to the static-file fallback and answered 404, not 410 --
# live on production for /inspection/index and /inspection/index.css since
# the day of that withdrawal (code review finding, 2026-09-19, PR #255).
WITHDRAWN_PATH_PREFIXES = ("/inspection", "/practice")


def _is_withdrawn_path(path: str) -> bool:
    """True if `path` names a withdrawn page or anything under it.

    Exact match OR startswith(prefix + "/"): "/practice", "/practice/",
    "/practice/index", "/practice/index.css" and "/practice//" (a TRAILING
    empty segment under the prefix) are all withdrawn. "/practicex" is a
    different path that merely shares the prefix's characters and is not.
    `path` is expected pre-normalised the way do_GET already normalises it
    (query string stripped); no further normalisation happens here.

    A LEADING double slash ("//practice/") is not handled here and does
    not need to be: stdlib http.server's parse_request() already collapses
    it to a single "/" before self.path is ever set (gh-87389, an
    open-redirect mitigation upstream of this function), so this function
    never sees that shape from a real request.
    """
    return any(path == p or path.startswith(p + "/") for p in WITHDRAWN_PATH_PREFIXES)


def _is_private_path(rel_path: str) -> bool:
    """True if this static path is internal-only and must 404 publicly."""
    # Normalise FIRST: _serve_static resolves the path on disk AFTER this
    # check, so "./_mockups/x" or "a/../_mockups/x" would pass a raw string
    # match and still resolve to the private file. posixpath.normpath collapses
    # both; anything that escapes upward is private by definition.
    p = posixpath.normpath("/" + rel_path.lstrip("/")).lstrip("/")
    if p.startswith(".."):
        return True
    if p.endswith(".html"):
        p = p[: -len(".html")]
    p = p.rstrip("/")
    if p in _PRIVATE_PATH_EXACT:
        return True
    return any(p == pre.rstrip("/") or p.startswith(pre)
               for pre in _PRIVATE_PATH_PREFIXES)


# ─── the retired direct-BTC order rail ──────────────────────────────────────
#
# Founder decision 2026-09-19: the direct on-chain rail (customer sends BTC to
# a per-order address; the office watched mempool.space and minted a claim
# code) is withdrawn. It was never configured in production and never
# processed an order. The code that issued addresses and created orders is
# deleted, not dormant — see tests/test_direct_btc_rail_is_gone.py.
#
# 410 and not 404, for the same reason /inspection says Gone: /buy sat in both
# sitemaps at priority 0.8 and /api/btc-order/<id>/qr.svg was published API
# documentation, so these URLs are in indexes and in third-party code. A
# crawler drops a Gone page and its cached snippet far sooner than a Not Found,
# and a caller reading 410 knows to stop rather than to retry.
#
# NOT retired here, and each a separate decision: OpenTimestamps Bitcoin
# ANCHORING (the product), the L402/Lightning rail (/api/ln/quote — dormant),
# and the hosted crypto processor (/pay/crypto, /api/nowpayments/* — live).
# ASCII ONLY, and this is load-bearing. The reason phrase is written into the
# HTTP status line, which the stdlib encodes latin-1; an em dash here raised
# UnicodeEncodeError inside send_error and the server closed the connection
# with no response at all — a retired route that answers nothing, not 410.
# tests/test_direct_btc_rail_is_gone.py drives every path over the wire, which
# is how this was caught; test_the_reason_phrase_survives_the_status_line
# pins the encoding directly.
_RETIRED_BTC_MESSAGE = "Gone: the direct Bitcoin payment rail was withdrawn"

# NOT RETIRED, and this cost a paying customer a confirmation page once:
# /buy and /buy/<order_id> were TWO URLs on ONE document (web/buy.html).
# Bare /buy is the card buyer's post-Checkout landing — _handle_stripe_checkout
# builds it as Stripe's success_url — and fell through to the static handler.
# Only /buy/<order_id>, matched by the "/buy/" PREFIX, ever belonged to the BTC
# rail. Retiring the bare path put a 410 in front of every card buyer who
# finished paying. Keep /buy, /buy.html, /buy.js and /buy.css out of this set.
_RETIRED_BTC_EXACT = frozenset({
    "/pay/btc", "/pay/btc.html", "/pay/btc.css", "/pay-btc.js",
    "/api/btc/price", "/api/buy-btc", "/api/btc/claim",
    # No-id forms of the order API. Without these they fall to the static
    # handler and answer 404, which tells a caller "wrong URL" rather than
    # "this is gone".
    "/api/btc-order",
})
# Prefix-matched, so every id under them is covered. Spelled WITH the trailing
# slash on purpose: "/buy/" cannot match /buying-guide (that would need
# "/buy"), and it leaves the bare /buy card-confirmation page alone.
_RETIRED_BTC_PREFIXES = ("/buy/", "/api/btc-order/", "/pay/btc/")


def _is_retired_btc_path(path: str) -> bool:
    """True if this path belonged to the withdrawn direct-BTC order rail.

    Normalised the same way _is_private_path normalises, so a dotted or
    doubled-slash spelling of a retired path cannot slip past the guard and
    land on the static handler — /pay/./btc.html resolves to the same document.

    posixpath.normpath STRIPS a trailing slash, so "/buy/" arrives here as
    "/buy" and "/pay/btc/" as "/pay/btc". That is why there are no slashed
    entries in the exact set: they would be unreachable. It also means "/buy/"
    with no id normalises onto the card page and correctly does NOT retire.
    """
    p = posixpath.normpath("/" + path.lstrip("/"))
    if p in _RETIRED_BTC_EXACT:
        return True
    return any(p.startswith(pre) for pre in _RETIRED_BTC_PREFIXES)


def _serve_static(handler: BaseHTTPRequestHandler, rel_path: str) -> None:
    if rel_path in ("", "/"):
        rel_path = "index.html"
    rel_path = rel_path.lstrip("/")
    # Checked BEFORE the redirect and sibling-resolution logic below, so the
    # extensionless form cannot slip past by re-entering through another route.
    if _is_private_path(rel_path):
        handler.send_error(404, "not found")
        return
    target = (WEB_DIR / rel_path).resolve()
    if WEB_DIR not in target.parents and target != WEB_DIR:
        handler.send_error(403, "forbidden")
        return
    # Pages live at clean URLs: a direct GET of /<page>.html permanently
    # redirects to the extensionless form (query string preserved, so old
    # Stripe success URLs like /buy.html?stripe_session=… keep working).
    # Assets never end in .html. /index.html is left alone because the
    # root path is rewritten to it above and cannot be distinguished here.
    # The clean form re-resolves to the same file via the sibling logic
    # below (verified: no <name>.html coexists with <name>/index.html).
    # Gate on the RAW REQUEST path, not rel_path: internal callers (the
    # blog router, dir-index resolution) pass .html rel_paths for clean
    # request URLs — redirecting those loops the clean URL onto itself.
    from urllib.parse import urlparse as _urlparse
    _req_path = _urlparse(getattr(handler, "path", "")).path
    if _req_path.endswith(".html") and rel_path.endswith(".html") and rel_path != "index.html" and target.is_file():
        clean_rel = rel_path[: -len(".html")]
        if clean_rel.endswith("/index"):
            clean_rel = clean_rel[: -len("index")]
        from urllib.parse import urlparse
        query = urlparse(getattr(handler, "path", "")).query
        handler.send_response(301)
        handler.send_header("Location", "/" + clean_rel + (("?" + query) if query else ""))
        handler.send_header("Content-Length", "0")
        _security_headers(handler)
        handler.end_headers()
        return
    # Directory-style paths (e.g. /verify/) → resolve to <dir>/index.html.
    # But a directory with NO index.html that has a sibling <dir>.html
    # (e.g. web/mcp/ alongside web/mcp.html, web/press-kit/ alongside
    # web/press-kit.html) would otherwise 404 the clean URL /mcp. Prefer the
    # .html page in that case; the directory's own files stay reachable at
    # /<dir>/<file>.
    if target.is_dir():
        idx = target / "index.html"
        sibling = target.with_suffix(".html")
        target = idx if idx.is_file() else (sibling if sibling.is_file() else idx)
    # Clean URLs: an extensionless path (/faq, /method/architecture) serves the
    # matching <path>.html. The .html form keeps working too (backward-compatible
    # bookmarks/links); this only triggers when the bare path is not itself a
    # file or dir and a .html sibling exists.
    if target.suffix == "" and not target.exists():
        html_sibling = target.with_suffix(".html")
        if html_sibling.is_file():
            target = html_sibling
    if not target.exists() or not target.is_file():
        handler.send_error(404, "not found")
        return
    # The MIT LICENSE file ships extensionless (web/LICENSE, web/verify/LICENSE)
    # and is linked from footers sitewide + the verifier page; allow it through
    # the suffix allowlist and serve it as plain text below.
    if target.suffix not in ALLOWED_STATIC_SUFFIXES and target.name != "LICENSE":
        handler.send_error(403, "type not allowed")
        return

    # ETag from file mtime + size — weak validator, sufficient for our static set.
    try:
        stat = target.stat()
    except OSError:
        handler.send_error(500, "stat failed")
        return
    etag = _weak_etag(mtime=stat.st_mtime, size=stat.st_size)

    # If-None-Match short-circuit → 304 Not Modified (no body, no Content-Length>0).
    if_none = handler.headers.get("If-None-Match", "")
    if if_none and etag in if_none:
        handler.send_response(304)
        handler.send_header("ETag", etag)
        handler.send_header("Cache-Control", _static_cache_control(target.suffix, rel_path))
        _security_headers(handler)
        handler.end_headers()
        return

    ctype, _ = mimetypes.guess_type(target.name)
    if not ctype and target.name == "LICENSE":
        ctype = "text/plain; charset=utf-8"
    ctype = ctype or "application/octet-stream"
    data = target.read_bytes()
    data, enc = _maybe_compress(handler, data, ctype)

    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", _static_cache_control(target.suffix, rel_path))
    handler.send_header("ETag", etag)
    if enc:
        handler.send_header("Content-Encoding", enc)
        handler.send_header("Vary", "Accept-Encoding")
    _security_headers(handler)
    handler.end_headers()
    handler.wfile.write(data)


def _static_cache_control(suffix: str, rel_path: str = "") -> str:
    """Aggressive caching for binary assets, short caching for HTML.

    HTML can change with each deploy; let browsers revalidate fast.
    Binaries (svg, ico, ots, tar.gz) effectively immutable per filename.

    Brand assets — seal*.png, lockup.png, favicon*, apple-touch-icon-*.png,
    og-image.png — are served with a 30-day immutable cache. These filenames
    are cache-busted via ?v=N query strings, so the response is safe to pin.
    Blog HTML was ALSO pinned 30-day immutable on the premise that "an
    existing slug never changes its content" — but every post in web/blog/
    has been edited since publication, and `immutable` blocks revalidation
    even on an explicit reload. A published correction was therefore
    invisible to anyone who had loaded the page, for up to 30 days, with no
    cache-bust available (HTML carries no ?v=). Blog HTML now takes the same
    short-lived treatment as every other HTML page.
    """
    name = rel_path.rsplit("/", 1)[-1].lower()
    is_brand_asset = (
        (name.startswith("seal") and name.endswith(".png"))
        or name == "lockup.png"
        or name.startswith("favicon")
        or (name.startswith("apple-touch-icon-") and name.endswith(".png"))
        or name == "og-image.png"
    )
    if is_brand_asset:
        # Versioned filenames only — safe to pin because the URL changes
        # when the bytes do.
        return "public, max-age=2592000, immutable"
    short_lived = {".html", ".json", ".webmanifest"}
    if suffix in short_lived:
        return "public, max-age=300, must-revalidate"
    if rel_path.startswith("sample/"):
        # _serve_static already lstrips the leading slash; POSIX-only server.
        # The sample bundle's bytes CHANGE at fixed URLs when the canonical
        # receipt's proofs upgrade (pending → Bitcoin-attested). A day-stale
        # /sample/a.ots beside a 5-minute /sample/index.json is the drift
        # this bundle exists to disprove.
        return "public, max-age=300, must-revalidate"
    return "public, max-age=86400"


class Handler(BaseHTTPRequestHandler):
    server_version = "orphograph/0.1"
    timeout = REQUEST_TIMEOUT_SEC  # close slow / dead connections so a thread isn't pinned

    # send_error() previously rendered the stdlib "Error response" template —
    # unbranded, no stylesheet, no path back into the site. A visitor who
    # mistypes a URL (e.g. /lp/agent-receipt for /lp/agent-receipts) should
    # land on a page that looks like Orphograph and links onward. External
    # stylesheets only (CSP is style-src 'self'; inline styles are dropped).
    # The stdlib substitutes %(code)d / %(message)s and HTML-escapes the
    # message, so dynamic error text cannot inject markup here.
    error_content_type = "text/html;charset=utf-8"
    error_message_format = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>%(code)d — Orphograph</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<link rel="icon" type="image/png" href="/favicon.png?v=8">
<link rel="stylesheet" href="/style.css?v=8">
<link rel="stylesheet" href="/blog.css?v=1">
<link rel="stylesheet" href="/css/orpho-tokens.css?v=2">
<link rel="stylesheet" href="/css/orpho-primitives.css?v=3">
<link rel="stylesheet" href="/index.css?v=22">
</head>
<body class="orpho">
<header>
  <div class="brand"><a href="/">orphograph</a></div>
  <nav>
    <a href="/learn">how it works</a>
    <a href="/#tiers">pricing</a>
    <a href="/lp/">guides</a>
    <a href="/about">about</a>
  </nav>
</header>
<main class="blog-post">
  <article class="post-header">
    <h1>%(code)d — %(message)s</h1>
    <p class="post-meta muted">The address may have been mistyped, or the page may have moved.</p>
  </article>
  <section class="post-body">
    <ul>
      <li><a href="/">Home</a> — anchor a file, inspect the tiers</li>
      <li><a href="/lp/">Guides</a> — including agent action receipts</li>
      <li><a href="/learn">How anchoring works</a></li>
    </ul>
  </section>
</main>
<footer>
  <small><a href="/">← Home</a> · <a href="/lp/">Guides</a> · The office does not transmit your file</small>
</footer>
</body>
</html>
"""

    # The access log is REBUILT from the request, never pattern-matched over
    # the raw line. Four review rounds of #261 each found a URL shape the
    # pattern rules missed (`//a/`, `/A/`, `%61`, `/./`, `/../`, a nested
    # `?e=`, `;`, a vertical tab, `?token=` that no rule named) while the
    # server itself acted on the value, so a live sign-in token, claim code,
    # address, invite code or checkout-session id reached the log. Now:
    #   * the request line is split exactly as the stdlib splits it;
    #   * a path is kept only if it is plain characters and, resolved with
    #     posixpath.normpath and case-folded, names no bearer route; a bearer
    #     route keeps its name (`/a/[redacted]`) and nothing else survives;
    #   * a query value is kept only for a listed key AND a plain value;
    #   * an error message keeps only the text before `(`, where the stdlib
    #     echoes the raw request line;
    #   * control characters AND the backslash are escaped, as the stdlib does.
    # `q` and `label` are not listed (a private vault search on
    # /api/me/anchors), nor coupon/promo codes (spendable). `ref` is: a
    # referral code is made to be shared in links. NOT `pack`: the legacy
    # `/?pack=pk_…` link (web/app.js, web/assets/pack.js) carries the claim
    # code itself.
    _LOG_KEEP_PARAMS = frozenset({
        "v", "plan", "variant", "ref", "status", "size", "receipt_id",
        "limit", "before", "stripe", "print", "nolenis", "private", "probe",
        "next",
    })
    # Parameter NAMES the log may show (their values still go): the kept keys
    # plus the ones whose values are redacted by design.
    _LOG_KNOWN_KEYS = _LOG_KEEP_PARAMS | frozenset({
        "e", "email", "code", "token", "id", "stripe_session", "session_id",
        "pack", "q", "label", "coupon", "promo", "prefilled_promo_code",
        "path", "order", "from", "to", "subject",
    })
    _LOG_TOKEN_LIKE = re.compile(r"[A-Za-z0-9_-]{16,}")
    _LOG_PLAIN_PATH = re.compile(r"/[A-Za-z0-9/_.,~-]*")
    _LOG_PLAIN_VALUE = re.compile(r"[A-Za-z0-9_.,:-]{0,128}")
    _LOG_PLAIN_MESSAGE = re.compile(r"[A-Za-z0-9 ,.'_-]{0,200}")
    _LOG_BEARER_ROUTES = ("/a/", "/api/pack/balance/")
    _LOG_CONTROL_TABLE = {
        **{c: f"\\x{c:02x}" for c in (*range(0x20), *range(0x7f, 0xa0))},
        ord("\\"): "\\\\",
    }

    @classmethod
    def _log_bearer_route(cls, path: str) -> str | None:
        """The bearer route `path` resolves to, or None. Resolved the way a
        client or proxy might: slashes collapsed, dot segments applied,
        case folded."""
        folded = posixpath.normpath("/" + path.lower().lstrip("/")) + "/"
        for route in cls._LOG_BEARER_ROUTES:
            if route in folded:
                return route
        return None

    @classmethod
    def _log_path(cls, path: str) -> str:
        if not cls._LOG_PLAIN_PATH.fullmatch(path):
            return "[redacted-path]"
        route = cls._log_bearer_route(path)
        if route is None:
            return path
        if posixpath.normpath("/" + path.lower().lstrip("/")).startswith(route):
            return route + "[redacted]"
        return "[redacted-path]"

    @classmethod
    def _log_query(cls, query: str) -> str:
        parts = []
        for part in query.split("&"):
            if "=" not in part:
                parts.append(part if part == "" else "[redacted]")
                continue
            raw_key, raw_value = part.split("=", 1)
            key = unquote_plus(raw_key).strip().lower()
            value = unquote_plus(raw_value)
            if key == "next":
                keep = (bool(cls._LOG_PLAIN_PATH.fullmatch(value))
                        and cls._log_bearer_route(value) is None)
            else:
                keep = key in cls._LOG_KEEP_PARAMS and bool(cls._LOG_PLAIN_VALUE.fullmatch(value))
            # The KEY is request data too (`/?pk_<code>=1`): it is shown only
            # when it names a parameter this server knows.
            shown_key = raw_key if key in cls._LOG_KNOWN_KEYS else "[redacted]"
            parts.append(part if keep else f"{shown_key}=[redacted]")
        return "&".join(parts)

    @classmethod
    def _log_target(cls, target: str) -> str:
        path, sep, query = target.partition("?")
        if "://" in path:  # absolute form: keep the scheme and host, judge the path
            scheme, _, rest = path.partition("://")
            host, slash, tail = rest.partition("/")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*", scheme) or not re.fullmatch(r"[A-Za-z0-9.:-]*", host):
                return "[redacted-target]"
            path = f"{scheme}://{host}" + cls._log_path(slash + tail) if slash else f"{scheme}://{host}"
        else:
            path = cls._log_path(path)
        return path + (sep + cls._log_query(query) if sep else "")

    def _log_requestline(self) -> str:
        words = str(getattr(self, "requestline", "") or "").split()
        if len(words) == 3:
            method, target, version = words
            if re.fullmatch(r"[A-Z]{1,16}", method) and re.fullmatch(r"HTTP/\d(\.\d)?", version):
                return f"{method} {self._log_target(target)} {version}"
        elif len(words) == 2 and re.fullmatch(r"[A-Z]{1,16}", words[0]):
            return f"{words[0]} {self._log_target(words[1])}"
        return "[unparsed request line]"

    def log_request(self, code="-", size="-"):
        code = getattr(code, "value", code)
        self.log_message('"%s" %s %s', self._log_requestline(), str(code), str(size))

    def log_error(self, format, *args):  # noqa: A002 (stdlib signature)
        # Every stdlib and app error message is "code %d, message %s"; the
        # message is where the stdlib echoes the raw line ("Bad request
        # syntax ('GET /a/<token>')"). Keep the words before `(`, if plain.
        if format == "code %d, message %s" and len(args) == 2:
            message = str(args[1]).split("(", 1)[0].strip()
            # Plain words only, and no token-shaped run: a message is the
            # claim-code / sign-in-token alphabet too.
            if (not self._LOG_PLAIN_MESSAGE.fullmatch(message)
                    or self._LOG_TOKEN_LIKE.search(message)):
                message = "[redacted]"
            self.log_message("code %d, message %s", args[0], message)
            return
        # Anything else (the stdlib's "Request timed out: %r") keeps its own
        # words and the TYPE of each argument, never the value.
        words = format.replace("%r", "%s").replace("%d", "%s")
        try:
            text = words % tuple(f"<{type(a).__name__}>" for a in args)
        except (TypeError, ValueError):
            text = "[error message redacted]"
        self.log_message("%s", text if self._LOG_PLAIN_MESSAGE.fullmatch(
            text.replace("<", "").replace(">", "").replace(":", "")) else "[error message redacted]")

    def log_message(self, fmt, *args):
        truncated = truncate_ip(self.client_address[0] if self.client_address else "")
        line = (fmt % args).translate(self._LOG_CONTROL_TABLE)
        sys.stderr.write(f"[{self.log_date_time_string()}] {truncated} - {line}\n")

    # BaseHTTPRequestHandler.send_error() never calls _security_headers(), so every
    # error answer — unknown paths, GET on POST-only API routes, a malformed login
    # token; 35 call sites — reached clients without HSTS, CSP, nosniff,
    # X-Frame-Options or Referrer-Policy. Found 2026-09-12 by a production sweep of
    # every dispatched route. send_error() flags the response and end_headers()
    # adds the bundle exactly once; responses that already call _security_headers()
    # never set the flag, so nothing is sent twice. The HEAD shim in do_HEAD wraps
    # the bound end_headers, so HEAD errors get the same headers.
    def send_error(self, code, message=None, explain=None):
        self._error_needs_security_headers = True
        try:
            super().send_error(code, message, explain)
        finally:
            self._error_needs_security_headers = False

    def end_headers(self):
        if getattr(self, "_error_needs_security_headers", False):
            self._error_needs_security_headers = False
            _security_headers(self)
        super().end_headers()

    def handle_one_request(self):  # noqa: N802 (stdlib name)
        # Clients (browsers, health probes, proxies) routinely disconnect before a
        # response finishes sending. The stdlib then propagates the write failure as
        # an unhandled BrokenPipeError/ConnectionResetError and dumps a full traceback
        # to stderr per occurrence — benign (the peer is gone, nothing more can be
        # sent) but it flooded the error log (~9k tracebacks / 19MB). Swallow only
        # those two connection-teardown errors, end the keep-alive loop, move on.
        # Any other exception still propagates so real bugs stay visible.
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _client_key(self) -> str:
        peer = self.client_address[0] if self.client_address else ""
        chosen = _resolve_peer_ip(
            self.headers.get(REAL_IP_HEADER, ""),
            self.headers.get("X-Forwarded-For", ""),
            peer,
            TRUST_PROXY_HEADERS,
        )
        return truncate_ip(chosen)

    def _record_demand(self, event: str, *, auth_path: str, surface: str,
                       outcome: str, api_key: str = "",
                       authenticated: bool = False, paid: bool = False) -> None:
        """Best-effort server-side demand event with no receipt mutation.

        Classification uses only server-verified auth facts. A caller cannot
        label itself human or office through a request field/header, and an
        analytics failure never changes the customer-facing operation.
        """
        try:
            origin = analytics.classify_origin(
                api_key=api_key if authenticated else "",
                authenticated=authenticated,
                paid=paid,
            )
            analytics.record_demand(
                event,
                origin_class=origin,
                auth_path=auth_path,
                surface=surface,
                outcome=outcome,
                client_key=self._client_key(),
            )
        except Exception as exc:  # noqa: BLE001 - telemetry must never break product
            sys.stderr.write(f"[demand] instrumentation unavailable: {type(exc).__name__}\n")

    def _analytics_ip(self) -> tuple[str, str]:
        """(/24-or-/48 truncated visitor IP, source label) for funnel rows.

        Separate from `_client_key` on purpose: that one buckets rate limits
        and must prefer the least-forgeable value; this one measures distinct
        visitors and must prefer the most accurate one. See
        `_resolve_analytics_ip`. Truncation is identical (`truncate_ip`), so
        the privacy posture is unchanged — a /24 for IPv4, /48 for IPv6, never
        a full address.
        """
        peer = self.client_address[0] if self.client_address else ""
        chosen, source = _resolve_analytics_ip(
            self.headers.get(CDN_CLIENT_IP_HEADER, ""),
            self.headers.get("X-Forwarded-For", ""),
            peer,
            TRUST_PROXY_HEADERS,
        )
        return truncate_ip(chosen), source

    def _founder_authorized(self) -> bool:
        """Shared gate for every /api/founder/* endpoint.

        Semantics (unchanged from the per-endpoint copies this replaces):
        constant-time compare of the X-Orpho-Founder header against the
        ORPHO_FOUNDER_TOKEN env var; unset token means the endpoints do not
        exist. Callers answer 404 on False so a probe cannot distinguish
        "wrong token" from "no such endpoint".

        New (2026-07-18): failures-only brute-force lockout. A failed compare
        consumes one token from _founder_fail_limiter (keyed by truncated
        client IP); once the bucket is empty the gate refuses WITHOUT
        comparing — so guessing is bounded at FOUNDER_FAIL_CAPACITY tries
        per refill window. A correct token never consumes, so the founder's
        own use is never throttled (unless an attacker on the founder's own
        /24 has just exhausted the bucket, which self-heals on refill).
        """
        token = os.environ.get("ORPHO_FOUNDER_TOKEN", "").strip()
        if not token:
            return False
        key = f"founder-fail:{self._client_key()}"
        if _founder_fail_limiter.peek(key) < 1.0:
            return False  # locked out — do not even run the compare
        if _founder_fail_global_limiter.peek(_FOUNDER_GLOBAL_FAIL_KEY) < 1.0:
            return False  # global lockout — distributed spray in progress
        supplied = self.headers.get("X-Orpho-Founder", "").strip()
        # Constant-time compare to avoid timing-side-channel leaks of the token.
        # As bytes: http.server decodes header bytes as latin-1, so a byte
        # 0x80-0xFF arrives as a non-ASCII char, and compare_digest refuses
        # non-ASCII str with a TypeError. That escaped the handler (dropped
        # connection, traceback) on every founder route and never reached the
        # failure budget below, so it told a prober which routes are real.
        # latin-1 gives back the bytes the client sent.
        import hmac as _hmac
        if _hmac.compare_digest(supplied.encode("latin-1", "replace"),
                                token.encode("utf-8")):
            return True
        _founder_fail_limiter.check(key)  # count the failed guess (per-IP)
        _founder_fail_global_limiter.check(_FOUNDER_GLOBAL_FAIL_KEY)  # and globally
        # Log lockout ENGAGEMENT transitions server-side only (stderr → fly
        # logs). Emitted exactly when the failed guess that emptied a bucket
        # lands; subsequent locked-out probes short-circuit above and stay
        # silent, so a spray cannot flood the log. Truncated IP only — the
        # same privacy posture as every other rate-limit key.
        if _founder_fail_limiter.peek(key) < 1.0:
            sys.stderr.write(
                f"[founder-auth] per-IP lockout engaged ip_trunc={self._client_key()} "
                f"after {FOUNDER_FAIL_CAPACITY} failed token guesses\n")
        if _founder_fail_global_limiter.peek(_FOUNDER_GLOBAL_FAIL_KEY) < 1.0:
            sys.stderr.write(
                f"[founder-auth] GLOBAL lockout engaged after "
                f"{FOUNDER_GLOBAL_FAIL_CAPACITY} failed token guesses across all IPs\n")
        return False

    def _session_email(self) -> str | None:
        cookies = SimpleCookie()
        cookies.load(self.headers.get("Cookie", "") or "")
        # In prod we set the __Host- prefixed name; in dev we set plain.
        # Look both up since either could be present across env transitions.
        sid = cookies.get(auth.cookie_name(COOKIE_SECURE)) or cookies.get("orpho_sid") or cookies.get("__Host-orpho_sid")
        if not sid:
            return None
        return auth.session_email(sid.value)

    def _vault_email(self) -> str | None:
        """Resolve the requester for the vault endpoints (/api/me/anchors*).

        Precedence mirrors /api/anchor: an `X-Orpho-Api-Key` belonging to an
        active subscriber authenticates first (this is what the MCP server and
        SDKs send — they have no cookie jar), then the session cookie. A key
        whose owner's subscription has lapsed falls through to the cookie
        rather than erroring, matching the anchor path's behavior.
        """
        api_key = self.headers.get("X-Orpho-Api-Key", "").strip()
        if api_key:
            email = api_keys.email_for_key(api_key)
            if email and _subscription_active_for(email):
                return email
        return self._session_email()

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        # Withdrawn pages. Each sat in the sitemap (/inspection/ was also in the
        # homepage footer), so say Gone rather than Not Found: crawlers drop a
        # 410 and its cached snippet far sooner than a 404.
        if _is_withdrawn_path(path):
            self.send_error(410, "Gone")
            return
        if _is_retired_btc_path(path):
            self.send_error(410, _RETIRED_BTC_MESSAGE)
            return
        # homepage A/B: split "/" between the cream and dark documents
        if path == "/" and _serve_ab_home(self):
            return
        # homepage A/B: attribute checkout-page reach to the visitor's arm.
        # Only while the experiment runs, only the page itself, and not for
        # the bots the split never assigns: the cookie is forgeable, and the
        # write used to fire for every /pay/crypto* path with the test off.
        if path in AB_CHECKOUT_PATHS and _ab_counts_this_visitor(self):
            _ab_arm = _ab_cookie_variant(self)
            # A view is a person seeing the page; HEAD shows nobody anything.
            if _ab_arm and not self._is_head():
                _ab_log("checkout_view", _ab_arm)
        # /api/event is POST-only. Reject any other method (incl. GET) with
        # 405 so we don't leak internal state via inadvertent GET-as-probe.
        if path == "/api/event":
            self._event_method_not_allowed()
            return
        # /LICENSE — the static handler rejects extensionless files (no
        # MIME match). Serve the LICENSE file explicitly as text/plain so
        # the every-page "(c) Orphograph. MIT — see /LICENSE" footer
        # reference resolves correctly.
        if path == "/LICENSE":
            try:
                license_bytes = (WEB_DIR / "LICENSE").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(license_bytes)))
                self.send_header("Cache-Control", "public, max-age=3600")
                _security_headers(self)
                self.end_headers()
                self.wfile.write(license_bytes)
            except OSError:
                self.send_error(404, "LICENSE not found")
            return
        if path == "/api/health":
            _json_response(self, 200, health.snapshot())
            return
        if path == "/api/stats":
            _json_response(self, 200, stats.snapshot())
            return
        # NOWPayments validates the webhook URL by issuing a GET first; respond
        # 200 so their setup wizard accepts the URL. POSTs go through the
        # signed-IPN handler in do_POST.
        if path == "/api/nowpayments/webhook":
            _json_response(self, 200, {
                "ok": True,
                "endpoint": "nowpayments_ipn",
                "method": "POST",
                "signature_header": "x-nowpayments-sig",
            })
            return
        if path == "/api/config":
            cfg = public_config.snapshot()
            # Card CTAs render only when the Stripe ACCOUNT can actually
            # charge — a configured-but-restricted account (charges_enabled
            # false) otherwise sends buyers into checkouts that die at pay
            # time. Cached in stripe_api; never allowed to break config.
            try:
                stripe_cfg = cfg.get("stripe")
                if isinstance(stripe_cfg, dict):
                    stripe_cfg["card_charges_enabled"] = stripe_api.charges_enabled() is True
            except Exception:
                pass
            _json_response(self, 200, cfg)
            return
        if path == "/api/stripe/session":
            # Read-only confirmation lookup. buy.js calls this on the
            # success-redirect page to show the buyer something specific
            # before the webhook has finished minting their Pack code.
            # The webhook is still the source of truth — this endpoint is
            # cosmetic but important for the "did my payment go through?"
            # moment.
            self._handle_stripe_session_status()
            return
        # Maintenance mode: allow only health/stats and static pages (founder dashboards stay live)
        if ORPHO_MAINTENANCE_MODE and not path.startswith(("/founder/", "/api/founder/", "/status.html")):
            _json_response(self, 503, {
                "error": "service unavailable",
                "detail": "Server undergoing maintenance. We'll be back shortly.",
            })
            return
        if path.startswith("/api/receipt/") or path.startswith("/api/verify/"):
            prefix = "/api/receipt/" if path.startswith("/api/receipt/") else "/api/verify/"
            rid_with_suffix = path[len(prefix):]
            # Determine the response shape and extract the receipt id.
            response_shape: str
            if rid_with_suffix.endswith(".zip"):
                rid = rid_with_suffix[:-4]
                response_shape = "zip"
            elif rid_with_suffix.endswith("/summary"):
                rid = rid_with_suffix[:-len("/summary")]
                response_shape = "summary"
            elif rid_with_suffix.endswith("/nft-metadata"):
                # NFT-friendly JSON snippet — designed to be copy-pasted into
                # ERC-721 / ERC-1155 / Solana SPL metadata. We don't mint
                # anything; the user's tooling does. We just describe the
                # pre-existence attestation in a metadata-server-friendly shape.
                rid = rid_with_suffix[:-len("/nft-metadata")]
                response_shape = "nft"
            else:
                rid = rid_with_suffix
                response_shape = "json"
            if not RECEIPT_ID_RE.match(rid):
                _json_response(self, 400, {"error": "invalid receipt id"})
                return
            # Load the record once and apply the private-receipt gate uniformly
            # across all three response shapes. A previous version gated only
            # the JSON path — the .zip and /summary endpoints would return
            # private receipt contents to anyone who knew the ID.
            record = engine.verify_receipt(rid)
            if not record.get("found"):
                _json_response(self, 404, record)
                return
            if record.get("private"):
                session_email = self._session_email()
                viewer_id = auth.email_id(session_email) if session_email else None
                if not viewer_id or viewer_id != record.get("owner_id"):
                    # Return the same 404 shape for non-owners on every path
                    # so the existence of a private receipt is not leaked
                    # by response code or shape.
                    _json_response(self, 404, {
                        "receipt_id": rid,
                        "found": False,
                        "error": "receipt not found",
                    })
                    return
            # Don't leak owner_id on public receipts — an external observer
            # could otherwise cluster every public receipt by HMAC(email)
            # owner. owner_id stays only when the viewer is the owner (and
            # the receipt is private).
            if not record.get("private"):
                record.pop("owner_id", None)
            # Past the gate above, a private receipt is only ever seen by its
            # owner; a public receipt is served as a public document to
            # everyone, its owner included (the owner's full record is their
            # vault export, /api/me/anchors.zip). So this is the owner's view
            # exactly when the receipt is private.
            owner_view = bool(record.get("private"))
            # OPTIONAL acceptance block — null unless a value-layer resolver is
            # configured via ORPHO_ACCEPTANCE_RESOLVER. Additive + standalone-safe:
            # acceptance_hook.resolve never raises and never imports a closed layer.
            record["acceptance"] = acceptance_hook.resolve(rid, record)
            if response_shape == "zip":
                import receipt_export
                zipped, err = receipt_export.export_zip(rid, owner_view=owner_view)
                if err == receipt_export.NOT_FOUND or zipped is None and err is None:
                    _json_response(self, 404, {"error": "receipt not found"})
                    return
                if err == receipt_export.BROKEN:
                    _json_response(self, 500, {"error": "could not build receipt zip"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(zipped)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Disposition", f"attachment; filename=\"receipt_{rid}.zip\"")
                _security_headers(self)
                self.end_headers()
                self.wfile.write(zipped)
                return
            if response_shape == "summary":
                import receipt_export
                summary, err = receipt_export.export_readable_json(rid, owner_view=owner_view)
                if err == receipt_export.NOT_FOUND or summary is None and err is None:
                    _json_response(self, 404, {"error": "receipt not found"})
                    return
                if err == receipt_export.BROKEN:
                    _json_response(self, 500, {"error": "could not build receipt summary"})
                    return
                summary["acceptance"] = acceptance_hook.resolve(rid, summary)
                _json_response(self, 200, summary)
                return
            if response_shape == "nft":
                # Build a metadata snippet the user can drop into their NFT
                # mint. We do NOT mint, custody, or wrap any token — this is
                # informational JSON describing the pre-existence proof.
                site = os.environ.get("SITE_URL", "https://orphograph.com").rstrip("/")
                nft = {
                    "name": f"Orphograph attestation {rid}",
                    "description": (
                        "Bitcoin-anchored proof, via the OpenTimestamps "
                        "protocol, that a file with the SHA-256 fingerprint "
                        "below existed on or before the recorded Bitcoin "
                        "block. Orphograph issues no claim of authorship, "
                        "ownership, or legality; the instrument is a "
                        "verifiable empirical fact."
                    ),
                    "external_url": f"{site}/r/{rid}",
                    "attributes": [
                        {"trait_type": "Receipt ID", "value": rid},
                        {"trait_type": "SHA-256", "value": record.get("hash_hex")},
                        {"trait_type": "SHA-512", "value": record.get("sha512_hex")},
                        {"trait_type": "Submitted (UTC)", "value": record.get("created_at")},
                        {
                            "trait_type": "Calendars",
                            "value": f"{record.get('calendars_ok', 0)} / {record.get('calendars_total', 5)}",
                        },
                        {"trait_type": "BTC pinned at", "value": record.get("btc_pinned_at")},
                        {"trait_type": "Status", "value": record.get("status")},
                        {"trait_type": "Verifier", "value": f"{site}/api/verify/{rid}"},
                    ],
                    "orphograph": {
                        "receipt_id": rid,
                        "hash_sha256": record.get("hash_hex"),
                        "hash_sha512": record.get("sha512_hex"),
                        "submitted_at_utc": record.get("created_at"),
                        "btc_pinned_at": record.get("btc_pinned_at"),
                        "calendars_ok": record.get("calendars_ok"),
                        "calendars_total": record.get("calendars_total"),
                        "status": record.get("status"),
                        "receipt_url": f"{site}/r/{rid}",
                        "verifier_url": f"{site}/api/verify/{rid}",
                        "protocol": "OpenTimestamps",
                        "anchor_chain": "Bitcoin",
                    },
                }
                _json_response(self, 200, nft)
                return
            _json_response(self, 200, record)
            return
        if path.startswith("/api/verify_folder/"):
            rid = path[len("/api/verify_folder/"):]
            if not RECEIPT_ID_RE.match(rid):
                _json_response(self, 400, {"error": "invalid receipt id"})
                return
            self._handle_verify_folder(rid)
            return
        if path == "/api/inclusion_proof":
            self._handle_inclusion_proof()
            return
        if path.startswith("/api/badge/") and path.endswith(".svg"):
            # Embeddable verification badge. Single GET, public, cacheable.
            # Privacy: badge_svg.render() reads only receipt_id + created_at
            # — no filename, no email, no hash bytes — so the SVG CONTENT is
            # safe to expose without authentication.
            #
            # Content was not the whole question. The STATUS CODE was an
            # existence oracle: a private receipt rendered 200 while an
            # unknown id gave 404, so a stranger could confirm a private
            # receipt exists by requesting its badge — the exact thing
            # /api/verify returns 404 to prevent ("don't reveal whether a
            # receipt exists for another owner").
            #
            # Private receipts 404 here for EVERYONE, including the owner.
            # A badge exists to be embedded on the public web, which is
            # precisely what "private" withdraws; and because this response
            # is cached `public, max-age=3600`, an owner-specific 200 could
            # be stored by a CDN and then served to strangers. One answer
            # for all callers is both the correct policy and the only
            # cache-safe one.
            rid = path[len("/api/badge/"):-len(".svg")]
            if not RECEIPT_ID_RE.match(rid):
                self.send_error(400, "invalid receipt id")
                return
            record = engine.verify_receipt(rid)
            if not record.get("found") or record.get("private"):
                self.send_error(404, "receipt not found")
                return
            site = os.environ.get("SITE_URL", "").rstrip("/")
            svg = badge_svg.render(record, base_url=site)
            body = svg.encode("utf-8")
            ctype = "image/svg+xml; charset=utf-8"
            body, enc = _maybe_compress(self, body, ctype)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            if enc:
                self.send_header("Content-Encoding", enc)
                self.send_header("Vary", "Accept-Encoding")
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/api/pack/balance/"):
            code = path[len("/api/pack/balance/"):]
            if not RECEIPT_ID_RE.match(code.lstrip("pk_")):
                _json_response(self, 400, {"error": "invalid claim code"})
                return
            _json_response(self, 200, {"claim_code": code, "balance": credits.balance(code)})
            return
        if path.startswith("/r/"):
            # Print-friendly receipt view. JS reads the ID from the URL
            # and fetches /api/verify/<id>; we additionally template the
            # OG meta tags so social-card unfurlers (X, LinkedIn, Slack,
            # iMessage) show the receipt ID in the preview tile. Without
            # this, every receipt URL shares the same generic preview
            # and there is no organic-distribution lift from a shared
            # receipt.
            rid = path[len("/r/"):].rstrip("/")
            if not RECEIPT_ID_RE.match(rid):
                self.send_error(400, "invalid receipt id")
                return
            try:
                html_path = WEB_DIR / "receipt.html"
                body = html_path.read_text()
                # Whitelisted substitution — `rid` already passed the
                # RECEIPT_ID_RE shape gate above, so no HTML-escape pass
                # is required, but we still avoid injecting raw user
                # input by limiting substitution to the validated id.
                body = body.replace("{{RECEIPT_ID}}", rid)
                # Per-receipt unfurl text: the sealed date travels in the
                # og/twitter description (stdlib templating — the branded
                # card image is static). Private receipts leak nothing.
                _tail = " Verified against the Bitcoin chain — check it yourself, no account required."
                sealed = "A file existed at the recorded moment." + _tail
                lineage_html = ""
                try:
                    _rec = engine.verify_receipt(rid)
                    if not _rec.get("found") or _rec.get("private"):
                        # honest unfurl for dead links: claim nothing. A
                        # private receipt reads the same, as its page does
                        # to anyone but the owner, or the unfurl confirms it.
                        sealed = "No record with this id."
                    else:
                        _d = str(_rec.get("created_at", ""))[:10]
                        if _d:
                            sealed = f"Sealed {_d}." + _tail
                            if _rec.get("btc_pinned_at"):
                                sealed = f"Sealed {_d} — anchored in Bitcoin." + _tail
                        # Edit-lineage "Version history" — server-rendered so
                        # the section (incl. the parent link) travels in the
                        # HTML itself, no JS required. Public receipts only:
                        # templating the parent id into a private receipt's
                        # page would leak it to anyone holding the URL
                        # (owners still see lineage on the certificate view,
                        # gated by /api/verify_folder).
                        lineage_html = _lineage_section_html(rid, _rec.get("lineage"))
                except Exception:
                    pass
                body = body.replace("{{OG_SEALED}}", sealed)
                body = body.replace("<!--LINEAGE_SECTION-->", lineage_html)
                payload = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "public, max-age=300")
                _security_headers(self)
                self.end_headers()
                self.wfile.write(payload)
            except OSError:
                _serve_static(self, "/receipt.html")
            return
        if path.startswith("/certificate/"):
            # Hosted dataset-provenance certificate view for folder anchors.
            # Mirrors /r/<id>: the JS reads the id from the URL and fetches
            # /api/verify_folder/<id>; we template the id into the page and
            # the OG meta so a shared certificate link unfurls with its id.
            rid = path[len("/certificate/"):].rstrip("/")
            if not RECEIPT_ID_RE.match(rid):
                self.send_error(400, "invalid receipt id")
                return
            _cert_missing = False
            try:
                # Private renders as missing: the page is public and cached
                # (max-age=300), so it cannot depend on who is looking, and a
                # difference here confirmed that a private receipt exists.
                # The owner's own view is the JS, via /api/verify_folder.
                _cert_rec = engine.verify_receipt(rid)
                _cert_missing = not _cert_rec.get("found") or bool(_cert_rec.get("private"))
            except Exception:
                pass
            try:
                html_path = WEB_DIR / "certificate.html"
                body = html_path.read_text()
                body = body.replace("{{RECEIPT_ID}}", rid)
                if _cert_missing:
                    body = body.replace(
                        "A dataset existed in this exact form by the anchored date — every file independently verifiable against the Bitcoin chain.",
                        "No certificate with this id.")
                # Per-receipt unfurl text: the sealed date travels in the
                # og/twitter description (stdlib templating — the branded
                # card image is static). Private receipts leak nothing.
                _tail = " Verified against the Bitcoin chain — check it yourself, no account required."
                sealed = "A file existed at the recorded moment." + _tail
                try:
                    _rec = engine.verify_receipt(rid)
                    if not _rec.get("found") or _rec.get("private"):
                        # honest unfurl for dead links: claim nothing. A
                        # private receipt reads the same, as its page does
                        # to anyone but the owner, or the unfurl confirms it.
                        sealed = "No record with this id."
                    else:
                        _d = str(_rec.get("created_at", ""))[:10]
                        if _d:
                            sealed = f"Sealed {_d}." + _tail
                            if _rec.get("btc_pinned_at"):
                                sealed = f"Sealed {_d} — anchored in Bitcoin." + _tail
                except Exception:
                    pass
                body = body.replace("{{OG_SEALED}}", sealed)
                payload = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "public, max-age=300")
                _security_headers(self)
                self.end_headers()
                self.wfile.write(payload)
            except OSError:
                _serve_static(self, "/certificate.html")
            return
        if path in ("/blog", "/blog/"):
            # Serve the curated static index at web/blog/index.html. It
            # lists both the static HTML posts (under /blog/<slug>.html)
            # and the markdown-rendered posts (under /blog/<slug>). The
            # dynamic blog.py renderer is retained for individual posts
            # only — keeping one editorial index avoids divergence.
            _serve_static(self, "/blog/index.html")
            return
        if path == "/blog/atom.xml":
            _send_xml(self, 200, blog.atom_feed_xml(),
                      content_type="application/atom+xml; charset=utf-8")
            return
        if path == "/blog/rss.xml":
            # Serve the statically-generated RSS 2.0 feed (built by the
            # press-kit / discoverability tooling). Bypasses the
            # /blog/ slug-routing branch below which would otherwise
            # 400 on the bare .xml path.
            _serve_static(self, "/blog/rss.xml")
            return
        if path.startswith("/blog/"):
            # Two independent blog content surfaces share /blog/:
            #   1. Static HTML files at web/blog/<slug>.html — long-form
            #      posts authored as HTML, served byte-for-byte.
            #   2. Markdown-rendered posts at content/blog/<slug>.md —
            #      addressed at /blog/<slug> with no extension.
            # We dispatch by URL shape: anything ending in .html or .css and
            # matching a real static file goes to the static path; bare slugs
            # go to the markdown renderer. The .css case covers the per-post
            # stylesheets (web/blog/<slug>.css, cache-busted via ?v=N) that
            # the static HTML posts reference — without it every dotted path
            # fell through to the slug validator and 400ed.
            rest = path[len("/blog/"):]
            if re.match(r"^[a-z0-9-]{1,80}\.(html|css)$", rest):
                # Try the static file under web/blog/. _serve_static returns
                # a 404 if the file is missing. The slug charset ([a-z0-9-])
                # admits no dots or slashes before the suffix, so traversal
                # (../) can never reach this branch.
                _serve_static(self, "/blog/" + rest)
                return
            slug = rest.rstrip("/")
            if not re.match(r"^[a-z0-9-]{1,80}$", slug):
                self.send_error(400, "invalid slug")
                return
            # Clean URL for a static HTML post: /blog/<slug> serves
            # web/blog/<slug>.html when it exists (this is what the on-site
            # links now use). Fall back to the markdown renderer otherwise.
            if (WEB_DIR / "blog" / (slug + ".html")).is_file():
                _serve_static(self, "/blog/" + slug + ".html")
                return
            html_page = blog.render_post_html(slug)
            if not html_page:
                self.send_error(404, "post not found")
                return
            _send_html(self, 200, html_page)
            return
        if path == "/sitemap.xml":
            _send_xml(self, 200, _build_sitemap())
            return
        if path == "/robots.txt":
            site = os.environ.get("SITE_URL", "https://orphograph.com").rstrip("/")
            body_txt = (
                "User-agent: *\n"
                "Allow: /\n"
                "Disallow: /api/\n"
                "Disallow: /a/\n"
                "Disallow: /r/\n"
                "Disallow: /account.html\n"
                "Disallow: /signin.html\n"
                f"Sitemap: {site}/sitemap.xml\n"
            )
            body_bytes = body_txt.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Cache-Control", "public, max-age=3600")
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body_bytes)
            return
        if path.startswith("/a/"):
            # Magic-link redemption. One-time consume → set session cookie → redirect.
            token = path[len("/a/"):].rstrip("/")
            if not TOKEN_RE.match(token):
                self.send_error(400, "invalid login token")
                return
            sid = None
            # The verdict is sent AFTER the try, never inside it: `OSError` also
            # covers a client that hung up mid-response, and answering that
            # with a second status line on a dead socket hides the real error.
            refusal: tuple[int, str] | None = None
            redeemed = None
            try:
                if self._is_head():
                    # Mail gateways and link checkers probe this link with
                    # HEAD before the person clicks it. Report what GET would
                    # answer; spend nothing, mint nothing.
                    if not auth.link_token_is_redeemable(token):
                        refusal = (404, "link expired or already used")
                else:
                    # Find out that we cannot record a sign-in BEFORE the link
                    # is spent, so "try the link again" is true when we say it.
                    auth.require_sign_in_writable()
                    redeemed = auth.redeem_link_token(token)
                    if not redeemed:
                        refusal = (404, "link expired or already used")
            except OSError:
                # A ledger we could not write. Answer, rather than drop the
                # connection: the click deserves "try again". Nothing was spent.
                refusal = (503, "We could not sign you in just now. "
                                "Please try the link again in a few minutes.")
            if not self._is_head() and refusal is None and redeemed is not None:
                try:
                    sid, _exp = auth.create_session(redeemed["email"])
                except OSError:
                    # The link IS spent by now, so do not say to try it again.
                    refusal = (503, "Your sign-in link was used, but we could not "
                                    "finish signing you in. Please request a new "
                                    "sign-in link.")
            if refusal is not None:
                self.send_error(*refusal)
                return
            # `?next=…` lets the caller pick the landing page after sign-in
            # so a welcome email can drop the user directly on the home
            # anchoring UI instead of forcing them through /account.html.
            # Whitelist: must be a same-site, single-segment-leading path
            # (no scheme, no host, no protocol-relative). Falls back to
            # /account.html on any rejection — open-redirect defense.
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            next_raw = (qs.get("next", [""])[0] or "").strip()
            location = "/account"
            # 2026-08-25 audit: a literal `//` test is NOT sufficient. Browsers
            # normalise "\\" to "/" and STRIP control characters before resolving a
            # Location, so all three of these bypassed the previous check and
            # produced a cross-origin redirect (verified against a live server):
            #     ?next=/%5Cevil.example    -> Location: /\evil.example  -> //evil.example
            #     ?next=/%09//evil.example  -> Location: /<TAB>//evil...  -> //evil.example
            #     ?next=/./%5C/evil.example -> Location: /\/evil.example -> //evil.example
            # Rather than trying to out-guess every normalisation a browser
            # performs, this allows only the conservative shape a real landing
            # path has: no backslash, no control characters or space, one
            # leading slash. Anything else falls back to /account.
            if (next_raw
                    and len(next_raw) < 200
                    and "\\" not in next_raw
                    and not any(ord(c) < 0x21 or ord(c) == 0x7F for c in next_raw)
                    and next_raw.startswith("/")
                    and not next_raw.startswith("//")):
                location = next_raw
            self.send_response(303)
            self.send_header("Location", location)
            if sid is not None:
                self.send_header("Set-Cookie", auth.build_session_cookie(sid, secure=COOKIE_SECURE))
            self.send_header("Cache-Control", "no-store")
            _security_headers(self)
            self.end_headers()
            return
        if path == "/api/me":
            email = self._session_email()
            if not email:
                _json_response(self, 401, {"error": "not authenticated"})
                return
            team = teams.team_for_member(email)
            team_role = None
            if team:
                team_role = "owner" if team.get("owner") == email else "member"
            sub_status = subscriptions.status_for(email) or {}
            sub_active = _subscription_active_for(email)
            # Anchor count under this subscription. Uses the count-only
            # fast path so /api/me does not become tail-latency for every
            # page nav via the status strip.
            anchor_count = _count_anchors_for_email(email)
            # Days remaining on the current Stripe period, if known.
            days_remaining: int | None = None
            cpe = sub_status.get("current_period_end")
            if cpe:
                try:
                    days_remaining = max(0, int((float(cpe) - datetime.now(timezone.utc).timestamp()) / 86400))
                except (TypeError, ValueError):
                    days_remaining = None
            # Plan label inferred from the Stripe customer record.
            plan_label = "Standing Order" if sub_active else None
            _json_response(self, 200, {
                "email": email,
                "signed_in": True,
                "plan": plan_label,
                "subscription_active": sub_active,
                "subscription_status": sub_status or None,
                "days_remaining": days_remaining,
                "anchor_count": anchor_count,
                "api_key_prefix": api_keys.active_key_prefix(email),
                "team": team,
                "team_role": team_role,
            })
            return
        if path.startswith("/api/nowpayments/order/"):
            # Status-only poll for web/pay/success.html so it can tell the
            # buyer whether their crypto payment has been credited yet.
            #
            # PRIVACY CONTRACT — this response is STATUS ONLY. It MUST NEVER
            # echo the claim code (a "pk_..." bearer token), the customer
            # email, or any secret. We look the ledger row up server-side but
            # project out ONLY a boolean and the integer credit count. The
            # order_id is a public reference printed on the success page.
            order_id = path[len("/api/nowpayments/order/"):].rstrip("/")
            if not RECEIPT_ID_RE.match(order_id):
                _json_response(self, 400, {"error": "invalid order id"})
                return
            allowed, retry = _status_limiter.check(f"orderstat:{self._client_key()}")
            if not allowed:
                _json_response(self, 429, {
                    "error": "too many requests",
                    "retry_after_seconds": int(retry) + 1,
                })
                return
            # Only a crypto order answers, and only by its own order id: see
            # credits.find_nowpayments_mint for what the looser lookup allowed.
            ledger_row = credits.find_nowpayments_mint(order_id)
            credited = ledger_row is not None
            # NOTE: ledger_row contains claim_code + email + source — do NOT
            # spread it into the response. Only the int delta is safe to echo.
            credit_count = int(ledger_row.get("credits_delta", 0)) if ledger_row else None
            _json_response(self, 200, {
                "ok": True,
                "order_id": order_id,
                "credited": credited,
                "credits": credit_count,
            })
            return
        if path == "/api/me/webhooks":
            email = self._session_email()
            if not email:
                _json_response(self, 401, {"error": "not authenticated"})
                return
            _json_response(self, 200, {"webhooks": webhooks.list_for_email(email)})
            return
        if path == "/api/me/referral-code":
            email = self._session_email()
            if not email:
                _json_response(self, 401, {"error": "not authenticated"})
                return
            code = affiliate.code_for_email(email, register=not self._is_head())
            site = os.environ.get("SITE_URL", "").rstrip("/")
            share_url = f"{site}/?ref={code}" if (site and code) else (
                f"/?ref={code}" if code else ""
            )
            _json_response(self, 200, {
                "ref_code": code,
                "share_url": share_url,
            })
            return
        if path == "/api/me/affiliate":
            email = self._session_email()
            if not email:
                _json_response(self, 401, {"error": "not authenticated"})
                return
            s = affiliate.stats(email, register=not self._is_head())
            # Privacy: stats() returns aggregate counters + masked history;
            # never an email or referee identifier. Pass through as-is.
            _json_response(self, 200, s)
            return
        if path == "/api/me/team":
            email = self._session_email()
            if not email:
                _json_response(self, 401, {"error": "not authenticated"})
                return
            t = teams.team_for_member(email)
            if not t:
                _json_response(self, 200, {"team": None})
                return
            _json_response(self, 200, {"team": t, "role": "owner" if t.get("owner") == email else "member"})
            return
        if path == "/api/me/anchors":
            email = self._vault_email()
            if not email:
                _json_response(self, 401, {"error": "not authenticated"})
                return
            qs = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = {}
            for pair in qs.split("&"):
                if "=" not in pair:
                    continue
                k, v = pair.split("=", 1)
                params[k] = v
            from urllib.parse import unquote
            before_raw = unquote(params.get("before", ""))
            limit_raw = params.get("limit", "50")
            hash_prefix = unquote(params.get("q", "")).strip().lower()
            label_substr = unquote(params.get("label", "")).strip()
            private_filter_raw = unquote(params.get("private", "")).strip().lower()
            private_only: bool | None
            if private_filter_raw == "true":
                private_only = True
            elif private_filter_raw == "false":
                private_only = False
            else:
                private_only = None
            try:
                limit = max(1, min(int(limit_raw), 200))
            except ValueError:
                limit = 50
            before = before_raw if before_raw else None
            anchors, has_more = _list_anchors_for_email(
                email,
                limit=limit,
                before=before,
                with_more_flag=True,
                hash_prefix=hash_prefix if hash_prefix else None,
                label_substr=label_substr if label_substr else None,
                private_only=private_only,
            )
            next_before = anchors[-1].get("created_at") if (has_more and anchors) else None
            _json_response(self, 200, {
                "anchors": anchors,
                "has_more": has_more,
                "next_before": next_before,
            })
            return
        if path == "/api/me/anchors.zip":
            email = self._vault_email()
            if not email:
                _json_response(self, 401, {"error": "not authenticated"})
                return
            if not subscriptions.is_active(email):
                _json_response(self, 402, {"error": "receipt vault requires an active subscription"})
                return
            import io as _io
            import zipfile as _zipfile
            anchors = _list_anchors_for_email(email, limit=10000)
            buf = _io.BytesIO()
            with _zipfile.ZipFile(buf, "w", _zipfile.ZIP_DEFLATED) as zf:
                for a in anchors:
                    rid = a.get("receipt_id")
                    if not rid:
                        continue
                    rdir = engine.RECEIPTS_DIR / rid
                    rjson = rdir / "receipt.json"
                    if rjson.exists():
                        zf.write(rjson, arcname=f"{rid}/receipt.json")
                    # Folder receipts: the manifest is what lets a relying
                    # party recompute the root from the files. Without it
                    # the .ots proves a root nobody can re-derive.
                    mjson = rdir / "manifest.json"
                    if mjson.exists():
                        zf.write(mjson, arcname=f"{rid}/manifest.json")
                    for ots in sorted(rdir.glob("*.ots")):
                        zf.write(ots, arcname=f"{rid}/{ots.name}")
                    # Renewal records, as receipt_export.export_zip ships
                    # them: verify_renewal.py treats a missing batch block as
                    # a hard failure, so a vault without them is not
                    # self-sufficient for a renewed subscriber.
                    renewal_dir = rdir / "renewal"
                    if renewal_dir.is_dir():
                        for rec in sorted(renewal_dir.glob("*.json")):
                            zf.write(rec, arcname=f"{rid}/renewal/{rec.name}")
            body = buf.getvalue()
            ts = datetime.now(timezone.utc).strftime("%Y%m%d")
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'attachment; filename="orphograph_vault_{ts}.zip"')
            self.send_header("Cache-Control", "no-store")
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/me/anchors.csv":
            email = self._vault_email()
            if not email:
                _json_response(self, 401, {"error": "not authenticated"})
                return
            anchors = _list_anchors_for_email(email, limit=10000)
            csv_body = _anchors_to_csv(anchors)
            body = csv_body.encode("utf-8")
            ts = datetime.now(timezone.utc).strftime("%Y%m%d")
            filename = f"orphograph_anchors_{ts}.csv"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-store")
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/me/anchors.jsonld":
            # JSON-LD vault export in a C2PA-compatible shape: each anchor
            # is a CreativeWork attested by a TimeStamp activity that
            # references its Bitcoin commitment and the issuing office.
            # Downstream verifiers built against C2PA's JSON-LD vocabulary
            # can ingest this directly.
            email = self._session_email()
            if not email:
                _json_response(self, 401, {"error": "not authenticated"})
                return
            anchors = _list_anchors_for_email(email, limit=10000)
            site = os.environ.get("SITE_URL", "https://orphograph.com").rstrip("/")
            graph = []
            for rec in anchors:
                rid = rec.get("receipt_id", "")
                created = rec.get("created_at", "")
                pinned = rec.get("btc_pinned_at") or None
                node: dict = {
                    "@type": "CreativeWork",
                    "@id": f"{site}/r/{rid}",
                    "identifier": rid,
                    "sha256": rec.get("hash_hex"),
                    "dateCreated": created,
                    "additionalType": "https://orphograph.com/vocab#anchored-fingerprint",
                    "potentialAction": {
                        "@type": "VerifyAction",
                        "target": f"{site}/r/{rid}",
                        "name": "Verify against the Bitcoin chain",
                    },
                }
                if rec.get("sha512_hex"):
                    node["sha512"] = rec["sha512_hex"]
                if rec.get("client_label"):
                    node["name"] = rec["client_label"]
                if rec.get("c2pa_manifest_hash"):
                    node["c2paManifestHash"] = rec["c2pa_manifest_hash"]
                if pinned:
                    node["bitcoinCommittedAt"] = pinned
                    node["pinnedCalendars"] = int(rec.get("pinned_count", 0))
                    node["totalCalendars"] = int(rec.get("pinned_total", rec.get("calendars_total", 0)))
                graph.append(node)
            doc = {
                "@context": {
                    "@vocab": "https://schema.org/",
                    "sha256": "https://orphograph.com/vocab#sha256",
                    "sha512": "https://orphograph.com/vocab#sha512",
                    "c2paManifestHash": "https://orphograph.com/vocab#c2paManifestHash",
                    "bitcoinCommittedAt": "https://orphograph.com/vocab#bitcoinCommittedAt",
                    "pinnedCalendars": "https://orphograph.com/vocab#pinnedCalendars",
                    "totalCalendars": "https://orphograph.com/vocab#totalCalendars",
                },
                "@type": "Collection",
                "name": "Orphograph receipt vault",
                "publisher": {
                    "@type": "Organization",
                    "name": "Orphograph",
                    "url": site,
                },
                "dateModified": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "size": len(graph),
                "hasPart": graph,
            }
            body = json.dumps(doc, indent=2).encode("utf-8")
            ts = datetime.now(timezone.utc).strftime("%Y%m%d")
            filename = f"orphograph_vault_{ts}.jsonld"
            self.send_response(200)
            self.send_header("Content-Type", "application/ld+json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-store")
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/me/export":
            email = self._session_email()
            if not email:
                _json_response(self, 401, {"error": "not authenticated"})
                return
            _json_response(self, 200, gdpr.export_for_email(email))
            return
        if path == "/api/unsubscribe":
            # GET — render confirmation page. RFC 8058 also accepts POST
            # for one-click via List-Unsubscribe-Post header.
            self._handle_unsubscribe_get()
            return
        if path == "/api/founder/payout-status":
            # Founder-only — hot BTC balance + sweep recommendation.
            # Gated by ORPHO_FOUNDER_TOKEN env var (shared-secret in header).
            self._handle_payout_status()
            return
        if path == "/api/founder/metrics":
            # Founder-only — revenue metrics (MRR, ARR, churn, LTV).
            # Gated by ORPHO_FOUNDER_TOKEN env var (shared-secret in header).
            self._handle_founder_metrics()
            return
        if path.startswith("/api/founder/customer"):
            # Founder-only — customer lookup by email.
            self._handle_founder_customer_lookup()
            return
        if path == "/api/founder/admin/toggles":
            # Founder-only — view operational admin toggles (maintenance, checkout, anchoring).
            self._handle_founder_admin_toggles()
            return
        if path == "/api/founder/morning-summary":
            # Founder-only — aggregated one-call snapshot (health + revenue + pending feedback).
            # Designed for the login-trigger morning-check script.
            self._handle_founder_morning_summary()
            return
        if path == "/api/founder/funnel":
            # Founder-only — analytics funnel rollup from data/events.jsonl.
            self._handle_founder_funnel()
            return
        if path in ("/affiliate", "/affiliate/"):
            # No standalone public landing page exists for the affiliate
            # program; referral/affiliate details live on the signed-in
            # account page. Redirect there rather than 404 on a missing
            # static file. 302 (temporary) so a future landing page can
            # reclaim this URL without a cached 301 getting in the way.
            self.send_response(302)
            self.send_header("Location", "/account")
            self.send_header("Content-Length", "0")
            _security_headers(self)
            self.end_headers()
            return
        if path in ("/pay/success", "/pay/success.html"):
            # Post-payment landing for the NOWPayments success_url redirect.
            _serve_static(self, "/pay/success.html")
            return
        # Vertical landing pages — rendered from config/verticals/<slug>.yml.
        # Reachable by direct URL only; not linked from the homepage. This
        # branch precedes the static fallback so /verticals/<slug>.html is
        # served from the YAML rather than from the on-disk file (if any).
        if path.startswith("/verticals/") and path.endswith(".html"):
            if verticals is None:
                self.send_error(404, "Vertical not found")
                return
            slug = path[len("/verticals/"):-len(".html")]
            if slug and "/" not in slug:
                body = verticals.render_html(slug)
                if body is not None:
                    payload = body.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "public, max-age=600")
                    _security_headers(self)
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_error(404, "Vertical not found")
                return
        # /docs/mcp is the URL developers reach for when they are already in
        # the docs, but the canonical MCP page is /mcp and has been since it
        # shipped. Two pages describing one product drift apart, so this is a
        # redirect rather than a second page — same reasoning as the private-
        # path rule below: one canonical set of claim wording, never two.
        if path in ("/docs/mcp", "/docs/mcp.html"):
            self.send_response(301)
            self.send_header("Location", "/mcp")
            self.send_header("Content-Length", "0")
            _security_headers(self)
            self.end_headers()
            return
        # RFC 9116 — security.txt. Served explicitly so the Content-Type
        # is unambiguous (text/plain; charset=utf-8) and so the path is
        # never refused by the static-handler's suffix allowlist. The
        # short-URL form /security.txt 301-redirects to the canonical
        # /.well-known/security.txt per RFC 9116 §3.
        if path == "/security.txt":
            self.send_response(301)
            self.send_header("Location", "/.well-known/security.txt")
            self.send_header("Content-Length", "0")
            _security_headers(self)
            self.end_headers()
            return
        if path == "/.well-known/security.txt":
            try:
                body_bytes = (WEB_DIR / ".well-known" / "security.txt").read_bytes()
            except OSError:
                self.send_error(404, "Not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Cache-Control", "public, max-age=3600")
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body_bytes)
            return
        # SEO discoverability — sitemap.xml and robots.txt are served
        # explicitly with the correct Content-Type and a 1h cache. The
        # files live in web/; we bypass the generic static handler so
        # the response shape (and Content-Type) is unambiguous.
        if path == "/api/standing-record":
            _json_response(self, 200, {"anchors": _list_weekly_anchors()})
            return
        if path in ("/sitemap.xml", "/robots.txt"):
            try:
                rel = path.lstrip("/")
                body_bytes = (WEB_DIR / rel).read_bytes()
            except OSError:
                self.send_error(404, "Not found")
                return
            content_type = (
                "application/xml; charset=utf-8"
                if path == "/sitemap.xml"
                else "text/plain; charset=utf-8"
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Cache-Control", "public, max-age=3600")
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body_bytes)
            return
        # Agent discoverability — /llms.txt (the agent-readable site summary)
        # and the MCP server card at /.well-known/mcp/server-card.json. Served
        # explicitly (same pattern as security.txt / robots.txt above) so the
        # Content-Type is unambiguous and the dotted .well-known path never
        # depends on the generic static handler's resolution rules. Both files
        # live in web/; this route is purely additive.
        if path in ("/llms.txt", "/.well-known/mcp/server-card.json"):
            try:
                body_bytes = (WEB_DIR / path.lstrip("/")).read_bytes()
            except OSError:
                self.send_error(404, "Not found")
                return
            content_type = (
                "application/json; charset=utf-8"
                if path.endswith(".json")
                else "text/plain; charset=utf-8"
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Cache-Control", "public, max-age=3600")
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body_bytes)
            return
        _serve_static(self, path)

    # Third-party callers we do not control. Both verify a provider signature
    # over the raw body, which is a stronger check than content-type, so they
    # are exempt from the gate below rather than being made to conform.
    _CT_EXEMPT_POST_PATHS = (
        # Signature-verified third-party callers. Both fail closed with 503
        # when their secret is unset and reject on signature mismatch, which
        # is a stronger check than a content type.
        "/api/stripe/webhook",
        "/api/nowpayments/webhook",
        # RFC 8058 one-click unsubscribe. Gmail / Yahoo / Microsoft POST here
        # with `Content-Type: application/x-www-form-urlencoded` and a body of
        # `List-Unsubscribe=One-Click` -- that is the SPEC, not a client we can
        # change, and mailer.py advertises it on every non-transactional send.
        # Gating it answered 415, so the opt-out silently vanished and the mail
        # kept going: a deliverability and bulk-sender-compliance failure, not
        # a cosmetic one. Safe to exempt because the handler takes the address
        # from the QUERY STRING and ignores the body entirely, so there is
        # nothing in the body to forge; the worst a forged call achieves is
        # unsubscribing an address the attacker already knows.
        "/api/unsubscribe",
    )

    # The three content types a cross-origin HTML form can produce, plus an
    # absent header, are CORS "simple requests": they reach the server with NO
    # preflight. Every POST body here is JSON (nothing parses form encoding),
    # and `application/json` is NOT simple, so requiring it forces a preflight
    # that no CORS policy on this origin answers. That is what makes a forged
    # cross-origin POST fail at the door.
    def _reject_non_json_post(self) -> bool:
        """True if the request was rejected. Blocks cross-origin form forgery.

        Session-authenticated endpoints were already covered by the session
        cookie's SameSite=Lax. This closes the UNAUTHENTICATED ones, where
        SameSite has nothing to act on: /api/waitlist and /api/event feed the
        demand and funnel numbers this project makes decisions from, and
        /api/auth/email-link sends mail. Forging those from visitors' browsers
        also borrows their IPs, which is precisely how the per-IP rate limits
        get walked around.

        Note the trailing case: a MISSING Content-Type is also a simple
        request (a cross-origin fetch with an untyped Blob sends none), so
        rejecting only the three form enctypes would leave the hole open.
        Every documented example, the MCP server and both SDKs already send
        `application/json`, so requiring it breaks no published contract.
        """
        path = self.path.split("?", 1)[0]
        # Exact match only. A `startswith` here would exempt
        # /api/stripe/webhookANYTHING as well; nothing routes such a path
        # today, but an exemption list should not widen on its own.
        if path in self._CT_EXEMPT_POST_PATHS:
            return False
        if not path.startswith("/api/"):
            return False
        # BODYLESS POSTS ARE EXEMPT, and this is load-bearing rather than a
        # convenience. The forgery vector needs a BODY to smuggle -- an empty
        # request has nothing to parse. Meanwhile three shipped clients post
        # with no body and no header at all: account.js sign-out and
        # logout-all, and statusbar.js sign-out. Requiring the header
        # unconditionally would have 415'd every sign-out in production.
        # Those endpoints are session-authenticated anyway, so SameSite=Lax
        # already keeps a cross-site POST from carrying the cookie they act on.
        #
        # An unparseable or absent Content-Length also falls through here on
        # purpose -- which includes `Transfer-Encoding: chunked`. That is safe
        # only because of a real invariant, stated so it is not rediscovered:
        # EVERY handler sizes its body via _read_content_length and reads
        # nothing it did not size, so a chunked POST is answered 400 and writes
        # nothing. A body the server will not read is not a delivery mechanism.
        # If a handler ever learns to stream a chunked body, this branch stops
        # being safe and the gate must size such requests instead.
        try:
            declared = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return False
        if declared <= 0:
            return False
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype == "application/json":
            return False
        self._drain_request_body()
        _json_response(self, 415, {
            "error": "unsupported media type",
            "detail": "POST bodies must be sent as Content-Type: application/json",
        })
        return True

    def _drain_request_body(self) -> int:
        """Read and discard a bounded request body before answering an error.

        Returns the number of bytes consumed (for tests; callers ignore it).

        WHY THIS EXISTS, and why it is not optional on any refusal path. This
        is HTTP/1.0: the socket closes after the response. Closing a socket
        that still holds UNREAD received data makes the kernel send RST instead
        of FIN, and an RST can discard the response the client has not read
        yet. The client then sees ECONNRESET / a broken pipe / a proxy 502
        instead of the status we actually sent — an actionable refusal turned
        into an opaque network error.

        SIZE IS IRRELEVANT. A body small enough to already sit in the kernel
        receive buffer is exactly the case that produces the RST, because those
        are the bytes that are unread at close. MAX_BODY_BYTES is 4096, so
        every body we ever accept is in that class.

        Bounded by MAX_BODY_BYTES so a large declared body is not read on our
        dime; a client that declared more than we will read gets the same RST,
        but it also gets no service, which is the trade the cap is making.
        """
        try:
            declared = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return 0
        to_drain = min(max(declared, 0), MAX_BODY_BYTES)
        if to_drain <= 0:
            return 0
        try:
            return len(self.rfile.read(to_drain) or b"")
        except (OSError, ValueError):
            return 0

    def _anchor_payload(self, max_bytes: int):
        """Parse untrusted anchor input before charging any allowance."""
        # credit_refunded / max_bytes: keys these 400s carried before
        # validation moved ahead of charging. Nothing is charged yet here.
        length = _read_content_length(self)
        if length <= 0 or length > max_bytes:
            _json_response(self, 400, {"error": "invalid body size",
                                       "max_bytes": max_bytes, "credit_refunded": False})
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            _json_response(self, 400, {"error": "body must be JSON", "credit_refunded": False})
            return None
        if not isinstance(payload, dict):
            _json_response(self, 400, {"error": "body must be a JSON object"})
            return None
        return payload

    @staticmethod
    def _anchor_input_error(payload: dict, *, hash_required: bool = False):
        """Validate types and digest values before paid/free accounting."""
        types = {
            "client_label": str, "notify_email": str, "sha512_hex": str,
            "c2pa_manifest_hash": str, "hardware_attestation": dict,
            "zk_proof": dict, "attestation": dict, "metadata": dict,
            "private": bool, "paths_public": bool,
        }
        for field, want in types.items():
            value = payload.get(field)
            if value is not None and not isinstance(value, want):
                return f"{field} must be {want.__name__}"
        digests = {"sha512_hex": 128, "c2pa_manifest_hash": 64}
        if hash_required:
            digests["hash_hex"] = 64
        for field, size in digests.items():
            value = payload.get(field)
            if value is None and field != "hash_hex":
                continue
            if (not isinstance(value, str) or
                    re.fullmatch(r"[0-9a-f]{" + str(size) + r"}", value.strip().lower()) is None):
                return f"{field} must be {size} lowercase hex characters"
        return None

    def _optional_typed(self, payload: dict, field: str, want: type, label: str):
        """Read an OPTIONAL structured field, or 400 if it is present with the
        wrong type. Returns (value_or_None, handled) — `handled` True means a
        response has already been sent and the caller must return.

        2026-08-25 audit: these three fields used to be read as
        `payload.get(f) if isinstance(payload.get(f), T) else None`, which
        SILENTLY dropped a wrong-typed value. A client sending
        `c2pa_manifest_hash: 12345` got a 200 and a receipt with no binding and
        never learned that the binding it asked for does not exist — while the
        same field sent as a malformed STRING was correctly rejected with 400.
        Silent data loss on a trust product is worse than a loud refusal, and
        the inconsistency was the tell. JSON null and an absent key both still
        mean "not supplied" and are accepted.
        """
        if field not in payload or payload[field] is None:
            return None, False
        value = payload[field]
        # bool is a subclass of int; it is never a valid value for these.
        if isinstance(value, want) and not isinstance(value, bool):
            return value, False
        _json_response(self, 400, {
            "error": f"{field} must be {label} when supplied",
            "detail": "omit the field, or send null, if you have no value for it",
        })
        return None, True

    def do_POST(self):  # noqa: N802
        # BEFORE the content-type gate, deliberately. A retired endpoint that
        # answered 415 to a CORS-simple POST would be saying "wrong type" —
        # i.e. "send the right one and I will serve you". There is nothing
        # behind these paths any more, and every spelling must say so.
        #
        # The body is drained FIRST, exactly as the 415 path does. Answering a
        # POST without reading its body closes the socket with unread data,
        # which makes the kernel send RST and can destroy the 410 before the
        # client reads it — so the caller sees a connection reset instead of
        # being told the endpoint is gone. Cached copies of the old v2.js and
        # app.js still POST here from browsers that have not revalidated.
        if _is_retired_btc_path(self.path.split("?", 1)[0]):
            self._drain_request_body()
            self.send_error(410, _RETIRED_BTC_MESSAGE)
            return
        if self._reject_non_json_post():
            return
        if self.path == "/api/stripe/webhook":
            self._handle_stripe_webhook()
            return
        if self.path == "/api/stripe/checkout":
            self._handle_stripe_checkout()
            return
        if self.path == "/api/nowpayments/webhook":
            self._handle_nowpayments_webhook()
            return
        if self.path == "/api/nowpayments/create":
            self._handle_nowpayments_create()
            return
        # Maintenance mode: block user-facing requests but allow critical ops
        if ORPHO_MAINTENANCE_MODE:
            _json_response(self, 503, {
                "error": "service unavailable",
                "detail": "Server undergoing maintenance. We'll be back shortly.",
            })
            return
        if self.path == "/api/auth/email-link":
            self._handle_request_email_link()
            return
        if self.path == "/api/auth/signout":
            self._handle_signout()
            return
        if self.path == "/api/me/logout-all":
            self._handle_logout_all()
            return
        if self.path == "/api/me/delete":
            self._handle_account_delete()
            return
        if self.path == "/api/me/cancel-subscription":
            self._handle_cancel_subscription()
            return
        if self.path == "/api/me/reactivate-subscription":
            self._handle_reactivate_subscription()
            return
        if self.path == "/api/me/api-key":
            self._handle_issue_api_key()
            return
        if self.path.startswith("/api/me/receipt/") and self.path.endswith("/privacy"):
            self._handle_toggle_receipt_privacy()
            return
        if self.path == "/api/me/team/create":
            self._handle_team_create()
            return
        if self.path == "/api/me/team/invite":
            self._handle_team_invite()
            return
        if self.path == "/api/me/team/redeem":
            self._handle_team_redeem()
            return
        if self.path == "/api/me/team/remove":
            self._handle_team_remove()
            return
        if self.path == "/api/me/team/leave":
            self._handle_team_leave()
            return
        if self.path == "/api/me/api-key/revoke":
            self._handle_revoke_api_key()
            return
        if self.path == "/api/me/webhooks":
            self._handle_webhook_register()
            return
        if self.path == "/api/me/webhooks/delete":
            self._handle_webhook_delete()
            return
        if self.path == "/api/me/refund-request":
            self._handle_refund_request()
            return
        if self.path == "/api/recover":
            self._handle_recover_payment()
            return
        if self.path == "/api/pack/recover":
            self._handle_pack_recover()
            return
        if self.path == "/api/me/affiliate/payout":
            self._handle_affiliate_payout()
            return
        if self.path == "/api/waitlist":
            self._handle_waitlist()
            return
        if self.path.startswith("/api/unsubscribe"):
            self._handle_unsubscribe_post()
            return
        if self.path == "/api/event":
            self._handle_event()
            return
        if self.path == "/api/anchor/batch":
            self._handle_anchor_batch()
            return
        if self.path == "/api/anchor_folder":
            self._handle_anchor_folder()
            return
        if self.path == "/api/ln/quote":
            self._handle_ln_quote()
            return
        if self.path != "/api/anchor":
            self.send_error(404, "not found")
            return
        # Admin toggle: disable anchoring if external services are down
        if ORPHO_DISABLE_ANCHORING:
            _json_response(self, 503, {
                "error": "anchoring temporarily unavailable",
                "detail": "Calendar service unavailable. Anchoring is temporarily disabled.",
            })
            return
        payload = self._anchor_payload(MAX_BODY_BYTES)
        if payload is None:
            return
        error = self._anchor_input_error(payload, hash_required=True)
        if error:
            _json_response(self, 400, {"error": error})
            return
        pack_token = self.headers.get("X-Pack-Token", "").strip()
        pack_consumed = False
        pack_remaining = 0
        if pack_token:
            pack_consumed, pack_remaining = credits.consume_credit(pack_token)
        # L402 Lightning payment: `Authorization: L402 <macaroon>:<preimage>`.
        # A valid, settled, UNSPENT credential buys exactly one anchor — the
        # agent-pays path (docs/LIGHTNING_L402.md). An invalid attempt fails
        # loudly with 401; it never silently falls through to the free tier.
        ln_payment_hash = None
        auth_header = self.headers.get("Authorization", "").strip()
        if not pack_consumed and auth_header.startswith("L402 "):
            ln_payment_hash, ln_err = lightning.verify_l402(auth_header)
            if ln_payment_hash is None:
                _json_response(self, 401, {"error": f"L402 rejected: {ln_err}"})
                return
            # Fast reject only. The BINDING single-use decision is the
            # atomic lightning.claim() immediately before the anchor below;
            # this check just saves obvious replays the work. It used to BE
            # the decision, 176 lines and five calendar submissions before
            # mark_spent, on a threading server — eight concurrent requests
            # with one paid credential produced eight receipts.
            try:
                already_spent = lightning.is_spent(ln_payment_hash)
            except lightning.SpentSetUnavailable as e:
                _json_response(self, 503, {
                    "error": "cannot verify L402 credential freshness",
                    "detail": f"the spent-credential ledger is unreadable: {e}",
                    "hint": "this is an office-side fault; nothing was "
                            "charged and no anchor was made. Retry shortly."})
                return
            if already_spent:
                _json_response(self, 401, {
                    "error": "L402 rejected: credential already spent",
                    "hint": "each payment buys exactly one anchor — request "
                            "a new quote at POST /api/ln/quote"})
                return
        # API key path: alternative to session cookie / pack token. The key
        # owner must have an active subscription for the key to bypass limits.
        api_key = self.headers.get("X-Orpho-Api-Key", "").strip()
        api_key_email = api_keys.email_for_key(api_key) if api_key else None
        api_key_active = bool(api_key_email and _subscription_active_for(api_key_email))
        # Authenticated subscribers bypass the free-tier rate limit.
        subscriber_email = api_key_email or (self._session_email() if not pack_consumed else None)
        subscription_active = api_key_active or _subscription_active_for(subscriber_email)
        # Private receipts: subscriber-only feature. Anonymous and pack-only
        # anchors cannot be marked private (no owner_id to gate by).
        #
        # FAIL CLOSED. This used to be `bool(...) and subscription_active`,
        # which silently PUBLISHED a receipt the caller had explicitly asked
        # to keep private — no error, no warning, nothing in the response.
        # Publishing is not undoable; retrying without `private` costs the
        # caller one line. So when the request cannot be honoured we decline
        # to anchor at all and say why.
        if bool(payload.get("private", False)) and not subscription_active:
            _reject_private(self, pack_consumed, pack_token)
            return
        want_private = bool(payload.get("private", False))
        if not pack_consumed and not subscription_active and ln_payment_hash is None:
            allowed, retry_after = _anchor_limiter.check(self._client_key())
            if not allowed and lightning.configured():
                # L402 challenge: agents past the free tier can pay sats for
                # one anchor, no account. Falls back to the classic 429 when
                # Lightning isn't armed, so behavior is unchanged until then.
                ok, inv = lightning.create_invoice(
                    lightning.PRICE_SATS, "orphograph anchor")
                if ok:
                    macaroon = lightning.mint_macaroon(
                        inv["payment_hash"], lightning.PRICE_SATS)
                    self.send_response(402)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header(
                        "WWW-Authenticate",
                        f'L402 token="{macaroon}", invoice="{inv["bolt11"]}"')
                    body = json.dumps({
                        "error": "payment required",
                        "price_sats": lightning.PRICE_SATS,
                        "invoice": inv["bolt11"],
                        "macaroon": macaroon,
                        "how": "pay the invoice, then retry with "
                               "Authorization: L402 <macaroon>:<preimage_hex>",
                        "free_tier_retry_after_seconds": int(retry_after) + 1,
                    }).encode("utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    _security_headers(self)
                    self.end_headers()
                    self.wfile.write(body)
                    return
            if not allowed:
                Handler._record_demand(self,
                    "free_limit_reached", auth_path="free", surface="single",
                    outcome="limited")
                self.send_response(429)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Retry-After", str(int(retry_after) + 1))
                body = json.dumps({
                    "error": "rate limit exceeded",
                    "retry_after_seconds": int(retry_after) + 1,
                    "limit_per_day": ANCHOR_RATE_CAPACITY,
                    "hint": "Buy a Pack to anchor without rate limits.",
                }).encode("utf-8")
                self.send_header("Content-Length", str(len(body)))
                _security_headers(self)
                self.end_headers()
                self.wfile.write(body)
                return
        hash_hex = payload.get("hash_hex", "")
        sha512_hex = payload.get("sha512_hex")
        client_label = payload.get("client_label")
        notify_email = payload.get("notify_email")
        # Attestation + metadata: any caller can submit these. The engine
        # sanitizes (allowlist + size caps); unknown fields are dropped.
        attestation = payload.get("attestation") if isinstance(payload.get("attestation"), dict) else None
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
        # Hardware attestation: a device-resident-key signature over the
        # anchored hash (docs/HARDWARE_ATTESTATION_SPIKE.md). The engine
        # strictly validates shape + hash binding and rejects the whole
        # record on any violation; absent field changes nothing.
        hardware_attestation, _handled = self._optional_typed(
            payload, "hardware_attestation", dict, "an object")
        if _handled:
            return
        # ZK provenance proof (schnorr-zk-pok-v1 / snark-exec-v1): the engine
        # sanitizer recomputes every hash binding and rejects the whole
        # record on any violation. This passthrough was MISSING from the
        # HTTP surface until 2026-08-04 — engine-level tests all passed
        # while the field silently vanished on the wire.
        zk_proof, _handled = self._optional_typed(payload, "zk_proof", dict, "an object")
        if _handled:
            return
        # Optional C2PA manifest hash — the engine validates shape before
        # accepting. Coexistence-first: an Orphograph receipt can reference
        # a C2PA manifest hash so verifiers see both attestations.
        c2pa_manifest_hash, _handled = self._optional_typed(
            payload, "c2pa_manifest_hash", str, "a string")
        if _handled:
            return
        if isinstance(client_label, str):
            client_label = client_label[:200]
        else:
            client_label = None
        if sha512_hex is not None and not isinstance(sha512_hex, str):
            sha512_hex = None
        # Tag the anchor source so the expiry worker can distinguish free vs paid.
        # Pack tokens are bearer credentials so we record only a short prefix.
        if pack_consumed:
            source = f"pack:{pack_token[:8]}"
        elif ln_payment_hash is not None:
            source = f"ln:{ln_payment_hash[:10]}"
        elif api_key_active:
            source = f"api:{api_key[:10]}"
        elif subscription_active:
            # HMAC-derived identifier — an attacker with only disk access
            # cannot dictionary-attack receipts→email without also stealing
            # the per-installation HMAC secret.
            source = "sub:" + auth.email_id(subscriber_email)
        else:
            source = "free"
        # Atomic single-use claim, as late as possible but strictly BEFORE
        # the anchor. Late so that every rejection above costs the customer
        # nothing; before, so the credential can never buy two anchors. If
        # anchoring then fails we release it, so a server-side error does not
        # eat a payment.
        if ln_payment_hash is not None:
            try:
                if not lightning.claim(ln_payment_hash):
                    _json_response(self, 401, {
                        "error": "L402 rejected: credential already spent",
                        "hint": "each payment buys exactly one anchor — "
                                "request a new quote at POST /api/ln/quote"})
                    return
            except lightning.SpentSetUnavailable as e:
                _json_response(self, 503, {
                    "error": "cannot claim L402 credential",
                    "detail": f"the spent-credential ledger is unwritable: {e}",
                    "hint": "office-side fault; nothing was charged and no "
                            "anchor was made."})
                return
        try:
            owner_id = auth.email_id(subscriber_email) if subscriber_email else None
            record = engine.anchor_hash(
                hash_hex,
                client_label=client_label,
                sha512_hex=sha512_hex,
                source=source,
                private=want_private,
                owner_id=owner_id if want_private else None,
                # Only a subscription-paid receipt joins the account. A pack
                # or L402 anchor stays unowned, as it was before this field.
                account_id=owner_id if source.startswith(("api:", "sub:")) else None,
                attestation=attestation,
                metadata=metadata,
                c2pa_manifest_hash=c2pa_manifest_hash,
                hardware_attestation=hardware_attestation,
                zk_proof=zk_proof,
            )
        except ValueError as e:
            # Anchor definitively failed (bad hash, etc.); no receipt produced.
            # Refund the consumed pack credit so the buyer isn't charged for a
            # request that yielded nothing — and release the L402 claim for
            # the same reason. A claim taken for an anchor that never existed
            # would silently eat a Lightning payment.
            if pack_consumed:
                credits.refund_credit(pack_token)
            if ln_payment_hash is not None:
                lightning.release(ln_payment_hash)
            _json_response(self, 400, {"error": str(e),
                                       "credit_refunded": pack_consumed,
                                       "ln_credential_released": ln_payment_hash is not None})
            return
        # homepage A/B: attribute the successful anchor to the visitor's arm
        _ab_arm = _ab_cookie_variant(self)
        if _ab_arm and _ab_counts_this_visitor(self):
            _ab_log("anchor", _ab_arm)
        # Distinct upstream calendars, not server acknowledgements. One
        # helper for every anchor surface (single, batch, folder) so the pair
        # of fields and the flag cannot drift between them.
        distinct = engine.receipt_distinct_counts(record)
        low_redundancy = distinct["calendars_distinct_ok"] < MIN_CALENDARS_OK
        # Total calendar outage: 0 calendars accepted the hash, so the receipt
        # has no Bitcoin commitment and can never upgrade — it is worthless.
        # Refund the consumed credit (the buyer can re-anchor when calendars
        # recover) while still returning the receipt for transparency.
        credit_refunded = False
        if pack_consumed and record["calendars_ok"] == 0:
            credits.refund_credit(pack_token, reason="anchor-refund:no-calendars")
            credit_refunded = True
            pack_remaining += 1
        # L402 fairness, preserved through the move to an atomic claim: a
        # worthless 0-calendar anchor has no Bitcoin commitment and can never
        # upgrade, so the credential is RELEASED and the agent can retry —
        # the same deal the pack refund above gives card buyers. The claim is
        # now taken before the anchor (it has to be, to be atomic), so this
        # is an explicit release rather than simply declining to mark.
        if ln_payment_hash is not None:
            if record["calendars_ok"] > 0:
                # Annotate the existing claim row with the receipt it bought,
                # so the audit trail links payment to artifact.
                lightning.mark_spent(ln_payment_hash, record["receipt_id"])
            else:
                lightning.release(ln_payment_hash)
                credit_refunded = True
        # Receipt email: fires for any paid path (Pack consumed, active
        # subscription, or active API key). Previously this was Pack-only,
        # which silently dropped receipts for subscribers — exact 2026-05-18
        # customer complaint ("x1 purchased … wasn't sent"). For subscribers
        # who didn't pass an explicit notify_email, fall back to their
        # signed-in account email so they at least get the receipt.
        candidate = ""
        if isinstance(notify_email, str):
            candidate = notify_email[:200].strip()
        if not candidate and subscription_active and subscriber_email:
            candidate = subscriber_email
        is_paid_anchor = pack_consumed or subscription_active or api_key_active
        if candidate and is_paid_anchor and EMAIL_RE.match(candidate):
            mailer.send_receipt_email(candidate, record)
        # Webhook dispatch — fire-and-forget on background threads.
        # Subscribers and API-key holders receive anchor.created; Pack-only
        # buyers do not, since Pack-only sessions have no signed-in
        # identity to dispatch under.
        if subscription_active and subscriber_email:
            webhooks.dispatch("anchor.created", subscriber_email, {
                "receipt_id": record["receipt_id"],
                "hash_hex": record["hash_hex"],
                "sha512_hex": record.get("sha512_hex"),
                "created_at": record["created_at"],
                "client_label": record.get("client_label"),
                "calendars_ok": record["calendars_ok"],
                "calendars_total": record["calendars_total"],
                # A receiver that acts on low_redundancy must also be able to
                # see WHY it fired, without a second fetch.
                **distinct,
                "low_redundancy": low_redundancy,
                "private": want_private,
                "receipt_url": f"{os.environ.get('SITE_URL', 'https://orphograph.com').rstrip('/')}/r/{record['receipt_id']}",
            })
        # Persist notify_email so upgrade_worker can email the customer when
        # the BTC pin actually lands (~1h later). This used to be nested
        # inside the subscriber-only branch above, so a PACK buyer who passed
        # notify_email — the exact audience docs/api.html documents the field
        # for, "Pack only — emails the receipt" — got the immediate receipt
        # and was then never told their anchor reached Bitcoin. They supplied
        # an address for precisely that notification.
        #
        # Dispatch stays subscriber-gated: webhooks resolve by owner identity,
        # which a Pack-only session does not have. webhooks.dispatch returns
        # silently when an address has no registered endpoint, so persisting
        # for Pack buyers adds no traffic and no log noise (verified).
        #
        # Saved only AFTER format validation, so the on-disk value is always a
        # syntactically valid address.
        if candidate and is_paid_anchor and EMAIL_RE.match(candidate):
            try:
                receipt_path = engine.RECEIPTS_DIR / record["receipt_id"] / "receipt.json"
                on_disk = json.loads(receipt_path.read_text())
                on_disk["notify_email"] = candidate
                receipt_path.write_text(json.dumps(on_disk, indent=2))
                record["notify_email"] = candidate
            except (OSError, json.JSONDecodeError):
                pass
        site = os.environ.get("SITE_URL", "https://orphograph.com").rstrip("/")
        rid = record["receipt_id"]
        if pack_consumed:
            demand_auth_path = "pack"
        elif ln_payment_hash is not None:
            demand_auth_path = "l402"
        elif api_key_active:
            demand_auth_path = "api_key"
        elif subscription_active:
            demand_auth_path = "subscription"
        else:
            demand_auth_path = "free"
        Handler._record_demand(self,
            "anchor_succeeded",
            auth_path=demand_auth_path,
            surface="single",
            outcome="success" if record["calendars_ok"] > 0 else "uncommitted",
            api_key=api_key if api_key_active else "",
            authenticated=api_key_active or subscription_active,
            paid=demand_auth_path != "free",
        )
        _json_response(self, 200, {
            "receipt_id": rid,
            "created_at": record["created_at"],
            "hash_hex": record["hash_hex"],
            "sha512_hex": record.get("sha512_hex"),
            "client_label": record["client_label"],
            "calendars_ok": record["calendars_ok"],
            "calendars_total": record["calendars_total"],
            # Five servers reach four calendars: both counts are reported so
            # neither number has to carry a meaning it does not have.
            **distinct,
            "low_redundancy": low_redundancy,
            "pack_consumed": pack_consumed,
            "pack_remaining": pack_remaining,
            "credit_refunded": credit_refunded,
            "subscription_active": subscription_active,
            "successes": [{"calendar": s["calendar"], "ots_path": s["ots_path"]} for s in record["successes"]],
            "failures": record["failures"],
            # Provenance fields echo only when the sanitizer accepted them —
            # a client that sent a proof needs to SEE whether it survived
            # (the capture daemon's sidecar writer relies on this echo).
            **({"zk_provenance": record["zk_provenance"]}
               if record.get("zk_provenance") else {}),
            **({"hardware_attestation": record["hardware_attestation"]}
               if record.get("hardware_attestation") else {}),
            # Distribution-friendly URLs. Every API caller (a workflow tool,
            # an SDK user, a curl script) gets the receipt's public URL and
            # an embeddable badge URL without having to read docs and
            # hand-construct them. Receipt UI uses these too.
            "receipt_url": f"{site}/r/{rid}",
            "badge_url": f"{site}/api/badge/{rid}.svg",
            "verify_url": f"{site}/api/receipt/{rid}",
        })

    def _handle_request_email_link(self) -> None:
        # Rate-limited by IP to prevent email bombing.
        allowed, retry = _anchor_limiter.check(f"auth:{self._client_key()}")
        if not allowed:
            _json_response(self, 429, {"error": "too many requests", "retry_after_seconds": int(retry) + 1})
            return
        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, RecursionError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        if not isinstance(payload, dict):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        email = payload.get("email", "")
        if not isinstance(email, str) or not EMAIL_RE.match(email.strip()):
            # Enumeration defense: still return 200 with neutral body. Don't leak
            # whether the address shape was valid via different status codes.
            _json_response(self, 200, {"ok": True, "message": "If that address is valid, a link is on the way."})
            return
        email = email.strip()
        token, _exp = auth.issue_link_token(email)
        mailer.send_login_link_email(email, token)
        _json_response(self, 200, {"ok": True, "message": "Check your inbox for a sign-in link."})

    def _handle_anchor_batch(self) -> None:
        """Anchor up to 50 hashes in one request. Same auth model as /api/anchor.

        Each item gets its own receipt (one OTS submission per hash; calendars
        already batch internally). Useful for the folder-watcher CLI sending
        a backlog. API-key auth bypasses the rate limit; pack tokens consume
        one credit per item; subscribers anchor under their session.
        """
        if ORPHO_DISABLE_ANCHORING:
            _json_response(self, 503, {"error": "anchoring temporarily unavailable"})
            return
        payload = self._anchor_payload(MAX_BATCH_BODY_BYTES)
        if payload is None:
            return
        # Auth resolution mirrors /api/anchor but with one twist: pack-token
        # credit-consumption happens per-item below so partial fills work.
        pack_token = self.headers.get("X-Pack-Token", "").strip()
        api_key = self.headers.get("X-Orpho-Api-Key", "").strip()
        api_key_email = api_keys.email_for_key(api_key) if api_key else None
        # _subscription_active_for (not subscriptions.is_active) — the former
        # resolves TEAM-INHERITED subscriptions. With the direct call, a team
        # member's batch anchors were tagged source="free" and expire_worker
        # rmtree'd them after 30 days: silent data loss for a paying customer.
        api_key_active = bool(api_key_email and _subscription_active_for(api_key_email))
        session_email = self._session_email()
        sub_active = api_key_active or bool(session_email and _subscription_active_for(session_email))
        # The account whose subscription pays is the one the receipt is filed
        # under. A lapsed key's owner is not paying when the session is, and
        # /api/anchor already treats that key as not subscribed.
        effective_email = api_key_email if api_key_active else session_email

        items = payload.get("hashes") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            _json_response(self, 400, {"error": "expected non-empty 'hashes' array"})
            return
        if len(items) > MAX_BATCH_ITEMS:
            _json_response(self, 400, {"error": f"too many items (max {MAX_BATCH_ITEMS})"})
            return

        # A supplied bearer token must actually fund work.
        paid_available = bool(pack_token and credits.balance(pack_token) > 0)
        if pack_token and not paid_available and not api_key_active and not sub_active:
            # No item can be submitted under this entitlement. Reject before
            # touching free allowance; a bad token must neither buy work nor
            # burn the anonymous request the caller could make instead.
            _json_response(self, 402, {"error": "pack credits exhausted or invalid"})
            return
        free_budget_checked = False
        results: list[dict] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                results.append({"index": idx, "ok": False, "error": "item must be an object"})
                continue
            error = self._anchor_input_error(item, hash_required=True)
            if error:
                results.append({"index": idx, "ok": False, "error": error})
                continue
            # Charge one free request only when a valid item is ready.
            if not paid_available and not api_key_active and not sub_active and not free_budget_checked:
                allowed, retry_after = _anchor_limiter.check(self._client_key())
                if not allowed:
                    Handler._record_demand(self, "free_limit_reached", auth_path="free",
                                           surface="batch", outcome="limited")
                    _json_response(self, 429, {"error": "rate limit exceeded",
                        "retry_after_seconds": int(retry_after) + 1,
                        "limit_per_day": ANCHOR_RATE_CAPACITY,
                        "hint": "Buy a Pack or subscribe to skip rate limits."})
                    return
                free_budget_checked = True
            hash_hex = item.get("hash_hex", "")
            sha512_hex = item.get("sha512_hex")
            client_label = item.get("client_label")
            if isinstance(client_label, str):
                client_label = client_label[:200]
            else:
                client_label = None
            if sha512_hex is not None and not isinstance(sha512_hex, str):
                sha512_hex = None

            # Determine per-item source tag + auth disposition.
            pack_consumed_here = False
            if pack_token and not api_key_active and not sub_active:
                pack_consumed_here, _ = credits.consume_credit(pack_token)
                if not pack_consumed_here:
                    results.append({"index": idx, "ok": False,
                                    "error": "pack credits exhausted",
                                    "client_label": client_label})
                    continue
                source = f"pack:{pack_token[:8]}"
            elif api_key_active:
                source = f"api:{api_key[:10]}"
            elif sub_active:
                source = "sub:" + auth.email_id(effective_email)
            else:
                source = "free"

            try:
                record = engine.anchor_hash(
                    hash_hex,
                    client_label=client_label,
                    sha512_hex=sha512_hex,
                    source=source,
                    account_id=(auth.email_id(effective_email)
                                if source.startswith(("api:", "sub:")) else None),
                )
            except ValueError as e:
                # A credit was consumed above but this item produced NO
                # receipt — refund it. Previously a batch of malformed
                # hashes silently burned one credit per bad item and still
                # returned HTTP 200 with no refund signal at all.
                refunded_here = False
                if pack_consumed_here:
                    credits.refund_credit(pack_token,
                                          reason="batch-item-rejected")
                    refunded_here = True
                results.append({"index": idx, "ok": False, "error": str(e),
                                "credit_refunded": refunded_here,
                                "client_label": client_label})
                continue
            credit_refunded = False
            if pack_consumed_here and record["calendars_ok"] == 0:
                credits.refund_credit(pack_token, reason="batch-refund:no-calendars")
                credit_refunded = True
            site = os.environ.get("SITE_URL", "https://orphograph.com").rstrip("/")
            rid = record["receipt_id"]
            if pack_consumed_here:
                demand_auth_path = "pack"
            elif api_key_active:
                demand_auth_path = "api_key"
            elif sub_active:
                demand_auth_path = "subscription"
            else:
                demand_auth_path = "free"
            Handler._record_demand(self,
                "anchor_succeeded",
                auth_path=demand_auth_path,
                surface="batch",
                outcome="success" if record["calendars_ok"] > 0 else "uncommitted",
                api_key=api_key if api_key_active else "",
                authenticated=api_key_active or sub_active,
                paid=demand_auth_path != "free",
            )
            item_distinct = engine.receipt_distinct_counts(record)
            results.append({
                "index": idx,
                "ok": True,
                "receipt_id": rid,
                "created_at": record["created_at"],
                "client_label": record["client_label"],
                "calendars_ok": record["calendars_ok"],
                "credit_refunded": credit_refunded,
                "calendars_total": record["calendars_total"],
                **item_distinct,
                "low_redundancy": (item_distinct["calendars_distinct_ok"]
                                   < MIN_CALENDARS_OK),
                "receipt_url": f"{site}/r/{rid}",
                "badge_url": f"{site}/api/badge/{rid}.svg",
            })

        succeeded = sum(1 for r in results if r.get("ok"))
        _json_response(self, 200, {
            "ok": True,
            "submitted": len(items),
            "succeeded": succeeded,
            "failed": len(items) - succeeded,
            "results": results,
        })

    def _event_method_not_allowed(self) -> None:
        """Emit 405 Method Not Allowed for /api/event on non-POST.

        Sets Allow: POST per RFC 7231 §6.5.5. No body — we never want
        this endpoint to surface internal state on any method but POST.
        """
        self.send_response(405)
        self.send_header("Allow", "POST")
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        _security_headers(self)
        self.end_headers()

    def _is_head(self) -> bool:
        return self.command == "HEAD"

    def do_HEAD(self):  # noqa: N802
        """HEAD is GET without a body — RFC 9110 §9.3.2.

        This used to answer 501 for every path but /api/event, which broke
        every non-browser client: Cloudflare's security scanner, link
        checkers, uptime monitors and CDN prefetch all probe with HEAD.
        Because the 501 error page carried none of our response headers,
        the scanner concluded the site had no HSTS and no security.txt when
        in fact both are served correctly on GET.

        Status and headers MUST match what GET would return, and the body
        MUST be absent, so we run the ordinary GET routing and discard
        everything written after the header block.
        """
        path = self.path.split("?", 1)[0]
        if path == "/api/event":
            self._event_method_not_allowed()
            return

        # Running the GET routing also runs its side effects. HEAD is a safe
        # method (RFC 9110 §9.2.1), so a GET handler that writes must ask
        # `_is_head()` and answer from a read-only lookup instead.
        real_wfile = self.wfile
        shim = _HeadBodySuppressor(real_wfile)
        inherited_end_headers = self.end_headers

        def _end_headers_then_suppress() -> None:
            inherited_end_headers()
            shim.suppress = True

        self.wfile = shim
        self.end_headers = _end_headers_then_suppress  # type: ignore[method-assign]
        try:
            self.do_GET()
        finally:
            self.wfile = real_wfile
            # Drop the instance attribute so the class method is visible again.
            self.__dict__.pop("end_headers", None)

    def do_OPTIONS(self):  # noqa: N802
        """Advertise the methods we implement rather than claiming none.

        Same defect class as HEAD: a blanket 501 told every preflight and
        capability probe that this server implements nothing.
        """
        path = self.path.split("?", 1)[0]
        if path == "/api/event":
            self._event_method_not_allowed()
            return
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        _security_headers(self)
        self.end_headers()

    def do_PUT(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/event":
            self._event_method_not_allowed()
            return
        self.send_error(501, "Unsupported method ('PUT')")

    def do_DELETE(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/event":
            self._event_method_not_allowed()
            return
        self.send_error(501, "Unsupported method ('DELETE')")

    def do_PATCH(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/event":
            self._event_method_not_allowed()
            return
        self.send_error(501, "Unsupported method ('PATCH')")

    def _handle_event(self) -> None:
        """Privacy-preserving funnel event collector.

        Accepts: {"event": "<one of FUNNEL_EVENTS>", "page": "<path>"}.
        Rejects any other top-level keys with 400. No cookies, no full
        IPs, no user-agent, no referer recorded — only the truncated IP
        prefix (/24 for v4, /48 for v6) for abuse-detection bucketing.

        Stored row shape (authoritative): {ts, event, page, ip_trunc, ip_src}.

        `ip_trunc` is derived from the real VISITOR address via
        `_resolve_analytics_ip` (CF-Connecting-IP → first X-Forwarded-For
        entry → socket peer), then truncated. Before 2026-07-25 it was the
        socket/Fly-edge address, which behind Cloudflare is a Cloudflare
        egress IP that rotates per request — inflating distinct-IP counts.
        `ip_src` ("cf" | "xff" | "socket") records which source was used, so
        pre-fix rows (no ip_src) are never silently mixed with post-fix rows
        when the funnel is read. Existing lines are left exactly as written.

        Returns 204 No Content on success (success is silent so beacon
        clients don't waste bandwidth on a body they won't read).
        """
        # 60 events / IP / minute. Silent drop on excess — analytics is
        # best-effort, never authoritative; surfacing 429 just teaches an
        # abuser the bucket exists.
        client_key = self._client_key()
        allowed, _ = _event_limiter.check(f"event:{client_key}")
        if not allowed:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            _security_headers(self)
            self.end_headers()
            return
        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        if not isinstance(payload, dict):
            _json_response(self, 400, {"error": "body must be a JSON object"})
            return
        # Strict shape: exactly {event, page}. Extra keys are rejected so
        # callers can't smuggle PII / fingerprints through the schema.
        extra = set(payload.keys()) - FUNNEL_EVENT_FIELDS
        if extra:
            _json_response(self, 400, {"error": "unexpected fields", "fields": sorted(extra)})
            return
        event = payload.get("event")
        page = payload.get("page")
        if not isinstance(event, str) or event not in FUNNEL_EVENTS:
            _json_response(self, 400, {"error": "invalid event"})
            return
        if not isinstance(page, str) or not page:
            _json_response(self, 400, {"error": "invalid page"})
            return
        # Bound page UTF-8 bytes; the client only ever sends location.pathname
        # which is well under this cap. We do NOT coerce the value — it's
        # written verbatim so the funnel report can show real paths.
        try:
            page = page.encode("utf-8")[:MAX_EVENT_PAGE_LEN].decode("utf-8", errors="ignore")
        except UnicodeEncodeError:
            _json_response(self, 400, {"error": "invalid page"})
            return
        # NOT client_key: that is the rate-limit bucket (Fly-edge address,
        # i.e. Cloudflare behind the CDN). The recorded row wants the real
        # visitor, truncated the same way.
        ip_trunc, ip_src = self._analytics_ip()
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "page": page,
            "ip_trunc": ip_trunc,
            "ip_src": ip_src,
        }
        analytics.append_event(row, path=FUNNEL_EVENTS_PATH)
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        _security_headers(self)
        self.end_headers()

    def _handle_waitlist(self) -> None:
        # Same per-IP rate limit as the auth endpoint to prevent spam.
        allowed, retry = _anchor_limiter.check(f"waitlist:{self._client_key()}")
        if not allowed:
            _json_response(self, 429, {"error": "too many requests", "retry_after_seconds": int(retry) + 1})
            return
        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        if not isinstance(payload, dict):
            _json_response(self, 400, {"error": "body must be a JSON object"})
            return
        email = payload.get("email", "")
        interest = payload.get("interest", "personal")
        # A lone surrogate (JSON "\ud800") matches EMAIL_RE but is not text:
        # it cannot be encoded, so it is never stored or mailed.
        if (not isinstance(email, str) or not EMAIL_RE.match(email.strip())
                or not _utf8_encodable(email)):
            # Don't leak whether the address was valid.
            _json_response(self, 200, {"ok": True})
            return
        if not isinstance(interest, str) or not _utf8_encodable(interest):
            interest = "personal"
        waitlist.add(email.strip(), interest)
        _json_response(self, 200, {"ok": True, "message": "On the list."})

    # Neutral response for the pack-recovery endpoint. Identical wording is
    # returned whether or not the address has a pack on file so the endpoint
    # can never be used to enumerate which emails purchased a Pack.
    _PACK_RECOVER_NEUTRAL = (
        "If a pack is associated with that email, we've sent the code(s)."
    )

    def _handle_pack_recover(self) -> None:
        """Lost-code recovery for a Pack buyer.

        Input:  { "email": "you@example.com" }
        Output: ALWAYS the same neutral 200 (see _PACK_RECOVER_NEUTRAL) so the
                endpoint leaks nothing about which addresses own a Pack.

        On a genuine hit, the customer's claim code(s) — those still holding a
        positive balance — are re-sent to the address on file via
        mailer.send_pack_claim_email. No new code is minted; no PII is stored.

        Rate-limited per IP with the shared _anchor_limiter (same pattern as
        /api/waitlist and /api/recover) to blunt enumeration/mail-flood abuse.

        Residual side-channel (accepted for this threat model): a hit performs
        a ledger scan + mail send while a miss returns almost immediately, so
        response timing weakly distinguishes the two. The rate limit bounds how
        much an attacker can sample this.
        """
        allowed, retry = _anchor_limiter.check(f"pack_recover:{self._client_key()}")
        if not allowed:
            _json_response(self, 429, {"error": "too many requests", "retry_after_seconds": int(retry) + 1})
            return
        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, RecursionError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        if not isinstance(payload, dict):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        email = payload.get("email", "")
        # A malformed address returns the SAME neutral response as a valid one
        # with no pack — never a distinguishing 400 (mirrors _handle_waitlist).
        if isinstance(email, str) and EMAIL_RE.match(email.strip()):
            addr = email.strip()
            try:
                codes = credits.find_claim_codes_by_email(addr)
                for code in codes:
                    remaining = credits.balance(code)
                    # Only re-send codes with anchors still on them — a fully
                    # spent pack has nothing to reuse, and a "Pack of 0" notice
                    # would be misleading. The neutral response is unchanged.
                    if remaining > 0:
                        try:
                            mailer.send_pack_claim_email(addr, code, remaining)
                        except Exception:
                            # Never surface a mailer failure — it would turn the
                            # neutral response into an oracle.
                            pass
            except Exception:
                pass
        _json_response(self, 200, {"ok": True, "message": self._PACK_RECOVER_NEUTRAL})

    def _handle_affiliate_payout(self) -> None:
        """POST /api/me/affiliate/payout.

        do_POST dispatches here, but the handler was never defined — every
        request 500'd with an AttributeError. The backing logic exists
        (affiliate.request_payout), but self-serve payouts are intentionally
        NOT enabled yet: the BTC/credit payout rail is a deferred feature and
        the "credits" method would auto-grant boosted value. Fail CLOSED with a
        clear, honest message instead of crashing, and never settle value
        autonomously. To enable self-serve payouts later, wire this handler to
        affiliate.request_payout(email, method, destination) behind a session
        check (see _handle_refund_request for the session-gating pattern).
        """
        _json_response(self, 503, {
            "ok": False,
            "reason": "payouts_not_self_serve",
            "error": "Referral payouts are settled manually for now — reply to "
                     "your referral email with your payout details and we'll "
                     "process it.",
        })

    def _parse_unsub_email(self) -> str:
        """Extract ?e=<email> from the request path. Returns '' if absent/invalid."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        e = (qs.get("e") or [""])[0].strip()
        if not e or not EMAIL_RE.match(e):
            return ""
        # Deliberately NO tighter than EMAIL_RE, the check every intake path
        # uses. An address we accepted and may mail must always be able to
        # unsubscribe (CAN-SPAM, RFC 8058); refusing odd-looking ones here
        # would strand exactly those. EMAIL_RE is only a shape check and does
        # admit markup characters, so every place this value is SHOWN escapes
        # it — that is the guard, not rejection.
        return e

    def _handle_unsubscribe_get(self) -> None:
        """Confirmation page for marketing-email unsubscribe.

        CAN-SPAM, GDPR Art. 21, CASL, LGPD all accept a single-click flow.
        We process the unsubscribe on GET too (idempotent) so users who
        merely click the link from their inbox don't need a second action.
        """
        email = self._parse_unsub_email()
        if not email:
            self.send_error(400, "invalid email")
            return
        try:
            if self._is_head():
                # A scanner that only looked at the link must not unsubscribe
                # the recipient. Still read the ledger, so HEAD answers 503
                # exactly when GET would.
                unsubscribe.would_add(email)
            else:
                unsubscribe.add(email, source="link_get")
        except unsubscribe.SuppressionUnavailable:
            # Without this the socket just closed: the visitor could not tell
            # whether the unsubscribe was recorded. It was not. Say so.
            self.send_error(503, "We could not record this just now. "
                                 "Please try the link again in a few minutes.")
            return
        from html import escape as _h
        body = (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>Unsubscribed — Orphograph</title>"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta name=\"robots\" content=\"noindex\">"
            f"{_SITE_STYLESHEET_LINKS}</head><body class=\"orpho\">"
            "<main class=\"blog-post\"><article class=\"post-header\">"
            "<h1>Done — you're unsubscribed.</h1>"
            # In the header, not .post-body: `.post-body p` out-specifies
            # `.muted`, and the error page places its muted line here too.
            "<p class=\"post-meta muted\">If this was a mistake, just sign in "
            "again or buy a pack and you'll be re-enrolled per your action.</p>"
            "</article><section class=\"post-body\">"
            # The address arrives in a URL anyone can craft. Escape on output.
            f"<p>We've removed <strong>{_h(email)}</strong> from all marketing "
            "email. You will still receive <em>transactional</em> mail "
            "tied to actions you take on the site (receipts, sign-in "
            "links, pack codes) — those are required by the service "
            "itself, not promotional.</p>"
            # One sentence whether or not the address was already there: the
            # two used to differ, so a HEAD (which writes nothing) told anyone
            # holding an address whether its owner had unsubscribed.
            "<p>Confirmed — this address is on the suppression list.</p>"
            "<p><a href=\"/\">Back to Orphograph</a></p>"
            "</section></main></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The page carries the recipient's address.
        self.send_header("Cache-Control", "no-store")
        _security_headers(self)
        self.end_headers()
        self.wfile.write(body)

    def _handle_payout_status(self) -> None:
        """JSON endpoint — founder-only view of hot BTC balance + sweep status.

        Gated by ORPHO_FOUNDER_TOKEN via header `X-Orpho-Founder`. Customers
        have no need to see this; exposing it publicly would leak the
        founder's revenue cadence to anyone who polls. If the token is unset,
        endpoint returns 404 (looks like the endpoint doesn't exist).
        """
        if payout_monitor is None:
            self.send_error(404, "not found")
            return
        if not self._founder_authorized():
            self.send_error(404, "not found")  # Lie about endpoint existence.
            return
        _json_response(self, 200, payout_monitor.payout_status())

    def _handle_founder_metrics(self) -> None:
        """JSON endpoint — founder-only revenue metrics (MRR, ARR, churn, LTV).

        Gated by ORPHO_FOUNDER_TOKEN via header `X-Orpho-Founder`. Returns:
        {
          "timestamp": "2026-05-14T...",
          "period_days": 90,
          "mrr": 1234.56,
          "arr": 14814.72,
          "churn_rate": 0.05,
          "customers": { "active": 12, "churned_this_month": 2, "total": 14 },
          "ltv": 15000.00
        }
        """
        if not self._founder_authorized():
            self.send_error(404, "not found")
            return
        # Import here to avoid circular dependency
        import analytics
        metrics = analytics.metrics(days_back=90)
        # Demand readout (2026-08-19). Revenue metrics answer "did anyone
        # pay"; this answers "did anyone ASK", which is the prior question
        # while external revenue is zero. An unreadable waitlist reports
        # UNAVAILABLE rather than zero -- a failed read must never render as
        # a measured absence of demand.
        try:
            import newsletter
            metrics["waitlist"] = newsletter.audience_snapshot()
        except Exception as e:  # noqa: BLE001 - readout must not 500 the page
            metrics["waitlist"] = {"error": f"unavailable: {e}"}
        _json_response(self, 200, metrics)

    def _handle_founder_customer_lookup(self) -> None:
        """JSON endpoint — founder-only customer lookup by email.

        Query params: ?email=buyer@example.com
        Returns customer profile: anchors, purchases, subscription, total spent.
        """
        if not self._founder_authorized():
            self.send_error(404, "not found")
            return

        # Parse email from query string
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        email = (params.get("email") or [""])[0].strip()

        if not email:
            _json_response(self, 400, {"error": "email param required"})
            return

        # Import here to avoid circular dependency
        import support_tools
        customer = support_tools.lookup_customer(email)
        if not customer:
            _json_response(self, 404, {"error": "customer not found"})
            return
        _json_response(self, 200, customer)

    def _handle_team_create(self) -> None:
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        if not subscriptions.is_active(email):
            _json_response(self, 402, {"error": "creating a team requires an active subscription"})
            return
        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, RecursionError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        if not isinstance(payload, dict):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        wrong = _wrong_type_field(payload, "team_name")
        if wrong:
            _json_response(self, 400, {"error": f"{wrong} must be a string"})
            return
        name = _json_str(payload, "team_name").strip()[:80]
        try:
            team_id = teams.create_team(email, name or "My Team")
        except ValueError as e:
            _json_response(self, 400, {"error": str(e)})
            return
        _json_response(self, 200, {"ok": True, "team_id": team_id})

    def _handle_team_invite(self) -> None:
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        t = teams.team_for_member(email)
        if not t or t.get("owner") != email:
            _json_response(self, 403, {"error": "only the team owner can issue invites"})
            return
        if not subscriptions.is_active(email):
            _json_response(self, 402, {"error": "active subscription required to issue invites"})
            return
        code = teams.issue_invite_code(t["team_id"], email)
        if not code:
            _json_response(self, 500, {"error": "could not issue invite"})
            return
        # Body may be empty; we don't need anything from it.
        length = _read_content_length(self)
        if 0 < length <= MAX_BODY_BYTES:
            try:
                self.rfile.read(length)
            except OSError:
                pass
        site = os.environ.get("SITE_URL", "").rstrip("/")
        share_url = f"{site}/team/join?code={code}" if site else f"/team/join?code={code}"
        _json_response(self, 200, {
            "ok": True,
            "invite_code": code,
            "share_url": share_url,
            "expires_at": None,
            "note": "Single-use. Share with the person you want to add.",
        })

    def _handle_team_redeem(self) -> None:
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, RecursionError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        if not isinstance(payload, dict):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        wrong = _wrong_type_field(payload, "invite_code")
        if wrong:
            _json_response(self, 400, {"error": f"{wrong} must be a string"})
            return
        code = _json_str(payload, "invite_code").strip()
        result = teams.redeem_invite_code(code, email)
        status = 200 if result.get("ok") else 400
        _json_response(self, status, result)

    def _handle_team_remove(self) -> None:
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        t = teams.team_for_member(email)
        if not t or t.get("owner") != email:
            _json_response(self, 403, {"error": "only the team owner can remove members"})
            return
        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, RecursionError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        if not isinstance(payload, dict):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        wrong = _wrong_type_field(payload, "member_email")
        if wrong:
            _json_response(self, 400, {"error": f"{wrong} must be a string"})
            return
        member_email = _json_str(payload, "member_email").strip().lower()
        if not member_email:
            _json_response(self, 400, {"error": "member_email required"})
            return
        ok = teams.remove_member(t["team_id"], email, member_email)
        _json_response(self, 200, {"ok": ok})

    def _handle_team_leave(self) -> None:
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        # Drain body (may be empty)
        length = _read_content_length(self)
        if 0 < length <= MAX_BODY_BYTES:
            try:
                self.rfile.read(length)
            except OSError:
                pass
        ok = teams.leave_team(email)
        _json_response(self, 200, {"ok": ok})

    def _handle_toggle_receipt_privacy(self) -> None:
        """POST /api/me/receipt/<id>/privacy — toggle private flag.

        Owner-only: requires session cookie matching the receipt owner_id.
        Body: { "private": true | false }
        """
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        if not subscriptions.is_active(email):
            _json_response(self, 402, {"error": "private receipts require an active subscription"})
            return
        # Extract receipt id from path /api/me/receipt/<id>/privacy
        prefix = "/api/me/receipt/"
        suffix = "/privacy"
        if not (self.path.startswith(prefix) and self.path.endswith(suffix)):
            self.send_error(404)
            return
        rid = self.path[len(prefix):-len(suffix)]
        if not RECEIPT_ID_RE.match(rid):
            _json_response(self, 400, {"error": "invalid receipt id"})
            return
        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, RecursionError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        if not isinstance(payload, dict):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        want_private = bool(payload.get("private", False))
        # Load receipt + verify ownership
        rfile = engine.RECEIPTS_DIR / rid / "receipt.json"
        if not rfile.exists():
            _json_response(self, 404, {"error": "receipt not found"})
            return
        try:
            rec = json.loads(rfile.read_text())
        except (OSError, json.JSONDecodeError):
            _json_response(self, 500, {"error": "could not read receipt"})
            return
        viewer_id = auth.email_id(email)
        # Ownership covers API-key-anchored receipts too — they are the
        # customer's own. This used to compare against sub:<id> alone, so a
        # subscriber who anchored through the API was told their own receipt
        # did not exist. 404 (not 403) is deliberate: it still refuses to
        # reveal whether a receipt exists for a DIFFERENT owner.
        if not _receipt_belongs_to(rec, email):
            _json_response(self, 404, {"error": "receipt not found"})
            return
        # Preserve the proven account association before clearing a legacy
        # private owner. Privacy changes must never erase account ownership.
        rec.setdefault("account_id", viewer_id)
        rec["private"] = want_private
        rec["owner_id"] = viewer_id if want_private else None
        # Atomic write: a crash mid-write_text would leave a truncated
        # receipt.json and permanently corrupt the user's verifiable proof.
        # Write to a sibling tmp file, then os.replace (POSIX-atomic rename).
        tmp = rfile.with_suffix(rfile.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(rec, indent=2))
            os.replace(tmp, rfile)
        except OSError as e:
            sys.stderr.write(f"[privacy-toggle] atomic write failed for {rfile}: {e}\n")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            _json_response(self, 500, {"error": "could not update receipt"})
            return
        _json_response(self, 200, {
            "ok": True,
            "receipt_id": rid,
            "private": want_private,
        })

    def _handle_founder_admin_toggles(self) -> None:
        """JSON endpoint — view/manage operational admin toggles.

        GET: returns current toggle state (founder-only, token-gated)
        Response: {
          "maintenance_mode": bool,
          "checkout_disabled": bool,
          "anchoring_disabled": bool,
          "timestamp": "2026-05-15T..."
        }
        """
        if not self._founder_authorized():
            self.send_error(404, "not found")
            return

        _json_response(self, 200, {
            "maintenance_mode": ORPHO_MAINTENANCE_MODE,
            "checkout_disabled": ORPHO_DISABLE_CHECKOUT,
            "anchoring_disabled": ORPHO_DISABLE_ANCHORING,
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "notice": "Toggles are controlled by environment variables. SSH into the server to change them: fly ssh console, then 'fly secrets set ORPHO_MAINTENANCE_MODE=1'",
        })

    def _handle_founder_morning_summary(self) -> None:
        """JSON endpoint — single-call snapshot for the login-trigger morning-check script.

        Gated by ORPHO_FOUNDER_TOKEN via header X-Orpho-Founder. Aggregates the
        three pieces of state the founder asked to see on every login:
          1. Website health (counts, ledger_bytes, uptime, last-anchor age)
          2. Paying customers (MRR, active count, churned-this-month)
          3. Customer feedback (pending refund requests, recent support events)
        """
        if not self._founder_authorized():
            self.send_error(404, "not found")
            return

        now_utc = datetime.now(timezone.utc)
        today_iso = now_utc.date().isoformat()

        # 1. Health snapshot
        try:
            import health as _health
            hs = _health.snapshot()
        except Exception as e:  # noqa: BLE001
            hs = {"error": f"{type(e).__name__}"}

        # 2. Revenue snapshot
        try:
            import analytics as _analytics
            metrics = _analytics.metrics(days_back=30)
        except Exception as e:  # noqa: BLE001
            metrics = {"error": f"{type(e).__name__}"}

        # 3. Feedback / inbox snapshot — count pending refund_requests + recent events
        feedback = {"refund_requests_pending": 0, "refund_requests_today": 0,
                    "recent_events_24h": 0}
        try:
            ledger_path = Path(os.environ.get(
                "ORPHO_REFUND_LEDGER",
                str(ROOT / "data" / "refund_requests.jsonl"),
            ))
            if ledger_path.exists():
                pending = 0
                today_n = 0
                with ledger_path.open() as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        pending += 1
                        if str(rec.get("ts", "")).startswith(today_iso):
                            today_n += 1
                feedback["refund_requests_pending"] = pending
                feedback["refund_requests_today"] = today_n
        except OSError:
            pass

        try:
            # The ledger the collector writes (DATA_DIR; /app/data in production,
            # where ROOT/data is the same directory).
            events_path = FUNNEL_EVENTS_PATH
            if events_path.exists():
                cutoff = now_utc - timedelta(hours=24)
                n = 0
                # Read only the last 64 KiB: a magnitude estimate, not a full
                # scan. The ledger is capped and compacted by analytics, which
                # replaces it whole, so this unlocked read sees a complete file.
                with events_path.open("rb") as f:
                    f.seek(0, 2)
                    end = f.tell()
                    f.seek(max(0, end - 65536))
                    tail = f.read().decode("utf-8", errors="ignore")
                for line in tail.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = rec.get("ts") or rec.get("timestamp")
                    if not ts:
                        continue
                    try:
                        when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if when >= cutoff:
                        n += 1
                feedback["recent_events_24h"] = n
        except OSError:
            pass

        _json_response(self, 200, {
            "timestamp": now_utc.isoformat() + "Z",
            "health": hs,
            "revenue": metrics,
            "feedback": feedback,
        })

    def _handle_founder_funnel(self) -> None:
        """JSON funnel rollup from data/events.jsonl.

        Gated by ORPHO_FOUNDER_TOKEN via header X-Orpho-Founder. Returns
        per-day event counts for the 4 funnel events, conversion rates
        between adjacent stages, and a 30-day rolling total.
        """
        if not self._founder_authorized():
            self.send_error(404, "not found")
            return

        # The ledger the collector writes (DATA_DIR; /app/data in production,
        # where ROOT/data is the same directory).
        events_path = FUNNEL_EVENTS_PATH
        funnel_events = ["drop_zone_visible", "file_anchored", "checkout_clicked", "checkout_returned_success"]
        now_utc = datetime.now(timezone.utc)
        cutoff = now_utc - timedelta(days=30)

        per_day: dict[str, dict[str, int]] = {}  # date_iso -> event -> count
        totals: dict[str, int] = {e: 0 for e in funnel_events}
        total_lines = 0
        if events_path.exists():
            try:
                with events_path.open("rb") as f:
                    raw = f.read().decode("utf-8", errors="ignore")
                for line in raw.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    total_lines += 1
                    ev = rec.get("event")
                    ts = rec.get("ts") or rec.get("timestamp")
                    if not ev or not ts:
                        continue
                    if ev not in funnel_events:
                        continue
                    try:
                        when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if when < cutoff:
                        continue
                    day = when.date().isoformat()
                    per_day.setdefault(day, {e: 0 for e in funnel_events})
                    per_day[day][ev] = per_day[day].get(ev, 0) + 1
                    totals[ev] += 1
            except OSError:
                pass

        # NOTHING OBSERVED IS NOT ZERO PERCENT. A rate whose denominator is 0
        # used to render as 0.0, which a founder reads as "nobody converted" —
        # a measurement — when the truth is "nothing was measured". The two
        # are opposite signals: the first says the funnel is broken, the second
        # says the instrument is. Return null plus a reason instead, and say
        # which rates could not be computed.
        #
        # This was not hypothetical. checkout_to_paid and visible_to_paid are
        # both driven by checkout_returned_success, whose ONLY emitter is
        # web/buy.js. When the BTC-rail retirement deleted that file, both
        # rates sat at 0.0 with no error anywhere.
        unmeasured: dict[str, str] = {}

        def _rate(name: str, num: int, den: int, den_event: str):
            if den:
                return round(100.0 * num / den, 1)
            unmeasured[name] = (
                f"no {den_event} events in the window — nothing to measure "
                f"against, so this is not 0%"
            )
            return None

        rates_30d = {
            "visible_to_anchored": _rate(
                "visible_to_anchored", totals["file_anchored"],
                totals["drop_zone_visible"], "drop_zone_visible"),
            "anchored_to_checkout": _rate(
                "anchored_to_checkout", totals["checkout_clicked"],
                totals["file_anchored"], "file_anchored"),
            "checkout_to_paid": _rate(
                "checkout_to_paid", totals["checkout_returned_success"],
                totals["checkout_clicked"], "checkout_clicked"),
            "visible_to_paid": _rate(
                "visible_to_paid", totals["checkout_returned_success"],
                totals["drop_zone_visible"], "drop_zone_visible"),
        }

        days_sorted = sorted(per_day.keys(), reverse=True)
        series = [{"date": d, **per_day[d]} for d in days_sorted]

        # The ledger is capped: a compaction drops its oldest rows and leaves
        # a marker. If the marker's oldest kept row is inside the window, the
        # totals are a lower bound for a shorter window, not 30-day counts.
        marker = analytics.compaction_marker(events_path)
        window_complete = True
        if marker:
            try:
                kept_from = datetime.fromisoformat(
                    str(marker.get("oldest_kept_ts", "")).replace("Z", "+00:00"))
                window_complete = kept_from <= cutoff
            except ValueError:
                window_complete = False

        _json_response(self, 200, {
            "timestamp": now_utc.isoformat() + "Z",
            "totals_30d": totals,
            "rates_30d_pct": rates_30d,
            # Which rates are null, and why. Empty when everything computed.
            "unmeasured_reason": unmeasured,
            "events_scanned": total_lines - (1 if marker else 0),
            "window_complete": window_complete,
            "ledger_compacted": marker,
            "series_by_day": series,
        })

    def _handle_unsubscribe_post(self) -> None:
        """RFC 8058 one-click POST endpoint.

        Gmail / Yahoo / Microsoft bulk-sender programs require this exact
        path: POST with List-Unsubscribe-Post: List-Unsubscribe=One-Click.
        Body may be form-encoded or empty.
        """
        email = self._parse_unsub_email()
        if not email:
            _json_response(self, 400, {"error": "invalid email"})
            return
        # Drain body without reading large payloads.
        length = _read_content_length(self)
        if 0 < length <= 4096:
            try:
                self.rfile.read(length)
            except OSError:
                pass
        try:
            unsubscribe.add(email, source="link_post")
        except unsubscribe.SuppressionUnavailable:
            # A mailbox provider's one-click POST must get an answer it can
            # retry on, never a dropped connection read as success or failure
            # at the provider's discretion.
            _json_response(self, 503, {"error": "suppression ledger unavailable; retry"})
            return
        _json_response(self, 200, {"ok": True})

    def _handle_issue_api_key(self) -> None:
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        if not subscriptions.is_active(email):
            _json_response(self, 402, {"error": "API access requires an active subscription"})
            return
        key = api_keys.issue(email)
        _json_response(self, 200, {
            "ok": True,
            "api_key": key,
            "message": "Save this key now — we cannot show it again. Any previous key has been revoked.",
        })

    def _handle_revoke_api_key(self) -> None:
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        revoked = api_keys.revoke(email)
        _json_response(self, 200, {"ok": True, "revoked": revoked})

    def _handle_webhook_register(self) -> None:
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        # Subscriber-tier benefit: webhooks ride on the same gate as
        # private receipts and API keys. Free tier cannot register.
        if not _subscription_active_for(email):
            _json_response(self, 402, {"error": "webhooks require an active subscription"})
            return
        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, RecursionError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        if not isinstance(payload, dict):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        wrong = _wrong_type_field(payload, "url")
        if wrong:
            _json_response(self, 400, {"error": f"{wrong} must be a string"})
            return
        url = _json_str(payload, "url").strip()
        result = webhooks.register(email=email, url=url)
        if not result.get("ok"):
            _json_response(self, 400, {"error": result.get("reason", "register_failed")})
            return
        # The secret is returned ONCE here; clients must persist it.
        _json_response(self, 200, result)

    def _handle_ln_quote(self) -> None:
        """L402 quote: mint an invoice + macaroon for one pay-per-anchor.

        Proactive form of the 402 challenge — an agent can fetch payment
        terms before burning a free-tier slot. 503 until the founder arms a
        Lightning backend (fly secrets), so today's behavior is unchanged.
        """
        if not lightning.configured():
            _json_response(self, 503, {
                "error": "lightning payments not configured",
                "hint": "card packs and subscriptions remain available"})
            return
        ok, inv = lightning.create_invoice(lightning.PRICE_SATS,
                                           "orphograph anchor")
        if not ok:
            _json_response(self, 503, {"error": f"invoice creation failed: {inv}"})
            return
        macaroon = lightning.mint_macaroon(inv["payment_hash"], lightning.PRICE_SATS)
        _json_response(self, 200, {
            "price_sats": lightning.PRICE_SATS,
            "invoice": inv["bolt11"],
            "macaroon": macaroon,
            "expires_in_seconds": lightning.MACAROON_TTL_SEC,
            "how": "pay the invoice, then POST /api/anchor with "
                   "Authorization: L402 <macaroon>:<preimage_hex>",
            "scope": "one anchor per payment; the receipt then verifies "
                     "independently of Orphograph forever",
        })

    def _handle_anchor_folder(self) -> None:
        """POST /api/anchor_folder, one request in flight per client address."""
        key = self._client_key()
        with _folder_in_flight_lock:
            busy = key in _folder_in_flight
            if not busy:
                _folder_in_flight.add(key)
        if busy:
            self.send_response(429)
            body = json.dumps({
                "error": "a folder anchor from this address is already in progress",
                "detail": "Send folder manifests one at a time. Nothing was charged.",
                "retry_after_seconds": 5,
            }).encode("utf-8")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Retry-After", "5")
            self.send_header("Content-Length", str(len(body)))
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            self._anchor_folder_one()
        finally:
            with _folder_in_flight_lock:
                _folder_in_flight.discard(key)

    def _anchor_folder_one(self) -> None:
        """Anchor a folder-Merkle root.

        Body: { manifest: <orphograph-merkle-v1-rfc6962 manifest>, client_label? }
        The server reconstructs the tree from the supplied manifest, verifies
        the recomputed root matches manifest.root_hex, then submits the root
        to OpenTimestamps via the existing single-hash anchoring path. The
        manifest is persisted alongside the receipt under
        ``RECEIPTS_DIR/<rid>/manifest.json`` so inclusion proofs can be
        served later without rebuilding from the original folder.
        """
        if ORPHO_DISABLE_ANCHORING:
            # _json_response, NOT _reject: `_reject` is defined as a LOCAL
            # function further down this same body, so Python binds the name
            # local for the whole function and calling it here raised
            # UnboundLocalError -- the operator got a 500 with no detail
            # instead of the 503 with retry guidance, precisely when the
            # service was deliberately paused. This also matches the sibling
            # branch in /api/anchor (see the same toggle above). No credit has
            # been consumed at this point, so _reject's refund path is not
            # needed here anyway.
            _json_response(self, 503, {
                "error": "anchoring temporarily unavailable",
                "detail": "Calendar service unavailable. Anchoring is temporarily disabled.",
            })
            return
        # Authentication / paid-path: same precedence as /api/anchor.
        pack_token = self.headers.get("X-Pack-Token", "").strip()
        pack_consumed = False
        pack_available = bool(pack_token and credits.balance(pack_token) > 0)

        def _reject(code: int, payload: dict) -> None:
            """Respond to a REJECTED folder anchor, refunding the credit.

            Validation precedes accounting. Failures after the eventual
            consume still refund exactly once through this responder.
            """
            if pack_consumed:
                credits.refund_credit(pack_token, reason="folder-anchor-rejected")
                payload = {**payload, "credit_refunded": True}
            if code == 400:
                _folder_reject_limiter.check(client_key)
            _json_response(self, code, payload)

        def _limited(retry_after: float) -> None:
            Handler._record_demand(self,
                "free_limit_reached", auth_path="free", surface="folder",
                outcome="limited")
            self.send_response(429)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Retry-After", str(int(retry_after) + 1))
            body = json.dumps({
                "error": "rate limit exceeded",
                "retry_after_seconds": int(retry_after) + 1,
                "limit_per_day": ANCHOR_RATE_CAPACITY,
                "hint": "Buy a Pack or sign in to anchor without rate limits.",
            }).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body)

        client_key = self._client_key()
        api_key = self.headers.get("X-Orpho-Api-Key", "").strip()
        api_key_email = api_keys.email_for_key(api_key) if api_key else None
        api_key_active = bool(api_key_email and _subscription_active_for(api_key_email))
        subscriber_email = api_key_email or (self._session_email() if not pack_available else None)
        subscription_active = api_key_active or _subscription_active_for(subscriber_email)
        # Both gates look without spending, before the body is read: a caller
        # whose manifests keep failing, or a free caller with no allowance
        # left, is answered before 8 MB is parsed. Spending stays after
        # validation, so input that fails still costs no allowance.
        reject_tokens = _folder_reject_limiter.peek(client_key)
        if reject_tokens < 1.0:
            retry = int((1.0 - reject_tokens) / FOLDER_REJECT_REFILL) + 1
            self.send_response(429)
            body = json.dumps({
                "error": "too many rejected manifests",
                "detail": ("Recent manifests from this address failed validation. "
                           "Nothing was charged; check the manifest and retry later."),
                "retry_after_seconds": retry,
            }).encode("utf-8")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Retry-After", str(retry))
            self.send_header("Content-Length", str(len(body)))
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body)
            return
        if not pack_available and not subscription_active:
            free_tokens = _anchor_limiter.peek(client_key)
            if free_tokens < 1.0:
                _limited((1.0 - free_tokens) / _anchor_limiter.refill_per_sec)
                return
        payload = self._anchor_payload(MAX_FOLDER_MANIFEST_BYTES)
        if payload is None:
            _folder_reject_limiter.check(client_key)
            return
        error = self._anchor_input_error(payload)
        if error:
            _reject(400, {"error": error})
            return
        # Accept either { manifest: {...}, client_label?: "..." } or the raw
        # manifest as the top-level object. The frontend currently posts the
        # raw manifest; future API consumers may wrap it. The
        # algorithm-tag check is unambiguous because the wrapper shape has no
        # "algorithm" field.
        if isinstance(payload.get("manifest"), dict):
            manifest = payload["manifest"]
        elif payload.get("algorithm") == merkle.ALGORITHM:
            manifest = payload
        else:
            _reject(400, {"error": "manifest is required"})
            return
        if manifest.get("signature") is not None and not isinstance(manifest["signature"], dict):
            _reject(400, {"error": "manifest signature must be an object"})
            return
        leaves = manifest.get("leaves")
        if not isinstance(leaves, list) or not leaves or len(leaves) > MAX_FOLDER_LEAVES:
            _reject(400, {
                "error": "manifest leaves must be a non-empty list",
                "max_leaves": MAX_FOLDER_LEAVES,
            })
            return
        # Reconstruct the tree from the manifest. from_manifest re-derives
        # every leaf from (path, file_sha256) and the full set of internal
        # nodes, then refuses to instantiate if the recomputed root does not
        # equal manifest.root_hex. This protects against a tampered manifest
        # in which the leaves do not actually commit to the stated root.
        try:
            tree = merkle.MerkleTree.from_manifest(manifest)
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError,
                OverflowError) as e:
            # OverflowError: a leaf size of 1e400 or Infinity parses as a JSON
            # float and int() of it overflows.
            _reject(400, {"error": f"manifest invalid: {e}"})
            return
        # Optional edit-lineage elements (design: docs/DESIGN_EDIT_LINEAGE.md).
        # Validate BEFORE anchoring so a malformed lineage costs a 400, not a
        # burned anchor. verify_tree=False: from_manifest already re-folded
        # the tree two lines up. A plain folder manifest returns None here
        # and anchors exactly as before.
        try:
            lineage_pre = engine.derive_lineage_from_manifest(manifest, verify_tree=False)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError,
                OverflowError) as e:
            _reject(400, {"error": f"lineage invalid: {e}"})
            return
        if lineage_pre is not None:
            # Fail-fast twin of attach_lineage's local-parent rule: if the
            # named parent receipt exists on this server, its anchored hash
            # must equal the committed parent root — catch the contradiction
            # before spending calendar submissions.
            parent_file = engine.RECEIPTS_DIR / lineage_pre["parent_receipt_id"] / "receipt.json"
            if parent_file.exists():
                try:
                    parent_rec = json.loads(parent_file.read_text())
                except (OSError, json.JSONDecodeError):
                    parent_rec = {}
                if parent_rec.get("hash_hex") != lineage_pre["parent_root"]:
                    _reject(400, {
                        "error": "lineage invalid: parent receipt exists but its "
                                 "anchored hash does not match the committed parent root",
                    })
                    return
        # Optional Ed25519 authorship signature. The signature block is
        # additive: a manifest with no signature anchors exactly as before.
        # If a signature IS present, it MUST verify — a manifest that claims
        # a signature but fails verification is worse than no signature.
        sig_verified: bool | None = None
        signer_kid: str | None = None
        if isinstance(manifest.get("signature"), dict):
            if manifest_signature is None:
                _reject(503, {
                    "error": "manifest signature verification unavailable in this build",
                    "detail": "Anchor the manifest without a signature block, or use a build with Ed25519 support.",
                })
                return
            ok, reason = manifest_signature.verify_manifest_signature(manifest)
            if not ok:
                _reject(400, {
                    "error": "manifest signature invalid",
                    "detail": reason,
                })
                return
            import receipt_export
            projected = receipt_export.manifest_view(manifest, redact_paths=False)
            if (manifest_signature.canonical_manifest_bytes(projected) !=
                    manifest_signature.canonical_manifest_bytes(manifest)):
                _reject(400, {"error": "signed manifest contains unsupported fields",
                              "detail": "Sign only manifest schema fields; place request options outside the manifest."})
                return
            sig_verified = True
            signer_kid = manifest["signature"].get("kid")
        root_hex = tree.root_hex()
        client_label = payload.get("client_label")
        if isinstance(client_label, str):
            client_label = client_label[:200]
        else:
            client_label = None
        # Fail closed — see _reject_private. This path is where it bit us:
        # the daily repo anchor asks for private and has been publishing.
        # _reject refunds the pack credit and is the folder path's refunding
        # responder, so the 402 does not cost the caller an anchor.
        if bool(payload.get("private", False)) and not subscription_active:
            _reject(402, {
                "error": "private anchors require an active subscription",
                "detail": (
                    "This request asked for private: true, and this caller is "
                    "not subscription-authenticated. Rather than publish a "
                    "manifest you asked to keep private, no anchor was created."
                ),
                "how_to_proceed": (
                    "Authenticate with an active subscription (session cookie "
                    "or X-Orpho-Api-Key), or resend without `private`."
                ),
                "private_requested": True,
                "private_granted": False,
            })
            return
        want_private = bool(payload.get("private", False))
        # Opt-in: publish the manifest's file paths so a shared certificate
        # renders them to anyone (default keeps paths owner-only). Independent
        # of `private`, which gates the whole receipt to its owner.
        want_public_paths = bool(payload.get("paths_public", False))
        if pack_token:
            pack_consumed, _ = credits.consume_credit(pack_token)
        if not pack_consumed and not subscription_active:
            allowed, retry_after = _anchor_limiter.check(client_key)
            if not allowed:
                _limited(retry_after)
                return
        if pack_consumed:
            source = f"pack:{pack_token[:8]}"
        elif api_key_active:
            source = f"api:{api_key[:10]}"
        elif subscription_active:
            source = "sub:" + auth.email_id(subscriber_email)
        else:
            source = "free"
        try:
            record = engine.anchor_hash(
                root_hex,
                client_label=client_label,
                source=source,
                private=want_private,
                owner_id=auth.email_id(subscriber_email) if (want_private and subscriber_email) else None,
                account_id=(auth.email_id(subscriber_email)
                            if subscriber_email and source.startswith(("api:", "sub:")) else None),
            )
        except ValueError as e:
            _reject(400, {"error": str(e)})
            return
        # Persist the manifest alongside the receipt. The receipt's hash_hex
        # already equals manifest.root_hex, so the OTS anchor binds every
        # leaf transitively: tamper with a single path or file digest, the
        # root changes, the anchor no longer verifies.
        rid = record["receipt_id"]
        # The request body doubles as the manifest; store only the manifest's
        # own schema so request fields (notify_email, private, ...) never
        # land in manifest.json.
        import receipt_export
        manifest_to_store = receipt_export.manifest_view(manifest, redact_paths=False)
        manifest_to_store["receipt_id"] = rid
        manifest_to_store["kind"] = "folder"
        try:
            mpath = engine.RECEIPTS_DIR / rid / "manifest.json"
            mpath.write_text(json.dumps(manifest_to_store, indent=2))
            try:
                os.chmod(mpath, 0o600)
            except OSError:
                pass
        except OSError as e:
            _reject(500, {"error": f"could not persist manifest: {e}"})
            return
        # Mark the receipt itself as a folder anchor so verifiers know to
        # fetch the manifest in addition to the .ots files.
        #
        # The IN-MEMORY record must carry these too, not just the file. Every
        # consumer downstream in this handler is handed `record`, not a
        # re-read of the JSON: the receipt email branches on
        # record["kind"] == "folder" to decide whether to tell the customer to
        # keep a manifest or "the original file". Writing them only to disk
        # meant the email silently took the single-file branch — the fix
        # shipped in 82c0f94 never reached the wire, and the test missed it by
        # calling the mailer directly instead of driving the endpoint.
        record["kind"] = "folder"
        record["leaf_count"] = len(leaves)
        record["merkle_algorithm"] = merkle.ALGORITHM
        try:
            rfile = engine.RECEIPTS_DIR / rid / "receipt.json"
            on_disk = json.loads(rfile.read_text())
            on_disk["kind"] = "folder"
            on_disk["leaf_count"] = len(leaves)
            on_disk["merkle_algorithm"] = merkle.ALGORITHM
            if want_public_paths:
                on_disk["paths_public"] = True
            if sig_verified is not None:
                on_disk["signature_verified"] = sig_verified
                on_disk["signer_kid"] = signer_kid
            rfile.write_text(json.dumps(on_disk, indent=2))
        except OSError:
            pass
        # Mirror committed lineage onto the persisted receipt (design §2.4).
        # Pre-anchor validation above means this can only fail on a race;
        # the receipt itself is already real either way, so a late failure
        # is reported inside the response, never as an error status.
        lineage_out = None
        if lineage_pre is not None:
            try:
                lineage_out = engine.attach_lineage(rid, manifest_to_store)
            except ValueError as e:
                lineage_out = {**lineage_pre, "committed": False,
                               "error": f"lineage not mirrored: {e}"}
        folder_distinct = engine.receipt_distinct_counts(record)
        folder_low_redundancy = (
            folder_distinct["calendars_distinct_ok"] < MIN_CALENDARS_OK)
        response_body = {
            "receipt_id": rid,
            "root_hex": root_hex,
            "leaf_count": len(leaves),
            "kind": "folder",
            "merkle_algorithm": merkle.ALGORITHM,
            "calendars_ok": record["calendars_ok"],
            "calendars_total": record["calendars_total"],
            **folder_distinct,
            # The folder path returned the counts but not the verdict, so a
            # dataset anchored across two calendars looked as healthy as one
            # across four. Same flag, same threshold, same meaning.
            "low_redundancy": folder_low_redundancy,
            "created_at": record["created_at"],
            # Always report the privacy state. The folder response omitted it
            # entirely, so a caller who asked for private had no way to learn
            # the request had been dropped — the silence was half the defect.
            "private": want_private,
        }
        if lineage_out is not None:
            response_body["lineage"] = lineage_out
        if sig_verified is not None:
            response_body["signature_verified"] = sig_verified
            response_body["signer_kid"] = signer_kid
        if want_public_paths:
            response_body["paths_public"] = True

        # Receipt email + webhook. The folder path had NEITHER: a subscriber
        # anchoring a dataset got no anchor.created event, no receipt email,
        # and — because notify_email was never persisted — no notice when the
        # pin landed either. An integration watching the webhook stream saw
        # folder anchors simply not happen. Mirrors the single-file path
        # deliberately; the two diverging is what produced this gap.
        notify_email = payload.get("notify_email")
        candidate = ""
        if isinstance(notify_email, str):
            candidate = notify_email[:200].strip()
        if not candidate and subscription_active and subscriber_email:
            candidate = subscriber_email
        is_paid_anchor = pack_consumed or subscription_active or api_key_active
        if candidate and is_paid_anchor and EMAIL_RE.match(candidate):
            mailer.send_receipt_email(candidate, record)
            try:
                rfile2 = engine.RECEIPTS_DIR / rid / "receipt.json"
                on_disk2 = json.loads(rfile2.read_text())
                on_disk2["notify_email"] = candidate
                rfile2.write_text(json.dumps(on_disk2, indent=2))
                record["notify_email"] = candidate
            except (OSError, json.JSONDecodeError):
                pass
        if subscription_active and subscriber_email:
            webhooks.dispatch("anchor.created", subscriber_email, {
                "receipt_id": rid,
                "hash_hex": record["hash_hex"],
                "sha512_hex": record.get("sha512_hex"),
                "created_at": record["created_at"],
                "client_label": record.get("client_label"),
                "calendars_ok": record["calendars_ok"],
                "calendars_total": record["calendars_total"],
                **folder_distinct,
                "low_redundancy": folder_low_redundancy,
                "private": want_private,
                # Folder-specific, so a receiver can tell the two apart
                # without a follow-up fetch.
                "kind": "folder",
                "leaf_count": len(leaves),
                "root_hex": root_hex,
                "receipt_url": f"{os.environ.get('SITE_URL', 'https://orphograph.com').rstrip('/')}/r/{rid}",
            })
        if pack_consumed:
            demand_auth_path = "pack"
        elif api_key_active:
            demand_auth_path = "api_key"
        elif subscription_active:
            demand_auth_path = "subscription"
        else:
            demand_auth_path = "free"
        Handler._record_demand(self,
            "anchor_succeeded",
            auth_path=demand_auth_path,
            surface="folder",
            outcome="success" if record["calendars_ok"] > 0 else "uncommitted",
            api_key=api_key if api_key_active else "",
            authenticated=api_key_active or subscription_active,
            paid=demand_auth_path != "free",
        )
        response_body["credit_refunded"] = False
        if pack_consumed and record["calendars_ok"] == 0:
            credits.refund_credit(pack_token, reason="folder-refund:no-calendars")
            response_body["credit_refunded"] = True
        _json_response(self, 200, response_body)

    def _handle_verify_folder(self, rid: str) -> None:
        """Return the receipt + manifest for a folder anchor.

        Private folder receipts gate on the session cookie identically to
        single-file private receipts.
        """
        record = engine.verify_receipt(rid)
        if not record.get("found"):
            _json_response(self, 404, {"receipt_id": rid, "found": False, "error": "receipt not found"})
            return
        is_owner = False
        # Privacy first: a private receipt answers exactly like a missing one
        # to anyone but its owner. Checking the kind first answered 400 "not a
        # folder anchor" for a private single-file receipt, which confirmed it
        # exists while /api/verify and /api/badge said 404.
        if record.get("private"):
            session_email = self._session_email()
            viewer_id = auth.email_id(session_email) if session_email else None
            if not viewer_id or viewer_id != record.get("owner_id"):
                _json_response(self, 404, {"receipt_id": rid, "found": False, "error": "receipt not found"})
                return
            is_owner = True
        if record.get("kind") != "folder":
            _json_response(self, 400, {"error": "receipt is not a folder anchor"})
            return
        else:
            session_email = self._session_email()
            viewer_id = auth.email_id(session_email) if session_email else None
            is_owner = bool(viewer_id and viewer_id == record.get("owner_id"))
            record.pop("owner_id", None)
        try:
            manifest = json.loads((engine.RECEIPTS_DIR / rid / "manifest.json").read_text())
        except (OSError, json.JSONDecodeError):
            _json_response(self, 500, {"error": "manifest missing"})
            return
        # Privacy guard: for a public folder receipt viewed by a non-owner,
        # do not echo the full leaf-path list — the path list is workflow
        # metadata that customers may not realise is public. The leaves still
        # appear by index (so verifiers can count and identify them by hash),
        # but the human-readable path is redacted unless the requester is the
        # owner. The full manifest is required to construct inclusion proofs,
        # but inclusion-proof requests already require the caller to KNOW the
        # path — so withholding the index is the right default.
        import receipt_export
        manifest = receipt_export.manifest_view(
            manifest, redact_paths=not is_owner and not record.get("paths_public"))
        _json_response(self, 200, {"receipt": record, "manifest": manifest})

    def _handle_inclusion_proof(self) -> None:
        """Return an inclusion proof for one path in a folder anchor.

        Query: ?receipt_id=<rid>&path=<posix-rel-path>
        Returns: { receipt_id, root_hex, path, file_sha256_hex, proof: [...] }
        The proof lets a third party verify locally that a specific file
        belonged to the anchored folder without seeing any other path.
        """
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        rid = (qs.get("receipt_id", [""])[0]).strip()
        rel_path = (qs.get("path", [""])[0]).strip()
        if not RECEIPT_ID_RE.match(rid):
            _json_response(self, 400, {"error": "invalid receipt id"})
            return
        if not rel_path or len(rel_path) > 4096 or "\x00" in rel_path:
            _json_response(self, 400, {"error": "invalid path"})
            return
        record = engine.verify_receipt(rid)
        if not record.get("found") or record.get("kind") != "folder":
            _json_response(self, 404, {"error": "folder receipt not found"})
            return
        if record.get("private"):
            session_email = self._session_email()
            viewer_id = auth.email_id(session_email) if session_email else None
            if not viewer_id or viewer_id != record.get("owner_id"):
                _json_response(self, 404, {"error": "folder receipt not found"})
                return
        try:
            manifest = json.loads((engine.RECEIPTS_DIR / rid / "manifest.json").read_text())
        except (OSError, json.JSONDecodeError):
            _json_response(self, 500, {"error": "manifest missing"})
            return
        try:
            tree = merkle.MerkleTree.from_manifest(manifest)
            proof = tree.inclusion_proof(rel_path)
        except ValueError as e:
            _json_response(self, 404, {"error": str(e)})
            return
        # Pull the file's SHA-256 from the manifest entry so the verifier
        # can reconstruct the leaf locally without contacting the server again.
        file_hex = None
        for leaf in manifest.get("leaves", []):
            if leaf.get("path") == rel_path:
                file_hex = leaf.get("file_sha256_hex")
                break
        _json_response(self, 200, {
            "receipt_id": rid,
            "root_hex": manifest.get("root_hex"),
            "path": rel_path,
            "file_sha256_hex": file_hex,
            "merkle_algorithm": manifest.get("algorithm"),
            "proof": proof,
        })

    def _handle_recover_payment(self) -> None:
        """Customer self-serve recovery: a customer who paid (Stripe) but
        never received their claim-code email or welcome email can recover
        without contacting support.

        Inputs: { stripe_session_id, email }
        Behavior:
          - Validates session_id shape
          - Rate-limits per IP (cheap to abuse otherwise)
          - Verifies Stripe says the session is paid AND the email
            matches the customer_email Stripe holds (cross-customer-leak guard)
          - For one-time-Pack mode: looks up the EXISTING claim_code from
            the credits ledger by source containing session_id; re-sends
            via mailer.send_pack_claim_email; NEVER mints a new code
          - For subscription mode: issues a fresh magic-link via
            auth.issue_link_token (auto-supersedes prior tokens) and
            sends the welcome email with that link
          - All errors return a generic message — no PII leak in failure cases
        """
        # Light per-IP rate limit
        allowed, _ = _anchor_limiter.check(f"recover:{self._client_key()}")
        if not allowed:
            _json_response(self, 429, {"error": "too many requests"})
            return
        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid request"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, RecursionError):
            _json_response(self, 400, {"error": "invalid request"})
            return
        if not isinstance(payload, dict):
            _json_response(self, 400, {"error": "invalid request"})
            return
        wrong = _wrong_type_field(payload, "stripe_session_id", "email")
        if wrong:
            _json_response(self, 400, {"error": f"{wrong} must be a string"})
            return
        # The same field carries either a Stripe checkout session id
        # (cs_test_/cs_live_) or a crypto (NOWPayments) order id (np_...).
        sid = _json_str(payload, "stripe_session_id").strip()
        provided_email = _json_str(payload, "email").strip().lower()
        # Email shape is required for BOTH paths; check it once up front so the
        # generic 400 below is identical regardless of which path is taken.
        if not provided_email or "@" not in provided_email or len(provided_email) > 254:
            _json_response(self, 400, {"error": "invalid request"})
            return

        # ---- Crypto (NOWPayments) recovery branch -------------------------
        # A crypto buyer's order id begins with "np_". Dispatch BEFORE the
        # Stripe shape check (which would otherwise reject any non-cs_ id).
        # This branch NEVER mints: it looks up the already-issued claim code
        # and re-sends it only when the requester's email matches the ledger
        # row exactly. The per-IP rate limit above already applies; the
        # Stripe path below is left completely untouched.
        if sid.startswith("np_"):
            self._recover_crypto_claim(sid, provided_email)
            return

        # Strict shape check on the session id. Stripe ids are cs_test_ or
        # cs_live_ followed by alphanumerics + underscores.
        if not sid.startswith(("cs_test_", "cs_live_")) or len(sid) > 256 \
           or not _ascii_word(sid):
            _json_response(self, 400, {"error": "invalid request"})
            return
        if not stripe_api.is_configured():
            _json_response(self, 503, {"error": "recovery temporarily unavailable"})
            return
        # Fetch the session from Stripe and verify it is paid + email matches.
        result = stripe_api._request("GET", f"/checkout/sessions/{sid}")
        if not result.get("ok"):
            _json_response(self, 404, {"error": "session not found or not accessible"})
            return
        data = result.get("data") or {}
        payment_status = data.get("payment_status")
        if payment_status != "paid":
            _json_response(self, 400, {"error": "session is not in a paid state"})
            return
        stripe_email = ((data.get("customer_details") or {}).get("email") or data.get("customer_email") or "").strip().lower()
        if not stripe_email or stripe_email != provided_email:
            # Generic message — never confirm/deny which side mismatched.
            _json_response(self, 400, {"error": "session and email do not match"})
            return
        mode = data.get("mode") or ""

        if mode == "subscription":
            # No claim code to re-send. Issue a fresh magic-link sign-in
            # instrument; auth.issue_link_token auto-supersedes any prior
            # token, so re-running this is idempotent.
            token, _exp = auth.issue_link_token(provided_email)
            sent = mailer.send_subscription_welcome_email(
                to=provided_email,
                plan_label="Standing Order",
                signin_token=token,
            )
            sys.stderr.write(
                f"[recover] subscription path session={stripe_api.mask_session_ids(sid)} "
                f"email={auth.mask_email(provided_email)} email_sent={sent}\n"
            )
            _json_response(self, 200, {
                "ok": True,
                "mode": "subscription",
                "message": (
                    "A fresh sign-in instrument has been sent to the address "
                    "on file. The instrument is valid for twenty-four hours."
                ),
            })
            return

        # One-time Pack: look up the existing claim_code minted for this session.
        ledger_row = credits.find_claim_code_by_source(sid)
        if not ledger_row:
            # Paid session but no claim code yet — webhook may not have
            # processed yet, or there is a real fulfillment gap. Either
            # way: do NOT mint speculatively. Log for founder + ask
            # customer to retry in a few minutes.
            sys.stderr.write(
                f"[recover] NO CLAIM FOUND for paid session {stripe_api.mask_session_ids(sid)} "
                f"email={auth.mask_email(provided_email)} — likely webhook race or fulfillment gap\n"
            )
            try:
                gap_path = Path(os.environ.get(
                    "ORPHO_RECOVERY_GAP_LOG",
                    str(ROOT / "data" / "recovery_gaps.jsonl"),
                ))
                gap_path.parent.mkdir(parents=True, exist_ok=True)
                with gap_path.open("a") as f:
                    f.write(json.dumps({
                        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "session_id": sid,
                        "email": provided_email,
                        "payment_status": payment_status,
                        "mode": mode,
                    }, separators=(",", ":")) + "\n")
            except OSError:
                pass
            _json_response(self, 202, {
                "ok": False,
                "retryable": True,
                "message": (
                    "Payment is on file but fulfillment has not yet completed. "
                    "Try again in five minutes; the office has been notified."
                ),
            })
            return

        claim_code = ledger_row["claim_code"]
        credit_count = ledger_row.get("credits_delta", 0)
        sent = mailer.send_pack_claim_email(provided_email, claim_code, credit_count)
        sys.stderr.write(
            f"[recover] resent claim_code for session={stripe_api.mask_session_ids(sid)} "
            f"email={auth.mask_email(provided_email)} email_sent={sent}\n"
        )
        _json_response(self, 200, {
            "ok": True,
            "mode": "payment",
            "message": (
                "The claim instrument has been re-sent to the address on file. "
                "It is the same instrument originally issued — no duplicate has been minted."
            ),
        })

    def _recover_crypto_claim(self, order_id: str, provided_email: str) -> None:
        """Crypto (NOWPayments) self-serve claim-code recovery.

        A crypto buyer whose claim-code email never arrived can re-trigger it
        here. We look up the EXISTING claim code minted for `order_id` and
        re-send it — we NEVER mint. The most important line is the
        cross-customer-leak guard: the requester's email MUST equal the email
        on the ledger row (case-insensitive, stripped). On any failure —
        bad shape, order not found, OR email mismatch — we return the EXACT
        SAME generic error the Stripe path returns, so the endpoint cannot be
        used to enumerate orders or confirm whether an address is on file.

        Pre-state: caller has already (a) applied the per-IP rate limit and
        (b) validated `provided_email` shape. `provided_email` is already
        stripped + lower-cased by the caller.
        """
        # 1. Validate order_id shape: [A-Za-z0-9_-], length 1..64.
        if not (1 <= len(order_id) <= 64) or not _ascii_word(order_id, "_-"):
            _json_response(self, 400, {"error": "invalid request"})
            return

        # 2. Look up the EXISTING claim code by source. Never mint.
        # Only this order's own crypto mint ("nowpayments:<invoice>:<order_id>",
        # order id LAST), matched inside the scan so a later row sharing some
        # other part cannot stand in for it or hide it. This closes the
        # 200-vs-400 enumeration oracle and the unsolicited-resend vector, on
        # top of the email guard below.
        ledger_row = credits.find_nowpayments_mint(order_id)

        # 3. CROSS-CUSTOMER-LEAK GUARD (load-bearing): the request email must
        #    equal the ledger-row email exactly (case-insensitive, stripped).
        #    Not-found (including any id that is not this order's own) and
        #    email mismatch collapse to the SAME generic 400 — no enumeration, no confirmation of which differed.
        row_email = ((ledger_row or {}).get("email") or "").strip().lower()
        if not ledger_row or not row_email or row_email != provided_email:
            sys.stderr.write(
                f"[recover] crypto recovery DENIED order={order_id} "
                f"email={auth.mask_email(provided_email)} "
                f"(no match or email mismatch)\n"
            )
            _json_response(self, 400, {"error": "invalid request"})
            return

        # 4. Match. Re-send the EXISTING code via the same mailer the Stripe
        #    path and the webhook use. Mirror the one-time-Pack success body.
        claim_code = ledger_row["claim_code"]
        credit_count = int(ledger_row.get("credits_delta", 0))
        sent = mailer.send_pack_claim_email(provided_email, claim_code, credit_count)
        sys.stderr.write(
            f"[recover] resent crypto claim_code for order={order_id} "
            f"email={auth.mask_email(provided_email)} email_sent={sent}\n"
        )
        _json_response(self, 200, {
            "ok": True,
            "mode": "payment",
            "message": (
                "The claim instrument has been re-sent to the address on file. "
                "It is the same instrument originally issued — no duplicate has been minted."
            ),
        })

    def _handle_refund_request(self) -> None:
        """Customer-initiated refund request — does NOT process the refund.

        The actual Stripe refund still happens manually in the dashboard.
        This endpoint exists so the customer has a self-serve way to put
        the request on the founder's desk without having to find an
        email address; it appends to a refund_requests.jsonl ledger and
        emails the founder via Resend. Reply to the customer is a
        formal-tone acknowledgement, not a promise of outcome.
        """
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        # Rate-limit so a single account cannot spam the ledger / inbox.
        allowed, _ = _anchor_limiter.check(f"refund:{auth.email_id(email)}")
        if not allowed:
            _json_response(self, 429, {"error": "too many requests"})
            return
        length = _read_content_length(self)
        if length < 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        payload = {}
        if length > 0:
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, RecursionError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        reason = ""
        if isinstance(payload.get("reason"), str):
            reason = payload["reason"][:500].strip()
        sub_id = subscriptions.stripe_subscription_id_for(email)
        # Append to ledger.
        ledger_path = Path(os.environ.get(
            "ORPHO_REFUND_LEDGER",
            str(ROOT / "data" / "refund_requests.jsonl"),
        ))
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "email": email,
            "stripe_sub": sub_id or "",
            "reason": reason,
        }
        try:
            with ledger_path.open("a") as f:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
        except OSError as e:
            sys.stderr.write(f"[refund-request] ledger write failed: {e}\n")
        # Notify founder via Resend so the request lands in the inbox.
        # HTML-escape every interpolation point — `reason` is customer-
        # controlled free text up to 500 chars; without escaping, a
        # malicious reason could embed tracking pixels or spoofed
        # internal-formatting content in the founder's mail client.
        # `email` and `sub_id` come from validated server state, but we
        # escape them defensively (cheap and matches the established
        # pattern in mailer.send_pack_gift_email).
        from html import escape as _h
        try:
            founder_to = os.environ.get("ORPHO_FOUNDER_EMAIL", "hello@orphograph.com")
            safe_email = _h(email)
            safe_sub = _h(sub_id or "(none on file)")
            safe_reason = _h(reason or "(none provided)").replace("\n", "<br>")
            mailer._send(
                founder_to,
                f"Orphograph — refund request from {auth.mask_email(email)}",
                f"Customer: {email}\nSubscription: {sub_id or '(none on file)'}\nReason:\n{reason or '(none provided)'}\n",
                f"<p><strong>Customer:</strong> {safe_email}</p>"
                f"<p><strong>Subscription:</strong> {safe_sub}</p>"
                f"<p><strong>Reason:</strong><br>{safe_reason}</p>",
                transactional=True,
                category="refund_request_internal",
            )
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[refund-request] founder notify failed: {type(e).__name__}\n")
        # Customer-facing acknowledgement in the formal voice.
        _json_response(self, 200, {
            "ok": True,
            "message": (
                "The request has been received and registered. "
                "A reply is issued within one business day."
            ),
        })

    def _handle_webhook_delete(self) -> None:
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, RecursionError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        if not isinstance(payload, dict):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        wrong = _wrong_type_field(payload, "url")
        if wrong:
            _json_response(self, 400, {"error": f"{wrong} must be a string"})
            return
        url = _json_str(payload, "url").strip()
        ok = webhooks.delete(email=email, url=url)
        _json_response(self, 200 if ok else 404, {"ok": ok})

    def _handle_cancel_subscription(self) -> None:
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        sub_id = subscriptions.stripe_subscription_id_for(email)
        if not sub_id:
            _json_response(self, 404, {"error": "no active subscription found"})
            return
        result = stripe_api.cancel_at_period_end(sub_id)
        if not result.get("ok"):
            _json_response(self, 503, {"error": "stripe error", "detail": result.get("error")})
            return
        _json_response(self, 200, {
            "ok": True,
            "message": "Subscription will end at the period boundary; you keep access until then.",
        })

    def _handle_reactivate_subscription(self) -> None:
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        sub_id = subscriptions.stripe_subscription_id_for(email)
        if not sub_id:
            _json_response(self, 404, {"error": "no subscription found"})
            return
        result = stripe_api.reactivate(sub_id)
        if not result.get("ok"):
            _json_response(self, 503, {"error": "stripe error", "detail": result.get("error")})
            return
        _json_response(self, 200, {"ok": True, "message": "Subscription reactivated."})

    def _handle_account_delete(self) -> None:
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        result = gdpr.delete_for_email(email)
        # Tear down the active session too.
        cookies = SimpleCookie()
        cookies.load(self.headers.get("Cookie", "") or "")
        sid = cookies.get(auth.cookie_name(COOKIE_SECURE)) or cookies.get("orpho_sid") or cookies.get("__Host-orpho_sid")
        if sid:
            auth.revoke_session(sid.value)
        body = json.dumps({
            "ok": True,
            "email": email,
            "events_appended": result["events_appended"],
            "message": (
                "Your data has been marked for deletion. Append-only ledgers retain "
                "the deletion event for audit purposes; the email no longer resolves "
                "to any active state."
            ),
        }, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", auth.clear_session_cookie(secure=COOKIE_SECURE))
        _security_headers(self)
        self.end_headers()
        self.wfile.write(body)

    def _handle_signout(self) -> None:
        cookies = SimpleCookie()
        cookies.load(self.headers.get("Cookie", "") or "")
        sid = cookies.get(auth.cookie_name(COOKIE_SECURE)) or cookies.get("orpho_sid") or cookies.get("__Host-orpho_sid")
        if sid:
            auth.revoke_session(sid.value)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", auth.clear_session_cookie(secure=COOKIE_SECURE))
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        _security_headers(self)
        self.end_headers()
        self.wfile.write(body)

    def _handle_logout_all(self) -> None:
        """Revoke every session for the signed-in email (log out of all
        devices) and clear the current browser's cookie. Session-gated POST;
        SameSite=Lax on the session cookie blocks cross-site invocation."""
        email = self._session_email()
        if not email:
            _json_response(self, 401, {"error": "not authenticated"})
            return
        # Rate-limit per account, matching every other authenticated /api/me POST
        # (security review 2026-06-22) — bounds ledger-append churn from a
        # logout-all loop and restores parity with the sibling handlers.
        allowed, _ = _anchor_limiter.check(f"logout-all:{auth.email_id(email)}")
        if not allowed:
            _json_response(self, 429, {"error": "too many requests"})
            return
        n = auth.revoke_all_sessions(email)
        body = json.dumps({"ok": True, "sessions_revoked": n}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", auth.clear_session_cookie(secure=COOKIE_SECURE))
        _security_headers(self)
        self.end_headers()
        self.wfile.write(body)

    def _handle_stripe_session_status(self) -> None:
        """GET /api/stripe/session?id=cs_... — read-only lookup for the
        post-Checkout confirmation page.

        Returns a small, safe subset of the Stripe Session object:
          { id, payment_status, mode, customer_email, amount_total, currency }

        Used by web/buy.js after Stripe redirects the buyer to
        /buy.html?stripe_session=cs_...&status=success. The webhook is the
        source of truth for credit issuance — this endpoint exists only so
        the buyer sees something specific instead of a generic page while
        the webhook is in flight.
        """
        if not stripe_api.is_configured():
            _json_response(self, 503, {"error": "Stripe not configured"})
            return
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(self.path).query)
        sid_list = query.get("id", [])
        sid = sid_list[0] if sid_list else ""
        # Stripe session IDs are cs_test_… or cs_live_… plus alphanumerics
        if not sid or not sid.startswith("cs_") or len(sid) > 256 or not _ascii_word(sid):
            _json_response(self, 400, {"error": "invalid session id"})
            return
        # Light rate-limit so this can't be used as a session-id oracle. After
        # the shape check: a malformed id costs nothing, so a page that sent a
        # bad value does not spend the buyer's budget for a real lookup.
        allowed, _ = _session_lookup_limiter.check(f"stripe-session:{self._client_key()}")
        if not allowed:
            _json_response(self, 429, {"error": "rate limit exceeded"})
            return
        result = stripe_api._request("GET", f"/checkout/sessions/{sid}")
        if not result.get("ok"):
            status = result.get("status", 502)
            _json_response(self, status if status in (400, 404) else 502, {"error": result.get("error", "stripe error")})
            return
        data = result.get("data") or {}
        # Whitelist what we expose — never echo Stripe's full session blob
        _json_response(self, 200, {
            "id": data.get("id"),
            "payment_status": data.get("payment_status"),
            "status": data.get("status"),
            "mode": data.get("mode"),
            "customer_email": (data.get("customer_details") or {}).get("email") or data.get("customer_email"),
            "amount_total": data.get("amount_total"),
            "currency": data.get("currency"),
        })

    def _handle_stripe_checkout(self) -> None:
        """Create a Stripe Checkout Session and return its hosted URL.

        Request body (JSON):
            { "plan": "pack" | "pro", "email"?: "user@example.com" }

        Response:
            200 → { "url": "https://checkout.stripe.com/c/pay/cs_..." }
            400 → { "error": "..." }
            429 → if the per-IP rate limit is exceeded
            503 → if Stripe is not configured

        The buyer's browser redirects to the `url`. After payment, Stripe
        sends a `checkout.session.completed` webhook to /api/stripe/webhook,
        which mints the Pack code or activates the subscription.
        """
        if not stripe_api.is_configured():
            _json_response(self, 503, {"error": "Stripe is not configured on this server"})
            return
        if ORPHO_DISABLE_CHECKOUT:
            _json_response(self, 503, {"error": "Checkout is temporarily disabled"})
            return

        # Rate-limit: every other public POST gates on _anchor_limiter; this
        # one was missing it. Trivial unrestricted loop would create unbounded
        # cs_… sessions and pressure our Stripe API quota. Per-IP-prefix key.
        allowed, retry_after = _anchor_limiter.check(f"stripe:{self._client_key()}")
        if not allowed:
            self.send_response(429)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Retry-After", str(int(retry_after) + 1))
            body = json.dumps({
                "error": "rate limit exceeded",
                "retry_after_seconds": int(retry_after) + 1,
            }).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body)
            return

        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, RecursionError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        if not isinstance(payload, dict):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        wrong = _wrong_type_field(payload, "plan", "email")
        if wrong:
            _json_response(self, 400, {"error": f"{wrong} must be a string"})
            return

        plan = _json_str(payload, "plan").strip().lower()
        email = _json_str(payload, "email").strip()
        session_metadata: dict[str, str] = {}
        if plan == "pack":
            price_env, mode = "STRIPE_PRICE_PACK", "payment"
        elif plan in ("pack50", "pack_50"):
            price_env, mode = "STRIPE_PRICE_PACK50", "payment"
            # The 50-pack's credit count travels in session metadata and is
            # read back by the webhook, so it stays independent of the default
            # PACK_CREDIT_COUNT (=10) that the entry Writer Pack relies on.
            session_metadata = {"credit_count": "50", "plan": "pack50"}
        elif plan in ("pro", "sub", "subscription", "standing", "standing_order"):
            price_env, mode = "STRIPE_PRICE_SUB", "subscription"
        else:
            _json_response(self, 400, {"error": "plan must be 'pack', 'pack50' or 'pro'"})
            return
        price_id = os.environ.get(price_env, "")
        if not price_id:
            _json_response(self, 503, {
                "error": f"Stripe price not configured ({price_env} unset)",
            })
            return

        # Build absolute success/cancel URLs. SITE_URL is the authoritative
        # source in production; the loopback fallback is dev-only. We fail
        # closed if SITE_URL is unset in a production environment (per the
        # hardening review — relying on Host header is a proxy/SSRF foot-gun).
        site = os.environ.get("SITE_URL", "").rstrip("/")
        if not site:
            host = self.headers.get("Host", "")
            is_loopback = host.startswith("127.") or host.startswith("localhost") or host.startswith("[::1]")
            if os.environ.get("ORPHO_ENV", "").lower() == "production" and not is_loopback:
                sys.stderr.write("[stripe] SITE_URL not set in production; refusing to build success_url from Host header\n")
                _json_response(self, 503, {"error": "checkout misconfigured (SITE_URL unset)"})
                return
            scheme = "http" if is_loopback else "https"
            site = f"{scheme}://{host or 'orphograph.com'}"
        success_url = f"{site}/buy?stripe_session={{CHECKOUT_SESSION_ID}}&status=success"
        cancel_url = f"{site}/?stripe=canceled"

        result = stripe_api.create_checkout_session(
            price_id=price_id,
            mode=mode,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=email if "@" in email else "",
            metadata=session_metadata,
        )
        if not result.get("ok"):
            _json_response(self, 503, {"error": result.get("error", "stripe error")})
            return
        data = result.get("data") or {}
        Handler._record_demand(self,
            "checkout_created", auth_path="none", surface="stripe",
            outcome="success")
        _json_response(self, 200, {
            "url": data.get("url"),
            "session_id": data.get("id"),
        })

    def _handle_stripe_webhook(self) -> None:
        length = _read_content_length(self)
        if length < 0 or length > MAX_WEBHOOK_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        payload = self.rfile.read(length) if length > 0 else b""
        sig_header = self.headers.get("Stripe-Signature", "")
        if not STRIPE_WEBHOOK_SECRET:
            if not ALLOW_UNSIGNED_WEBHOOK_PROBE:
                sys.stderr.write("[webhook] STRIPE_WEBHOOK_SECRET not set; rejecting unsigned webhook\n")
                _json_response(self, 503, {"error": "webhook not configured"})
                return
            # Stripe's webhook URL validation POSTs a probe before registration.
            # Returning 503 fails their reachability check ("URL couldn't be
            # reached / not active"). 200 with a clear log line keeps the URL
            # "alive" enough for Stripe to accept it, while signed real events
            # would still be rejected as soon as the secret is configured.
            sys.stderr.write("[webhook] STRIPE_WEBHOOK_SECRET not set; accepting probe but discarding event\n")
            _json_response(self, 200, {"ok": False, "reason": "webhook not configured yet — probe accepted"})
            return
        if not stripe_webhook.verify_signature(payload, sig_header, STRIPE_WEBHOOK_SECRET):
            _json_response(self, 400, {"error": "invalid signature"})
            return
        result = stripe_webhook.handle_event(payload)
        for demand_event, demand_auth_path, demand_paid in stripe_webhook.demand_events(result):
            Handler._record_demand(self,
                demand_event, auth_path=demand_auth_path,
                surface="stripe", outcome="success", authenticated=True,
                paid=demand_paid)
        _json_response(self, 200, result)

    # ---------- NOWPayments (non-custodial crypto checkout) ----------

    def _handle_nowpayments_webhook(self) -> None:
        """IPN: NOWPayments POSTs payment-state updates.

        HMAC-SHA512 signature in `x-nowpayments-sig` header is verified
        against NOWPAYMENTS_IPN_SECRET before any state change.
        """
        length = _read_content_length(self)
        if length < 0 or length > MAX_WEBHOOK_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        payload = self.rfile.read(length) if length > 0 else b""
        sig_header = self.headers.get("x-nowpayments-sig", "") or self.headers.get(
            "X-Nowpayments-Sig", ""
        )
        if not NOWPAYMENTS_IPN_SECRET:
            sys.stderr.write(
                "[nowpayments_webhook] NOWPAYMENTS_IPN_SECRET not set; rejecting IPN\n"
            )
            _json_response(self, 503, {"error": "webhook not configured"})
            return
        if not sig_header:
            _json_response(self, 400, {"error": "missing signature"})
            return
        if not nowpayments_webhook.verify_signature(payload, sig_header, NOWPAYMENTS_IPN_SECRET):
            _json_response(self, 400, {"error": "invalid signature"})
            return
        result = nowpayments_webhook.handle_event(payload)
        if result.get("ok") and result.get("claim_code_minted"):
            Handler._record_demand(self,
                "payment_confirmed", auth_path="pack", surface="nowpayments",
                outcome="success", authenticated=True, paid=True)
            Handler._record_demand(self,
                "entitlement_activated", auth_path="pack", surface="nowpayments",
                outcome="success", authenticated=True, paid=True)
        _json_response(self, 200, result)

    def _handle_nowpayments_create(self) -> None:
        """Buyer-initiated: create an invoice and return its hosted URL.

        Body: {"currency": "usdc", "plan": "writer_pack"|"pack_50", "email": "<optional>"}
        Returns 200 {url, order_id} on success, 503 when not configured.
        """
        if ORPHO_DISABLE_CHECKOUT:
            _json_response(self, 503, {"error": "checkout disabled"})
            return
        if not nowpayments_api.is_configured():
            _json_response(self, 503, {
                "ok": False, "reason": "nowpayments_not_configured",
                "error": "Crypto checkout is not currently enabled.",
            })
            return
        # Rate-limit like every other public money POST (mirror the Stripe
        # checkout limiter): an unauthenticated create hits the NOWPayments
        # /invoice API, so an unthrottled caller could mint unlimited real
        # hosted invoices and exhaust the merchant quota. Per-IP-prefix key.
        allowed, retry_after = _anchor_limiter.check(f"nowpay:{self._client_key()}")
        if not allowed:
            self.send_response(429)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Retry-After", str(int(retry_after) + 1))
            body = json.dumps({
                "error": "rate limit exceeded",
                "retry_after_seconds": int(retry_after) + 1,
            }).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body)
            return
        length = _read_content_length(self)
        if length < 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, RecursionError):
            _json_response(self, 400, {"error": "bad json"})
            return
        if not isinstance(body, dict):
            _json_response(self, 400, {"error": "bad json shape"})
            return
        currency = str(body.get("currency", "")).strip().lower()
        plan = str(body.get("plan", "")).strip().lower()
        email = str(body.get("email", "")).strip()
        if plan not in nowpayments_api.PLANS:
            _json_response(self, 400, {"error": "unknown plan"})
            return
        if currency not in nowpayments_api.SUPPORTED_CURRENCIES:
            _json_response(self, 400, {"error": "unsupported currency"})
            return
        # Email is REQUIRED: the claim code is delivered by email and the
        # webhook refuses to mint a Pack for an order carrying no customer
        # email. A blank email here would mean a paid (irreversible) crypto
        # order that can never receive its code, so fail closed before the
        # invoice is created.
        if not email or "@" not in email or len(email) > 254:
            _json_response(self, 400, {
                "error": "A valid email is required — it is where we send your claim code.",
            })
            return
        plan_meta = nowpayments_api.PLANS[plan]
        # Order id is opaque + unguessable so retries/lookups are safe to leak
        # in URLs. We embed the plan token (np_<plan>_<rand>) so the IPN — which
        # echoes order_id verbatim — can recover which pack was bought even if
        # order_description is dropped. `plan` is a validated PLANS key above,
        # so it is a safe, fixed-vocabulary token. Stays well under the 64-char
        # order_id cap (np_pack_50_ + ~14 = ~25).
        order_id = f"np_{plan}_" + secrets.token_urlsafe(10)
        result = nowpayments_api.create_invoice(
            amount_usd=float(plan_meta["price_usd"]),
            currency=currency,
            order_id=order_id,
            customer_email=email,
            plan=plan,
        )
        if not result.get("ok"):
            _json_response(self, 503, {
                "ok": False,
                "error": "Crypto payment provider unavailable.",
                "reason": result.get("reason", ""),
            })
            return
        data = result.get("data") or {}
        invoice_url = (
            data.get("invoice_url")
            or data.get("invoiceUrl")
            or data.get("url")
            or ""
        )
        if not invoice_url:
            _json_response(self, 503, {
                "ok": False,
                "error": "Payment provider returned no invoice URL.",
            })
            return
        Handler._record_demand(self,
            "checkout_created", auth_path="none", surface="nowpayments",
            outcome="success")
        _json_response(self, 200, {
            "ok": True,
            "url": invoice_url,
            "order_id": order_id,
            "plan": plan,
            "currency": currency,
        })


# The site's stylesheet links, taken ONCE from the error template — the single
# place those ?v= pins live. Server-built pages reuse them instead of carrying
# a second list to drift. Empty would mean an unstyled page with no error, so
# fail at import, loudly, if the template ever stops yielding any.
_SITE_STYLESHEET_LINKS = "\n".join(re.findall(
    r'<link\b[^>]*\brel="stylesheet"[^>]*>', Handler.error_message_format))
if not _SITE_STYLESHEET_LINKS:
    raise RuntimeError("error_message_format yields no stylesheet links")


def _owned_sources_for_email(email: str) -> set[str]:
    """The source tag this account's session anchors carry (sub:<email_id>).

    API-key tags (api:<key[:10]>) are not listed: whether a key prefix is this
    account's depends on when the receipt was made, so _receipt_belongs_to
    resolves them per receipt through api_keys.prefix_owner."""
    if not email:
        return set()
    return {"sub:" + auth.email_id(email)}


def _receipt_ownership_context(email: str) -> tuple[str, set[str], dict]:
    """Build once per list/count, rather than rescan the key ledger per row."""
    return (auth.email_id(email), _owned_sources_for_email(email),
            api_keys.prefix_issuers())


def _receipt_belongs_to(rec: dict, email: str, *,
                        context: tuple[str, set[str], dict] | None = None) -> bool:
    """Account identity wins; a private legacy owner wins; then the legacy
    source tag. An api: tag belongs to the one account that held a key with
    that prefix when the receipt was made; an ambiguous prefix denies."""
    if not email:
        return False
    account_id, sources, issuers = (context if context is not None
                                    else _receipt_ownership_context(email))
    if "account_id" in rec:
        return bool(account_id) and rec["account_id"] == account_id
    if rec.get("private"):
        return bool(account_id) and rec.get("owner_id") == account_id
    source = rec.get("source")
    if source in sources:
        return True
    if isinstance(source, str) and source.startswith("api:"):
        owner = api_keys.prefix_owner(issuers.get(source[4:], []), rec.get("created_at"))
        return owner is not None and owner == email.lower()
    return False


def _count_anchors_for_email(email: str) -> int:
    """Fast O(receipts) count of anchors owned by this email.

    Uses the same ownership predicate as exports and privacy changes, with
    the historical key lookup precomputed once for the entire scan.
    """
    if not email:
        return 0
    ownership = _receipt_ownership_context(email)
    receipts_dir = engine.RECEIPTS_DIR
    if not receipts_dir.exists():
        return 0
    count = 0
    for child in receipts_dir.iterdir():
        if not child.is_dir():
            continue
        rfile = child / "receipt.json"
        if not rfile.exists():
            continue
        try:
            rec = json.loads(rfile.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if _receipt_belongs_to(rec, email, context=ownership):
            count += 1
    return count


_WEEKLY_CACHE: dict = {"ts": 0.0, "rows": []}


def _is_office_anchor(rec: dict) -> bool:
    """Whether a receipt may stand on the office's Standing Record.

    The label is the client's text, so it cannot be the test: selecting on
    `weekly-*` alone let any anonymous caller publish rows on the office's
    own chain of custody, and 16 of them pushed every real entry off the
    page. ORPHO_STANDING_RECORD_SOURCES (comma-separated source tags, e.g.
    the weekly job's `api:<key[:10]>`) pins the record to exactly those.
    Unpinned, only a paid, identified account's anchor qualifies (api:/sub:),
    never a free or pack one."""
    source = rec.get("source")
    if not isinstance(source, str):
        return False
    pins = {t.strip() for t in os.environ.get("ORPHO_STANDING_RECORD_SOURCES", "").split(",")
            if t.strip()}
    if pins:
        return source in pins
    return source.startswith(("api:", "sub:"))


def _list_weekly_anchors(limit: int = 16) -> list[dict]:
    """Latest public weekly self-anchors (client_label weekly-*), 300s cache.

    The office re-anchors its own foundations on a schedule
    (scripts/weekly_anchor.py); this powers the public /standing-record page.
    Only the office's own anchors count: see _is_office_anchor.
    """
    import time as _time
    now = _time.time()
    if now - _WEEKLY_CACHE["ts"] < 300:
        return _WEEKLY_CACHE["rows"]
    rows: list[dict] = []
    receipts_dir = engine.RECEIPTS_DIR
    if receipts_dir.exists():
        for child in receipts_dir.iterdir():
            if not child.is_dir():
                continue
            rfile = child / "receipt.json"
            if not rfile.exists():
                continue
            try:
                rec = json.loads(rfile.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            label = str(rec.get("client_label") or "")
            if not label.startswith("weekly-") or rec.get("private"):
                continue
            if not _is_office_anchor(rec):
                continue
            rows.append({
                "receipt_id": rec.get("receipt_id"),
                "client_label": label,
                "created_at": rec.get("created_at"),
                "btc_pinned_at": rec.get("btc_pinned_at"),
            })
    rows.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    _WEEKLY_CACHE.update(ts=now, rows=rows[:limit])
    return _WEEKLY_CACHE["rows"]


def _list_anchors_for_email(
    email: str,
    limit: int = 50,
    before: str | None = None,
    with_more_flag: bool = False,
    hash_prefix: str | None = None,
    label_substr: str | None = None,
    private_only: bool | None = None,
):
    """Return the most recent anchors anchored under this email's subscription.

    Pack purchases are not joined here — Pack receipts go via email at anchor
    time, so the dashboard scope is subscriber-only.

    Cursor pagination: pass `before=<created_at>` to fetch the page strictly
    older than that timestamp. When `with_more_flag=True`, returns
    (rows, has_more) instead of just rows.

    Vault filters (receipt vault feature):
      - hash_prefix: case-insensitive hex prefix match on hash_hex
      - label_substr: case-insensitive substring match on client_label
      - private_only: if True, only private receipts; if False, only public;
                      if None, both.
    """
    if not email:
        return ([], False) if with_more_flag else []
    # Resolve legacy key-prefix ambiguity once for this entire listing.
    ownership = _receipt_ownership_context(email)
    receipts_dir = engine.RECEIPTS_DIR
    if not receipts_dir.exists():
        return ([], False) if with_more_flag else []
    rows: list[dict] = []
    # Normalize filters once
    norm_prefix = (hash_prefix or "").strip().lower()
    norm_label = (label_substr or "").strip().lower()
    for child in receipts_dir.iterdir():
        if not child.is_dir():
            continue
        rfile = child / "receipt.json"
        if not rfile.exists():
            continue
        try:
            rec = json.loads(rfile.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not _receipt_belongs_to(rec, email, context=ownership):
            continue
        created = rec.get("created_at", "")
        if before is not None and created >= before:
            continue
        # Vault filters
        if norm_prefix and not (rec.get("hash_hex", "") or "").startswith(norm_prefix):
            continue
        if norm_label:
            lbl = (rec.get("client_label") or "").lower()
            if norm_label not in lbl:
                continue
        if private_only is True and not rec.get("private"):
            continue
        if private_only is False and rec.get("private"):
            continue
        rows.append({
            "receipt_id": rec.get("receipt_id"),
            "created_at": created,
            "client_label": rec.get("client_label"),
            "hash_hex": rec.get("hash_hex"),
            "sha512_hex": rec.get("sha512_hex"),
            "private": bool(rec.get("private", False)),
            "calendars_ok": rec.get("calendars_ok"),
            "calendars_total": rec.get("calendars_total"),
            # Map-based on purpose: a vault page lists many receipts and the
            # proof-first path would read five .ots files per row. The anchor
            # and verify surfaces, which already hold the bytes, read the
            # proofs themselves.
            "calendars_distinct_ok": engine.distinct_calendars(rec.get("successes")),
            "calendars_distinct_total": engine.CALENDARS_DISTINCT_TOTAL,
            "status": rec.get("status", "pending"),
            "btc_pinned_at": rec.get("btc_pinned_at"),
        })
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    page = rows[:limit]
    if with_more_flag:
        return page, len(rows) > limit
    return page


def _anchors_to_csv(anchors: list[dict]) -> str:
    """RFC 4180 CSV of anchor records. Header row included.

    Columns are the ones every B2B procurement workflow asks for:
    when, what (label + hash), where on the chain (status + pinned),
    redundancy (calendars).
    """
    buf = io.StringIO()
    fields = [
        "created_at_utc",
        "receipt_id",
        "client_label",
        "sha256",
        "sha512",
        "calendars_ok",
        "calendars_total",
        "status",
        "btc_pinned_at",
    ]
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(fields)
    for a in anchors:
        writer.writerow([
            a.get("created_at", ""),
            a.get("receipt_id", ""),
            _csv_text(a.get("client_label") or ""),
            a.get("hash_hex", ""),
            a.get("sha512_hex") or "",
            a.get("calendars_ok", ""),
            a.get("calendars_total", ""),
            a.get("status", ""),
            a.get("btc_pinned_at") or "",
        ])
    return buf.getvalue()


def _csv_text(value) -> str:
    """A free-text cell a spreadsheet will show as text, never evaluate.

    A label is whatever the anchoring client sent (up to 200 chars), and
    integrations build labels from file names and commit messages. A cell
    that starts with = + - @ or a tab/CR is a formula in Excel and Sheets,
    e.g. =HYPERLINK("http://…/?"&A2) exfiltrates a neighbouring cell when the
    owner opens the export. A leading apostrophe makes it literal text."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _seed_sample_receipt() -> None:
    """Copy web/sample/ → <RECEIPTS_DIR>/<sample_id>/ on first boot if missing.

    Keeps the canonical sample in one place (web/sample/, in git) while
    making /api/verify/<sample_id> work in prod without git-tracking
    the receipts/ dir. Targets engine.RECEIPTS_DIR which is env-configurable
    so prod points at the mounted volume.
    """
    sample_meta = WEB_DIR / "sample" / "index.json"
    if not sample_meta.exists():
        return
    import shutil
    try:
        meta = json.loads(sample_meta.read_text())
    except (OSError, json.JSONDecodeError):
        return
    rid = meta.get("receipt_id")
    if not rid:
        return
    target = engine.RECEIPTS_DIR / rid
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    sample_dir = WEB_DIR / "sample"
    # `target.exists()` above is a fast path, NOT the decision. Two server
    # processes booting against one ORPHO_DATA_DIR (an overlapping deploy, or a
    # second machine on a shared volume) both pass that check and then race
    # here — the loser used to die with FileExistsError BEFORE binding its
    # port, i.e. a crash-on-boot, not a warning. mkdir is atomic, so let it BE
    # the claim: whoever creates the directory seeds it, everyone else returns.
    try:
        target.mkdir()
    except FileExistsError:
        return
    for item in sample_dir.iterdir():
        if item.name in ("index.json",):
            continue
        shutil.copy2(item, target / item.name, follow_symlinks=False)
    sys.stderr.write(f"seeded sample receipt {rid} from {sample_dir} → {target}\n")


def _seed_sample_folder_receipt() -> None:
    """Copy web/sample-folder/ → <RECEIPTS_DIR>/<id>/ on first boot if missing.

    The folder analogue of _seed_sample_receipt: a permanent, git-tracked
    dataset-provenance certificate sample (receipt.json + manifest.json + the
    .ots proofs) so /certificate/<id> has a live demo that never prunes. The
    seeded receipt sets paths_public so the manifest renders in full publicly.
    """
    sample_meta = WEB_DIR / "sample-folder" / "index.json"
    if not sample_meta.exists():
        return
    import shutil
    try:
        meta = json.loads(sample_meta.read_text())
    except (OSError, json.JSONDecodeError):
        return
    rid = meta.get("receipt_id")
    if not rid:
        return
    target = engine.RECEIPTS_DIR / rid
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    sample_dir = WEB_DIR / "sample-folder"
    # Same boot race as _seed_sample_receipt — mkdir is the atomic claim.
    try:
        target.mkdir()
    except FileExistsError:
        return
    for item in sample_dir.iterdir():
        if item.name in ("index.json",):
            continue
        shutil.copy2(item, target / item.name, follow_symlinks=False)
    sys.stderr.write(f"seeded sample folder receipt {rid} from {sample_dir} → {target}\n")


def _start_upgrade_scheduler() -> None:
    """Background thread that runs upgrade_worker on a cadence.

    Without this the OTS calendars never get re-polled, so receipts stay
    in 'pending' status indefinitely even after Bitcoin pinning happens.

    Cadence: first run 60s after startup (give the server time to come up),
    then every hour.

    Concurrency note: `fcntl.flock` in upgrade_worker / file_lock is host-
    local — on Fly each machine has its own filesystem, so the lock does
    NOT prevent two VMs from running the worker simultaneously. The worker
    is idempotent so concurrent runs won't corrupt state, but they would
    triple our outbound OTS-calendar traffic at higher VM counts.

    Leader-election today is opt-in via the ORPHO_UPGRADE_LEADER env var:
    set it to "1" on exactly one Fly machine (or one local process). On
    every other machine the scheduler is a no-op. The default is "1" so
    the single-VM case works without extra config. When you scale past
    one machine, set ORPHO_UPGRADE_LEADER=0 on all but one.
    """
    is_leader = os.environ.get("ORPHO_UPGRADE_LEADER", "1") == "1"
    if not is_leader:
        sys.stderr.write("[upgrade] disabled on this machine (ORPHO_UPGRADE_LEADER != 1)\n")
        return
    import threading
    import upgrade_worker
    interval = int(os.environ.get("ORPHO_UPGRADE_INTERVAL_SEC", "3600"))
    initial_delay = int(os.environ.get("ORPHO_UPGRADE_INITIAL_DELAY_SEC", "60"))

    def loop() -> None:
        time.sleep(initial_delay)
        while True:
            try:
                summary = upgrade_worker.upgrade_all()
                sys.stderr.write(
                    f"[upgrade] scanned={summary['scanned']} upgraded={summary['upgraded']} "
                    f"skipped={summary['skipped']}\n"
                )
            except Exception as exc:  # noqa: BLE001 — worker errors must not kill the thread
                sys.stderr.write(f"[upgrade] error: {type(exc).__name__}: {exc}\n")
            time.sleep(interval)

    t = threading.Thread(target=loop, name="upgrade-worker", daemon=True)
    t.start()


def _start_cadence_scheduler() -> None:
    """Background thread that fires approved cold-outreach cadence runs.

    Default-off by design. Set ORPHO_CADENCE_AUTOMATION_ENABLED=1 after
    founder approval; otherwise the thread logs a disabled line and never
    invokes the runner. When enabled, it wakes once an hour, and when the
    current UTC time matches (hour == 14, weekday in {Tue=1, Wed=2, Thu=3})
    it invokes scripts/cadence_runner.py --execute.

    Idempotency: a state file at DATA_DIR/.cadence_last_run records the iso date
    of the last successful fire. The scheduler refuses to fire twice on the same
    UTC date even if clock drift / restart causes the hour-14 window to be
    observed more than once.

    Kill switch: set ORPHO_CADENCE_DISABLED=1 (e.g. via `fly secrets set`) and
    the loop becomes a no-op at the next wake. No restart required. The runner
    enforces the same switch and the same opt-in env before sending.

    The cadence_runner itself enforces the 20/day hard cap and the Tue-Thu
    day-of-week gate, so this scheduler is a thin wall-clock trigger.
    """
    import threading
    import subprocess
    from datetime import datetime, timezone

    state_path = DATA_DIR / ".cadence_last_run"
    runner_path = ROOT / "scripts" / "cadence_runner.py"

    def _parse_sent_ok(stdout: str) -> str:
        # cadence_runner prints "done · sent_ok=K failed=F dry=D" on its last line
        for token in stdout.split():
            if token.startswith("sent_ok="):
                return token.split("=", 1)[1]
        return "?"

    def loop() -> None:
        while True:
            try:
                if os.environ.get("ORPHO_CADENCE_DISABLED", "") == "1":
                    sys.stderr.write("[cadence] disabled via ORPHO_CADENCE_DISABLED=1\n")
                elif os.environ.get("ORPHO_CADENCE_AUTOMATION_ENABLED", "") != "1":
                    sys.stderr.write(
                        "[cadence] automation not enabled "
                        "(set ORPHO_CADENCE_AUTOMATION_ENABLED=1 to allow approved runs)\n"
                    )
                else:
                    now = datetime.now(timezone.utc)
                    hour = now.hour
                    weekday = now.weekday()  # Mon=0, Tue=1, Wed=2, Thu=3
                    today_iso = now.date().isoformat()
                    if hour == 14 and weekday in (1, 2, 3):
                        last_run = ""
                        if state_path.exists():
                            try:
                                last_run = state_path.read_text().strip()
                            except Exception:  # noqa: BLE001
                                last_run = ""
                        if last_run == today_iso:
                            sys.stderr.write(
                                f"[cadence] already fired today ({today_iso}); skipping\n"
                            )
                        else:
                            proc = subprocess.run(
                                ["python3", str(runner_path), "--execute"],
                                capture_output=True,
                                text=True,
                                timeout=600,
                            )
                            sent_ok = _parse_sent_ok(proc.stdout or "")
                            sys.stderr.write(
                                f"[cadence] hour={hour} weekday={weekday} "
                                f"returncode={proc.returncode} sent_ok={sent_ok}\n"
                            )
                            if proc.returncode == 0:
                                try:
                                    state_path.parent.mkdir(parents=True, exist_ok=True)
                                    state_path.write_text(today_iso)
                                except Exception as exc:  # noqa: BLE001
                                    sys.stderr.write(
                                        f"[cadence] state write error: "
                                        f"{type(exc).__name__}: {exc}\n"
                                    )
            except Exception as exc:  # noqa: BLE001 — scheduler errors must not kill the thread
                sys.stderr.write(f"[cadence] error: {type(exc).__name__}: {exc}\n")
            time.sleep(3600)

    t = threading.Thread(target=loop, name="cadence-scheduler", daemon=True)
    t.start()


def _start_funnel_digest_scheduler() -> None:
    """Background thread that emails the weekly funnel digest each Monday at 14:00 UTC.

    Wakes once an hour; when the current UTC time matches (hour == 14,
    weekday == 0 = Monday) it invokes scripts/funnel_digest.py. The
    underlying script enforces its own same-day idempotency guard via
    DATA_DIR/.funnel_digest_last_run, so a re-fire within the hour-14
    window is a no-op.

    Kill switch: set ORPHO_FUNNEL_DIGEST_DISABLED=1 (e.g. via `fly
    secrets set`) and the loop becomes a no-op at the next wake. The
    thread keeps running so the switch can be cleared without a restart.

    No yes/no founder click required — this is operational mail.
    """
    import threading
    import subprocess
    from datetime import datetime, timezone

    runner_path = ROOT / "scripts" / "funnel_digest.py"

    def loop() -> None:
        while True:
            try:
                if os.environ.get("ORPHO_FUNNEL_DIGEST_DISABLED", "") == "1":
                    sys.stderr.write(
                        "[funnel-digest] disabled via ORPHO_FUNNEL_DIGEST_DISABLED=1\n"
                    )
                else:
                    now = datetime.now(timezone.utc)
                    hour = now.hour
                    weekday = now.weekday()  # Mon=0 .. Sun=6
                    if hour == 14 and weekday == 0:
                        proc = subprocess.run(
                            ["python3", str(runner_path)],
                            capture_output=True,
                            text=True,
                            timeout=300,
                        )
                        sys.stderr.write(
                            f"[funnel-digest] hour={hour} weekday={weekday} "
                            f"returncode={proc.returncode}\n"
                        )
            except Exception as exc:  # noqa: BLE001 — scheduler errors must not kill the thread
                sys.stderr.write(
                    f"[funnel-digest] error: {type(exc).__name__}: {exc}\n"
                )
            time.sleep(3600)

    t = threading.Thread(target=loop, name="funnel-digest", daemon=True)
    t.start()


def main() -> int:
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    _seed_sample_receipt()
    _seed_sample_folder_receipt()
    _start_upgrade_scheduler()
    _start_cadence_scheduler()
    _start_funnel_digest_scheduler()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    sys.stderr.write(f"orphograph listening on http://{HOST}:{PORT}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nshutting down\n")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
