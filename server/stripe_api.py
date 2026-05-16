#!/usr/bin/env python3
"""stripe_api.py — outbound Stripe API helper for subscription actions.

Used for cancel + reactivate flows where the user wants to change
their Stripe subscription state from inside our app rather than
the Stripe Customer Portal.

Stdlib only. POSTs to the Stripe REST API with our secret key.
Inert if STRIPE_SECRET_KEY is unset — every call returns a clear
"not configured" error that the caller surfaces to the user.

Public API:
    cancel_at_period_end(subscription_id) -> dict
    reactivate(subscription_id) -> dict
    is_configured() -> bool
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
HTTP_TIMEOUT = 10
STRIPE_BASE = "https://api.stripe.com/v1"


def is_configured() -> bool:
    return bool(STRIPE_SECRET_KEY)


def _request(method: str, path: str, form: dict | None = None) -> dict:
    if not STRIPE_SECRET_KEY:
        return {"ok": False, "error": "Stripe API not configured (STRIPE_SECRET_KEY unset)"}
    data = urllib.parse.urlencode(form or {}).encode("utf-8") if form else None
    req = urllib.request.Request(
        STRIPE_BASE + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {STRIPE_SECRET_KEY}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            return {"ok": True, "data": json.loads(body) if body else {}}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        sys.stderr.write(f"[stripe_api] HTTP {e.code}: {body[:300]}\n")
        try:
            err = json.loads(body)
            msg = err.get("error", {}).get("message", str(e))
        except (json.JSONDecodeError, ValueError):
            msg = f"HTTP {e.code}"
        return {"ok": False, "error": msg, "status": e.code}
    except (urllib.error.URLError, OSError) as e:
        sys.stderr.write(f"[stripe_api] {type(e).__name__}: {e}\n")
        return {"ok": False, "error": f"{type(e).__name__}"}


def cancel_at_period_end(subscription_id: str) -> dict:
    """Mark the subscription to cancel at the end of the current period.

    The user keeps access until the period rolls over; no immediate
    revocation. Matches the gentle-cancel UX customers expect.
    """
    if not subscription_id:
        return {"ok": False, "error": "missing subscription id"}
    return _request("POST", f"/subscriptions/{subscription_id}",
                    form={"cancel_at_period_end": "true"})


def reactivate(subscription_id: str) -> dict:
    """Clear the cancel_at_period_end flag so the subscription continues."""
    if not subscription_id:
        return {"ok": False, "error": "missing subscription id"}
    return _request("POST", f"/subscriptions/{subscription_id}",
                    form={"cancel_at_period_end": "false"})
