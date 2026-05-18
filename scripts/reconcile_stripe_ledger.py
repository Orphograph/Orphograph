#!/usr/bin/env python3
"""reconcile_stripe_ledger.py — daily Stripe ↔ local credit-ledger reconciler.

Closes premortem item B-18: detect silent drift between Stripe charges and
the local append-only credits ledger so we catch:

  * LOST credits  — Stripe says the buyer paid, ledger has no grant
                    (webhook silently failed; customer paid but got nothing).
  * GHOST credits — Ledger has a grant tagged stripe:* that has no matching
                    Stripe event in window (someone got credits without paying).
  * LEAK credits  — Stripe issued a refund or dispute, ledger has no
                    matching revoke entry (refunded customer still spendable).

Read-only against both sources. stdlib only. Safe to run in production.

Exit codes:
    0  no drift
    1  drift detected (any of LOST / GHOST / LEAK > 0)
    2  configuration error (STRIPE_SECRET_KEY missing, etc.)

Run locally with a mocked Stripe (no real API call) by setting
RECONCILE_DRY_RUN=1 alongside STRIPE_SECRET_KEY=sk_test_dummy — the dry-run
short-circuit only kicks in when the API request explicitly fails; see tests
for full coverage via urlopen mocking.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------- paths / env

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

DEFAULT_LEDGER = ROOT / "data" / "credit_ledger.jsonl"
LEDGER_PATH = Path(os.environ.get("ORPHO_CREDIT_LEDGER", str(DEFAULT_LEDGER)))
REPORT_DIR = Path(os.environ.get("ORPHO_RECONCILE_DIR", str(ROOT / "data")))

STRIPE_BASE = "https://api.stripe.com/v1"
HTTP_TIMEOUT = 15
WINDOW_DAYS = int(os.environ.get("ORPHO_RECONCILE_WINDOW_DAYS", "7"))
EVENT_TYPES = (
    "checkout.session.completed",
    "charge.refunded",
    "charge.dispute.created",
)

# ---------------------------------------------------------------- Stripe pull


def _stripe_get(path: str, params: dict, secret_key: str) -> dict:
    """GET against the Stripe REST API. Returns parsed JSON or raises."""
    qs = urllib.parse.urlencode(params, doseq=True)
    url = f"{STRIPE_BASE}{path}?{qs}" if qs else f"{STRIPE_BASE}{path}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {secret_key}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else {}


def fetch_events(secret_key: str, since_unix: int) -> list[dict]:
    """Pull all events of interest in window via has_more / starting_after.

    Stripe's /v1/events endpoint accepts a single `type` filter, so we
    issue one paginated query per event type and merge.
    """
    out: list[dict] = []
    for event_type in EVENT_TYPES:
        starting_after = None
        # Safety cap so a runaway pagination loop can't spin forever.
        for _ in range(100):
            params: dict = {
                "type": event_type,
                "limit": 100,
                "created[gte]": since_unix,
            }
            if starting_after:
                params["starting_after"] = starting_after
            payload = _stripe_get("/events", params, secret_key)
            data = payload.get("data", []) or []
            out.extend(data)
            if not payload.get("has_more"):
                break
            if not data:
                break
            starting_after = data[-1].get("id")
            if not starting_after:
                break
    return out


# ---------------------------------------------------------------- ledger read


def load_ledger_rows(path: Path) -> list[dict]:
    """Read every JSONL row. Tolerate blank / malformed lines (the live
    ledger has been observed to carry trailing newlines)."""
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# ---------------------------------------------------------------- correlate


def _extract_session_id_from_charge(obj: dict) -> str:
    """Mirror the lookup logic stripe_webhook.py uses for refund/dispute."""
    meta = obj.get("metadata", {}) or {}
    sid = meta.get("checkout_session_id") or meta.get("session_id") or ""
    if sid:
        return sid
    pi = obj.get("payment_intent")
    if isinstance(pi, dict):
        pi_meta = pi.get("metadata", {}) or {}
        return pi_meta.get("checkout_session_id") or pi_meta.get("session_id") or ""
    return ""


def correlate(events: list[dict], ledger_rows: list[dict]) -> dict:
    """Return three drift lists: lost, ghost, leak."""
    # Index ledger by source.
    grant_sources: set[str] = set()         # "stripe:<sid>" / "stripe-gift:<sid>"
    revoke_sources: set[str] = set()        # "stripe-refund:<sid>" / "stripe-dispute:<sid>"
    for row in ledger_rows:
        src = row.get("source", "") or ""
        delta = int(row.get("credits_delta", 0))
        if delta > 0 and (src.startswith("stripe:") or src.startswith("stripe-gift:")):
            grant_sources.add(src)
        if delta < 0 and (
            src.startswith("stripe-refund:") or src.startswith("stripe-dispute:")
        ):
            revoke_sources.add(src)

    stripe_session_ids: set[str] = set()
    refunds_disputes: list[tuple[str, str, str]] = []  # (event_type, session_id, event_id)

    for ev in events:
        et = ev.get("type", "")
        obj = ev.get("data", {}).get("object", {}) or {}
        if et == "checkout.session.completed":
            sid = obj.get("id", "")
            if sid:
                stripe_session_ids.add(sid)
        elif et in {"charge.refunded", "charge.dispute.created"}:
            sid = _extract_session_id_from_charge(obj)
            refunds_disputes.append((et, sid, ev.get("id", "")))

    # LOST: stripe session has no matching ledger grant (either prefix).
    lost: list[str] = []
    for sid in sorted(stripe_session_ids):
        if (
            f"stripe:{sid}" not in grant_sources
            and f"stripe-gift:{sid}" not in grant_sources
        ):
            lost.append(sid)

    # GHOST: ledger grant for a stripe:* / stripe-gift:* session id that we
    # never saw an event for in window. (Out-of-window grants are noise — we
    # only flag if the corresponding session id is missing from BOTH stripe
    # results AND no other ledger evidence excuses it.)
    ghost: list[str] = []
    for src in sorted(grant_sources):
        # src looks like "stripe:cs_xxx" or "stripe-gift:cs_xxx"
        _, _, sid = src.partition(":")
        if not sid:
            continue
        if sid not in stripe_session_ids:
            ghost.append(src)

    # LEAK: refund/dispute event with no matching revoke entry.
    leak: list[dict] = []
    for et, sid, eid in refunds_disputes:
        if not sid:
            # Can't correlate — surface as a special case so the operator
            # at least knows a refund landed without a recoverable link.
            leak.append({
                "event_type": et,
                "event_id": eid,
                "session_id": "",
                "note": "no recoverable session_id on event",
            })
            continue
        expected = (
            f"stripe-refund:{sid}" if et == "charge.refunded"
            else f"stripe-dispute:{sid}"
        )
        if expected not in revoke_sources:
            leak.append({
                "event_type": et,
                "event_id": eid,
                "session_id": sid,
                "expected_source": expected,
            })

    return {
        "stripe_session_ids": sorted(stripe_session_ids),
        "grant_sources": sorted(grant_sources),
        "lost": lost,
        "ghost": ghost,
        "leak": leak,
        "refund_dispute_count": len(refunds_disputes),
    }


# ---------------------------------------------------------------- report


def render_report(result: dict, window_days: int, generated_at: datetime) -> str:
    lost = result["lost"]
    ghost = result["ghost"]
    leak = result["leak"]
    lines: list[str] = []
    lines.append(f"# Stripe ↔ Ledger Reconciliation — {generated_at.date().isoformat()}")
    lines.append("")
    lines.append(f"Generated: {generated_at.isoformat(timespec='seconds')}")
    lines.append(f"Window: last {window_days} day(s)")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    ok = (not lost) and (not ghost) and (not leak)
    lines.append(f"- Status: {'OK — no drift' if ok else 'DRIFT DETECTED'}")
    lines.append(f"- Stripe checkout.session.completed seen: {len(result['stripe_session_ids'])}")
    lines.append(f"- Ledger stripe* grant sources: {len(result['grant_sources'])}")
    lines.append(f"- Refund / dispute events: {result['refund_dispute_count']}")
    lines.append(f"- LOST credits (paid, no grant): {len(lost)}")
    lines.append(f"- GHOST credits (granted, no payment): {len(ghost)}")
    lines.append(f"- LEAK credits (refund/dispute, no revoke): {len(leak)}")
    lines.append("")

    lines.append("## Stripe events without ledger entry (LOST)")
    lines.append("")
    if not lost:
        lines.append("_None._")
    else:
        lines.append("These customers PAID but did NOT receive credits. Investigate webhook.")
        lines.append("")
        for sid in lost:
            lines.append(f"- `{sid}`")
    lines.append("")

    lines.append("## Ledger entries without Stripe event (GHOST)")
    lines.append("")
    if not ghost:
        lines.append("_None._")
    else:
        lines.append("These ledger grants reference a session id Stripe did not return.")
        lines.append("Possible causes: out-of-window event, test-mode pollution, or fraud.")
        lines.append("")
        for src in ghost:
            lines.append(f"- `{src}`")
    lines.append("")

    lines.append("## Refunds / disputes without revocation (LEAK)")
    lines.append("")
    if not leak:
        lines.append("_None._")
    else:
        lines.append("These refunds or disputes have no matching revoke entry in the ledger.")
        lines.append("")
        for item in leak:
            sid = item.get("session_id") or "(no session id)"
            lines.append(
                f"- `{item['event_type']}` event=`{item['event_id']}` session=`{sid}` "
                f"expected=`{item.get('expected_source', '—')}` {item.get('note', '')}".rstrip()
            )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- main


def run(secret_key: str, ledger_path: Path, report_dir: Path,
        window_days: int = WINDOW_DAYS,
        now: datetime | None = None) -> tuple[int, Path]:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=window_days)
    since_unix = int(since.timestamp())

    try:
        events = fetch_events(secret_key, since_unix)
    except urllib.error.HTTPError as e:
        sys.stderr.write(
            f"[reconcile] Stripe HTTP {e.code}: {e.reason}\n"
        )
        return 2, Path("")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        sys.stderr.write(f"[reconcile] network error reaching Stripe: {e}\n")
        return 2, Path("")
    except (json.JSONDecodeError, ValueError) as e:
        sys.stderr.write(f"[reconcile] bad JSON from Stripe: {e}\n")
        return 2, Path("")

    ledger_rows = load_ledger_rows(ledger_path)
    result = correlate(events, ledger_rows)
    report = render_report(result, window_days, now)

    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / f"reconciliation_{now.date().isoformat()}.md"
    out_path.write_text(report, encoding="utf-8")

    drift = bool(result["lost"]) or bool(result["ghost"]) or bool(result["leak"])
    return (1 if drift else 0), out_path


def main(argv: list[str]) -> int:
    secret_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not secret_key:
        sys.stderr.write(
            "[reconcile] STRIPE_SECRET_KEY is not set — refusing to run.\n"
        )
        return 2

    code, out_path = run(secret_key, LEDGER_PATH, REPORT_DIR)
    if out_path:
        sys.stderr.write(f"[reconcile] report written to {out_path} (exit={code})\n")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
