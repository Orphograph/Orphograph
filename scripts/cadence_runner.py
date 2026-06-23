#!/usr/bin/env python3
"""cadence_runner.py — controlled 4-touch cold-outreach sequence.

Reads `data/prospects.csv` (columns: email,first_name,vertical,public_detail,added_iso),
for each prospect dispatches day-0 / day-4 / day-10 / day-21 touches using the
embedded templates, and records state in `data/cadence_state.jsonl` so re-runs
are idempotent.

Enforced safety rails:
  - Opt-in execution: ORPHO_CADENCE_AUTOMATION_ENABLED=1 required for --execute
  - Kill switch: ORPHO_CADENCE_DISABLED=1 blocks all sends
  - 20 sends per day max (configurable lower via ORPHO_CADENCE_DAILY_CAP)
  - Tuesday-Thursday only (US-day-of-week filter — founder schedules cron run-time)
  - Skip any address in `data/suppressions.jsonl` (STOP replies, bounces)
  - Each touch idempotent — re-running this script never double-sends
  - Audit log: every run appends to `data/cadence_audit.jsonl`

Usage:
  python3 scripts/cadence_runner.py                # dry-run, prints planned sends
  python3 scripts/cadence_runner.py --execute      # actually dispatch
  python3 scripts/cadence_runner.py --execute --force-day-of-week  # skip Tue-Thu gate

Designed to be run from launchd / cron daily at e.g. 14:00 UTC (= 10 a.m. EDT).
Run on the Fly machine so RESEND_API_KEY is in env.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "server"))
sys.path.insert(0, str(REPO / "scripts"))

DATA_DIR = REPO / "data"
PROSPECTS_CSV = DATA_DIR / "prospects.csv"
STATE_LOG = DATA_DIR / "cadence_state.jsonl"
SUPPRESSIONS = DATA_DIR / "suppressions.jsonl"
AUDIT_LOG = DATA_DIR / "cadence_audit.jsonl"
DAILY_CAP = 20
SEND_WEEKDAYS = {1, 2, 3}  # Tue=1, Wed=2, Thu=3 in datetime.weekday()
AUTOMATION_ENV = "ORPHO_CADENCE_AUTOMATION_ENABLED"
DISABLE_ENV = "ORPHO_CADENCE_DISABLED"

# Day-0 templates live inside send_outreach.py; we import them here so the
# wording stays single-sourced. Day-4 / day-10 / day-21 are short follow-ups
# defined inline per the runbook in outbox/COLD_OUTREACH_README.md.
import send_outreach  # type: ignore  # noqa: E402

FOLLOWUP_TEMPLATES: dict[str, dict[int, tuple[str, str]]] = {
    "accounting": {
        4: (
            "Quick follow-up on the period-close receipt",
            "Did the close-receipt idea fit your last month-end? "
            "A two-minute scan of the method, with one screenshot, is at "
            "https://orphograph.com/blog/date-stamp-a-document-permanently.html",
        ),
        10: (
            "How the hash-and-anchor flow actually works",
            "If the receipt idea is still in the back of your mind: the method page "
            "walks through how a controller computes the fingerprint on their own machine "
            "and what the verifier returns. https://orphograph.com/learn.html",
        ),
        21: (
            "Closing the loop — no further mail from this address",
            "If timestamped proof-of-existence is not relevant to your practice, "
            "this is the last note from this address. The office at orphograph.com "
            "remains available if the use case ever surfaces.",
        ),
    },
    "construction": {
        4: (
            "Quick follow-up on the storm-damage photo receipt",
            "Did the receipt idea make sense for your storm season? "
            "A short read for the use case is at "
            "https://orphograph.com/blog/prove-a-photo-was-not-edited.html",
        ),
        10: (
            "How the verifier actually works",
            "If the receipt idea is still on your mind: the method page walks through "
            "how a contractor computes the fingerprint and what an adjuster sees on "
            "the verify link. https://orphograph.com/learn.html",
        ),
        21: (
            "Closing the loop — no further mail from this address",
            "If timestamped proof-of-existence is not relevant to your crew, "
            "this is the last note from this address. The office at orphograph.com "
            "remains available if the use case ever surfaces.",
        ),
    },
    "legal_solos": {
        4: (
            "Quick follow-up on the exhibit receipt",
            "Any exhibit this week the receipt would have helped? "
            "A short read on what a cryptographic timestamp proves, and what it does not, "
            "is at https://orphograph.com/blog/digital-notary-vs-cryptographic-timestamp.html",
        ),
        10: (
            "How the hash-and-anchor flow actually works",
            "If the receipt idea is still in the back of your mind: the method page "
            "walks through how a practitioner computes the fingerprint and what the "
            "verifier returns to opposing counsel. https://orphograph.com/learn.html",
        ),
        21: (
            "Closing the loop — no further mail from this address",
            "If timestamped proof-of-existence is not relevant to your practice, "
            "this is the last note from this address. The office at orphograph.com "
            "remains available if the use case ever surfaces.",
        ),
    },
}

TOUCH_OFFSETS_DAYS = [0, 4, 10, 21]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _daily_cap() -> int:
    """Return the active daily cap, never above the hard-coded maximum."""
    raw = os.environ.get("ORPHO_CADENCE_DAILY_CAP", "").strip()
    if not raw:
        return DAILY_CAP
    try:
        val = int(raw)
    except ValueError:
        return DAILY_CAP
    return max(0, min(DAILY_CAP, val))


def _append_audit(event: str, **fields) -> None:
    row = {"ts": _now_iso(), "event": event, **fields}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _automation_enabled() -> bool:
    return os.environ.get(AUTOMATION_ENV, "") == "1"


def _disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "") == "1"


def _read_prospects() -> list[dict[str, str]]:
    if not PROSPECTS_CSV.exists():
        return []
    out: list[dict[str, str]] = []
    with PROSPECTS_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k.strip(): (v or "").strip() for k, v in row.items()}
            if not row.get("email") or not row.get("vertical"):
                continue
            if row["vertical"] not in send_outreach.TEMPLATES:
                continue
            out.append(row)
    return out


def _read_state() -> list[dict]:
    if not STATE_LOG.exists():
        return []
    return [json.loads(line) for line in STATE_LOG.read_text().splitlines() if line.strip()]


def _read_suppressions() -> set[str]:
    if not SUPPRESSIONS.exists():
        return set()
    out: set[str] = set()
    for line in SUPPRESSIONS.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if row.get("email"):
                out.add(row["email"].lower().strip())
        except json.JSONDecodeError:
            continue
    return out


def _append_state(entry: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with STATE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


def _sent_today_count(state: list[dict]) -> int:
    today = datetime.date.today().isoformat()
    return sum(1 for e in state if e.get("sent_at", "").startswith(today))


def _has_been_sent(state: list[dict], email: str, touch_n: int) -> bool:
    email_lc = email.lower().strip()
    for e in state:
        if e.get("email", "").lower().strip() == email_lc and e.get("touch_n") == touch_n:
            if e.get("sent_at"):
                return True
    return False


def _plan_today(
    prospects: list[dict],
    state: list[dict],
    suppressed: set[str],
    today: datetime.date,
) -> list[dict]:
    """Return the list of (email, vertical, touch_n) actions due today."""
    plan: list[dict] = []
    for p in prospects:
        email_lc = p["email"].lower().strip()
        if email_lc in suppressed:
            continue
        try:
            added = datetime.date.fromisoformat(p.get("added_iso", "")[:10])
        except (ValueError, TypeError):
            continue
        for touch_n, offset in enumerate(TOUCH_OFFSETS_DAYS):
            due = added + datetime.timedelta(days=offset)
            if due != today:
                continue
            if _has_been_sent(state, p["email"], touch_n):
                continue
            plan.append({"prospect": p, "touch_n": touch_n})
    return plan


def _send_touch(prospect: dict, touch_n: int) -> tuple[bool, str]:
    """Send the touch_n-th message to prospect. Returns (ok, subject_used)."""
    vertical = prospect["vertical"]
    first_name = prospect.get("first_name") or ""
    public_detail = prospect.get("public_detail") or ""

    if touch_n == 0:
        subject, body = send_outreach.TEMPLATES[vertical]
        body = send_outreach._personalize(body, first_name, public_detail)
    else:
        offset_day = TOUCH_OFFSETS_DAYS[touch_n]
        subject, body = FOLLOWUP_TEMPLATES[vertical][offset_day]
        if first_name:
            body = f"{first_name},\n\n{body}"

    import mailer  # type: ignore

    html = "\n".join(f"<p>{p.strip()}</p>" for p in body.split("\n\n") if p.strip())
    ok = mailer._send(
        to=prospect["email"],
        subject=subject,
        text=body,
        html=html,
        transactional=False,
        category=f"cold_outreach_t{touch_n}",
    )
    return ok, subject


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="actually send; default is dry-run")
    ap.add_argument(
        "--force-day-of-week", action="store_true",
        help="bypass the Tue-Thu gate (use only for test sends)",
    )
    args = ap.parse_args()

    today = datetime.date.today()
    if _disabled():
        print(f"{DISABLE_ENV}=1 — cadence disabled; no sends")
        _append_audit("disabled", execute=bool(args.execute), date=today.isoformat())
        return 0

    if args.execute and not _automation_enabled():
        print(
            f"{AUTOMATION_ENV}=1 is required for --execute; refusing to send.",
            file=sys.stderr,
        )
        _append_audit("execute_blocked", reason=f"{AUTOMATION_ENV}_missing", date=today.isoformat())
        return 2

    if today.weekday() not in SEND_WEEKDAYS and not args.force_day_of_week:
        print(f"today is {today.strftime('%A')} — skip (Tue-Thu only). Use --force-day-of-week to override.")
        _append_audit("weekday_skipped", execute=bool(args.execute), date=today.isoformat(), weekday=today.weekday())
        return 0

    prospects = _read_prospects()
    state = _read_state()
    suppressed = _read_suppressions()

    plan = _plan_today(prospects, state, suppressed, today)
    sent_today = _sent_today_count(state)
    daily_cap = _daily_cap()
    remaining = daily_cap - sent_today

    print(f"date={today.isoformat()} weekday={today.strftime('%A')}")
    print(f"prospects={len(prospects)} suppressed={len(suppressed)} state_entries={len(state)}")
    print(f"sent_today={sent_today} cap={daily_cap} remaining={remaining}")
    print(f"plan_today={len(plan)}")

    if remaining <= 0:
        print("daily cap reached — no sends")
        _append_audit(
            "cap_reached",
            execute=bool(args.execute),
            date=today.isoformat(),
            prospects=len(prospects),
            suppressed=len(suppressed),
            state_entries=len(state),
            sent_today=sent_today,
            cap=daily_cap,
            plan_today=len(plan),
        )
        return 0

    plan = plan[:remaining]
    sends_ok = 0
    sends_fail = 0
    for item in plan:
        p = item["prospect"]
        touch_n = item["touch_n"]
        offset_day = TOUCH_OFFSETS_DAYS[touch_n]
        line = f"  touch={touch_n} (+{offset_day}d) vertical={p['vertical']} to={p['email']}"
        if not args.execute:
            print(f"  DRY-RUN {line}")
            continue
        ok, subj = _send_touch(p, touch_n)
        entry = {
            "ts": _now_iso(),
            "email": p["email"],
            "vertical": p["vertical"],
            "touch_n": touch_n,
            "offset_day": offset_day,
            "subject": subj,
            "sent_at": _now_iso() if ok else "",
            "ok": bool(ok),
        }
        _append_state(entry)
        if ok:
            sends_ok += 1
            print(f"  SENT {line}")
        else:
            sends_fail += 1
            print(f"  FAIL {line}")

    dry = len(plan) - sends_ok - sends_fail if not args.execute else 0
    _append_audit(
        "run_complete",
        execute=bool(args.execute),
        date=today.isoformat(),
        prospects=len(prospects),
        suppressed=len(suppressed),
        state_entries=len(state),
        sent_today_before=sent_today,
        cap=daily_cap,
        planned=len(plan),
        sent_ok=sends_ok,
        failed=sends_fail,
        dry=dry,
    )

    print(f"\ndone · sent_ok={sends_ok} failed={sends_fail} dry={dry}")
    return 0 if sends_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
