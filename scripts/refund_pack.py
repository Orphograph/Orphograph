#!/usr/bin/env python3
"""refund_pack.py — refund a Pack purchase via Stripe and zero the claim code.

Issues the Stripe refund (CLI fallback or REST) AND appends a refund-marked
negative-delta row to the (append-only) credits ledger so the bearer can no
longer spend a Pack whose money has gone back.

Usage:
    python3 refund_pack.py ch_xxx                    # refund a Pack purchase
    python3 refund_pack.py ch_xxx --reason "unused"  # with reason metadata
    python3 refund_pack.py --list-recent             # last 10 Pack charges
    python3 refund_pack.py ch_xxx --dry-run          # print plan, no writes
    python3 refund_pack.py ch_xxx --force            # override <$15 warning

Decision tree (pre-refund):
    1. amount < $15 → warn (Stripe fee may net-negative); --force required.
    2. charge is a subscription invoice → refuse; use cancel-subscription.
    3. charge already disputed → warn that refund may not stop the clock.

Stdlib only. STRIPE_API_KEY from env or .env.local. Uses `stripe` CLI if on
PATH; otherwise urllib POST to /v1/refunds.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_LOCAL = ROOT / ".env.local"
sys.path.insert(0, str(ROOT / "server"))

# ── ANSI palette (matches setup_email.py) ───────────────────────────
RESET = "\033[0m"
AMBER = "\033[38;2;192;138;62m"; SAGE = "\033[38;2;74;154;115m"
ERR = "\033[38;2;178;80;80m";    MUTED = "\033[38;2;131;126;117m"

def amber(s): return f"{AMBER}{s}{RESET}"
def sage(s):  return f"{SAGE}{s}{RESET}"
def red(s):   return f"{ERR}{s}{RESET}"
def dim(s):   return f"{MUTED}{s}{RESET}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_env() -> dict[str, str]:
    if not ENV_LOCAL.exists():
        return {}
    out: dict[str, str] = {}
    for line in ENV_LOCAL.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def stripe_key() -> str | None:
    return os.environ.get("STRIPE_API_KEY") or load_env().get("STRIPE_API_KEY")


def stripe_get(path: str, key: str, params: dict | None = None) -> dict:
    url = f"https://api.stripe.com{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def stripe_post(path: str, key: str, body: dict) -> dict:
    data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(
        f"https://api.stripe.com{path}", data=data,
        headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": json.loads(e.read().decode()).get("error", {})}


def refund_via_cli(charge_id: str, reason: str) -> tuple[bool, str]:
    """Use `stripe charges refund` CLI if available."""
    cmd = ["stripe", "charges", "refund", charge_id,
           "-d", f"metadata[orphograph_reason]={reason}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        return False, str(e)


def refund_via_rest(charge_id: str, reason: str, key: str) -> tuple[bool, str]:
    body = {"charge": charge_id, "metadata[orphograph_reason]": reason}
    resp = stripe_post("/v1/refunds", key, body)
    if "error" in resp:
        return False, json.dumps(resp["error"])
    return True, resp.get("id", "")


def check_dispute(charge_id: str, key: str) -> dict | None:
    resp = stripe_get("/v1/disputes", key, {"charge": charge_id, "limit": 1})
    data = resp.get("data") or []
    return data[0] if data else None


def evaluate(charge: dict, key: str, force: bool) -> tuple[bool, list[str]]:
    """Decision tree. Returns (ok_to_refund, messages)."""
    msgs: list[str] = []
    amount = charge.get("amount", 0)  # cents
    if charge.get("invoice"):
        msgs.append(red("REFUSED: charge is part of a subscription invoice "
                        f"({charge['invoice']}). Use cancel-subscription instead."))
        return False, msgs
    if charge.get("refunded"):
        msgs.append(amber("Charge already fully refunded; nothing to do."))
        return False, msgs
    if amount < 1500:
        line = amber(
            f"WARNING: charge is ${amount/100:.2f} — Stripe's fixed fee may make "
            "the refund net-negative for you.")
        msgs.append(line)
        if not force:
            msgs.append(red("Aborting. Re-run with --force to override."))
            return False, msgs
    dispute = check_dispute(charge["id"], key)
    if dispute:
        msgs.append(amber(
            f"WARNING: charge has an active dispute ({dispute['id']}, "
            f"status={dispute.get('status')}). Refunding may NOT stop the "
            "dispute clock — coordinate with Stripe Dashboard."))
    return True, msgs


def zero_claim_code(claim_code: str, reason: str, dry_run: bool) -> dict:
    import credits
    from file_lock import locked
    balance = credits.balance(claim_code)
    if balance <= 0:
        return {"claim_code": claim_code, "before": balance, "action": "noop"}
    if dry_run:
        return {"claim_code": claim_code, "before": balance, "action": "would_zero"}
    with locked(credits.LEDGER_PATH, mode="a", exclusive=True) as f:
        f.write(json.dumps({
            "ts": _now_iso(), "claim_code": claim_code, "email": "",
            "credits_delta": -balance, "source": f"refund:{reason}",
        }, separators=(",", ":")) + "\n")
    return {"claim_code": claim_code, "before": balance, "action": "zeroed"}


def zero_by_email(email: str, reason: str, dry_run: bool) -> int:
    """Zero every claim code associated with `email`. Returns process exit code.

    Scans credits.LEDGER_PATH for all claim_code rows tied to `email`. If none
    found, returns exit code 2 (matches test expectation — "no purchases"
    should be a non-zero exit so a caller can branch on it).
    """
    import credits
    codes_seen: set[str] = set()
    if credits.LEDGER_PATH.exists():
        with credits.LEDGER_PATH.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("email") == email and row.get("claim_code"):
                    codes_seen.add(row["claim_code"])
    if not codes_seen:
        print(dim(f"No claim codes found for {email}"))
        return 2
    results = [zero_claim_code(code, reason, dry_run) for code in sorted(codes_seen)]
    print(json.dumps({"email": email, "codes": results}, indent=2))
    return 0


def list_recent(key: str) -> int:
    resp = stripe_get("/v1/charges", key, {"limit": 25})
    rows = [c for c in resp.get("data", []) if not c.get("invoice")][:10]
    if not rows:
        print(dim("No recent non-subscription charges found."))
        return 0
    print(amber(f"{'CHARGE':<30}{'AMOUNT':>10}  {'STATUS':<10}{'REFUNDABLE'}"))
    for c in rows:
        refundable = "yes" if (not c.get("refunded")
                               and c.get("status") == "succeeded") else "no"
        amt = f"${c.get('amount', 0)/100:.2f}"
        color = sage if refundable == "yes" else dim
        print(color(f"{c['id']:<30}{amt:>10}  {c.get('status', ''):<10}{refundable}"))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Refund a Pack purchase via Stripe and zero its claim code.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("charge_id", nargs="?", help="Stripe charge id (ch_...).")
    p.add_argument("--reason", default="customer_request",
                   help="Human-readable reason (stored in Stripe metadata + ledger).")
    p.add_argument("--list-recent", action="store_true",
                   help="List the last 10 Pack charges with refundable status.")
    p.add_argument("--force", action="store_true",
                   help="Override the <$15 net-negative refund warning.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print plan; do not call Stripe or touch the ledger.")
    # Ledger-only refund paths — don't touch Stripe at all. Used for
    # manual operator interventions: customer paid via BTC, support
    # decides to comp, etc. The original CLI before the Stripe upgrade.
    p.add_argument("--claim-code",
                   help="Zero a specific claim code (no Stripe call).")
    p.add_argument("--email",
                   help="Zero all claim codes belonging to this email (no Stripe call).")
    args = p.parse_args()

    key = stripe_key()
    if args.list_recent:
        if not key:
            print(red("STRIPE_API_KEY required for --list-recent.")); return 2
        return list_recent(key)

    # Ledger-only modes — bypass Stripe entirely.
    if args.claim_code:
        result = zero_claim_code(args.claim_code, args.reason, args.dry_run)
        print(json.dumps(result, indent=2))
        return 0
    if args.email:
        return zero_by_email(args.email, args.reason, args.dry_run)

    if not args.charge_id:
        p.error("charge_id, --claim-code, --email, or --list-recent is required.")

    if args.dry_run and not key:
        print(amber(f"[dry-run] would fetch /v1/charges/{args.charge_id}"))
        print(amber(f"[dry-run] would refund via stripe CLI or POST /v1/refunds"))
        print(amber(f"[dry-run] would zero claim code linked to {args.charge_id}"))
        return 0

    charge = stripe_get(f"/v1/charges/{args.charge_id}", key)
    if "error" in charge or not charge.get("id"):
        print(red(f"Failed to fetch charge: {charge}")); return 2

    ok, msgs = evaluate(charge, key, args.force)
    for m in msgs:
        print(m)
    if not ok:
        return 2

    claim_code = (charge.get("metadata") or {}).get("claim_code", "")
    print(dim(f"Charge {charge['id']} amount=${charge['amount']/100:.2f} "
              f"claim_code={claim_code or '<none>'}"))

    if args.dry_run:
        print(amber(f"[dry-run] would refund {charge['id']} via "
                    f"{'CLI' if shutil.which('stripe') else 'REST'}"))
        if claim_code:
            print(json.dumps(zero_claim_code(claim_code, args.reason, True), indent=2))
        return 0

    if shutil.which("stripe"):
        success, info = refund_via_cli(args.charge_id, args.reason)
        path_used = "stripe CLI"
    else:
        success, info = refund_via_rest(args.charge_id, args.reason, key)
        path_used = "REST /v1/refunds"
    if not success:
        print(red(f"Refund failed via {path_used}: {info}")); return 2
    print(sage(f"Refund accepted via {path_used}: {info[:80]}"))

    if claim_code:
        try:
            result = zero_claim_code(claim_code, args.reason, False)
            print(sage(f"Claim code zeroed: {json.dumps(result)}"))
        except Exception as e:
            print(red(f"WARN: refund succeeded but ledger zeroing failed: {e}"))
            return 1
    else:
        print(dim("No claim_code in charge metadata — ledger untouched."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
