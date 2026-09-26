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
    able to tell them apart from what actually comes back on the wire."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Answer(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            path = self.path[len("/v1"):] if self.path.startswith("/v1") else self.path
            script = _answers(answers).get(path) or {}
            status = int(script.get("status") or 200)
            raw = json.dumps({"error": script["error"]} if "error" in script else {}).encode()
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
    args = parser.parse_args()
    if not args.stub_calendars:
        parser.error("this launcher requires --stub-calendars")
    if args.stub_stripe:
        _stub_stripe(Path(os.environ["ORPHO_DATA_DIR"]))

    import engine
    # The one definition of the well-formed pending body the tests compare
    # against; a hand copy here drifted from it once.
    from _ots_bodies import PENDING_BODY

    refused = {t.strip() for t in args.fail_calendars.split(",") if t.strip()}
    unknown = refused - {engine._calendar_short(c) for c in engine.CALENDARS}
    if unknown:
        parser.error(f"--fail-calendars names no shipped calendar: {sorted(unknown)}")

    def accepted(calendar_url: str, hash_bytes: bytes):
        if len(hash_bytes) != 32:
            return False, "hash must be exactly 32 bytes (SHA-256)"
        if engine._calendar_short(calendar_url) in refused:
            return False, "HTTP 503: stubbed calendar outage"
        return True, PENDING_BODY

    engine._submit = accepted
    import app
    return app.main()


if __name__ == "__main__":
    raise SystemExit(main())
