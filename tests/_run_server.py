#!/usr/bin/env python3
"""Test-only server launcher for process-boundary dependency replacement.

This file is not shipped and exposes no production configuration. It exists
because a subprocess cannot receive pytest's in-memory monkeypatches.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
# Explicit, not the implicit script-dir entry: PYTHONSAFEPATH / -P remove that.
sys.path.insert(0, str(ROOT / "tests"))


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
    args = parser.parse_args()
    if not args.stub_calendars:
        parser.error("this launcher requires --stub-calendars")

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
