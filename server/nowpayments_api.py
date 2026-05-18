#!/usr/bin/env python3
"""nowpayments_api.py — outbound NOWPayments REST client (non-custodial).

NOWPayments is a payment-processor; they hold the customer-facing keys
and settle to us in USDC. No private keys live on this server.

Stdlib only. Bearer-authenticates with NOWPAYMENTS_API_KEY (env). Inert
if the key is unset — every call returns {"ok": False, "reason":
"nowpayments_not_configured"} so the buy page can degrade gracefully.

Public API:
    is_configured() -> bool
    create_invoice(amount_usd, currency, order_id, customer_email) -> dict
    get_invoice_status(invoice_id) -> dict

Reference: https://documenter.getpostman.com/view/7907941/2s9YsGittd
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = "https://api.nowpayments.io/v1"
HTTP_TIMEOUT = 12

# 8 coins approved for the buy page. NOWPayments expects the lowercase
# ticker. Some currencies have explicit network suffixes (e.g. usdcsol
# vs usdcmatic) but for an MVP we accept the bare ticker and let the
# customer pick the network in NOWPayments' hosted invoice UI.
SUPPORTED_CURRENCIES = (
    "btc", "usdc", "sol", "xrp", "eth", "usdt", "ltc", "doge", "matic",
)

# Plan -> credit-pack metadata.
PLANS = {
    "writer_pack": {"price_usd": 19, "credit_count": 10, "label": "Writer Pack"},
    "pack_50":     {"price_usd": 29, "credit_count": 50, "label": "Pack of 50"},
}


def _api_key() -> str:
    # Re-read each call so tests can monkeypatch os.environ between cases.
    return os.environ.get("NOWPAYMENTS_API_KEY", "").strip()


def is_configured() -> bool:
    return bool(_api_key())


def _site_origin() -> str:
    return os.environ.get("ORPHO_SITE_ORIGIN", "https://orphograph.com").rstrip("/")


def _request(method: str, path: str, body: dict | None = None) -> dict:
    """Low-level call. Returns {"ok": bool, ...}; never raises."""
    key = _api_key()
    if not key:
        return {"ok": False, "reason": "nowpayments_not_configured"}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        API_BASE + path,
        data=data,
        method=method,
        headers={
            # NOWPayments uses an `x-api-key` header (per their docs) rather
            # than Bearer. The spec asks for Bearer; we send both so either
            # gateway shape works without needing to mutate this later.
            "x-api-key": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # NOWPayments hosts api.nowpayments.io behind Cloudflare which
            # blocks unspecified or python-urllib User-Agents as bots
            # (Cloudflare error 1010). A browser-shaped UA passes through.
            "User-Agent": "Mozilla/5.0 (compatible; OrphographServer/0.1; +https://orphograph.com)",
            # Explicitly NOT advertising gzip/deflate — urllib does not
            # transparently decompress and we'd hit bad_json parsing the
            # raw compressed bytes. Plain `identity` is fine here.
            "Accept-Encoding": "identity",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return {"ok": False, "reason": "bad_json", "status": resp.status}
            return {"ok": True, "data": parsed, "status": resp.status}
    except urllib.error.HTTPError as e:
        body_raw = ""
        try:
            body_raw = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        # Never log the api key, and clip remote body in case it echoes the
        # request. NOWPayments' error format is {"statusCode":…,"message":…}.
        sys.stderr.write(
            f"[nowpayments] HTTP {e.code} path={path} body={body_raw[:200]}\n"
        )
        return {
            "ok": False,
            "reason": "http_error",
            "status": e.code,
            "body": body_raw[:500],
        }
    except urllib.error.URLError as e:
        sys.stderr.write(f"[nowpayments] URLError path={path} reason={getattr(e, 'reason', e)}\n")
        return {"ok": False, "reason": "network_error"}
    except TimeoutError:
        sys.stderr.write(f"[nowpayments] timeout path={path}\n")
        return {"ok": False, "reason": "timeout"}
    except OSError as e:
        sys.stderr.write(f"[nowpayments] OSError path={path} {type(e).__name__}\n")
        return {"ok": False, "reason": "network_error"}


def create_invoice(
    amount_usd: float,
    currency: str,
    order_id: str,
    customer_email: str | None = None,
) -> dict:
    """Create a hosted NOWPayments invoice the buyer will be redirected to.

    Returns either:
      {"ok": True, "data": {... NOWPayments invoice payload including invoice_url ...}}
      {"ok": False, "reason": "<machine code>", ...}
    """
    if not is_configured():
        return {"ok": False, "reason": "nowpayments_not_configured"}
    cur = (currency or "").strip().lower()
    if cur not in SUPPORTED_CURRENCIES:
        return {"ok": False, "reason": "unsupported_currency"}
    try:
        amt = float(amount_usd)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "bad_amount"}
    if amt <= 0 or amt > 10_000:
        return {"ok": False, "reason": "bad_amount"}
    if not order_id or len(order_id) > 64:
        return {"ok": False, "reason": "bad_order_id"}

    origin = _site_origin()
    payload: dict = {
        "price_amount": round(amt, 2),
        "price_currency": "usd",
        "pay_currency": cur,
        "order_id": order_id,
        "order_description": "Orphograph credit pack",
        "ipn_callback_url": f"{origin}/api/nowpayments/webhook",
        "success_url": f"{origin}/pay/success?order={order_id}",
        "cancel_url": f"{origin}/",
    }
    # NOWPayments accepts customer_email on the invoice for receipt purposes.
    if customer_email and "@" in customer_email and len(customer_email) <= 254:
        payload["customer_email"] = customer_email
    return _request("POST", "/invoice", body=payload)


def get_invoice_status(invoice_id: str) -> dict:
    """Poll the current status of a previously-created invoice/payment.

    NOWPayments exposes `/payment/<id>` for payment-state lookups.
    """
    if not is_configured():
        return {"ok": False, "reason": "nowpayments_not_configured"}
    if not invoice_id or not invoice_id.isalnum():
        # invoice ids from NOWPayments are numeric / alphanumeric;
        # reject obvious garbage early so we don't issue an obviously-bad GET.
        return {"ok": False, "reason": "bad_invoice_id"}
    return _request("GET", f"/payment/{invoice_id}")
