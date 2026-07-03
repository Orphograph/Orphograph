#!/usr/bin/env python3
"""ab_report.py — readout for the cream-vs-dark homepage experiment.

Reads the server-side experiment ledger (data/ab_home.jsonl) written by
_serve_ab_home/_ab_log in server/app.py and prints, per arm:

  assignments   first-time visitors randomized into the arm (home_view, new=true)
  views         every homepage serve to the arm (includes returns)
  anchors       successful /api/anchor calls attributed by the arm cookie
  checkouts     /pay/crypto page views attributed by the arm cookie

plus anchor/view and checkout/view rates and a two-proportion z-test on the
primary metric (anchors per view). Stdlib only.

Usage:
  python3 scripts/ab_report.py                      # $ORPHO_DATA_DIR or ./data
  python3 scripts/ab_report.py /app/data/ab_home.jsonl   # e.g. via fly ssh console
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import sys


def load(path: pathlib.Path) -> dict[str, dict[str, int]]:
    arms: dict[str, dict[str, int]] = {}
    if not path.exists():
        return arms
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        arm = rec.get("variant")
        if arm not in ("cream", "dark"):
            continue
        a = arms.setdefault(arm, {"assignments": 0, "views": 0, "anchors": 0, "checkouts": 0})
        event = rec.get("event")
        if event == "home_view":
            a["views"] += 1
            if rec.get("new"):
                a["assignments"] += 1
        elif event == "anchor":
            a["anchors"] += 1
        elif event == "checkout_view":
            a["checkouts"] += 1
    return arms


def two_proportion_z(x1: int, n1: int, x2: int, n2: int) -> tuple[float, float]:
    """z statistic and two-sided p-value for rate(x1/n1) vs rate(x2/n2)."""
    if min(n1, n2) == 0:
        return 0.0, 1.0
    p1, p2 = x1 / n1, x2 / n2
    pooled = (x1 + x2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    p = math.erfc(abs(z) / math.sqrt(2))
    return z, p


def main() -> None:
    if len(sys.argv) > 1:
        path = pathlib.Path(sys.argv[1])
    else:
        data = pathlib.Path(os.environ.get("ORPHO_DATA_DIR", "data"))
        path = data / "ab_home.jsonl"
    arms = load(path)
    if not arms:
        print(f"No experiment records at {path} yet.")
        return

    print(f"Homepage experiment — {path}")
    print(f"{'arm':<8}{'assigned':>10}{'views':>8}{'anchors':>9}{'checkouts':>11}"
          f"{'anchor/view':>13}{'checkout/view':>15}")
    for arm in ("cream", "dark"):
        a = arms.get(arm, {"assignments": 0, "views": 0, "anchors": 0, "checkouts": 0})
        av = a["anchors"] / a["views"] if a["views"] else 0.0
        cv = a["checkouts"] / a["views"] if a["views"] else 0.0
        print(f"{arm:<8}{a['assignments']:>10}{a['views']:>8}{a['anchors']:>9}"
              f"{a['checkouts']:>11}{av:>12.1%}{cv:>14.1%}")

    c = arms.get("cream", {"views": 0, "anchors": 0, "checkouts": 0})
    d = arms.get("dark", {"views": 0, "anchors": 0, "checkouts": 0})
    for label, key in (("anchors/view (primary)", "anchors"), ("checkouts/view", "checkouts")):
        z, p = two_proportion_z(d.get(key, 0), d.get("views", 0), c.get(key, 0), c.get("views", 0))
        sig = "SIGNIFICANT at 95%" if p < 0.05 else "not significant yet"
        print(f"\n{label}: dark vs cream  z={z:+.2f}  p={p:.3f}  → {sig}")
    total_views = c.get("views", 0) + d.get("views", 0)
    if total_views < 800:
        print(f"\nNote: {total_views} total views — at low traffic expect weeks before "
              f"p<0.05 is meaningful. Don't call the test early.")


if __name__ == "__main__":
    main()
