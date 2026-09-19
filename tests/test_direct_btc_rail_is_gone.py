"""test_direct_btc_rail_is_gone.py — the direct-BTC order rail is retired.

Founder decision, 2026-09-19: the direct on-chain rail (customer sends BTC to a
per-order address, the office watches mempool.space and mints a claim code) is
withdrawn. It was never configured in production and never processed an order.

This file REPLACES two regression tests that pinned defects in code that no
longer exists:

  * tests/test_btc_amount_never_undercharges.py — pinned that the per-order
    disambiguation tag in btc_price.sats_for_usd could never reduce the charge.
    sats_for_usd is deleted; nothing can mis-charge because nothing charges.
  * tests/test_btc_settle_one_tx_one_order.py — pinned that one transaction
    could settle at most one order in scripts/btc_settle.py. That script is
    deleted; nothing can double-settle because nothing settles.

A deleted test asserts nothing. The properties those two bought are re-bought
here in the only form still meaningful once the rail is gone:

  1. EVERY former route answers 410 Gone, on GET and on HEAD, with the security
     headers intact (send_error lost them once — see
     tests/test_security_headers_on_errors.py) and with identical status and
     headers between the two methods.
  2. POST order-creation answers 410 too, BEFORE the JSON content-type gate, so
     a cross-origin form post cannot reach a handler either. /api/btc/claim was
     in tests/test_csrf_and_reflected_xss.py's STATE_CHANGING list while it
     still changed state; it answers 410 to every CORS-simple content type now,
     which is strictly stronger than the 415 that guard asked for.
  3. NO ADDRESS IS EVER ISSUED: the address-issuing modules are gone, no served
     page or server template carries a bech32 mainlnet address, and the server
     source has no order-creation entry point left.

Every scan carries a NEGATIVE CONTROL. A grep that finds nothing is
indistinguishable from a grep that cannot reach its files.

What is deliberately NOT touched, and must not be swept up here:
  * Bitcoin ANCHORING via OpenTimestamps — the product itself.
  * The L402 / Lightning rail (server/lightning.py, /api/ln/quote) — dormant,
    a separate decision. tests/test_capability_copy.py still pins its copy.
  * The hosted crypto processor (/pay/crypto, /api/nowpayments/*) — untouched,
    and it is the checkout the retired offers now point at.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import _srv

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SERVER = ROOT / "server"
SCRIPTS = ROOT / "scripts"

# Every path the direct rail ever answered on. Order pages, order API, the
# price proxy the pay page polled, and the static assets that drove them.
GONE_GET_PATHS = (
    "/pay/btc",
    "/pay/btc.html",
    "/pay/btc.css",
    "/pay/btc/",
    "/pay-btc.js",
    # The PER-ORDER pages only. Bare /buy is the card buyer's Stripe landing
    # and must keep serving — see SERVES_GET_PATHS below.
    "/buy/btc_AbCdEf12345",
    "/api/btc/price",
    "/api/buy-btc",
    "/api/btc/claim",
    "/api/btc-order",
    "/api/btc-order/",
    "/api/btc-order/btc_AbCdEf12345",
    "/api/btc-order/btc_AbCdEf12345/qr.svg",
)

# Paths that must NOT be retired. Each is here because something would break
# if the guard widened onto it.
SERVES_GET_PATHS = (
    "/buy",                 # Stripe success_url target — a 410 here strands
    "/buy/",                # every customer who just paid by card. normpath
    "/buy.js",              # folds the trailing slash onto the same page.
    "/buy.css",
    "/pay/crypto",          # the hosted processor
    "/pay/crypto.css",
    "/pay/crypto.js",
    "/pay/success",
    "/pricing",             # the card path the retired offers now point at
)

GONE_POST_PATHS = ("/api/buy-btc", "/api/btc/claim")

# A cross-origin HTML form can only send these three. The CSRF guard in
# tests/test_csrf_and_reflected_xss.py requires 415 for every endpoint that
# still changes state; these no longer do, and answer 410 to all three.
CORS_SIMPLE_CONTENT_TYPES = (
    "text/plain",
    "application/x-www-form-urlencoded",
    "multipart/form-data",
)

SECURITY_HEADERS = (
    "Strict-Transport-Security",
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
)

# Deleted with the rail. Each one either issued an address, took money, or
# rendered a page that did.
DELETED_FILES = (
    SERVER / "btc_payments.py",
    SERVER / "btc_hd.py",
    SERVER / "btc_claims.py",
    SERVER / "qrcode_svg.py",
    SCRIPTS / "btc_settle.py",
    WEB / "pay" / "btc.html",
    WEB / "pay" / "btc.css",
    WEB / "pay-btc.js",
)

# Kept on purpose — assert them, so a later sweep deleting one is a decision
# rather than a side effect.
KEPT_FILES = (
    SERVER / "lightning.py",          # L402: dormant, separate decision
    SERVER / "nowpayments_api.py",    # hosted processor: the live crypto path
    SERVER / "nowpayments_webhook.py",
    SERVER / "ots_timestamp.py",      # Bitcoin ANCHORING: the product
    WEB / "pay" / "crypto.html",
    WEB / "pay" / "crypto.js",
    WEB / "buy.html",                 # Stripe's success_url target
    WEB / "buy.js",
    WEB / "buy.css",
)

# bech32 mainnet, the shape every address the rail ever handed out had.
BECH32_MAINNET = re.compile(r"\bbc1[qp][02-9ac-hj-np-z]{25,87}\b")

# Order-creation entry points. If any of these names comes back in server/ or
# scripts/, an order can be created again.
ISSUING_SYMBOLS = (
    "address_for_order",
    "create_order",
    "btc_payments",
    "btc_claims",
    "_handle_buy_btc",
    "_handle_btc_claim",
)


# ── the wire ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def base(tmp_path_factory):
    # BTC_RECEIVE_ADDRESS deliberately SET. If any issuing code survived, a
    # configured rail is exactly what would let it answer 200 instead of 410 —
    # testing against an unconfigured server would pass on a 503 that proves
    # nothing. The address below is a syntactically valid bech32 that belongs
    # to nobody; no money can move either way, the rail is gone.
    yield from _srv.server_processes(
        tmp_path_factory.mktemp("btc-gone"),
        stub_calendars=True,
        BTC_RECEIVE_ADDRESS="bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
        BTC_PAYMENTS_ENABLED="1",
    )


@pytest.mark.parametrize("method", ("GET", "HEAD"))
@pytest.mark.parametrize("path", GONE_GET_PATHS)
def test_every_former_route_says_gone(base, path, method) -> None:
    """410, not 404: these sat in the sitemap, the homepage footer and the
    published API docs, and a crawler drops a Gone page and its cached snippet
    far sooner than a Not Found."""
    status, _body, headers = _srv.request(base, path, method)
    assert status == 410, (method, path, status)
    for h in SECURITY_HEADERS:
        assert headers.get(h), f"{method} {path}: the 410 path lost {h}"


@pytest.mark.parametrize("path", GONE_GET_PATHS)
def test_get_and_head_answer_identically(base, path) -> None:
    """RFC 9110 §9.3.2. A HEAD that disagrees with its GET is how a scanner
    concluded the site had no HSTS while GET served it correctly."""
    g_status, g_body, g_headers = _srv.request(base, path, "GET")
    h_status, h_body, h_headers = _srv.request(base, path, "HEAD")
    assert g_status == h_status == 410, (path, g_status, h_status)
    assert h_body == b"", f"HEAD {path} returned a body ({len(h_body)} bytes)"
    for h in SECURITY_HEADERS + ("Content-Type",):
        assert g_headers.get(h) == h_headers.get(h), (
            f"{path}: {h} differs between GET and HEAD "
            f"({g_headers.get(h)!r} vs {h_headers.get(h)!r})")


@pytest.mark.parametrize("path", GONE_POST_PATHS)
def test_order_creation_is_impossible(base, path) -> None:
    status, raw = _srv.post_json(base, path, {"email": "buyer@example.com",
                                              "txid": "a" * 64})
    assert status == 410, (path, status, raw)
    assert "address" not in raw, f"{path} handed back an address: {raw}"
    assert "order_id" not in raw, f"{path} created an order: {raw}"


@pytest.mark.parametrize("ctype", CORS_SIMPLE_CONTENT_TYPES)
@pytest.mark.parametrize("path", GONE_POST_PATHS)
def test_gone_is_answered_before_the_content_type_gate(base, path, ctype) -> None:
    """The 410 must sit ahead of _reject_non_json_post, or a CORS-simple POST
    reads 415 — 'wrong content type', i.e. 'right type and I would serve you'."""
    status, _raw, _h = _srv.request(
        base, path, "POST", b'{"email":"buyer@example.com"}',
        {"Content-Type": ctype})
    assert status == 410, (path, ctype, status)


def test_the_reason_phrase_survives_the_status_line() -> None:
    """The reason phrase goes into the HTTP status line, which the stdlib
    encodes latin-1. The first draft of this retirement used an em dash: every
    retired route raised UnicodeEncodeError inside send_error and closed the
    connection with NO RESPONSE — which a status-code assertion alone would
    have reported as a connection error, not as a missing 410."""
    app_src = (SERVER / "app.py").read_text(encoding="utf-8")
    m = re.search(r'^_RETIRED_BTC_MESSAGE = "([^"]*)"', app_src, re.MULTILINE)
    assert m, "_RETIRED_BTC_MESSAGE is not a plain string literal any more"
    phrase = m.group(1)
    assert phrase, "the reason phrase is empty"
    ("HTTP/1.0 410 %s\r\n" % phrase).encode("latin-1", "strict")
    assert "\r" not in phrase and "\n" not in phrase, "reason phrase splits the response"


def _handler_for_post(path: str, body: bytes, ctype: str = "application/json"):
    """A Handler instance wired to in-memory streams, with send_error stubbed.

    BaseHTTPRequestHandler.__init__ runs the whole request cycle, so the
    instance is built with __new__ and the four attributes do_POST touches are
    set by hand. Nothing but the dispatch and the drain is exercised.
    """
    import email.message
    import io
    import sys as _sys
    _sys.path.insert(0, str(_srv.REPO_ROOT / "server"))
    import app as _app

    h = _app.Handler.__new__(_app.Handler)
    h.path = path
    h.command = "POST"
    h.request_version = "HTTP/1.0"
    h.client_address = ("127.0.0.1", 40000)
    h.rfile = io.BytesIO(body)
    h.wfile = io.BytesIO()
    msg = email.message.Message()
    msg["Content-Type"] = ctype
    msg["Content-Length"] = str(len(body))
    h.headers = msg
    sent = {}

    def _send_error(code, message=None, explain=None):
        sent["code"] = code
        sent["message"] = message
    h.send_error = _send_error
    return _app, h, sent


@pytest.mark.parametrize("path", GONE_POST_PATHS)
def test_the_410_drains_the_request_body_before_answering(path) -> None:
    """THE DRAIN, pinned by a means that CAN fail.

    This is HTTP/1.0: the socket closes after the response. Closing it while
    received bytes are still unread makes the kernel send RST rather than FIN,
    and an RST can discard the response before the client reads it — so a
    caller POSTing to a retired endpoint sees ECONNRESET or a proxy 502
    instead of the 410. Cached copies of the previous v2.js and app.js still
    POST here from browsers that have not revalidated.

    Loopback usually does NOT reproduce the RST, so a wire test that merely
    gets a 410 proves nothing about the drain. This one asserts the handler
    actually CONSUMED Content-Length bytes: it fails if the drain is removed.

    Body size is irrelevant to the hazard and deliberately small — a body that
    already sits in the kernel receive buffer is exactly the unread-at-close
    case. MAX_BODY_BYTES is 4096, so every body the server accepts is in it.
    """
    body = b'{"email":"buyer@example.com","pad":"' + b"x" * 900 + b'"}'
    _app, h, sent = _handler_for_post(path, body)
    _app.Handler.do_POST(h)
    assert sent.get("code") == 410, sent
    assert h.rfile.tell() == len(body), (
        f"{path}: handler answered 410 having consumed {h.rfile.tell()} of "
        f"{len(body)} body bytes — the socket would close with unread data")


def test_the_drain_assertion_can_fail() -> None:
    """NEGATIVE CONTROL for the test above. A handler that answers WITHOUT
    draining must leave the stream unconsumed — otherwise the assertion is
    measuring something that is true either way."""
    import io
    _app, h, sent = _handler_for_post("/api/buy-btc", b'{"a":1}')
    h.rfile = io.BytesIO(b'{"a":1}')
    h.send_error(410, "stub")          # answer without draining
    assert h.rfile.tell() == 0, "an undrained stream must read as unconsumed"


def test_the_415_path_still_drains(base) -> None:
    """The drain helper is shared with the content-type gate; a refactor that
    breaks one breaks both. Wire-level: a CORS-simple POST to a LIVE endpoint
    still answers 415 with a complete body."""
    status, raw, _h = _srv.request(
        base, "/api/anchor", "POST", b"hash_hex=" + b"a" * 64,
        {"Content-Type": "text/plain"})
    assert status == 415, status
    assert raw, "the 415 response body was lost"


# NO RAW-SOCKET TEST HERE, deliberately.
#
# The obvious integration half — open a socket, send headers, send the body,
# read to EOF, and fail on ConnectionResetError — cannot live in this module:
# tests/test_server_fixture_hygiene.py forbids a module that imports _srv from
# opening its own connections, because hand-rolled clients follow redirects,
# lose duplicated headers, and report a dead server as an empty body.
#
# That guard is worth more than the test it blocks. By this file's own
# reasoning the raw-socket version was never what proved the drain anyway —
# loopback rarely reproduces the RST, so a green run there is compatible with
# no drain at all. test_the_410_drains_the_request_body_before_answering is
# the real gate: it asserts the bytes were consumed, and it goes red the
# moment the drain is removed.


def test_the_site_still_answers_at_all(base) -> None:
    """NEGATIVE CONTROL for the wire tests. If every path 410'd, or the server
    were wedged, the parametrised tests above would pass while proving nothing."""
    status, _raw, _h = _srv.request(base, "/api/health")
    assert status == 200, status
    # The hosted crypto checkout is the path the retired offers now point at.
    # `.html` canonicalises to the extensionless URL with a 301, which is also
    # the proof that a NON-retired `.html` path still reaches the static
    # handler — the retired ones are intercepted before it and never 301.
    status, _raw, headers = _srv.request(base, "/pay/crypto.html")
    assert status == 301, ("the hosted crypto checkout must still serve", status)
    assert headers.get("Location", "").endswith("/pay/crypto")
    status, _raw, _h = _srv.request(base, "/pay/crypto")
    assert status == 200, ("the hosted crypto checkout must still serve", status)


@pytest.mark.parametrize("path", SERVES_GET_PATHS + (
    "/pay/success.css", "/pay/success.js",
))
def test_the_neighbours_of_the_guard_still_serve(base, path) -> None:
    """The retirement guard matches /pay/btc*, /buy/<id> and /api/btc-order*.
    Its NEIGHBOURS are the WORKING payment paths — the card confirmation page
    and the hosted processor — and a guard that widened onto one of them would
    break checkout silently. This is the boundary, asserted."""
    status, _body, _h = _srv.request(base, path)
    assert status in (200, 301), (path, status)


# ── the card path the retirement must never have touched ────────────────────

def test_the_stripe_success_url_target_serves(base) -> None:
    """THE REGRESSION THIS FILE MISSED THE FIRST TIME.

    /buy and /buy/<order_id> were two URLs on one document. Only the second
    belonged to the BTC rail; bare /buy is what _handle_stripe_checkout builds
    as Stripe's success_url, so retiring it put a 410 in front of every card
    buyer at the moment their card had already been charged.

    Driven in the exact shape Stripe redirects to."""
    status, body, _h = _srv.request(
        base, "/buy?stripe_session=cs_test_abc123&status=success")
    assert status == 200, ("Stripe's success_url must serve", status)
    text = body.decode("utf-8", "replace")
    assert "buy.js" in text, "the confirmation page must load its script"
    assert 'id="settled"' in text, "the confirmation block must be on the page"


def test_the_success_url_shape_is_built_from_a_path_that_is_not_retired() -> None:
    """Pin the two halves together. A future edit that repoints success_url, or
    that re-adds the bare path to the retired set, must fail here rather than
    in production after a customer has paid."""
    import re as _re
    app_src = (SERVER / "app.py").read_text(encoding="utf-8")
    m = _re.search(r'success_url = f"\{site\}(/[^?"]*)', app_src)
    assert m, "could not find the Stripe success_url construction"
    target = m.group(1)
    sys_path_guard = _srv.REPO_ROOT / "server"
    import sys
    if str(sys_path_guard) not in sys.path:
        sys.path.insert(0, str(sys_path_guard))
    import app as _app
    assert not _app._is_retired_btc_path(target), (
        f"Stripe sends paying customers to {target}, which is retired")


def test_the_dot_html_form_redirects_and_keeps_the_query(base) -> None:
    """Old Stripe success URLs are /buy.html?stripe_session=…; the static
    handler canonicalises to the clean form and must carry the query, or the
    confirmation page loses the session id it needs."""
    status, _body, headers = _srv.request(
        base, "/buy.html?stripe_session=cs_test_abc123&status=success")
    assert status == 301, status
    loc = headers.get("Location", "")
    assert loc.endswith("/buy?stripe_session=cs_test_abc123&status=success"), loc


def test_the_confirmation_page_still_emits_the_conversion_beacon() -> None:
    """checkout_returned_success has exactly one emitter, and it is this page.
    Deleting the file zeroed checkout_to_paid and visible_to_paid on
    /api/founder/funnel without any error anywhere."""
    src = (WEB / "buy.js").read_text(encoding="utf-8")
    assert 'orphoEvent("checkout_returned_success")' in src
    assert "/api/stripe/session" in src, "the page must look up the session"


def test_the_confirmation_page_carries_no_btc_rail_content() -> None:
    """Restored card-only. No order polling, no address, no sat amount."""
    js = (WEB / "buy.js").read_text(encoding="utf-8")
    html = (WEB / "buy.html").read_text(encoding="utf-8")
    for banned in ("/api/btc-order", "amount_sats", "bitcoin:", "wallet-link",
                   "orderIdFromUrl", "POLL_MS"):
        assert banned not in js, f"buy.js still carries BTC rail code: {banned}"
        assert banned not in html, f"buy.html still carries BTC rail markup: {banned}"


@pytest.mark.parametrize("path", ("/buying-guide", "/buyers", "/pay/cryptocurrency"))
def test_the_guard_does_not_swallow_lookalike_paths(base, path) -> None:
    """NEGATIVE CONTROL for the prefix tuple. "/buy/" cannot match
    /buying-guide — only a bare "/buy" prefix could, which is why the entries
    are spelled with the slash. These 404 (no such page), never 410."""
    status, _body, _h = _srv.request(base, path)
    assert status == 404, (path, status, "a lookalike path must not be retired")


def test_robots_does_not_hide_the_410_from_crawlers(base) -> None:
    """`Disallow: /buy/` was removed on purpose. /buy sat in both sitemaps at
    priority 0.8, so the URL is indexed; a crawler that is told not to fetch it
    never observes the Gone and keeps the stale entry. The rationale only holds
    if nothing else in robots.txt blocks it."""
    status, body, _h = _srv.request(base, "/robots.txt")
    assert status == 200
    text = body.decode("utf-8", "replace")
    disallowed = [ln.split(":", 1)[1].strip()
                  for ln in text.splitlines()
                  if ln.lower().startswith("disallow:") and ln.split(":", 1)[1].strip()]
    # Every retired path, not just the bare prefixes. `Disallow: /buy/` — which
    # this commit removes — does NOT block "/buy" but DOES block "/buy/<id>",
    # and the per-order pages are the ones a crawler actually has indexed. A
    # check written only against "/buy" passes on the pre-cut tree and proves
    # nothing.
    for path in GONE_GET_PATHS:
        blocked = [d for d in disallowed if path.startswith(d) and not d.startswith("/api/")]
        assert not blocked, f"robots.txt hides the {path} 410 from crawlers via {blocked}"
    # CONTROL: robots.txt really does disallow things, so the loop above is
    # reading a populated list rather than an empty one.
    assert "/api/" in disallowed, f"robots.txt looks empty: {disallowed}"


def test_no_order_appears_in_the_generated_sitemap(base) -> None:
    status, body, _h = _srv.request(base, "/sitemap.xml")
    assert status == 200
    # Read the sitemap as a sitemap: parse every <loc> and compare parsed hosts
    # and paths. A substring test over the raw body would also pass on a URL
    # that merely CONTAINS the text somewhere, which is not what is meant.
    from urllib.parse import urlparse
    from xml.etree import ElementTree

    root = ElementTree.fromstring(body)
    locs = [el.text.strip() for el in root.iter() if el.tag.endswith("}loc") and el.text]
    paths = {urlparse(u).path.rstrip("/") or "/" for u in locs}
    for gone in ("/buy", "/pay/btc"):
        assert gone not in paths, f"sitemap still lists {gone}"
    # Control: a real, populated sitemap for this site, not an empty or error body.
    assert len(locs) > 20, f"sitemap has only {len(locs)} entries"
    assert {urlparse(u).hostname for u in locs} == {"orphograph.com"}
    assert "/" in paths and "/about" in paths


# ── the deploy gate agrees with the server ──────────────────────────────────

PROBE_SH = SCRIPTS / "probe_all.sh"
_PROBE_LINE = re.compile(r'^probe\s+GET\s+"([^"]+)"\s+(\d{3})\s*$', re.MULTILINE)

# The paths this retirement moved. Scoped deliberately: probe_all.sh also
# covers auth and prod-config routes whose local status legitimately differs,
# and a test that swept all of them would be flaky rather than strict.
_PROBE_PATHS_UNDER_TEST = frozenset({
    "/buy", "/buy.html", "/pay/btc", "/buy/btc_AbCdEf12345",
    "/api/btc/price", "/api/btc-order/btc_AbCdEf12345",
})


def _declared_probes() -> dict:
    text = PROBE_SH.read_text(encoding="utf-8")
    return {m.group(1): int(m.group(2)) for m in _PROBE_LINE.finditer(text)}


def test_the_deploy_probe_expects_what_the_server_actually_answers(base) -> None:
    """scripts/probe_all.sh is the deploy readiness gate. It declared
    `GET /buy.html 200` while the branch was answering 410, which would have
    turned the gate red on EVERY deploy — a gate that cannot go green stops
    meaning anything, and gets ignored or deleted.

    Each declared status is driven against a real server here, so the script
    and the handler cannot drift apart again silently."""
    declared = _declared_probes()
    covered = _PROBE_PATHS_UNDER_TEST & set(declared)
    assert covered == _PROBE_PATHS_UNDER_TEST, (
        "probe_all.sh no longer declares: "
        f"{sorted(_PROBE_PATHS_UNDER_TEST - covered)}")
    wrong = []
    for path in sorted(covered):
        want = declared[path]
        got, _body, _h = _srv.request(base, path)
        if got != want:
            wrong.append(f"{path}: script says {want}, server answers {got}")
    assert wrong == [], "probe_all.sh disagrees with the server:\n  " + "\n  ".join(wrong)


def test_the_probe_parser_reads_the_file() -> None:
    """NEGATIVE CONTROL. An empty parse would make the test above vacuous."""
    declared = _declared_probes()
    assert len(declared) > 20, f"probe_all.sh parse found {len(declared)} probes"
    assert declared.get("/api/health") == 200, "control probe missing"


def test_lighthouse_does_not_audit_the_confirmation_page() -> None:
    """/buy is noindex and only reachable mid-checkout with a session id.
    Auditing it produces a permanent SEO complaint about a page that must not
    be indexed."""
    text = (SCRIPTS / "audit_lighthouse.sh").read_text(encoding="utf-8")
    assert '"/buy.html"' not in text
    assert '"/"' in text, "control: the PAGES array is still populated"


def test_the_makefile_no_longer_manages_the_retired_agent() -> None:
    """`make local-start` bootstrapped a launchd plist for btc_settle whose
    script this branch deletes, and `make local-logs` tailed its log. Both
    would fail forever on a founder machine."""
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    agents_line = [ln for ln in mk.splitlines() if ln.startswith("LAUNCHD_AGENTS :=")]
    assert len(agents_line) == 1, agents_line
    assert "btc_settle" not in agents_line[0], agents_line[0]
    assert "server" in agents_line[0], "control: the agent list is still populated"
    assert "btc_settle.err.log" not in mk, "local-logs still tails the deleted agent"


def test_the_makefile_offers_an_idempotent_user_domain_uninstall() -> None:
    """Deleting a plist from the repo does NOT unload an agent launchd already
    registered — it leaves one re-launching against a missing file on its
    schedule. The founder runs this target; nothing in CI does.

    User domain only, and guarded: asserted here so a later edit cannot
    quietly add sudo or a system domain to something that boots out agents."""
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "local-uninstall-btc:" in mk, "no uninstall target"
    body = mk.split("local-uninstall-btc:", 1)[1].split("\n\n", 1)[0]
    for label in ("com.orphograph.btc_settle", "com.orphograph.payout"):
        assert label in body, f"{label} is not booted out"
    assert "launchctl print" in body, "not guarded — must check before bootout"
    assert "gui/$(UID)" in body, "must act in the user domain"
    assert "sudo" not in body, "an uninstall target must never use sudo"
    assert "system/" not in body, "must never touch the system domain"
    assert "local-uninstall-btc" in mk.split("help:", 1)[0], "not declared .PHONY"


# ── the dead price oracle ───────────────────────────────────────────────────

def test_the_health_endpoint_has_no_permanently_false_oracle(base) -> None:
    """btc_price.cached_usd_per_btc_source() is CACHE-ONLY by design, and the
    only two callers that ever filled that cache were GET /api/btc/price and
    the order-creation handler. Both are deleted, so the field could only ever
    report available: false — on /api/health, and on the PUBLIC /api/stats.

    A field that is structurally always false is worse than an absent one: it
    reads as "our price feed is down" rather than "we do not have one". Read
    twice, seconds apart, because a cache that could warm would warm."""
    import json as _json
    for _ in range(2):
        status, body, _h = _srv.request(base, "/api/health")
        assert status == 200
        payload = _json.loads(body)
        assert "btc_oracle" not in payload, (
            "btc_oracle is back and can never be populated: " 
            + repr(payload.get("btc_oracle")))
    status, body, _h = _srv.request(base, "/api/stats")
    assert status == 200
    public = _json.loads(body)
    assert "btc_oracle" not in public, "the PUBLIC stats endpoint advertises a dead oracle"
    # CONTROL: both payloads are real, so the absences above mean something.
    assert "calendars" in public and "anchors" in public, sorted(public)


def test_the_price_module_is_gone() -> None:
    """Nothing reads it once the oracle field is removed. Left on disk it is an
    outbound-HTTP helper with no caller, which is how a later change
    accidentally puts a third-party request back on the health path."""
    assert not (SERVER / "btc_price.py").exists()


def test_no_page_renders_a_price_oracle_tile() -> None:
    """The public /stats page and the founder dashboard both rendered the
    field. Left in place they would show "offline" / "no live price" forever."""
    for rel in ("stats.html", "stats.js", "founder/admin.html",
                "founder/admin.js", "founder/admin.css"):
        text = (WEB / rel).read_text(encoding="utf-8")
        for token in ("btc_oracle", "btc-source", "btc-val", "btc-price"):
            assert token not in text, f"{rel} still renders the dead oracle ({token})"
    # CONTROL: the stats page still renders the metrics that DO exist.
    stats_html = (WEB / "stats.html").read_text(encoding="utf-8")
    assert 'id="cal-list"' in stats_html and 'id="uptime"' in stats_html


def test_the_published_health_example_matches_the_server(base) -> None:
    """web/docs/api.html publishes an /api/health example. It showed a live
    oracle and a configured payout block with an address pool, none of which
    the server can return any more. Compared against the real response."""
    import json as _json
    import re as _re
    status, body, _h = _srv.request(base, "/api/health")
    live = set(_json.loads(body))
    docs = (WEB / "docs" / "api.html").read_text(encoding="utf-8")
    documented = set(_re.findall(r'^\s*"([a-z_]+)":', docs, _re.MULTILINE))
    # As a JSON KEY, not as prose — the page explains in words WHY the oracle
    # is absent, and that sentence must not trip its own guard.
    for stale in ("btc_oracle", "address_pool_size", "xpub_set"):
        assert stale not in documented, f"docs still publish a {stale} field"
    assert "payout" in live and "payout" in documented
    assert "calendars" in live
    # CONTROL: the parse really found the example's keys.
    assert {"version", "uptime_sec"} <= documented, sorted(documented)[:20]


# ── the tree ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", DELETED_FILES, ids=lambda p: p.name)
def test_the_issuing_code_is_off_disk(path) -> None:
    assert not path.exists(), f"{path.relative_to(ROOT)} came back"


@pytest.mark.parametrize("path", KEPT_FILES, ids=lambda p: p.name)
def test_the_neighbouring_rails_were_not_swept_up(path) -> None:
    """L402, the hosted processor and OpenTimestamps anchoring are each a
    separate decision. Deleting one must be deliberate, not collateral."""
    assert path.exists(), f"{path.relative_to(ROOT)} was removed with the direct rail"


def _server_sources() -> list[tuple[str, Path]]:
    out = []
    for d in (SERVER, SCRIPTS):
        for p in sorted(d.rglob("*.py")):
            out.append((p.relative_to(ROOT).as_posix(), p))
    return out


def _code_names(source: str) -> set[str]:
    """Every NAME token in a Python source, i.e. the identifiers the file
    actually executes.

    Deliberately NOT a text grep. `btc_payments` appears in this retirement's
    own prose (mempool_watcher and payout_monitor both explain what was
    removed), and `"btc_claims.jsonl"` is a STRING naming a historical ledger
    that scripts/interim_pii_scrub.py must go on scrubbing. Neither is an
    entry point, and a guard that failed on them would be deleted by the next
    person who wrote an honest comment.
    """
    names: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover — a broken file is a louder failure
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
                if a.asname:
                    names.add(a.asname)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
            for a in node.names:
                names.add(a.asname or a.name)
    return names


def test_no_order_creation_entry_point_survives_in_server_code() -> None:
    offenders = []
    for rel, p in _server_sources():
        names = _code_names(p.read_text(encoding="utf-8", errors="ignore"))
        for sym in ISSUING_SYMBOLS:
            if sym in names:
                offenders.append(f"{rel}: executes `{sym}`")
    assert offenders == [], (
        "direct-BTC order-creation code is back:\n  " + "\n  ".join(offenders))


def test_the_symbol_scan_reaches_the_files() -> None:
    """NEGATIVE CONTROL for the walk. Zero hits on a name that is certainly
    present means the sweep above parsed nothing."""
    seen = hits = 0
    for _rel, p in _server_sources():
        seen += 1
        if "Path" in _code_names(p.read_text(encoding="utf-8", errors="ignore")):
            hits += 1
    assert seen > 30, f"the server/scripts walk collected almost nothing ({seen})"
    assert hits > 10, "control name not found — the scan is not parsing files"


def test_the_symbol_list_would_fire_on_a_reintroduction() -> None:
    """NEGATIVE CONTROL for the PATTERN. Each symbol must be found in the exact
    CODE that was removed, or a reintroduction passes silently."""
    removed_for_real = (
        "import btc_payments",
        "order = btc_payments.create_order(email=email, usd_amount=u, sats_amount=s)",
        "addr = address_for_order(order_id)",
        "def _handle_buy_btc(self): pass",
        "ok, result = btc_claims.submit(email)",
        "def _handle_btc_claim(self): pass",
    )
    for snippet in removed_for_real:
        names = _code_names(snippet)
        assert any(sym in names for sym in ISSUING_SYMBOLS), (
            f"the symbol list would NOT catch a reintroduction of: {snippet}")


def test_the_symbol_scan_ignores_prose_and_ledger_names() -> None:
    """The other half of the control. Explaining what was removed, and naming
    the historical ledger that must stay scrubbed, are both correct."""
    quiet = (
        '# matching lives in btc_payments.py against the order ledger\n',
        '"""Sourced its list from `btc_payments` — the address pool."""\n',
        'SCRUB_FILES = ["btc_claims.jsonl", "anchors.jsonl"]\n',
    )
    for snippet in quiet:
        names = _code_names(snippet)
        assert not any(sym in names for sym in ISSUING_SYMBOLS), (
            f"the guard fires on prose or a ledger filename: {snippet!r}")


def _served_text_files() -> list[tuple[str, Path]]:
    """Everything the image copies that a visitor can read, plus the server
    modules that render HTML. The Dockerfile copies server/, web/, scripts/
    and content/."""
    out: list[tuple[str, Path]] = []
    for root, exts in ((WEB, ("*.html", "*.js", "*.css", "*.txt", "*.json", "*.xml")),
                       (SERVER, ("*.py",)),
                       (SCRIPTS, ("*.py",))):
        for ext in exts:
            for p in sorted(root.rglob(ext)):
                rel = p.relative_to(ROOT).as_posix()
                if "/_mockups/" in rel or "/vendor/" in rel or "/node_modules/" in rel:
                    continue
                out.append((rel, p))
    return out


def test_no_receive_address_is_offered_anywhere_on_the_served_surface() -> None:
    """NO ADDRESS EVER ISSUED — the static half. The pay page shipped a
    hard-coded bech32 receive address in its markup and again in its script;
    the order page rendered one per order. None may remain."""
    offenders = []
    for rel, p in _served_text_files():
        for n, line in enumerate(
                p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if BECH32_MAINNET.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()[:100]}")
    assert offenders == [], (
        "a Bitcoin receive address is on the served surface:\n  "
        + "\n  ".join(offenders))


def test_the_address_scan_reaches_the_files() -> None:
    """NEGATIVE CONTROL for the walk."""
    seen = hits = 0
    for _rel, p in _served_text_files():
        seen += 1
        if "orphograph" in p.read_text(encoding="utf-8", errors="ignore").lower():
            hits += 1
    assert seen > 100, f"the served-surface walk collected almost nothing ({seen})"
    assert hits > 20, "control token not found — the scan is not reading files"


def test_the_address_pattern_discriminates() -> None:
    """NEGATIVE CONTROL for the PATTERN, on literal input. The first two are
    the exact shapes that shipped; the rest must not fire, or the guard gets
    switched off by the next person it trips."""
    fires = (
        '      <span id="btc-addr">bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4</span>',
        '  const ADDR = "bc1pw508d6qejxtdg4y5r3zarvary0c5xw7kw508d6qejxtdg4y5r3zarvary0c5xw7k";',
    )
    for line in fires:
        assert BECH32_MAINNET.search(line), f"pattern misses a real address: {line}"
    quiet = (
        "bitcoin-anchored proof of existence",
        "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx",   # testnet, not ours
        "abc1qqqq",                                      # no word boundary
        "bc1",                                           # too short
        "the Bitcoin blockchain via OpenTimestamps",
    )
    for line in quiet:
        assert not BECH32_MAINNET.search(line), f"false positive on: {line}"


# ── the copy ────────────────────────────────────────────────────────────────

# Phrases that OFFER the retired rail, or link to one of its URLs.
#
# Calibrated against two things that must NOT match:
#   * Bitcoin ANCHORING — the product. Every page says "Bitcoin" constantly.
#   * The hosted crypto processor, which genuinely accepts BTC and says so
#     ("Pay in BTC, ETH, USDC, SOL and more"). "pay in btc" is therefore NOT
#     an offer phrase; "pay WITH btc" and "pay with bitcoin" were the retired
#     page's own wording and are.
OFFER_PHRASES = (
    "pay with bitcoin",
    "pay with btc",
    "/pay/btc",
    "/pay-btc.js",
    "/api/buy-btc",
    "/api/btc/claim",
    "/api/btc/price",
    "/api/btc-order",
    "open in your bitcoin wallet",
    "open in bitcoin wallet",
)

# The retirement guard in server/app.py necessarily NAMES every retired path —
# that list is what makes them answer 410. Scanning it would be circular.
RETIREMENT_DECLARATIONS = (
    "_RETIRED_BTC_EXACT", "_RETIRED_BTC_PREFIXES", "_RETIRED_BTC_MESSAGE",
)


def _visitor_readable_lines():
    """(rel, lineno, text) for everything a visitor can actually read.

    web/** is served verbatim. Server modules contribute only their STRING
    CONSTANTS — the error page, the blog shell and the vertical pages are all
    built from string literals in Python — and never their comments, which a
    visitor never sees and which must be free to explain the retirement.
    """
    for p in sorted(WEB.rglob("*")):
        if not p.is_file() or p.suffix not in (".html", ".js", ".css", ".txt",
                                               ".json", ".xml"):
            continue
        rel = p.relative_to(ROOT).as_posix()
        if "/_mockups/" in rel or "/vendor/" in rel or "/node_modules/" in rel:
            continue
        for n, line in enumerate(
                p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            yield rel, n, line

    for p in sorted(SERVER.glob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:  # pragma: no cover
            continue
        skip = set()
        for node in ast.walk(tree):
            # The retirement guard necessarily names every retired path; that
            # list is what makes them answer 410, so scanning it is circular.
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id in RETIREMENT_DECLARATIONS
                    for t in node.targets):
                skip.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
            # DOCSTRINGS are documentation, not output. A visitor never reads
            # one, and the functions that implement the retirement have to be
            # able to describe what they retired. Only a bare string
            # EXPRESSION is a docstring; a string passed to a call, assigned,
            # or interpolated into a template is still scanned.
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                body = getattr(node, "body", None)
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    d = body[0].value
                    skip.update(range(d.lineno, (d.end_lineno or d.lineno) + 1))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.lineno in skip:
                    continue
                yield rel, node.lineno, node.value


def test_no_served_page_offers_the_retired_rail() -> None:
    offenders = []
    for rel, n, text in _visitor_readable_lines():
        low = text.lower()
        for phrase in OFFER_PHRASES:
            if phrase in low:
                offenders.append(f"{rel}:{n} [{phrase}]: {text.strip()[:90]}")
    assert offenders == [], (
        "a served surface still offers the retired direct-BTC rail:\n  "
        + "\n  ".join(offenders))


def test_the_offer_scan_reaches_the_surface() -> None:
    """NEGATIVE CONTROL for the walk, over both halves of it."""
    rows = list(_visitor_readable_lines())
    assert len(rows) > 5000, f"the visitor-surface walk collected almost nothing ({len(rows)})"
    assert any(r.startswith("web/") for r, _n, _t in rows), "web/ not read"
    assert any(r.startswith("server/") for r, _n, _t in rows), "server strings not read"
    assert any("orphograph" in t.lower() for _r, _n, t in rows), "control token absent"


def test_the_docstring_exclusion_does_not_blind_the_offer_scan() -> None:
    """NEGATIVE CONTROL for the docstring skip. Only a bare string EXPRESSION
    at the top of a module/def/class is a docstring. A string ASSIGNED to a
    name or PASSED to a call is real output and must still be scanned, or the
    exclusion becomes a hole to hide retired copy in."""
    q = '"' * 3
    probe = "\n".join([
        q + "Module docstring naming /api/buy-btc - skip me." + q,
        'LINK = "Pay with Bitcoin"',
        "def f():",
        "    " + q + "Function docstring naming /pay/btc - skip me." + q,
        '    render("<a href=/pay/btc>go</a>")',
    ])
    tree = ast.parse(probe)
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                d = body[0].value
                skip.update(range(d.lineno, (d.end_lineno or d.lineno) + 1))
    seen = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.lineno not in skip]
    assert any("Pay with Bitcoin" in v for v in seen), (
        "an assigned string was skipped — the docstring exclusion is too wide")
    assert any("/pay/btc" in v for v in seen), (
        "a string passed to a call was skipped — the exclusion is too wide")
    assert not any("skip me" in v for v in seen), (
        "a docstring was scanned — the exclusion is not working")


def test_the_offer_scan_does_not_fire_on_the_product_or_the_kept_rails() -> None:
    """NEGATIVE CONTROL for the PATTERN. Bitcoin ANCHORING is the product and
    says 'Bitcoin' constantly; the hosted processor really does take BTC. A
    guard that matched either would be switched off by whoever it tripped."""
    must_not_fire = (
        "Anchor a file's SHA-256 fingerprint to the Bitcoin blockchain",
        "Bitcoin-anchored proof of existence",
        "the Bitcoin block commitment time",
        "You do not need a Bitcoin wallet. You do not need to pay a miner.",
        "Pay with crypto",                                  # hosted processor
        "Pay in BTC, ETH, USDC, SOL and more",              # hosted processor
        "Lightning L402 pay-per-anchor is coming",          # dormant L402 rail
    )
    for line in must_not_fire:
        low = line.lower()
        assert not any(p in low for p in OFFER_PHRASES), (
            f"the offer guard fires on product or kept-rail copy: {line}")

    must_fire = (
        '<a class="btn u-bg-btc" href="/pay/btc">Pay with Bitcoin</a>',
        "  <h1>Pay with Bitcoin</h1>",
        '      const r = await fetch("/api/buy-btc", {',
        '  <a id="wallet-link" href="#">Open in your Bitcoin wallet</a>',
        '    const r = await fetch(`/api/btc-order/${orderId}`);',
    )
    for line in must_fire:
        low = line.lower()
        assert any(p in low for p in OFFER_PHRASES), (
            f"the offer guard would MISS a reintroduction of: {line}")
