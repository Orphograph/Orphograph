#!/usr/bin/env python3
"""waitlist.py — append-only waitlist for tiers / features not yet live.

Stores (timestamp, email, interest) so we can email the list the day we
launch Personal, Capture, or any future tier.

Public API:
    add(email, interest) -> bool
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from file_lock import locked

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
WAITLIST_PATH = Path(os.environ.get("ORPHO_WAITLIST", str(DATA_DIR / "waitlist.jsonl")))

ALLOWED_INTERESTS = {"personal", "creator", "capture", "b2b", "other",
                     # Card-checkout notify list: buyers who arrived while
                     # card_charges_enabled was false and asked to be told
                     # when card checkout returns. Value encodes the tier.
                     "card_pack", "card_pack50", "card_personal",
                     # Demand instrument for /lp/agent-receipts (2026-08-19).
                     # MUST be listed here: an interest outside this set is
                     # silently rewritten to "other" below, which would make
                     # the landing page's leads indistinguishable from every
                     # other source -- a capture that cannot attribute is not
                     # a measurement.
                     "agent_receipts"}


def add(email: str, interest: str) -> bool:
    """Append to waitlist. Returns True on success, False on bad input."""
    if not isinstance(email, str) or "@" not in email or len(email) > 320:
        return False
    if interest not in ALLOWED_INTERESTS:
        interest = "other"
    with locked(WAITLIST_PATH, mode="a", exclusive=True) as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "email": email.strip(),
            "interest": interest,
        }, separators=(",", ":")) + "\n")
    return True

