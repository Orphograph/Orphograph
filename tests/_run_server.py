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


def _stub_stripe(data_dir: Path) -> None:
    """Replace stripe_api._request, the one function that talks to Stripe.

    Each call is appended to <data dir>/stub_stripe_calls.jsonl, so a test
    can assert exactly what would have been sent, and answered as a success.
    While <data dir>/stub_stripe_down exists, calls go on to the REAL
    _request, which with no STRIPE_SECRET_KEY answers "not configured"
    without opening a socket: a genuine failure the product itself produces,
    not a hand-written copy of one. STRIPE_BASE is pointed at loopback too,
    so a key left in the caller's shell still cannot reach api.stripe.com."""
    import stripe_api

    stripe_api.STRIPE_BASE = "http://127.0.0.1:1/v1"  # nothing listens there
    real_request = stripe_api._request
    calls = data_dir / STRIPE_CALLS
    down = data_dir / STRIPE_DOWN
    lock = threading.Lock()

    def recorded(method: str, path: str, form: dict | None = None) -> dict:
        with lock, calls.open("a") as f:
            f.write(json.dumps({"method": method, "path": path, "form": form or {}}) + "\n")
        if down.exists():
            return real_request(method, path, form)
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
