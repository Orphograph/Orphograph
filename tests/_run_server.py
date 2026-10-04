#!/usr/bin/env python3
"""Test-only server launcher for process-boundary dependency replacement.

This file is not shipped and exposes no production configuration. It exists
because a subprocess cannot receive pytest's in-memory monkeypatches.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
# Explicit, not the implicit script-dir entry: PYTHONSAFEPATH / -P remove that.
sys.path.insert(0, str(ROOT / "tests"))


STRIPE_CALLS = "stub_stripe_calls.jsonl"
STRIPE_DOWN = "stub_stripe_down"
STRIPE_ANSWERS = "stub_stripe_answers.json"
# While <data dir>/stub_calendars_down exists, every stubbed calendar
# refuses. --fail-calendars is fixed at launch; this is a total outage a
# test can start and END on the same server, which the x402 rail needs: a
# payment settled before the anchor must be shown held through an outage
# and redeemed after it, on one process with one ledger.
CALENDARS_DOWN = "stub_calendars_down"


def _answers(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _fake_stripe(answers: Path) -> str:
    """Serve scripted Stripe errors on loopback and return the base URL.

    <data dir>/stub_stripe_answers.json maps an API path to the answer Stripe
    would give it, {"status": 404, "error": {"code": ..., "message": ...}},
    and this server sends that status with Stripe's error body. A test needs
    this to tell one refusal from another: a subscription Stripe has no
    record of is not the same failure as an outage, and the product must be
    able to tell them apart from what actually comes back on the wire.

    An answer with "data" in place of "error" is sent as the body, for a
    route that reads the object Stripe returns (a paid checkout session)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Answer(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            path = self.path[len("/v1"):] if self.path.startswith("/v1") else self.path
            script = _answers(answers).get(path) or {}
            status = int(script.get("status") or 200)
            raw = json.dumps({"error": script["error"]} if "error" in script
                             else script.get("data") or {}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = do_DELETE = _answer

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Answer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/v1"


def _stub_stripe(data_dir: Path) -> None:
    """Replace stripe_api._request, the one function that talks to Stripe.

    Each call is appended to <data dir>/stub_stripe_calls.jsonl, so a test
    can assert exactly what would have been sent, and answered as a success.
    While <data dir>/stub_stripe_down exists, calls go on to the REAL
    _request, which with no STRIPE_SECRET_KEY answers "not configured"
    without opening a socket: a genuine failure the product itself produces,
    not a hand-written copy of one. STRIPE_BASE is pointed at loopback too,
    so a key left in the caller's shell still cannot reach api.stripe.com.

    A path listed in <data dir>/stub_stripe_answers.json is answered by the
    REAL _request code against _fake_stripe, so its HTTP error is parsed the
    way a real Stripe error is. That copy of the function runs with its own
    globals, a placeholder key and the loopback base, so no other request in
    this process ever sees a key set or a different base."""
    import types

    import stripe_api

    stripe_api.STRIPE_BASE = "http://127.0.0.1:1/v1"  # nothing listens there
    real_request = stripe_api._request
    calls = data_dir / STRIPE_CALLS
    down = data_dir / STRIPE_DOWN
    answers = data_dir / STRIPE_ANSWERS
    scripted_request = types.FunctionType(
        real_request.__code__,
        {**vars(stripe_api), "STRIPE_SECRET_KEY": "stub-placeholder",
         "STRIPE_BASE": _fake_stripe(answers)},
        real_request.__name__, real_request.__defaults__, real_request.__closure__)
    lock = threading.Lock()

    def recorded(method: str, path: str, form: dict | None = None) -> dict:
        with lock, calls.open("a") as f:
            f.write(json.dumps({"method": method, "path": path, "form": form or {}}) + "\n")
        if down.exists():
            return real_request(method, path, form)
        if path in _answers(answers):
            return scripted_request(method, path, form)
        return {"ok": True, "data": {}}

    stripe_api._request = recorded


MAIL_SENT = "stub_mail_sent.jsonl"
MAIL_DOWN = "stub_mail_down"
_RESEND_EMAILS = "https://api.resend.com/emails"


def _capture_mail(data_dir: Path) -> None:
    """Record every email the mailer would hand to Resend, instead of sending it.

    The inert mailer (no RESEND_API_KEY) logs only a masked address and the
    subject, so a test could see THAT a mail went out but never read the link
    in it. Here mailer._send runs for real (suppression gate, footer, payload)
    and only its last step changes: the request it would POST to Resend is
    appended to <data dir>/stub_mail_sent.jsonl. While <data dir>/stub_mail_down
    exists the attempt is still recorded and then fails like an outage, so the
    mailer's own retry and give-up path runs.

    Only the mailer module's reference to urllib is replaced, and the stand-in
    refuses every URL but Resend's send endpoint, so nothing in this process
    can reach the network through it."""
    import types
    import urllib.error
    import urllib.parse
    import urllib.request

    import mailer

    sent = data_dir / MAIL_SENT
    down = data_dir / MAIL_DOWN
    lock = threading.Lock()

    class _Accepted:
        def read(self) -> bytes:
            return b'{"id":"stub"}'

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

    def urlopen(req, timeout=None):
        if getattr(req, "full_url", None) != _RESEND_EMAILS:
            raise urllib.error.URLError("capture-mail stub refuses this URL")
        outage = down.exists()
        with lock, sent.open("a") as f:
            f.write(json.dumps({"payload": json.loads(req.data),
                                "delivered": not outage}) + "\n")
        if outage:
            raise urllib.error.URLError("capture-mail stub: outage")
        return _Accepted()

    mailer.RESEND_API_KEY = "stub-placeholder"
    mailer.urllib = types.SimpleNamespace(
        parse=urllib.parse, error=urllib.error,
        request=types.SimpleNamespace(Request=urllib.request.Request, urlopen=urlopen))


EGRESS_BLOCKED = "stub_egress_blocked.jsonl"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _guard_egress(data_dir: Path) -> None:
    """Refuse every connection and name lookup for a host that is not this
    machine, from any code in this process, and record each one (host and
    port only) in <data dir>/stub_egress_blocked.jsonl.

    The other stubs replace the one function each third party is reached
    through. This is the floor under them: a call that goes around them is
    refused here, and the test can read that it was tried. The refusal is an
    OSError, as a real unreachable host would raise, so the product's own
    failure handling runs instead of a crash in a stub."""
    import socket
    import urllib.request

    # A proxy on this machine would pass the loopback test and forward the
    # request on, so urllib is given none, from the environment or the OS.
    for name in [k for k in os.environ if k.lower().endswith("_proxy")]:
        del os.environ[name]
    urllib.request.getproxies = lambda: {}

    blocked = data_dir / EGRESS_BLOCKED
    lock = threading.Lock()

    def refuse(host, port, kind):
        with lock, blocked.open("a") as f:
            f.write(json.dumps({"host": str(host), "port": port, "via": kind}) + "\n")

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def _host(address):
        return address[0] if isinstance(address, tuple) else address

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and _host(address) not in _LOOPBACK:
            refuse(_host(address), address[1] if isinstance(address, tuple) else None, "connect")
            raise ConnectionRefusedError("egress guard: only loopback may be reached")
        return real_connect(self, address)

    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and _host(address) not in _LOOPBACK:
            refuse(_host(address), address[1] if isinstance(address, tuple) else None, "connect_ex")
            return 111
        return real_connect_ex(self, address)

    def getaddrinfo(host, port, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name is not None and name not in _LOOPBACK:
            refuse(name, port, "getaddrinfo")
            raise socket.gaierror(socket.EAI_NONAME, "egress guard: only loopback may be reached")
        return real_getaddrinfo(host, port, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stub-calendars", action="store_true")
    # Which calendars the stub refuses, as a comma-separated list of the short
    # tokens engine._calendar_short produces ("a", "b", "alice", "finney",
    # "btc"). Default: none, so every existing caller of _srv is unaffected.
    # Needed because the durability threshold now counts DISTINCT upstream
    # calendars, and "three servers acknowledged" versus "three calendars
    # reached" can only be told apart by shaping WHICH ones succeed.
    parser.add_argument("--fail-calendars", default="")
    parser.add_argument("--stub-stripe", action="store_true")
    # Lifts lightning.LIGHTNING_RETIRED in this process only, so the armed
    # rail's tests keep proving the code a re-arm would switch back on. A flag
    # on this launcher and not an environment variable on purpose: production
    # starts server/app.py through scripts/init_volume.sh, and this file is
    # neither copied into the image (Dockerfile COPY lines) nor in its build
    # context (.dockerignore excludes tests/), so no secret, typo or stray
    # setting can reach it.
    parser.add_argument("--arm-lightning", action="store_true")
    parser.add_argument("--capture-mail", action="store_true")
    parser.add_argument("--egress-guard", action="store_true")
    args = parser.parse_args()
    if not args.stub_calendars:
        parser.error("this launcher requires --stub-calendars")
    if args.egress_guard:
        # First, so nothing imported below can open a connection before it.
        _guard_egress(Path(os.environ["ORPHO_DATA_DIR"]))
    if args.stub_stripe:
        _stub_stripe(Path(os.environ["ORPHO_DATA_DIR"]))
    if args.arm_lightning:
        import lightning
        lightning.LIGHTNING_RETIRED = False
    if args.capture_mail:
        _capture_mail(Path(os.environ["ORPHO_DATA_DIR"]))

    import engine
    # The one definition of the well-formed pending body the tests compare
    # against; a hand copy here drifted from it once.
    from _ots_bodies import PENDING_BODY

    refused = {t.strip() for t in args.fail_calendars.split(",") if t.strip()}
    unknown = refused - {engine._calendar_short(c) for c in engine.CALENDARS}
    if unknown:
        parser.error(f"--fail-calendars names no shipped calendar: {sorted(unknown)}")

    calendars_down = Path(os.environ["ORPHO_DATA_DIR"]) / CALENDARS_DOWN

    def accepted(calendar_url: str, hash_bytes: bytes):
        if len(hash_bytes) != 32:
            return False, "hash must be exactly 32 bytes (SHA-256)"
        if engine._calendar_short(calendar_url) in refused or calendars_down.exists():
            return False, "HTTP 503: stubbed calendar outage"
        return True, PENDING_BODY

    engine._submit = accepted
    import app
    return app.main()


if __name__ == "__main__":
    raise SystemExit(main())
