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
import mimetypes
import os
import re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402
import affiliate  # noqa: E402
import analytics  # noqa: E402
import api_keys  # noqa: E402
import blog  # noqa: E402
import btc_payments  # noqa: E402
import btc_price  # noqa: E402
import og_svg  # noqa: E402
import public_config  # noqa: E402
import qrcode_svg  # noqa: E402
import badge_svg  # noqa: E402
import credits  # noqa: E402
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
import btc_claims  # noqa: E402
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
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
EMAIL_RE = re.compile(r"^[^@\s,]{1,64}@[^@\s,]{1,255}$")
COOKIE_SECURE = os.environ.get("ORPHO_COOKIE_SECURE", "1") != "0"
TRUST_PROXY_HEADERS = os.environ.get("ORPHO_TRUST_PROXY_HEADERS", "0") == "1"
ALLOW_UNSIGNED_WEBHOOK_PROBE = os.environ.get("ORPHO_ALLOW_UNSIGNED_WEBHOOK_PROBE", "0") == "1"
ALLOWED_STATIC_SUFFIXES = {
    ".html", ".css", ".js", ".svg", ".png", ".ico", ".webmanifest",
    ".json", ".txt", ".ots",  # sample receipt assets under web/sample/
    ".py", ".md", ".tar", ".gz",  # self-hosted OSS verifier under web/verify/
    ".zip",  # offline verifier kit under web/dist/orphograph-verify.zip
    ".xml",  # sitemap.xml for SEO discoverability
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

# Funnel-event limiter: 60 events / IP / minute. Separate bucket so noisy
# analytics traffic can't burn the anchor-rate budget (and vice versa).
# Cookieless: keyed by truncated IP only. In-memory only (no snapshot) —
# analytics is best-effort and a restart resetting the bucket is fine.
EVENT_RATE_CAPACITY = 60
EVENT_RATE_REFILL = 60 / 60.0  # 1 token/sec refill, burst 60
_event_limiter = TokenBucket(EVENT_RATE_CAPACITY, EVENT_RATE_REFILL)

# Allowlist for the 4 funnel events. Any value outside this set is rejected.
FUNNEL_EVENTS = frozenset({
    "drop_zone_visible",
    "file_anchored",
    "checkout_clicked",
    "checkout_returned_success",
})
FUNNEL_EVENT_FIELDS = frozenset({"event", "page"})
FUNNEL_EVENTS_PATH = DATA_DIR / "events.jsonl"
MAX_EVENT_PAGE_LEN = 256


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


def _security_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
    handler.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
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


def _build_sitemap() -> str:
    site = os.environ.get("SITE_URL", "https://orphograph.com").rstrip("/")
    urls: list[tuple[str, str]] = [
        ("/", "1.0"),
        ("/verify/", "0.9"),
        ("/blog/", "0.8"),
        ("/status.html", "0.5"),
        ("/stats.html", "0.6"),
        ("/terms.html", "0.3"),
        ("/privacy.html", "0.3"),
        ("/badge-demo.html", "0.4"),
        ("/docs/api.html", "0.6"),
        ("/compare.html", "0.7"),
        ("/about.html", "0.7"),
        ("/learn.html", "0.8"),
        ("/press.html", "0.4"),
        ("/gift.html", "0.6"),
        ("/lp/", "0.8"),
        ("/lp/prove-photo-pre-ai.html", "0.7"),
        ("/lp/bitcoin-timestamp-file.html", "0.7"),
        ("/lp/c2pa-alternative.html", "0.7"),
        ("/lp/opentimestamps-explained.html", "0.7"),
        ("/lp/proof-of-existence-document.html", "0.7"),
        ("/lp/wedding-photographer-proof.html", "0.7"),
        ("/lp/manuscript-priority-date.html", "0.7"),
        ("/lp/screenshot-evidence-timestamp.html", "0.7"),
        ("/lp/ai-image-detector-vs-provenance.html", "0.7"),
    ]
    for post in blog.list_posts():
        urls.append((f"/blog/{post['slug']}", "0.7"))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, prio in urls:
        lines += [
            "  <url>",
            f"    <loc>{site}{path}</loc>",
            f"    <priority>{prio}</priority>",
            "  </url>",
        ]
    lines.append("</urlset>")
    return "\n".join(lines)


def _serve_static(handler: BaseHTTPRequestHandler, rel_path: str) -> None:
    if rel_path in ("", "/"):
        rel_path = "index.html"
    rel_path = rel_path.lstrip("/")
    target = (WEB_DIR / rel_path).resolve()
    if WEB_DIR not in target.parents and target != WEB_DIR:
        handler.send_error(403, "forbidden")
        return
    # Directory-style paths (e.g. /verify/) → resolve to <dir>/index.html.
    if target.is_dir():
        target = target / "index.html"
    if not target.exists() or not target.is_file():
        handler.send_error(404, "not found")
        return
    if target.suffix not in ALLOWED_STATIC_SUFFIXES:
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
        handler.send_header("Cache-Control", _static_cache_control(target.suffix))
        _security_headers(handler)
        handler.end_headers()
        return

    ctype, _ = mimetypes.guess_type(target.name)
    ctype = ctype or "application/octet-stream"
    data = target.read_bytes()
    data, enc = _maybe_compress(handler, data, ctype)

    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", _static_cache_control(target.suffix))
    handler.send_header("ETag", etag)
    if enc:
        handler.send_header("Content-Encoding", enc)
        handler.send_header("Vary", "Accept-Encoding")
    _security_headers(handler)
    handler.end_headers()
    handler.wfile.write(data)


def _static_cache_control(suffix: str) -> str:
    """Aggressive caching for binary assets, short caching for HTML.

    HTML can change with each deploy; let browsers revalidate fast.
    Binaries (svg, ico, ots, tar.gz) effectively immutable per filename.
    """
    short_lived = {".html", ".json", ".webmanifest"}
    if suffix in short_lived:
        return "public, max-age=300, must-revalidate"
    return "public, max-age=86400"


class Handler(BaseHTTPRequestHandler):
    server_version = "orphograph/0.1"
    timeout = REQUEST_TIMEOUT_SEC  # close slow / dead connections so a thread isn't pinned

    def log_message(self, fmt, *args):
        truncated = truncate_ip(self.client_address[0] if self.client_address else "")
        sys.stderr.write(f"[{self.log_date_time_string()}] {truncated} - {fmt % args}\n")

    def _client_key(self) -> str:
        # Trust X-Forwarded-For only when the deployment has explicitly said
        # every request arrives through a trusted proxy. Local/tunnel/direct
        # traffic can carry attacker-supplied XFF and must not bypass limits.
        xff = ""
        if TRUST_PROXY_HEADERS:
            xff = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        peer = xff or (self.client_address[0] if self.client_address else "")
        return truncate_ip(peer)

    def _session_email(self) -> str | None:
        cookies = SimpleCookie()
        cookies.load(self.headers.get("Cookie", "") or "")
        # In prod we set the __Host- prefixed name; in dev we set plain.
        # Look both up since either could be present across env transitions.
        sid = cookies.get(auth.cookie_name(COOKIE_SECURE)) or cookies.get("orpho_sid") or cookies.get("__Host-orpho_sid")
        if not sid:
            return None
        return auth.session_email(sid.value)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
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
            _json_response(self, 200, public_config.snapshot())
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
        if ORPHO_MAINTENANCE_MODE and not path.startswith(("/web/founder/", "/status.html")):
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
            if response_shape == "zip":
                import receipt_export
                zipped, err = receipt_export.export_zip(rid)
                if err == receipt_export.NOT_FOUND or zipped is None and err is None:
                    _json_response(self, 404, {"error": "receipt not found"})
                    return
                if err == receipt_export.BROKEN:
                    _json_response(self, 500, {"error": "could not build receipt zip"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(zipped)))
                self.send_header("Content-Disposition", f"attachment; filename=\"receipt_{rid}.zip\"")
                _security_headers(self)
                self.end_headers()
                self.wfile.write(zipped)
                return
            if response_shape == "summary":
                import receipt_export
                summary, err = receipt_export.export_readable_json(rid)
                if err == receipt_export.NOT_FOUND or summary is None and err is None:
                    _json_response(self, 404, {"error": "receipt not found"})
                    return
                if err == receipt_export.BROKEN:
                    _json_response(self, 500, {"error": "could not build receipt summary"})
                    return
                # Re-apply the owner_id redaction on the summary path too
                if not summary.get("private"):
                    summary.pop("owner_id", None)
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
            # — no filename, no email, no hash bytes — so the SVG output is
            # safe to expose without authentication.
            rid = path[len("/api/badge/"):-len(".svg")]
            if not RECEIPT_ID_RE.match(rid):
                self.send_error(400, "invalid receipt id")
                return
            record = engine.verify_receipt(rid)
            if not record.get("found"):
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
        if path.startswith("/buy/"):
            # BTC payment page — page is static; JS reads order_id from URL.
            oid = path[len("/buy/"):].rstrip("/")
            if not re.match(r"^btc_[A-Za-z0-9_-]{1,32}$", oid):
                self.send_error(400, "invalid order id")
                return
            _serve_static(self, "/buy.html")
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
        if path.startswith("/blog/"):
            # Two independent blog content surfaces share /blog/:
            #   1. Static HTML files at web/blog/<slug>.html — long-form
            #      posts authored as HTML, served byte-for-byte.
            #   2. Markdown-rendered posts at content/blog/<slug>.md —
            #      addressed at /blog/<slug> with no extension.
            # We dispatch by URL shape: anything ending in .html and matching
            # a real static file goes to the static path; bare slugs go to
            # the markdown renderer.
            rest = path[len("/blog/"):]
            if rest.endswith(".html") and re.match(r"^[a-z0-9-]{1,80}\.html$", rest):
                # Try the static file under web/blog/. _serve_static returns
                # a 404 if the file is missing.
                _serve_static(self, "/blog/" + rest)
                return
            slug = rest.rstrip("/")
            if not re.match(r"^[a-z0-9-]{1,80}$", slug):
                self.send_error(400, "invalid slug")
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
                "Disallow: /buy/\n"
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
        if path.startswith("/api/btc-order/"):
            # Status lookup for the buy page to poll. Sub-path /qr.svg
            # returns a server-rendered QR-code SVG for the BIP-21 URI —
            # public address + amount ONLY, no customer/email data.
            tail = path[len("/api/btc-order/"):].rstrip("/")
            if "/" in tail:
                parts = tail.split("/", 1)
                oid, sub = parts[0], parts[1]
            else:
                oid, sub = tail, ""
            if not re.match(r"^btc_[A-Za-z0-9_-]{1,32}$", oid):
                _json_response(self, 400, {"error": "invalid order id"})
                return
            order = btc_payments.get_order(oid)
            if not order:
                _json_response(self, 404, {"error": "not found"})
                return
            if sub == "qr.svg":
                # Build the BIP-21 URI from on-disk fields only — never the
                # request — so the QR can't be spoofed via the URL.
                addr = order.get("address") or ""
                sats = int(order.get("amount_sats") or 0)
                if not addr or sats <= 0:
                    self.send_error(404, "order not ready")
                    return
                btc_amount = sats / 100_000_000
                # NO label, NO email, NO order_id in the QR payload.
                # The privacy contract: only the public address + amount.
                bip21 = f"bitcoin:{addr}?amount={btc_amount:.8f}"
                try:
                    svg = qrcode_svg.make_svg(bip21)
                except ValueError as e:
                    self.send_error(500, f"qr encode failed: {e}")
                    return
                _send_xml(self, 200, svg, content_type="image/svg+xml; charset=utf-8")
                return
            _json_response(self, 200, {
                "order_id": oid,
                "status": btc_payments.status_of(oid),
                "address": order.get("address"),
                "amount_sats": order.get("amount_sats"),
                "expires_at": order.get("expires_at"),
                "tx_hash": order.get("tx_hash"),
            })
            return
        if path.startswith("/a/"):
            # Magic-link redemption. One-time consume → set session cookie → redirect.
            token = path[len("/a/"):].rstrip("/")
            if not TOKEN_RE.match(token):
                self.send_error(400, "invalid login token")
                return
            redeemed = auth.redeem_link_token(token)
            if not redeemed:
                self.send_error(404, "link expired or already used")
                return
            sid, _exp = auth.create_session(redeemed["email"])
            # `?next=…` lets the caller pick the landing page after sign-in
            # so a welcome email can drop the user directly on the home
            # anchoring UI instead of forcing them through /account.html.
            # Whitelist: must be a same-site, single-segment-leading path
            # (no scheme, no host, no protocol-relative). Falls back to
            # /account.html on any rejection — open-redirect defense.
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            next_raw = (qs.get("next", [""])[0] or "").strip()
            location = "/account.html"
            if next_raw.startswith("/") and not next_raw.startswith("//") and "\n" not in next_raw and "\r" not in next_raw and len(next_raw) < 200:
                location = next_raw
            self.send_response(303)
            self.send_header("Location", location)
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
            code = affiliate.code_for_email(email)
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
            s = affiliate.stats(email)
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
            email = self._session_email()
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
            email = self._session_email()
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
                    for ots in sorted(rdir.glob("*.ots")):
                        zf.write(ots, arcname=f"{rid}/{ots.name}")
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
            email = self._session_email()
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
            # Public landing for the affiliate program.
            _serve_static(self, "/affiliate.html")
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
        _serve_static(self, path)

    def do_POST(self):  # noqa: N802
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
        if self.path == "/api/me/affiliate/payout":
            self._handle_affiliate_payout()
            return
        if self.path == "/api/waitlist":
            self._handle_waitlist()
            return
        if self.path == "/api/btc/claim":
            self._handle_btc_claim()
            return
        if self.path.startswith("/api/unsubscribe"):
            self._handle_unsubscribe_post()
            return
        if self.path == "/api/event":
            self._handle_event()
            return
        if self.path == "/api/buy-btc":
            self._handle_buy_btc()
            return
        if self.path == "/api/anchor/batch":
            self._handle_anchor_batch()
            return
        if self.path == "/api/anchor_folder":
            self._handle_anchor_folder()
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
        pack_token = self.headers.get("X-Pack-Token", "").strip()
        pack_consumed = False
        pack_remaining = 0
        if pack_token:
            pack_consumed, pack_remaining = credits.consume_credit(pack_token)
        # API key path: alternative to session cookie / pack token. The key
        # owner must have an active subscription for the key to bypass limits.
        api_key = self.headers.get("X-Orpho-Api-Key", "").strip()
        api_key_email = api_keys.email_for_key(api_key) if api_key else None
        api_key_active = bool(api_key_email and _subscription_active_for(api_key_email))
        # Authenticated subscribers bypass the free-tier rate limit.
        subscriber_email = api_key_email or (self._session_email() if not pack_consumed else None)
        subscription_active = api_key_active or _subscription_active_for(subscriber_email)
        if not pack_consumed and not subscription_active:
            allowed, retry_after = _anchor_limiter.check(self._client_key())
            if not allowed:
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
        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        hash_hex = payload.get("hash_hex", "")
        sha512_hex = payload.get("sha512_hex")
        client_label = payload.get("client_label")
        notify_email = payload.get("notify_email")
        # Private receipts: subscriber-only feature. Anonymous and pack-only
        # anchors cannot be marked private (no owner_id to gate by).
        want_private = bool(payload.get("private", False)) and subscription_active
        # Attestation + metadata: any caller can submit these. The engine
        # sanitizes (allowlist + size caps); unknown fields are dropped.
        attestation = payload.get("attestation") if isinstance(payload.get("attestation"), dict) else None
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
        # Optional C2PA manifest hash — the engine validates shape before
        # accepting. Coexistence-first: an Orphograph receipt can reference
        # a C2PA manifest hash so verifiers see both attestations.
        c2pa_manifest_hash = payload.get("c2pa_manifest_hash") if isinstance(payload.get("c2pa_manifest_hash"), str) else None
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
        elif api_key_active:
            source = f"api:{api_key[:10]}"
        elif subscription_active:
            # HMAC-derived identifier — an attacker with only disk access
            # cannot dictionary-attack receipts→email without also stealing
            # the per-installation HMAC secret.
            source = "sub:" + auth.email_id(subscriber_email)
        else:
            source = "free"
        try:
            owner_id = auth.email_id(subscriber_email) if subscriber_email else None
            record = engine.anchor_hash(
                hash_hex,
                client_label=client_label,
                sha512_hex=sha512_hex,
                source=source,
                private=want_private,
                owner_id=owner_id if want_private else None,
                attestation=attestation,
                metadata=metadata,
                c2pa_manifest_hash=c2pa_manifest_hash,
            )
        except ValueError as e:
            _json_response(self, 400, {"error": str(e)})
            return
        low_redundancy = record["calendars_ok"] < MIN_CALENDARS_OK
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
                "private": want_private,
                "receipt_url": f"{os.environ.get('SITE_URL', 'https://orphograph.com').rstrip('/')}/r/{record['receipt_id']}",
            })
            # Persist on the record so the upgrade worker can email the
            # customer when the BTC pin actually lands (~1h later). Saved
            # only AFTER format validation so the on-disk value is always
            # a syntactically valid address.
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
        _json_response(self, 200, {
            "receipt_id": rid,
            "created_at": record["created_at"],
            "hash_hex": record["hash_hex"],
            "sha512_hex": record.get("sha512_hex"),
            "client_label": record["client_label"],
            "calendars_ok": record["calendars_ok"],
            "calendars_total": record["calendars_total"],
            "low_redundancy": low_redundancy,
            "pack_consumed": pack_consumed,
            "pack_remaining": pack_remaining,
            "subscription_active": subscription_active,
            "successes": [{"calendar": s["calendar"], "ots_path": s["ots_path"]} for s in record["successes"]],
            "failures": record["failures"],
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
        except (UnicodeDecodeError, json.JSONDecodeError):
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

    def _handle_buy_btc(self) -> None:
        # Per-IP rate limit so anonymous order creation can't be abused.
        allowed, retry = _anchor_limiter.check(f"btc:{self._client_key()}")
        if not allowed:
            _json_response(self, 429, {"error": "too many requests",
                                       "retry_after_seconds": int(retry) + 1})
            return
        if not btc_payments.is_configured():
            _json_response(self, 503, {"error": "BTC checkout not configured"})
            return
        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        email = payload.get("email", "")
        if not isinstance(email, str) or not EMAIL_RE.match(email.strip()):
            _json_response(self, 400, {"error": "invalid email"})
            return
        email = email.strip()

        # USD amount: $7 default Pack. (Tier param is reserved for
        # future Personal subscriptions via BTC — not built yet.)
        usd_amount = 7.0

        # Use a random 4-digit suffix so the exact sat amount is unique
        # to this order. The settle worker matches by exact amount.
        suffix = secrets.randbelow(10000) if "secrets" in globals() else int.from_bytes(os.urandom(2), "big") % 10000
        sats = btc_price.sats_for_usd(usd_amount, suffix=suffix)
        if sats <= 0:
            _json_response(self, 503, {"error": "BTC price feed unavailable; try again in a minute"})
            return

        try:
            order = btc_payments.create_order(email=email, usd_amount=usd_amount, sats_amount=sats)
        except (RuntimeError, ValueError) as e:
            _json_response(self, 400, {"error": str(e)})
            return

        # bitcoin: URI — opens in the user's wallet app on click.
        btc_amount = sats / 100_000_000
        bitcoin_uri = f"bitcoin:{order['address']}?amount={btc_amount:.8f}&label=Orphograph+Pack"
        _json_response(self, 200, {
            "ok": True,
            "order_id": order["order_id"],
            "address": order["address"],
            "amount_sats": sats,
            "amount_btc": f"{btc_amount:.8f}",
            "usd_amount": usd_amount,
            "expires_at": order["expires_at"],
            "bitcoin_uri": bitcoin_uri,
            "buy_page": f"/buy/{order['order_id']}",
        })

    def _handle_anchor_batch(self) -> None:
        """Anchor up to 50 hashes in one request. Same auth model as /api/anchor.

        Each item gets its own receipt (one OTS submission per hash; calendars
        already batch internally). Useful for the folder-watcher CLI sending
        a backlog. API-key auth bypasses the rate limit; pack tokens consume
        one credit per item; subscribers anchor under their session.
        """
        # Auth resolution mirrors /api/anchor but with one twist: pack-token
        # credit-consumption happens per-item below so partial fills work.
        pack_token = self.headers.get("X-Pack-Token", "").strip()
        api_key = self.headers.get("X-Orpho-Api-Key", "").strip()
        api_key_email = api_keys.email_for_key(api_key) if api_key else None
        api_key_active = bool(api_key_email and subscriptions.is_active(api_key_email))
        session_email = self._session_email()
        sub_active = api_key_active or bool(session_email and subscriptions.is_active(session_email))
        effective_email = api_key_email or session_email

        # Free tier is rate-limited per IP; consume ONE token for the whole
        # batch (the per-item OTS work is what we're budgeting against).
        if not pack_token and not api_key_active and not sub_active:
            allowed, retry_after = _anchor_limiter.check(self._client_key())
            if not allowed:
                _json_response(self, 429, {
                    "error": "rate limit exceeded",
                    "retry_after_seconds": int(retry_after) + 1,
                    "limit_per_day": ANCHOR_RATE_CAPACITY,
                    "hint": "Buy a Pack or subscribe to skip rate limits.",
                })
                return

        length = _read_content_length(self)
        if length <= 0 or length > MAX_BATCH_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size",
                                       "max_bytes": MAX_BATCH_BODY_BYTES})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return

        items = payload.get("hashes") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            _json_response(self, 400, {"error": "expected non-empty 'hashes' array"})
            return
        if len(items) > MAX_BATCH_ITEMS:
            _json_response(self, 400, {"error": f"too many items (max {MAX_BATCH_ITEMS})"})
            return

        results: list[dict] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                results.append({"index": idx, "ok": False, "error": "item must be an object"})
                continue
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
                )
            except ValueError as e:
                results.append({"index": idx, "ok": False, "error": str(e),
                                "client_label": client_label})
                continue
            site = os.environ.get("SITE_URL", "https://orphograph.com").rstrip("/")
            rid = record["receipt_id"]
            results.append({
                "index": idx,
                "ok": True,
                "receipt_id": rid,
                "created_at": record["created_at"],
                "client_label": record["client_label"],
                "calendars_ok": record["calendars_ok"],
                "calendars_total": record["calendars_total"],
                "low_redundancy": record["calendars_ok"] < MIN_CALENDARS_OK,
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

    def do_HEAD(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/event":
            self._event_method_not_allowed()
            return
        # Fall back to Python default for everything else.
        self.send_error(501, "Unsupported method ('HEAD')")

    def do_OPTIONS(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/event":
            self._event_method_not_allowed()
            return
        self.send_error(501, "Unsupported method ('OPTIONS')")

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
        except (UnicodeDecodeError, json.JSONDecodeError):
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
        # Bound page length; the client only ever sends location.pathname
        # which is well under this cap. We do NOT coerce the value — it's
        # written verbatim so the funnel report can show real paths.
        page = page[:MAX_EVENT_PAGE_LEN]
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "page": page,
            "ip_trunc": client_key,
        }
        try:
            FUNNEL_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with FUNNEL_EVENTS_PATH.open("a") as f:
                f.write(json.dumps(row) + "\n")
                f.flush()
        except OSError:
            # Disk full / read-only volume — drop silently. The page user
            # gets no benefit from being told their analytics ping failed.
            pass
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
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        email = payload.get("email", "")
        interest = payload.get("interest", "personal")
        if not isinstance(email, str) or not EMAIL_RE.match(email.strip()):
            # Don't leak whether the address was valid.
            _json_response(self, 200, {"ok": True})
            return
        waitlist.add(email.strip(), interest if isinstance(interest, str) else "personal")
        _json_response(self, 200, {"ok": True, "message": "On the list."})

    def _handle_btc_claim(self) -> None:
        """Buyer self-reports a Bitcoin payment. Stored for manual fulfillment."""
        allowed, retry = _anchor_limiter.check(f"btc_claim:{self._client_key()}")
        if not allowed:
            _json_response(self, 429, {"error": "too many requests", "retry_after_seconds": int(retry) + 1})
            return
        length = _read_content_length(self)
        if length <= 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        ok, result = btc_claims.submit(
            email      = payload.get("email", ""),
            txid       = payload.get("txid", ""),
            pack_size  = payload.get("pack_size", 0) if isinstance(payload.get("pack_size"), int) else 0,
            usd        = payload.get("usd"),
            btc_amount = payload.get("btc_amount"),
            btc_address= payload.get("btc_address", "") if isinstance(payload.get("btc_address"), str) else "",
            note       = payload.get("note", "") if isinstance(payload.get("note"), str) else "",
            source_ip  = _truncate_ip(self._client_ip()),
        )
        if not ok:
            _json_response(self, 400, {"error": result})
            return
        _json_response(self, 200, {"ok": True, "claim_id": result,
                                   "message": "Got it. We verify on-chain and email your claim code within ~1 hour."})

    def _parse_unsub_email(self) -> str:
        """Extract ?e=<email> from the request path. Returns '' if absent/invalid."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        e = (qs.get("e") or [""])[0].strip()
        if not e or not EMAIL_RE.match(e):
            return ""
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
        added = unsubscribe.add(email, source="link_get")
        body = (
            "<!doctype html><meta charset=utf-8>"
            "<title>Unsubscribed — Orphograph</title>"
            "<style>"
            "body{font:14px/1.6 system-ui;background:#fdfaf3;color:#1f1d1a;"
            "max-width:520px;margin:80px auto;padding:0 20px;}"
            "h1{color:#4a9a73;font-weight:500;}"
            ".muted{color:#837e75;}"
            "a{color:#4a9a73;}"
            "</style>"
            "<h1>Done — you're unsubscribed.</h1>"
            f"<p>We've removed <strong>{email}</strong> from all marketing "
            "email. You will still receive <em>transactional</em> mail "
            "tied to actions you take on the site (receipts, sign-in "
            "links, pack codes) — those are required by the service "
            "itself, not promotional.</p>"
            "<p class=muted>If this was a mistake, just sign in again "
            "or buy a pack and you'll be re-enrolled per your action.</p>"
            f"<p>{'Confirmed.' if added else 'Already on the suppression list — no action needed.'}</p>"
            "<p><a href='/'>Back to Orphograph</a></p>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
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
        token = os.environ.get("ORPHO_FOUNDER_TOKEN", "").strip()
        if not token:
            self.send_error(404, "not found")
            return
        supplied = self.headers.get("X-Orpho-Founder", "").strip()
        # Constant-time compare to avoid timing-side-channel leaks of the token.
        import hmac as _hmac
        if not _hmac.compare_digest(supplied, token):
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
        token = os.environ.get("ORPHO_FOUNDER_TOKEN", "").strip()
        if not token:
            self.send_error(404, "not found")
            return
        supplied = self.headers.get("X-Orpho-Founder", "").strip()
        import hmac as _hmac
        if not _hmac.compare_digest(supplied, token):
            self.send_error(404, "not found")
            return
        # Import here to avoid circular dependency
        import analytics
        metrics = analytics.metrics(days_back=90)
        _json_response(self, 200, metrics)

    def _handle_founder_customer_lookup(self) -> None:
        """JSON endpoint — founder-only customer lookup by email.

        Query params: ?email=buyer@example.com
        Returns customer profile: anchors, purchases, subscription, total spent.
        """
        token = os.environ.get("ORPHO_FOUNDER_TOKEN", "").strip()
        if not token:
            self.send_error(404, "not found")
            return
        supplied = self.headers.get("X-Orpho-Founder", "").strip()
        import hmac as _hmac
        if not _hmac.compare_digest(supplied, token):
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
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        name = (payload.get("team_name") or "").strip()[:80]
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
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        code = (payload.get("invite_code") or "").strip()
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
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        member_email = (payload.get("member_email") or "").strip().lower()
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
        except (UnicodeDecodeError, json.JSONDecodeError):
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
        expected_source = "sub:" + viewer_id
        if rec.get("source") != expected_source:
            # Don't reveal whether receipt exists for another owner
            _json_response(self, 404, {"error": "receipt not found"})
            return
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
        token = os.environ.get("ORPHO_FOUNDER_TOKEN", "").strip()
        if not token:
            self.send_error(404, "not found")
            return
        supplied = self.headers.get("X-Orpho-Founder", "").strip()
        import hmac as _hmac
        if not _hmac.compare_digest(supplied, token):
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
        token = os.environ.get("ORPHO_FOUNDER_TOKEN", "").strip()
        if not token:
            self.send_error(404, "not found")
            return
        supplied = self.headers.get("X-Orpho-Founder", "").strip()
        import hmac as _hmac
        if not _hmac.compare_digest(supplied, token):
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
            events_path = ROOT / "data" / "events.jsonl"
            if events_path.exists():
                cutoff = now_utc - timedelta(hours=24)
                n = 0
                # Read only the last 4 KiB — events are append-only and we just
                # want a magnitude estimate, not a full scan.
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
        token = os.environ.get("ORPHO_FOUNDER_TOKEN", "").strip()
        if not token:
            self.send_error(404, "not found")
            return
        supplied = self.headers.get("X-Orpho-Founder", "").strip()
        import hmac as _hmac
        if not _hmac.compare_digest(supplied, token):
            self.send_error(404, "not found")
            return

        events_path = Path(__file__).resolve().parent.parent / "data" / "events.jsonl"
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

        def _rate(num: int, den: int) -> float:
            return round(100.0 * num / den, 1) if den else 0.0

        rates_30d = {
            "visible_to_anchored": _rate(totals["file_anchored"], totals["drop_zone_visible"]),
            "anchored_to_checkout": _rate(totals["checkout_clicked"], totals["file_anchored"]),
            "checkout_to_paid": _rate(totals["checkout_returned_success"], totals["checkout_clicked"]),
            "visible_to_paid": _rate(totals["checkout_returned_success"], totals["drop_zone_visible"]),
        }

        days_sorted = sorted(per_day.keys(), reverse=True)
        series = [{"date": d, **per_day[d]} for d in days_sorted]

        _json_response(self, 200, {
            "timestamp": now_utc.isoformat() + "Z",
            "totals_30d": totals,
            "rates_30d_pct": rates_30d,
            "events_scanned": total_lines,
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
        unsubscribe.add(email, source="link_post")
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
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        url = (payload.get("url") or "").strip()
        result = webhooks.register(email=email, url=url)
        if not result.get("ok"):
            _json_response(self, 400, {"error": result.get("reason", "register_failed")})
            return
        # The secret is returned ONCE here; clients must persist it.
        _json_response(self, 200, result)

    def _handle_anchor_folder(self) -> None:
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
            _json_response(self, 503, {
                "error": "anchoring temporarily unavailable",
                "detail": "Calendar service unavailable. Anchoring is temporarily disabled.",
            })
            return
        # Authentication / paid-path: same precedence as /api/anchor.
        pack_token = self.headers.get("X-Pack-Token", "").strip()
        pack_consumed = False
        if pack_token:
            pack_consumed, _ = credits.consume_credit(pack_token)
        api_key = self.headers.get("X-Orpho-Api-Key", "").strip()
        api_key_email = api_keys.email_for_key(api_key) if api_key else None
        api_key_active = bool(api_key_email and _subscription_active_for(api_key_email))
        subscriber_email = api_key_email or (self._session_email() if not pack_consumed else None)
        subscription_active = api_key_active or _subscription_active_for(subscriber_email)
        if not pack_consumed and not subscription_active:
            allowed, retry_after = _anchor_limiter.check(self._client_key())
            if not allowed:
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
                return
        length = _read_content_length(self)
        if length <= 0 or length > MAX_FOLDER_MANIFEST_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "body must be JSON"})
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
            _json_response(self, 400, {"error": "manifest is required"})
            return
        leaves = manifest.get("leaves")
        if not isinstance(leaves, list) or not leaves or len(leaves) > MAX_FOLDER_LEAVES:
            _json_response(self, 400, {
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
        except (KeyError, TypeError, ValueError) as e:
            _json_response(self, 400, {"error": f"manifest invalid: {e}"})
            return
        # Optional Ed25519 authorship signature. The signature block is
        # additive: a manifest with no signature anchors exactly as before.
        # If a signature IS present, it MUST verify — a manifest that claims
        # a signature but fails verification is worse than no signature.
        sig_verified: bool | None = None
        signer_kid: str | None = None
        if isinstance(manifest.get("signature"), dict):
            if manifest_signature is None:
                _json_response(self, 503, {
                    "error": "manifest signature verification unavailable in this build",
                    "detail": "Anchor the manifest without a signature block, or use a build with Ed25519 support.",
                })
                return
            ok, reason = manifest_signature.verify_manifest_signature(manifest)
            if not ok:
                _json_response(self, 400, {
                    "error": "manifest signature invalid",
                    "detail": reason,
                })
                return
            sig_verified = True
            signer_kid = manifest["signature"].get("kid")
        root_hex = tree.root_hex()
        client_label = payload.get("client_label")
        if isinstance(client_label, str):
            client_label = client_label[:200]
        else:
            client_label = None
        if pack_consumed:
            source = f"pack:{pack_token[:8]}"
        elif api_key_active:
            source = f"api:{api_key[:10]}"
        elif subscription_active:
            source = "sub:" + auth.email_id(subscriber_email)
        else:
            source = "free"
        want_private = bool(payload.get("private", False)) and subscription_active
        try:
            record = engine.anchor_hash(
                root_hex,
                client_label=client_label,
                source=source,
                private=want_private,
                owner_id=auth.email_id(subscriber_email) if (want_private and subscriber_email) else None,
            )
        except ValueError as e:
            _json_response(self, 400, {"error": str(e)})
            return
        # Persist the manifest alongside the receipt. The receipt's hash_hex
        # already equals manifest.root_hex, so the OTS anchor binds every
        # leaf transitively: tamper with a single path or file digest, the
        # root changes, the anchor no longer verifies.
        rid = record["receipt_id"]
        manifest_to_store = dict(manifest)
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
            _json_response(self, 500, {"error": f"could not persist manifest: {e}"})
            return
        # Mark the receipt itself as a folder anchor so verifiers know to
        # fetch the manifest in addition to the .ots files.
        try:
            rfile = engine.RECEIPTS_DIR / rid / "receipt.json"
            on_disk = json.loads(rfile.read_text())
            on_disk["kind"] = "folder"
            on_disk["leaf_count"] = len(leaves)
            on_disk["merkle_algorithm"] = merkle.ALGORITHM
            if sig_verified is not None:
                on_disk["signature_verified"] = sig_verified
                on_disk["signer_kid"] = signer_kid
            rfile.write_text(json.dumps(on_disk, indent=2))
        except OSError:
            pass
        response_body = {
            "receipt_id": rid,
            "root_hex": root_hex,
            "leaf_count": len(leaves),
            "kind": "folder",
            "merkle_algorithm": merkle.ALGORITHM,
            "calendars_ok": record["calendars_ok"],
            "calendars_total": record["calendars_total"],
            "created_at": record["created_at"],
        }
        if sig_verified is not None:
            response_body["signature_verified"] = sig_verified
            response_body["signer_kid"] = signer_kid
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
        if record.get("kind") != "folder":
            _json_response(self, 400, {"error": "receipt is not a folder anchor"})
            return
        is_owner = False
        if record.get("private"):
            session_email = self._session_email()
            viewer_id = auth.email_id(session_email) if session_email else None
            if not viewer_id or viewer_id != record.get("owner_id"):
                _json_response(self, 404, {"receipt_id": rid, "found": False, "error": "receipt not found"})
                return
            is_owner = True
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
        if not is_owner:
            redacted_leaves = []
            for i, leaf in enumerate(manifest.get("leaves", [])):
                redacted_leaves.append({
                    "index": i,
                    "leaf_hex": leaf.get("leaf_hex"),
                    "file_sha256_hex": leaf.get("file_sha256_hex"),
                    "size_bytes": leaf.get("size_bytes"),
                    # path intentionally withheld
                })
            manifest = {
                **{k: v for k, v in manifest.items() if k != "leaves"},
                "leaves": redacted_leaves,
                "paths_redacted": True,
                "paths_redaction_reason": (
                    "Leaf paths are visible only to the receipt owner. "
                    "Inclusion proofs remain available to anyone who already "
                    "knows the path of the file they wish to prove."
                ),
            }
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
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "invalid request"})
            return
        sid = (payload.get("stripe_session_id") or "").strip()
        provided_email = (payload.get("email") or "").strip().lower()
        # Strict shape check on the session id. Stripe ids are cs_test_ or
        # cs_live_ followed by alphanumerics + underscores.
        if not sid.startswith(("cs_test_", "cs_live_")) or len(sid) > 256 \
           or not all(c.isalnum() or c == "_" for c in sid):
            _json_response(self, 400, {"error": "invalid request"})
            return
        if not provided_email or "@" not in provided_email or len(provided_email) > 254:
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
                f"[recover] subscription path session={sid} "
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
                f"[recover] NO CLAIM FOUND for paid session {sid} "
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
            f"[recover] resent claim_code for session={sid} "
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
            except (UnicodeDecodeError, json.JSONDecodeError):
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
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return
        url = (payload.get("url") or "").strip()
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
            _json_response(self, 502, {"error": "stripe error", "detail": result.get("error")})
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
            _json_response(self, 502, {"error": "stripe error", "detail": result.get("error")})
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
        # Light rate-limit so this can't be used as a session-id oracle
        allowed, _ = _anchor_limiter.check(f"stripe-session:{self._client_key()}")
        if not allowed:
            _json_response(self, 429, {"error": "rate limit exceeded"})
            return
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(self.path).query)
        sid_list = query.get("id", [])
        sid = sid_list[0] if sid_list else ""
        # Stripe session IDs are cs_test_… or cs_live_… plus alphanumerics
        if not sid or not sid.startswith("cs_") or len(sid) > 256 or not all(c.isalnum() or c == "_" for c in sid):
            _json_response(self, 400, {"error": "invalid session id"})
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
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(self, 400, {"error": "body must be JSON"})
            return

        plan = (payload.get("plan") or "").strip().lower()
        email = (payload.get("email") or "").strip()
        if plan == "pack":
            price_id = os.environ.get("STRIPE_PRICE_PACK", "")
            mode = "payment"
        elif plan in ("pro", "sub", "subscription", "standing", "standing_order"):
            price_id = os.environ.get("STRIPE_PRICE_SUB", "")
            mode = "subscription"
        else:
            _json_response(self, 400, {"error": "plan must be 'pack' or 'pro'"})
            return
        if not price_id:
            _json_response(self, 503, {
                "error": f"Stripe price not configured (STRIPE_PRICE_{'PACK' if plan == 'pack' else 'SUB'} unset)",
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
        success_url = f"{site}/buy.html?stripe_session={{CHECKOUT_SESSION_ID}}&status=success"
        cancel_url = f"{site}/?stripe=canceled"

        result = stripe_api.create_checkout_session(
            price_id=price_id,
            mode=mode,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=email if "@" in email else "",
        )
        if not result.get("ok"):
            _json_response(self, 502, {"error": result.get("error", "stripe error")})
            return
        data = result.get("data") or {}
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
        length = _read_content_length(self)
        if length < 0 or length > MAX_BODY_BYTES:
            _json_response(self, 400, {"error": "invalid body size"})
            return
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
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
        plan_meta = nowpayments_api.PLANS[plan]
        # Order id is opaque + unguessable so retries/lookups are safe to
        # leak in URLs. We use the same token shape as receipt ids.
        order_id = "np_" + secrets.token_urlsafe(10)
        result = nowpayments_api.create_invoice(
            amount_usd=float(plan_meta["price_usd"]),
            currency=currency,
            order_id=order_id,
            customer_email=email if "@" in email else None,
        )
        if not result.get("ok"):
            _json_response(self, 502, {
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
            _json_response(self, 502, {
                "ok": False,
                "error": "Payment provider returned no invoice URL.",
            })
            return
        _json_response(self, 200, {
            "ok": True,
            "url": invoice_url,
            "order_id": order_id,
            "plan": plan,
            "currency": currency,
        })


def _count_anchors_for_email(email: str) -> int:
    """Fast O(receipts) count of anchors owned by this email.

    Reads only `source` from each receipt.json (small field at the top
    via streaming json parse fallback to full-load), skipping body parse
    when possible. Replaces the previous "list 10000 then len()" pattern
    which was a tail-latency offender on /api/me and added 2.5s+ to every
    page navigation via the status strip.
    """
    if not email:
        return 0
    expected_source = "sub:" + auth.email_id(email)
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
        if rec.get("source") == expected_source:
            count += 1
    return count


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
    expected_source = "sub:" + auth.email_id(email)
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
        if rec.get("source") != expected_source:
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
            a.get("client_label") or "",
            a.get("hash_hex", ""),
            a.get("sha512_hex") or "",
            a.get("calendars_ok", ""),
            a.get("calendars_total", ""),
            a.get("status", ""),
            a.get("btc_pinned_at") or "",
        ])
    return buf.getvalue()


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
    target.mkdir()
    for item in sample_dir.iterdir():
        if item.name in ("index.json",):
            continue
        shutil.copy2(item, target / item.name, follow_symlinks=False)
    sys.stderr.write(f"seeded sample receipt {rid} from {sample_dir} → {target}\n")


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
    """Background thread that fires cold-outreach cadence runs on Tue/Wed/Thu at 14:00 UTC.

    Avoids the need for a separate Fly cron machine: the scheduler wakes once an
    hour, and when the current UTC time matches (hour == 14, weekday in {Tue=1,
    Wed=2, Thu=3}) it invokes scripts/cadence_runner.py --execute.

    Idempotency: a state file at DATA_DIR/.cadence_last_run records the iso date
    of the last successful fire. The scheduler refuses to fire twice on the same
    UTC date even if clock drift / restart causes the hour-14 window to be
    observed more than once.

    Kill switch: set ORPHO_CADENCE_DISABLED=1 (e.g. via `fly secrets set`) and
    the loop becomes a no-op at the next wake. No restart required.

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


def main() -> int:
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    _seed_sample_receipt()
    _start_upgrade_scheduler()
    _start_cadence_scheduler()
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
