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
    create_checkout_session(price_id, mode, success_url, cancel_url, ...) -> dict
    record_x402_payment(tx_hash, amount_atomic, network_id, payer, idempotency_key) -> dict
    is_configured() -> bool
"""
from __future__ import annotations

import http.client
import json
import re
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
HTTP_TIMEOUT = 10
STRIPE_BASE = "https://api.stripe.com/v1"


def is_configured() -> bool:
    return bool(STRIPE_SECRET_KEY)


# charges_enabled() cache — /api/config is hit on every homepage load, so the
# account lookup must not become a per-request Stripe round-trip. Stale-if-error:
# a transient API failure serves the last known answer instead of flapping the
# card buttons.
_ACCOUNT_CACHE: dict = {"ts": 0.0, "enabled": None, "failed": 0.0}
ACCOUNT_CACHE_TTL_SEC = 600
# A failed lookup is not cached as an answer, but its time is: no new lookup
# for this long after one. Without it a failing Stripe (revoked key, 429, 5xx,
# timeout) was asked again on every /api/config load, each call spending the
# read budget checkout shares and each 401 writing an ALERT line. While no
# answer has ever arrived (a fresh process) the wait is short, so one blip
# right after a deploy does not hide the card button for a minute.
ACCOUNT_RETRY_SEC = 60
ACCOUNT_RETRY_COLD_SEC = 5


def charges_enabled() -> bool | None:
    """Whether the Stripe account can currently make live charges.

    The account can be fully configured (valid key, prices, links) and still
    unable to charge — details_submitted with charges_enabled=false means
    Stripe is reviewing or restricting the account (observed live 2026-07-09:
    every card checkout died with "Your account cannot currently make live
    charges"). Card CTAs must not render while that is the state.

    Returns True/False from Stripe (cached ACCOUNT_CACHE_TTL_SEC), or None when
    unknown (key unset, or the lookup has never succeeded). Callers should
    treat anything but True as "do not offer card checkout".
    """
    if not STRIPE_SECRET_KEY:
        return None
    now = time.time()
    if _ACCOUNT_CACHE["enabled"] is not None and now - _ACCOUNT_CACHE["ts"] < ACCOUNT_CACHE_TTL_SEC:
        return _ACCOUNT_CACHE["enabled"]
    wait = ACCOUNT_RETRY_SEC if _ACCOUNT_CACHE["enabled"] is not None else ACCOUNT_RETRY_COLD_SEC
    if now - _ACCOUNT_CACHE.get("failed", 0.0) < wait:
        return _ACCOUNT_CACHE["enabled"]
    res = _request("GET", "/account")
    if res.get("ok"):
        _ACCOUNT_CACHE["ts"] = now
        _ACCOUNT_CACHE["enabled"] = bool((res.get("data") or {}).get("charges_enabled"))
    else:
        _ACCOUNT_CACHE["failed"] = now
    return _ACCOUNT_CACHE["enabled"]


def _categorize_http_error(code: int) -> tuple[str, bool, bool]:
    """Map Stripe HTTP status → (category, retryable, operator_alert).

    Categories let callers map upstream Stripe errors to specific customer
    messages instead of a generic 502.

    - auth_error    → 401/403: STRIPE_SECRET_KEY rotated / revoked. STOP THE LINE.
    - card_declined → 402: actionable by the buyer (retry with different card)
    - invalid_request → 400: our bug (bad price ID, missing param). Log + 502.
    - rate_limited  → 429: bounce; client may retry with Retry-After
    - stripe_outage → 5xx: Stripe is having issues. Retryable. Surface friendly msg.
    - http_error    → anything else 4xx
    """
    if code in (401, 403):
        return ("auth_error", False, True)
    if code == 402:
        return ("card_declined", False, False)
    if code == 429:
        return ("rate_limited", True, False)
    if 500 <= code < 600:
        return ("stripe_outage", True, False)
    if code == 400:
        return ("invalid_request", False, False)
    if 400 <= code < 500:
        return ("http_error", False, False)
    return ("http_error", False, False)


_SESSION_ID_RE = re.compile(r"\b(cs_(?:live|test)_)[A-Za-z0-9]+([A-Za-z0-9]{6})\b")


def mask_session_ids(text: str) -> str:
    """Replace every Checkout Session id in `text` with its mode and last six
    characters (`cs_live_…a1b2c3`). A full id answers /api/stripe/session with
    the buyer's email to whoever holds it, so it must not sit in the log; the
    tail is enough to find the session in the Stripe dashboard."""
    return _SESSION_ID_RE.sub(r"\1…\2", str(text))


def _request(method: str, path: str, form: dict | None = None,
             headers: dict | None = None) -> dict:
    if not STRIPE_SECRET_KEY:
        return {
            "ok": False,
            "category": "not_configured",
            "error": "Stripe API not configured (STRIPE_SECRET_KEY unset)",
        }
    data = urllib.parse.urlencode(form or {}).encode("utf-8") if form else None
    req = urllib.request.Request(
        STRIPE_BASE + path,
        data=data,
        method=method,
        # Extra headers (a preview Stripe-Version, an Idempotency-Key) go in
        # first, so none of them can replace the two this module owns.
        headers={
            **(headers or {}),
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
        try:
            err = json.loads(body)
            stripe_msg = err.get("error", {}).get("message") or ""
            stripe_code = err.get("error", {}).get("code") or ""
            decline_code = err.get("error", {}).get("decline_code") or ""
        except (json.JSONDecodeError, ValueError):
            stripe_msg = ""
            stripe_code = ""
            decline_code = ""
        category, retryable, operator_alert = _categorize_http_error(e.code)
        # Auth errors page the operator — STRIPE_SECRET_KEY may be rotated/revoked.
        if operator_alert:
            sys.stderr.write(
                f"[stripe_api] ALERT: auth failure ({e.code}) — STRIPE_SECRET_KEY may be invalid. "
                f"path={mask_session_ids(path)}\n"
            )
        else:
            # Both the body and Stripe's message are written with repr(), so
            # each stays on its one line. Stripe echoes the caller's input
            # back ("Invalid email address: <what they sent>"), and the
            # decoded message kept a newline in that input as a real one: a
            # caller could start a second log line that read like ours (an
            # "ALERT" line, say). Stripe also pretty-prints its JSON bodies
            # over several lines. repr() escapes every line break, including
            # \r, \x85 and \u2028, and keeps the "…" that masking leaves.
            sys.stderr.write(
                f"[stripe_api] HTTP {e.code} ({category}) path={mask_session_ids(path)} "
                f"body={mask_session_ids(body[:200])!r}\n"
            )
            if stripe_msg:
                # The body above is cut at 200 characters. Stripe's own
                # message no longer reaches the customer (below), so this
                # line is the only place it is kept whole.
                sys.stderr.write(
                    f"[stripe_api] stripe message ({category}, code={stripe_code or '-'}): "
                    f"{mask_session_ids(stripe_msg)!r}\n"
                )
        # Customer-facing message — never leak our internal Stripe error verbatim.
        # Card-declined we DO want the buyer to see: it is about their card and
        # tells them what to do. Every other 4xx is about OUR request. Stripe's
        # text for those named our configured price id and live/test key mode
        # to an anonymous /api/stripe/checkout caller, and echoed the caller's
        # own input back in a JSON error body, so they get a fixed message and
        # the detail stays in the log.
        if category == "auth_error":
            customer_msg = "Payment system misconfigured. We've been notified."
        elif category == "card_declined":
            customer_msg = stripe_msg or "Your card was declined. Try a different card."
        elif category == "rate_limited":
            customer_msg = "Too many requests. Try again in a minute."
        elif category == "stripe_outage":
            customer_msg = "Stripe is having issues — try again in a minute, or pay via Bitcoin."
        elif category == "invalid_request":
            customer_msg = "Request rejected by Stripe (invalid parameters)."
        else:
            customer_msg = f"Payment error ({e.code})."
        return {
            "ok": False,
            "category": category,
            "error": customer_msg,
            "status": e.code,
            "retryable": retryable,
            "operator_alert": operator_alert,
            "stripe_code": stripe_code,
            "decline_code": decline_code,
        }
    except urllib.error.URLError as e:
        # DNS failure, connection-refused, TLS handshake — Stripe unreachable
        sys.stderr.write(
            f"[stripe_api] URLError path={mask_session_ids(path)} reason={getattr(e, 'reason', e)}\n"
        )
        return {
            "ok": False,
            "category": "network_error",
            "error": "Could not reach payment provider — please retry.",
            "retryable": True,
        }
    except TimeoutError:
        sys.stderr.write(f"[stripe_api] timeout path={mask_session_ids(path)}\n")
        return {
            "ok": False,
            "category": "timeout",
            "error": "Payment provider timed out — please retry.",
            "retryable": True,
        }
    except OSError as e:
        sys.stderr.write(f"[stripe_api] OSError path={mask_session_ids(path)} "
                         f"{type(e).__name__}: {mask_session_ids(e)}\n")
        return {
            "ok": False,
            "category": "network_error",
            "error": "Network error reaching payment provider.",
            "retryable": True,
        }
    except (UnicodeError, http.client.InvalidURL) as e:
        # A path the HTTP client cannot put on the wire (a non-ASCII or control
        # character). Nothing was sent. Callers validate ids first; this keeps
        # one that slips through from escaping the handler with no response.
        sys.stderr.write(f"[stripe_api] unsendable path={mask_session_ids(path)} "
                         f"{type(e).__name__}\n")
        return {
            "ok": False,
            "category": "invalid_request",
            "error": "Request rejected (invalid parameters).",
            "status": 400,
            "retryable": False,
        }


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


def create_checkout_session(
    *,
    price_id: str,
    mode: str,
    success_url: str,
    cancel_url: str,
    customer_email: str = "",
    client_reference_id: str = "",
    metadata: dict | None = None,
) -> dict:
    """Create a Stripe Checkout Session.

    mode: "payment" for one-time (Pack), "subscription" for recurring (Standing Order).

    Returns {"ok": True, "data": {"id": "cs_…", "url": "https://checkout.stripe.com/…"}}
    on success, or {"ok": False, "error": "…"} on failure.

    The url field is the hosted Checkout page we redirect the buyer to. The
    session id is included in the webhook payload's data.object.id when the
    customer completes payment, which is how server/stripe_webhook.py
    correlates the inbound event back to this purchase.
    """
    if not price_id:
        return {"ok": False, "error": "missing price id"}
    if mode not in ("payment", "subscription"):
        return {"ok": False, "error": "mode must be 'payment' or 'subscription'"}

    # Stripe API uses repeated-form-field syntax for nested arrays.
    form: dict[str, str] = {
        "mode": mode,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
    }
    # Gated automatic-tax: enabled only when STRIPE_AUTOMATIC_TAX=1.
    # Defaults OFF because Stripe Tax must be registered in every buyer
    # jurisdiction; without that, non-US buyers hit tax_calculation_failed
    # and see our generic 502. Enable once tax registrations are in place
    # for the buyer countries you sell into.
    if os.environ.get("STRIPE_AUTOMATIC_TAX", "0") == "1":
        form["automatic_tax[enabled]"] = "true"
        form["billing_address_collection"] = "auto"
    if customer_email:
        form["customer_email"] = customer_email
    if client_reference_id:
        form["client_reference_id"] = client_reference_id
    # Optional checkout-session metadata (e.g. credit_count for multi-size
    # packs). Round-trips to the webhook on data.object.metadata. Only str
    # values; skip empties so we don't send blank keys.
    if metadata:
        for k, v in metadata.items():
            if v is not None and str(v) != "":
                form[f"metadata[{k}]"] = str(v)
    # For one-time Pack purchases, collect a phone+email and persist as
    # a Customer object so the buyer can come back later. For subs this
    # already happens automatically.
    if mode == "payment":
        form["customer_creation"] = "if_required"

    return _request("POST", "/checkout/sessions", form=form)


# ── x402 settlements recorded as PaymentIntents (2026-11-04) ───────────────
# When x402's payTo is a Stripe crypto deposit address, the USDC lands in
# Stripe, and Stripe books it once the server tells it which on-chain
# transaction it was: a PaymentIntent with the `crypto` payment method in
# `transaction_verification` mode. Off by default (server/x402.py,
# ORPHO_X402_STRIPE_RECORD).
#
# Field names are copied, not guessed, from Stripe's official sample
# stripe-samples/machine-payments (MIT License, Copyright (c) 2026 Stripe, LLC),
# commit f3b74fe587427b156c4a67069f82f64c11dadf1b,
# x402/server/python/main.py lines 133-156 (stripe.PaymentIntent.create:
# amount, currency, confirm, payment_method_data.type,
# allowed_payment_method_types, payment_method_options.crypto.mode,
# ...transaction_verification_options.network / .transaction_hash), and the
# same call in x402/server/node-typescript/main.ts lines 68-88. The API
# version is the one https://docs.stripe.com/payments/machine/x402 names for
# this call ("This feature requires the 2026-05-27.preview API version", read
# 2026-11-04); the sample's HEAD pins a later preview, 2026-10-28.preview.
STRIPE_X402_API_VERSION = "2026-05-27.preview"
# Stripe's `network` value per x402 network id. Base Sepolia is "base" too:
# the docs' sandbox section records Base Sepolia payments against a sandbox
# deposit address created with network=base.
STRIPE_X402_NETWORKS = {"eip155:8453": "base", "eip155:84532": "base"}
_USDC_ATOMIC_PER_CENT = 10_000   # USDC has 6 decimals: $0.01 = 10_000 atomic
_TX_HASH_RE = re.compile(r"[\x21-\x7e]{1,200}")


def _refused(why: str) -> dict:
    """A request this module will not send. Nothing reached Stripe."""
    return {"ok": False, "category": "invalid_input", "error": why, "retryable": False}


def record_x402_payment(*, tx_hash: str, amount_atomic, network_id: str,
                        payer: str | None, idempotency_key: str) -> dict:
    """Record one settled x402 payment in Stripe as a confirmed PaymentIntent.

    `idempotency_key` is the x402 payment identifier, sent as Stripe's
    Idempotency-Key header: a retry of the same payment returns the
    PaymentIntent the first call made instead of creating a second one.
    (Stripe keeps a key for at least 24 hours; the sample keys on the tx
    hash, which is equally unique per payment.)

    Amounts that are not a whole number of cents are refused here, as is an
    unknown network: nothing is sent. Returns _request's dict shape."""
    if not isinstance(tx_hash, str) or not _TX_HASH_RE.fullmatch(tx_hash):
        return _refused("transaction hash missing or malformed")
    try:
        atomic = int(str(amount_atomic), 10)
    except (TypeError, ValueError):
        return _refused("amount_atomic is not an integer")
    if atomic <= 0 or atomic % _USDC_ATOMIC_PER_CENT:
        return _refused("amount is not a positive whole number of cents")
    stripe_network = STRIPE_X402_NETWORKS.get(network_id)
    if stripe_network is None:
        return _refused("network has no Stripe transaction_verification equivalent")
    if (not isinstance(idempotency_key, str) or not idempotency_key
            or len(idempotency_key) > 255):
        return _refused("idempotency key missing or longer than 255 characters")
    form = {
        "amount": str(atomic // _USDC_ATOMIC_PER_CENT),
        "currency": "usd",
        "confirm": "true",
        "payment_method_data[type]": "crypto",
        "allowed_payment_method_types[0]": "crypto",
        "payment_method_options[crypto][mode]": "transaction_verification",
        "payment_method_options[crypto][transaction_verification_options][network]":
            stripe_network,
        "payment_method_options[crypto][transaction_verification_options][transaction_hash]":
            tx_hash,
        # Ours, not the sample's: lets the dashboard row be traced back to
        # the ledger. Public on-chain data only (the chain, the payer).
        "metadata[source]": "x402",
        "metadata[x402_network]": network_id,
    }
    if isinstance(payer, str) and payer:
        form["metadata[x402_payer]"] = payer[:100]
    return _request("POST", "/payment_intents", form=form, headers={
        "Stripe-Version": STRIPE_X402_API_VERSION,
        "Idempotency-Key": idempotency_key,
    })
