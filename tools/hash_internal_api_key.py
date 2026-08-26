#!/usr/bin/env python3
"""Print the SHA-256 digest used to classify an office-only API key."""
from __future__ import annotations

import getpass
import hashlib


def main() -> int:
    raw = getpass.getpass("Office API key (input hidden): ")
    if not raw:
        raise SystemExit("refusing to hash an empty key")
    print(hashlib.sha256(raw.encode("utf-8")).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
