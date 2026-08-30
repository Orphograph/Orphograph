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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stub-calendars", action="store_true")
    args = parser.parse_args()
    if not args.stub_calendars:
        parser.error("this launcher requires --stub-calendars")

    import engine

    def accepted(_calendar_url: str, hash_bytes: bytes):
        if len(hash_bytes) != 32:
            return False, "hash must be exactly 32 bytes (SHA-256)"
        # A well-formed pending timestamp (nonce · sha256 · pending attestation);
        # engine.anchor_hash rejects anything that is not one.
        return True, b"\xf0\x10" + b"\x01" * 16 + b"\x08" + b"\x00\x83\xdf\xe3\x0d\x2e\xf9\x0c\x8e" + b"\x02\x01x"

    engine._submit = accepted
    import app
    return app.main()


if __name__ == "__main__":
    raise SystemExit(main())
