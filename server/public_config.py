#!/usr/bin/env python3
"""public_config.py — public-safe config served to frontend.

Returns Stripe Payment Link URLs and feature flags that are safe to expose
publicly. Founder sets these via environment variables; the frontend fetches
them on page load.

Public API:
    snapshot() -> dict
"""
from __future__ import annotations

import os
import sys


def _int_env(name: str, default: int) -> int:
    """Parse an integer env var, falling back to `default` on bad input.

    Crashing /api/config on every request because PACK_PRICE_USD got "$7"
    pasted in is a worse failure than serving the default; we log loudly
    instead so the founder sees the misconfig.
    """
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        sys.stderr.write(
            f"[public_config] WARNING {name}={raw!r} is not an integer; "
            f"falling back to default {default}\n"
        )
        return default


def snapshot() -> dict:
    """Return public-safe config for the frontend.

    Includes:
      - Stripe Payment Link URLs (Pack, Personal monthly/annual)
      - Feature toggle states (maintenance, checkout disabled, etc.)
      - Pricing display values

    Excludes:
      - Stripe secret keys
      - Webhook secrets
      - Founder tokens
      - Any PII or server-side state
    """
    return {
        "stripe": {
            "pack_url": os.environ.get("STRIPE_PACK_URL", "").strip(),
            "personal_monthly_url": os.environ.get("STRIPE_PERSONAL_MONTHLY_URL", "").strip(),
            "personal_annual_url": os.environ.get("STRIPE_PERSONAL_ANNUAL_URL", "").strip(),
            "creator_monthly_url": os.environ.get("STRIPE_CREATOR_MONTHLY_URL", "").strip(),
        },
        "pricing": {
            "pack_usd": _int_env("PACK_PRICE_USD", 7),
            "pack_credits": _int_env("PACK_CREDIT_COUNT", 10),
            "personal_monthly_usd": _int_env("PERSONAL_MONTHLY_USD", 5),
            "personal_annual_usd": _int_env("PERSONAL_ANNUAL_USD", 60),
            "creator_monthly_usd": _int_env("CREATOR_MONTHLY_USD", 19),
        },
        "toggles": {
            "checkout_disabled": os.environ.get("ORPHO_DISABLE_CHECKOUT", "0") == "1",
            "anchoring_disabled": os.environ.get("ORPHO_DISABLE_ANCHORING", "0") == "1",
            "maintenance_mode": os.environ.get("ORPHO_MAINTENANCE_MODE", "0") == "1",
        },
        "features": {
            "btc_payments": bool(os.environ.get("BTC_PAYMENTS_ENABLED", "")),
            "creator_tier_live": bool(os.environ.get("CREATOR_TIER_LIVE", "")),
            "private_receipts": True,  # always available to subscribers
            "receipt_vault": True,     # always available to subscribers
        },
    }
