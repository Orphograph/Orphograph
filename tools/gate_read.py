#!/usr/bin/env python3
"""gate_read.py — read the 2026-08-06 demand gate honestly.

Gate (from the demand test): PASS if ANY of
    >=200 unique LP visits  OR  >=10 CTA clicks  OR  >=3 inbound emails

Every leg of that gate has been found to be a broken instrument at least
once. The design rule here, and the reason this tool exists:

    A leg that CANNOT BE MEASURED reports UNKNOWN, never 0.

Reporting 0 for an unmeasurable leg is what made the original gate
untrustworthy -- it could not distinguish "nobody came" from "the meter
was unplugged". Both readings are 0; only one is evidence.

Known instrument defects this tool compensates for:

  G1  Before the CF-Connecting-IP fix, `ip_trunc` recorded a CLOUDFLARE
      EGRESS NODE, not the visitor. Cloudflare rotates egress per request,
      so one visitor spans several /24s and many visitors share one. Those
      rows are unusable in EITHER direction -- they are excluded, not
      adjusted. Detected structurally (absence of `ip_src`), not by a
      hardcoded timestamp, so the cutover is discovered from the data.

  G3  `/` (root) emits events but no `page_view`, so any page_view-based
      count is blind to root traffic. Both countings are reported.

  G8  `ip_src` may be "cf" | "xff" | "socket". Behind Cloudflare only "cf"
      is the true visitor; the others are relay addresses. Only "cf" rows
      count toward the unique leg.

  G5  The founder's own events are demand-negative. Pass --exclude-prefix
      to drop them; the count of dropped rows is always reported.

Truncation caveat that survives every fix: ip_trunc is a /24 (v4) or /48
(v6) PREFIX. This tool says "distinct prefixes" and never "visitors",
because the two are not the same number and never will be.

Usage:
    gate_read.py EVENTS.jsonl [--exclude-prefix P]... [--inbound N] [--json]

Fetch the live log first (needs a Fly exec permission):
    flyctl machine exec <machine> -a orphograph 'cat /app/data/events.jsonl'
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

UNIQUE_LEG_TARGET = 200
CTA_LEG_TARGET = 10
INBOUND_LEG_TARGET = 3

# The CTA the gate was written against.
CTA_EVENT = "lp_cta_clicked"
# Purchase/engagement clicks that are CTA-ish but were NOT what the gate
# specified. Reported separately so nobody quietly widens the definition to
# clear the bar.
ADJACENT_CTA_EVENTS = ("buy_pack_click", "buy_personal_click",
                       "checkout_clicked", "share_link_click",
                       "try_sample_click", "verify_sample_click")

# Published Cloudflare egress ranges seen in the contaminated rows. Used
# only to CORROBORATE the structural detection in a warning -- never as the
# primary test, since the range list drifts.
CF_EGRESS_HINTS = ("104.22.", "104.23.", "162.158.", "162.159.", "108.162.",
                   "172.64.", "172.65.", "172.66.", "172.67.", "172.68.",
                   "172.69.", "172.70.", "172.71.")

UNKNOWN = "UNKNOWN"


def load(path):
    rows, bad = [], 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if isinstance(r, dict):
                    rows.append(r)
                else:
                    bad += 1
            except json.JSONDecodeError:
                bad += 1
    return rows, bad


def partition(rows, fix_deployed_after=None):
    """Split rows at the instrument-fix cutover.

    A row is trustworthy for identity only if it self-declares ip_src=="cf".
    Rows with no ip_src predate the fix (G1). Rows with ip_src in
    {socket,xff} are post-fix but relay-addressed (G8).

    G2 REGRESSION DETECTION: the CF fix currently exists only on branch
    `fix/real-client-ip` and is NOT merged to master, while production runs
    it. A deploy from master would silently revert analytics to logging
    Cloudflare egress nodes, and the ONLY symptom is `ip_src` disappearing
    from new rows. Pass --fix-deployed-after with the deploy timestamp and
    any later row missing `ip_src` is reported as a REGRESSION rather than
    quietly binned as pre-fix data. Without it, this tool documents G2;
    with it, this tool detects G2.
    """
    usable, pre_fix, relay, regressed = [], [], [], []
    for r in rows:
        src = r.get("ip_src")
        if src is None:
            if fix_deployed_after and str(r.get("ts", "")) > fix_deployed_after:
                regressed.append(r)
            else:
                pre_fix.append(r)
        elif src == "cf":
            usable.append(r)
        else:
            relay.append(r)
    return usable, pre_fix, relay, regressed


def leg_unique(usable, pre_fix, relay, excluded):
    """Unique-prefix leg. UNKNOWN if there is no trustworthy data at all."""
    if not usable:
        return {
            "status": UNKNOWN,
            "reason": ("no rows carry ip_src=='cf' -- either the fix is not "
                       "deployed or no traffic has arrived since it was. "
                       f"{len(pre_fix)} pre-fix rows exist and are UNUSABLE "
                       "(they record Cloudflare egress nodes, not visitors)."),
            "all_events_prefixes": None,
            "page_view_prefixes": None,
        }
    kept = [r for r in usable if r.get("ip_trunc") not in excluded]
    dropped = len(usable) - len(kept)
    all_pref = {r.get("ip_trunc") for r in kept if r.get("ip_trunc")}
    pv_pref = {r.get("ip_trunc") for r in kept
               if r.get("event") == "page_view" and r.get("ip_trunc")}
    # Report the CONSERVATIVE (smaller) count against the target; state both.
    headline = min(len(all_pref), len(pv_pref)) if pv_pref else len(all_pref)
    return {
        "status": "MET" if headline >= UNIQUE_LEG_TARGET else "NOT_MET",
        "headline_prefixes": headline,
        "all_events_prefixes": len(all_pref),
        "page_view_prefixes": len(pv_pref),
        "target": UNIQUE_LEG_TARGET,
        "founder_rows_dropped": dropped,
        "usable_rows": len(kept),
        "unusable_pre_fix_rows": len(pre_fix),
        "relay_rows_excluded": len(relay),
        "caveat": ("PREFIXES (/24 v4, /48 v6), not visitors. Counting method "
                   "changes the number: page_view-only misses root-page "
                   "traffic entirely (G3), all-events overcounts multi-event "
                   "sessions. Headline uses the smaller of the two."),
    }


def leg_cta(rows, excluded):
    """CTA leg. Countable from any row -- unaffected by the IP defect."""
    kept = [r for r in rows if r.get("ip_trunc") not in excluded]
    dropped = len(rows) - len(kept)
    n = sum(1 for r in kept if r.get("event") == CTA_EVENT)
    adjacent = Counter(r.get("event") for r in kept
                       if r.get("event") in ADJACENT_CTA_EVENTS)
    return {
        "status": "MET" if n >= CTA_LEG_TARGET else "NOT_MET",
        "count": n,
        "target": CTA_LEG_TARGET,
        "event": CTA_EVENT,
        "founder_rows_dropped": dropped,
        "adjacent_not_counted": dict(adjacent),
        "caveat": ("Only the gate's own CTA event is counted. Adjacent "
                   "clicks are listed but NOT summed -- widening the "
                   "definition to clear the bar would be moving the goal."),
    }


def leg_inbound(supplied):
    """Inbound leg. Not derivable from this log -- ever."""
    if supplied is None:
        return {
            "status": UNKNOWN,
            "reason": ("inbound email cannot be read from events.jsonl. It "
                       "must be counted in Proton (orphograph@pm.me). NOTE: "
                       "hello@ inbound delivery has never been confirmed "
                       "end-to-end, so an observed 0 may mean the forwarding "
                       "rule is missing, not that nobody wrote. Verify the "
                       "route before reading 0 as demand."),
            "count": None,
            "target": INBOUND_LEG_TARGET,
        }
    return {
        "status": "MET" if supplied >= INBOUND_LEG_TARGET else "NOT_MET",
        "count": supplied,
        "target": INBOUND_LEG_TARGET,
        "caveat": "Supplied manually via --inbound; not machine-verified.",
    }


def verdict(legs):
    """Gate verdict. UNKNOWN legs can never make it FAIL -- only PASS."""
    if any(l["status"] == "MET" for l in legs.values()):
        return "PASS"
    if any(l["status"] == UNKNOWN for l in legs.values()):
        return "INCONCLUSIVE"
    return "NOT_MET"


def contamination_warning(pre_fix):
    hits = sum(1 for r in pre_fix
               if any(str(r.get("ip_trunc", "")).startswith(p)
                      for p in CF_EGRESS_HINTS))
    if not pre_fix:
        return None
    return (f"{len(pre_fix)} pre-fix rows excluded; {hits} of them carry a "
            "known Cloudflare egress prefix, corroborating that these "
            "recorded CDN topology rather than visitors.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("events")
    ap.add_argument("--exclude-prefix", action="append", default=[],
                    metavar="P", help="ip_trunc to treat as the founder's own")
    ap.add_argument("--inbound", type=int, default=None,
                    help="inbound count from Proton; omit to report UNKNOWN")
    ap.add_argument("--fix-deployed-after", metavar="TS", default=None,
                    help=("ISO timestamp the CF-Connecting-IP fix went live "
                          "(e.g. 2026-07-25T21:53:16). Rows after it that "
                          "lack ip_src are flagged as a REGRESSION -- i.e. "
                          "someone deployed from master. See G2."))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rows, bad = load(a.events)
    excluded = set(a.exclude_prefix)
    usable, pre_fix, relay, regressed = partition(rows, a.fix_deployed_after)

    legs = {
        "unique_prefixes": leg_unique(usable, pre_fix, relay, excluded),
        "cta_clicks": leg_cta(rows, excluded),
        "inbound_email": leg_inbound(a.inbound),
    }
    out = {
        "gate": "2026-08-06 demand gate",
        "verdict": verdict(legs),
        "total_rows": len(rows),
        "malformed_rows": bad,
        "legs": legs,
        "warning": contamination_warning(pre_fix),
        "regression": (
            None if not regressed else
            f"REGRESSION: {len(regressed)} rows logged after "
            f"{a.fix_deployed_after} carry NO ip_src. The CF-Connecting-IP "
            "fix is no longer running -- almost certainly a deploy from "
            "master, which does not contain it (branch fix/real-client-ip). "
            "Analytics is logging Cloudflare egress nodes again. Every one "
            "of these rows is unusable."),
    }

    if a.json:
        print(json.dumps(out, indent=2))
        return 0

    print(f"GATE: {out['gate']}")
    print(f"VERDICT: {out['verdict']}   ({len(rows)} rows, {bad} malformed)")
    if out["regression"]:
        print(f"\n!!!! {out['regression']}")
    if out["warning"]:
        print(f"\n!! {out['warning']}")
    for name, leg in legs.items():
        print(f"\n[{name}] {leg['status']}")
        for k, v in leg.items():
            if k == "status" or v is None or v == {} or (v == 0 and k.endswith("dropped")):
                continue
            print(f"    {k}: {v}")
    if out["verdict"] == "INCONCLUSIVE":
        print("\nINCONCLUSIVE means at least one leg has no working instrument.")
        print("Do not read it as absence of demand. Fix the instrument, re-read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
