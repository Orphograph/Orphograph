#!/usr/bin/env python3
"""Fixtures for gate_read.py — one per known instrument defect.

Run: python3 tools/test_gate_read.py
Exit 0 = all pass. Stdlib only; writes only to a temp dir.
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "gate_read", Path(__file__).resolve().parent / "gate_read.py")
gr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gr)

TD = Path(tempfile.mkdtemp())
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}{'  -- ' + detail if detail and not cond else ''}")


def write(name, rows):
    p = TD / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def row(event="page_view", page="/lp/agent-receipts", ip="1.2.3", src="cf"):
    r = {"ts": "2026-07-26T10:00:00+00:00", "event": event, "page": page,
         "ip_trunc": ip}
    if src is not None:
        r["ip_src"] = src
    return r


def _run_fixtures():
    """Run every fixture and return the list of failure names.

    The body used to sit at module scope, which made ANY exception in it
    -- a KeyError from a renamed dict key, a TypeError from a changed
    signature -- a pytest COLLECTION error rather than a failing test.
    That is the same "not a failing test, a dead run" class the guarded
    sys.exit closed, one instance deeper: guarding the exit stopped the
    interpreter from quitting, but left 130 lines of fixture code still
    executing at import. Running it on call closes the class.
    """
    global TD
    results.clear()

    # G1: pre-fix rows (no ip_src) are UNUSABLE, not counted as visitors.
    rows = [row(ip=f"104.22.{i}", src=None) for i in range(250)]
    p = write("prefix.jsonl", rows)
    rs, _ = gr.load(p)
    usable, pre_fix, relay, _ = gr.partition(rs)
    leg = gr.leg_unique(usable, pre_fix, relay, set())
    check("G1 250 pre-fix rows do NOT clear the 200 target",
          leg["status"] == gr.UNKNOWN, f"got {leg['status']}")
    check("G1 pre-fix rows reported as unusable", len(pre_fix) == 250)

    # The whole point: unmeasurable != zero.
    legs = {"unique_prefixes": leg,
            "cta_clicks": gr.leg_cta(usable, pre_fix, relay, set()),
            "inbound_email": gr.leg_inbound(None)}
    check("G7 verdict is INCONCLUSIVE, not NOT_MET, when instruments are blind",
          gr.verdict(legs) == "INCONCLUSIVE", f"got {gr.verdict(legs)}")

    # A MET leg still passes the gate even when another leg is UNKNOWN.
    legs2 = {"a": {"status": "MET"}, "b": {"status": gr.UNKNOWN}}
    check("G7 an UNKNOWN leg cannot veto a MET leg", gr.verdict(legs2) == "PASS")
    legs3 = {"a": {"status": "NOT_MET"}, "b": {"status": "NOT_MET"}}
    check("G7 all-measured-and-short is NOT_MET, not INCONCLUSIVE",
          gr.verdict(legs3) == "NOT_MET")

    # G8: post-fix but relay-addressed rows are excluded from the unique leg.
    rows = [row(ip=f"9.9.{i}", src="socket") for i in range(250)]
    p = write("relay.jsonl", rows)
    rs, _ = gr.load(p)
    usable, pre_fix, relay, _ = gr.partition(rs)
    leg = gr.leg_unique(usable, pre_fix, relay, set())
    check("G8 ip_src=socket rows excluded from unique leg",
          leg["status"] == gr.UNKNOWN and len(relay) == 250)

    # G3/G4: root traffic has no page_view; both countings reported, headline
    # takes the smaller so the gate is never cleared by the generous method.
    rows = ([row(event="page_view", ip=f"5.5.{i}") for i in range(210)]
            + [row(event="scroll_25", page="/", ip=f"6.6.{i}") for i in range(60)])
    p = write("root.jsonl", rows)
    rs, _ = gr.load(p)
    usable, pre_fix, relay, _ = gr.partition(rs)
    leg = gr.leg_unique(usable, pre_fix, relay, set())
    check("G3 all-events count exceeds page_view count",
          leg["all_events_prefixes"] == 270 and leg["page_view_prefixes"] == 210)
    check("G4 headline uses the SMALLER counting method",
          leg["headline_prefixes"] == 210, f"got {leg['headline_prefixes']}")

    # G5: founder's own prefix is excluded and the drop is reported.
    rows = [row(event="lp_cta_clicked", ip="7.7.7") for _ in range(12)]
    p = write("founder.jsonl", rows)
    rs, _ = gr.load(p)
    u, pf, rel, _ = gr.partition(rs)
    leg = gr.leg_cta(u, pf, rel, {"7.7.7"})
    check("G5 founder CTA clicks excluded -> leg NOT met",
          leg["status"] == "NOT_MET" and leg["count"] == 0)
    check("G5 dropped rows are reported, not silently discarded",
          leg["founder_rows_dropped"] == 12)

    # Adjacent CTA events are listed but never summed into the leg.
    rows = ([row(event="lp_cta_clicked", ip="8.1.1")]
            + [row(event="buy_pack_click", ip=f"8.2.{i}") for i in range(15)])
    p = write("adjacent.jsonl", rows)
    rs, _ = gr.load(p)
    u, pf, rel, _ = gr.partition(rs)
    leg = gr.leg_cta(u, pf, rel, set())
    check("adjacent CTA clicks are NOT summed into the leg",
          leg["count"] == 1 and leg["adjacent_not_counted"]["buy_pack_click"] == 15)

    # G6: inbound is UNKNOWN unless supplied, and 0 supplied is NOT_MET (a real
    # measurement), which must read differently from UNKNOWN (no measurement).
    check("G6 inbound omitted -> UNKNOWN", gr.leg_inbound(None)["status"] == gr.UNKNOWN)
    check("G6 inbound=0 supplied -> NOT_MET (measured, not blind)",
          gr.leg_inbound(0)["status"] == "NOT_MET")
    check("G6 inbound=3 -> MET", gr.leg_inbound(3)["status"] == "MET")

    # A genuinely passing gate on trustworthy data.
    rows = [row(ip=f"10.{i // 250}.{i % 250}") for i in range(205)]
    p = write("pass.jsonl", rows)
    rs, _ = gr.load(p)
    usable, pre_fix, relay, _ = gr.partition(rs)
    leg = gr.leg_unique(usable, pre_fix, relay, set())
    check("clean data above target -> MET", leg["status"] == "MET")

    # Malformed lines are counted, not silently swallowed.
    p = TD / "bad.jsonl"
    p.write_text('{"event":"page_view","ip_src":"cf","ip_trunc":"1.1.1"}\nnot json\n\n[]\n')
    rs, bad = gr.load(p)
    check("malformed rows counted, not swallowed", len(rs) == 1 and bad == 2)

    # G2: rows AFTER the known deploy time that lack ip_src = master regression.
    DEPLOY = "2026-07-25T21:53:16"
    rows = [row(ip="1.1.1")]  # good, post-fix
    rows += [{"ts": "2026-07-28T09:00:00+00:00", "event": "page_view",
              "page": "/", "ip_trunc": "172.68.4"} for _ in range(5)]
    p2 = write("regress.jsonl", rows)
    rs, _ = gr.load(p2)
    u, pre, rel, regressed = gr.partition(rs, DEPLOY)
    check("G2 post-deploy rows missing ip_src flagged as REGRESSION",
          len(regressed) == 5, f"got {len(regressed)}")
    check("G2 regressed rows are NOT quietly binned as pre-fix", len(pre) == 0)
    check("G2 good post-fix row still usable", len(u) == 1)

    # Without the flag the same data must NOT cry regression (opt-in only).
    u, pre, rel, regressed = gr.partition(rs, None)
    check("G2 no false alarm when --fix-deployed-after is omitted",
          len(regressed) == 0 and len(pre) == 5)

    # Genuinely-old rows are pre-fix, not a regression.
    old = [{"ts": "2026-07-20T09:00:00+00:00", "event": "page_view",
            "page": "/", "ip_trunc": "104.22.9"}]
    u, pre, rel, regressed = gr.partition(old, DEPLOY)
    check("G2 pre-deploy rows are pre-fix, not regression",
          len(pre) == 1 and len(regressed) == 0)


    # ------------------------------------------------------------------
    # G9 (found by /code-review 2026-08-20): the CTA leg used to receive
    # EVERY row and exclude the founder by ip_trunc. On pre-fix rows
    # ip_trunc is a rotating Cloudflare egress prefix, so the exclusion
    # could never match -- twelve founder clicks scored MET, the leg
    # reported founder_rows_dropped: 0 while dropping nothing, and
    # verdict() promoted the single MET leg to a whole-gate PASS off rows
    # this same tool calls unusable. A false PASS is the worst output this
    # instrument can produce.
    rows = [row(event="lp_cta_clicked", ip=f"172.68.{i}", src=None)
            for i in range(12)]
    p = write("g9_prefix_cta.jsonl", rows)
    rs, _ = gr.load(p)
    u, pf, rel, _ = gr.partition(rs)
    leg = gr.leg_cta(u, pf, rel, {"7.7.7"})
    check("G9 pre-fix CTA clicks can NEVER make the leg MET",
          leg["status"] != "MET", f"got {leg['status']}")
    check("G9 unattributable clicks report UNKNOWN, not a silent 0",
          leg["status"] == gr.UNKNOWN and leg["count"] == 0,
          f"got {leg['status']} count={leg.get('count')}")
    check("G9 the uncounted clicks are still reported, not hidden",
          leg["unattributable_clicks_not_counted"] == 12)
    legs = {"unique_prefixes": gr.leg_unique(u, pf, rel, {"7.7.7"}),
            "cta_clicks": leg, "inbound_email": gr.leg_inbound(None)}
    check("G9 whole-gate verdict is INCONCLUSIVE, never PASS",
          gr.verdict(legs) == "INCONCLUSIVE", f"got {gr.verdict(legs)}")

    # A genuinely attributable set still clears the bar -- the fix must not
    # have simply made the leg unable to pass.
    rows = [row(event="lp_cta_clicked", ip=f"91.10.{i}") for i in range(10)]
    p = write("g9_attributable.jsonl", rows)
    rs, _ = gr.load(p)
    u, pf, rel, _ = gr.partition(rs)
    leg = gr.leg_cta(u, pf, rel, {"7.7.7"})
    check("G9 attributable clicks DO still clear the bar",
          leg["status"] == "MET" and leg["count"] == 10,
          f"got {leg['status']} count={leg.get('count')}")

    # G10: the three verdicts must be distinguishable from the exit status.
    # Returning 0 for all of them made "no working instrument" read as
    # "demand proven" to any cron wrapper or gate script.
    check("G10 PASS/NOT_MET/INCONCLUSIVE map to distinct exit codes",
          len({gr._exit_code("PASS"), gr._exit_code("NOT_MET"),
               gr._exit_code("INCONCLUSIVE")}) == 3)
    check("G10 PASS is the only zero exit", gr._exit_code("PASS") == 0
          and gr._exit_code("NOT_MET") != 0
          and gr._exit_code("INCONCLUSIVE") != 0)

    print()
    failed = [n for n, ok in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    return failed


def test_all_gate_read_fixtures_pass():
    """pytest entry point."""
    failed = _run_fixtures()
    assert not failed, "gate_read fixtures failed: " + "; ".join(failed)


if __name__ == "__main__":
    sys.exit(1 if _run_fixtures() else 0)
