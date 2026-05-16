#!/usr/bin/env python3
"""milestone_watcher.py — fire one-time Telegram alerts for revenue milestones.

Runs every 5 minutes via launchd. Idempotent — tracks which milestones
have already fired in data/.milestones_fired so each milestone alerts at
most once.

Milestones watched:
    - first_btc_settled  : first row in btc_orders.jsonl with event=settled
    - first_pack         : first row in credit_ledger.jsonl with source starting "stripe:"
    - first_subscription : first row in subscriptions.jsonl with status=active
    - five_packs         : 5 cumulative pack purchases
    - first_100_usd      : $100 cumulative revenue
    - first_1000_usd     : $1,000 cumulative revenue

The "first paying customer" alert is the one that matters most. The
rest are encouragement.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data")))
FIRED_PATH = DATA_DIR / ".milestones_fired"
NOTIFIER = Path.home() / ".claude" / "notifier.py"

BTC_ORDERS = DATA_DIR / "btc_orders.jsonl"
CREDIT_LEDGER = DATA_DIR / "credit_ledger.jsonl"
SUB_LEDGER = DATA_DIR / "subscriptions.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _already_fired(name: str) -> bool:
    if not FIRED_PATH.exists():
        return False
    with FIRED_PATH.open() as f:
        return name in (line.strip() for line in f)


def _mark_fired(name: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with FIRED_PATH.open("a") as f:
        f.write(name + "\n")


def _notify(text: str) -> bool:
    if not NOTIFIER.exists():
        sys.stderr.write(f"[milestone] notifier not found at {NOTIFIER}\n")
        return False
    try:
        subprocess.run(["python3", str(NOTIFIER), text], timeout=15, check=False)
        return True
    except (subprocess.TimeoutExpired, OSError) as e:
        sys.stderr.write(f"[milestone] notifier error: {e}\n")
        return False


def _fire(name: str, text: str) -> None:
    if _already_fired(name):
        return
    if _notify(text):
        _mark_fired(name)
        sys.stdout.write(f"[milestone] fired: {name}\n")


def check_first_btc_settled() -> None:
    rows = _read_jsonl(BTC_ORDERS)
    settled = [r for r in rows if r.get("event") == "settled"]
    if settled:
        first = settled[0]
        sats = first.get("sats_received", first.get("amount_sats", 0))
        _fire("first_btc_settled",
              f"🎉 FIRST BTC PAYMENT settled — {sats} sats for order {first.get('order_id','?')}. "
              f"This is real revenue. Sweep the receive wallet when convenient.")


def check_first_pack_stripe() -> None:
    rows = _read_jsonl(CREDIT_LEDGER)
    stripe_packs = [r for r in rows if str(r.get("source", "")).startswith("stripe:")]
    if stripe_packs:
        first = stripe_packs[0]
        _fire("first_pack_stripe",
              f"🎉 FIRST STRIPE PACK SALE — email {first.get('email','?')[:1]}***@... "
              f"({first.get('credits_delta',0)} credits minted). Real revenue via Stripe.")


def check_first_subscription() -> None:
    rows = _read_jsonl(SUB_LEDGER)
    active = [r for r in rows if r.get("status") in ("active", "trialing")]
    if active:
        first = active[0]
        _fire("first_subscription",
              f"🎉 FIRST PERSONAL SUBSCRIPTION — {first.get('email','?')[:1]}***@... is now active. "
              f"This is recurring revenue.")


def check_cumulative_revenue() -> None:
    rows = _read_jsonl(CREDIT_LEDGER)
    pack_count = sum(1 for r in rows if int(r.get("credits_delta", 0)) >= 10 and
                     str(r.get("source", "")).startswith(("stripe:", "btc:")))
    # rough revenue: $7 per Pack
    revenue_usd = pack_count * 7
    if pack_count >= 5:
        _fire("five_packs",
              f"📈 5+ pack sales recorded. Cumulative ~${revenue_usd}. "
              f"Time to write the second SEO post.")
    if revenue_usd >= 100:
        _fire("first_100_usd",
              f"💯 $100 cumulative revenue. {pack_count} packs sold. "
              f"Kill-criteria month-3 floor cleared.")
    if revenue_usd >= 1000:
        _fire("first_1000_usd",
              f"🚀 $1,000 cumulative revenue. {pack_count} packs sold. "
              f"Kill-criteria month-12 threshold cleared early. Take a victory lap.")


def main() -> int:
    if not NOTIFIER.exists():
        sys.stderr.write(f"[milestone] notifier missing at {NOTIFIER}; nothing to do\n")
        return 0
    check_first_btc_settled()
    check_first_pack_stripe()
    check_first_subscription()
    check_cumulative_revenue()
    return 0


if __name__ == "__main__":
    sys.exit(main())
