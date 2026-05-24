#!/usr/bin/env python3
"""funnel_digest.py — autonomous weekly funnel digest to the founder.

Reads data/events.jsonl directly (no HTTP — runs in-process on the Fly
machine with the same filesystem the server uses), rolls up the same
four funnel events the /api/founder/funnel endpoint exposes
(drop_zone_visible, file_anchored, checkout_clicked,
checkout_returned_success), and emails a 7-day rollup with a 30-day
context block to the founder via the Resend transactional path.

Operational invariants:
  - 7-day window for the headline numbers; 30-day window for context.
  - Idempotent: refuses to re-send if `data/.funnel_digest_last_run`
    already contains today's ISO date. Writes that file on send success.
  - --dry-run prints the email body to stdout and never calls the mailer.
  - Pure stdlib. No third-party dependencies.
  - Transactional category — operational founder mail, not marketing.

Usage:
    python3 scripts/funnel_digest.py             # send (idempotent)
    python3 scripts/funnel_digest.py --dry-run   # print, never send

Designed to be invoked by `_start_funnel_digest_scheduler()` in
server/app.py once per week (Monday 14:00 UTC). Honors the
ORPHO_FUNNEL_DIGEST_DISABLED kill switch at the scheduler layer.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html as _html
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "server"))

DATA_DIR = REPO / "data"
EVENTS_PATH = DATA_DIR / "events.jsonl"
STATE_PATH = DATA_DIR / ".funnel_digest_last_run"

# Founder destination resolved at runtime from ORPHO_FOUNDER_EMAIL so the
# repo never carries a personal address in a deploy-eligible path. Fly
# secrets set this; local dev defaults to the brand mailbox.
FOUNDER_EMAIL = os.environ.get("ORPHO_FOUNDER_EMAIL", "hello@orphograph.com")

FUNNEL_EVENTS = (
    "drop_zone_visible",
    "file_anchored",
    "checkout_clicked",
    "checkout_returned_success",
)

# Friendly labels for the body — checkout_returned_success is the paid
# event but the body should read as "checkout paid" to a non-engineer.
EVENT_LABELS = {
    "drop_zone_visible": "Drop zone visible",
    "file_anchored": "File anchored",
    "checkout_clicked": "Checkout clicked",
    "checkout_returned_success": "Checkout paid",
}


def _rate(num: int, den: int) -> float:
    return round(100.0 * num / den, 1) if den else 0.0


def _rollup(
    events_path: pathlib.Path,
    now_utc: _dt.datetime,
    window_days: int,
) -> tuple[dict[str, int], dict[str, dict[str, int]], int]:
    """Return (totals, per_day, scanned) for events in the trailing window."""
    totals: dict[str, int] = {e: 0 for e in FUNNEL_EVENTS}
    per_day: dict[str, dict[str, int]] = {}
    scanned = 0
    cutoff = now_utc - _dt.timedelta(days=window_days)
    if not events_path.exists():
        return totals, per_day, scanned
    try:
        raw = events_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return totals, per_day, scanned
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        scanned += 1
        ev = rec.get("event")
        ts = rec.get("ts") or rec.get("timestamp")
        if not ev or not ts or ev not in FUNNEL_EVENTS:
            continue
        try:
            when = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when < cutoff:
            continue
        day = when.date().isoformat()
        per_day.setdefault(day, {e: 0 for e in FUNNEL_EVENTS})
        per_day[day][ev] = per_day[day].get(ev, 0) + 1
        totals[ev] += 1
    return totals, per_day, scanned


def _format_text_body(
    week_ending: _dt.date,
    totals_7d: dict[str, int],
    per_day_7d: dict[str, dict[str, int]],
    totals_30d: dict[str, int],
    events_scanned: int,
) -> str:
    rates_7d = {
        "visible_to_anchored": _rate(totals_7d["file_anchored"], totals_7d["drop_zone_visible"]),
        "anchored_to_checkout": _rate(totals_7d["checkout_clicked"], totals_7d["file_anchored"]),
        "checkout_to_paid": _rate(totals_7d["checkout_returned_success"], totals_7d["checkout_clicked"]),
        "visible_to_paid": _rate(totals_7d["checkout_returned_success"], totals_7d["drop_zone_visible"]),
    }

    lines: list[str] = []
    lines.append("Orphograph weekly funnel digest")
    lines.append(f"Week ending {week_ending.isoformat()}")
    lines.append("─" * 32)
    lines.append("")
    lines.append("Totals (last 7 days):")
    lines.append(f"  Drop zone visible: {totals_7d['drop_zone_visible']}")
    lines.append(
        f"  File anchored:     {totals_7d['file_anchored']}  "
        f"(visible → anchored: {rates_7d['visible_to_anchored']}%)"
    )
    lines.append(
        f"  Checkout clicked:  {totals_7d['checkout_clicked']}  "
        f"(anchored → checkout: {rates_7d['anchored_to_checkout']}%)"
    )
    lines.append(
        f"  Checkout paid:     {totals_7d['checkout_returned_success']}  "
        f"(checkout → paid: {rates_7d['checkout_to_paid']}%)"
    )
    lines.append("")
    lines.append(f"End-to-end conversion (visible → paid): {rates_7d['visible_to_paid']}%")
    lines.append("")
    lines.append("Daily series:")
    # Walk the 7 days ending on week_ending in chronological order.
    for i in range(6, -1, -1):
        day = (week_ending - _dt.timedelta(days=i)).isoformat()
        row = per_day_7d.get(day, {e: 0 for e in FUNNEL_EVENTS})
        lines.append(
            f"  {day}  visible={row['drop_zone_visible']}  "
            f"anchored={row['file_anchored']}  "
            f"checkout={row['checkout_clicked']}  "
            f"paid={row['checkout_returned_success']}"
        )
    lines.append("")
    lines.append("30-day totals for context:")
    lines.append(f"  Drop zone visible: {totals_30d['drop_zone_visible']}")
    lines.append(f"  File anchored:     {totals_30d['file_anchored']}")
    lines.append(f"  Checkout clicked:  {totals_30d['checkout_clicked']}")
    lines.append(f"  Checkout paid:     {totals_30d['checkout_returned_success']}")
    lines.append("")
    lines.append("─" * 32)
    lines.append(f"Source: data/events.jsonl on the Fly machine ({events_scanned} lines scanned)")
    return "\n".join(lines) + "\n"


def _format_html_body(
    week_ending: _dt.date,
    totals_7d: dict[str, int],
    per_day_7d: dict[str, dict[str, int]],
    totals_30d: dict[str, int],
    events_scanned: int,
) -> str:
    rates_7d = {
        "visible_to_anchored": _rate(totals_7d["file_anchored"], totals_7d["drop_zone_visible"]),
        "anchored_to_checkout": _rate(totals_7d["checkout_clicked"], totals_7d["file_anchored"]),
        "checkout_to_paid": _rate(totals_7d["checkout_returned_success"], totals_7d["checkout_clicked"]),
        "visible_to_paid": _rate(totals_7d["checkout_returned_success"], totals_7d["drop_zone_visible"]),
    }

    def _row(label: str, count: int, rate_label: str = "", rate_val: float | None = None) -> str:
        rate_cell = (
            f"<td style=\"padding:4px 10px;text-align:right;color:#555;\">{rate_label}: {rate_val}%</td>"
            if rate_label
            else "<td></td>"
        )
        return (
            "<tr>"
            f"<td style=\"padding:4px 10px;\">{_html.escape(label)}</td>"
            f"<td style=\"padding:4px 10px;text-align:right;\"><b>{count}</b></td>"
            f"{rate_cell}"
            "</tr>"
        )

    daily_rows: list[str] = []
    for i in range(6, -1, -1):
        day = (week_ending - _dt.timedelta(days=i)).isoformat()
        row = per_day_7d.get(day, {e: 0 for e in FUNNEL_EVENTS})
        daily_rows.append(
            "<tr>"
            f"<td style=\"padding:3px 10px;font-family:monospace;\">{day}</td>"
            f"<td style=\"padding:3px 10px;text-align:right;\">{row['drop_zone_visible']}</td>"
            f"<td style=\"padding:3px 10px;text-align:right;\">{row['file_anchored']}</td>"
            f"<td style=\"padding:3px 10px;text-align:right;\">{row['checkout_clicked']}</td>"
            f"<td style=\"padding:3px 10px;text-align:right;\">{row['checkout_returned_success']}</td>"
            "</tr>"
        )

    html = []
    html.append(
        "<div style=\"font-family:Georgia,serif;color:#222;max-width:640px;\">"
    )
    html.append("<h2 style=\"margin:0 0 4px 0;\">Orphograph weekly funnel digest</h2>")
    html.append(f"<p style=\"margin:0 0 16px 0;color:#666;\">Week ending {week_ending.isoformat()}</p>")

    html.append("<h3 style=\"margin:16px 0 6px 0;\">Totals (last 7 days)</h3>")
    html.append("<table style=\"border-collapse:collapse;width:100%;\">")
    html.append(_row("Drop zone visible", totals_7d["drop_zone_visible"]))
    html.append(_row("File anchored", totals_7d["file_anchored"], "visible &rarr; anchored", rates_7d["visible_to_anchored"]))
    html.append(_row("Checkout clicked", totals_7d["checkout_clicked"], "anchored &rarr; checkout", rates_7d["anchored_to_checkout"]))
    html.append(_row("Checkout paid", totals_7d["checkout_returned_success"], "checkout &rarr; paid", rates_7d["checkout_to_paid"]))
    html.append("</table>")
    html.append(
        f"<p style=\"margin:12px 0;\">End-to-end conversion "
        f"(visible &rarr; paid): <b>{rates_7d['visible_to_paid']}%</b></p>"
    )

    html.append("<h3 style=\"margin:20px 0 6px 0;\">Daily series</h3>")
    html.append("<table style=\"border-collapse:collapse;width:100%;font-size:13px;\">")
    html.append(
        "<tr style=\"background:#f5f5f0;\">"
        "<th style=\"padding:4px 10px;text-align:left;\">Date</th>"
        "<th style=\"padding:4px 10px;text-align:right;\">visible</th>"
        "<th style=\"padding:4px 10px;text-align:right;\">anchored</th>"
        "<th style=\"padding:4px 10px;text-align:right;\">checkout</th>"
        "<th style=\"padding:4px 10px;text-align:right;\">paid</th>"
        "</tr>"
    )
    html.extend(daily_rows)
    html.append("</table>")

    html.append("<h3 style=\"margin:20px 0 6px 0;\">30-day totals for context</h3>")
    html.append("<table style=\"border-collapse:collapse;width:100%;\">")
    html.append(_row("Drop zone visible", totals_30d["drop_zone_visible"]))
    html.append(_row("File anchored", totals_30d["file_anchored"]))
    html.append(_row("Checkout clicked", totals_30d["checkout_clicked"]))
    html.append(_row("Checkout paid", totals_30d["checkout_returned_success"]))
    html.append("</table>")

    html.append(
        "<p style=\"margin-top:24px;color:#888;font-size:12px;\">"
        f"Source: data/events.jsonl on the Fly machine ({events_scanned} lines scanned)."
        "</p>"
    )
    html.append("</div>")
    return "".join(html)


def _already_ran_today(state_path: pathlib.Path, today: _dt.date) -> bool:
    if not state_path.exists():
        return False
    try:
        return state_path.read_text(encoding="utf-8").strip() == today.isoformat()
    except OSError:
        return False


def _write_state(state_path: pathlib.Path, today: _dt.date) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(today.isoformat(), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the email body and never call the mailer",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="bypass the same-day idempotency guard (test sends only)",
    )
    args = ap.parse_args(argv)

    now_utc = _dt.datetime.now(_dt.timezone.utc)
    today = now_utc.date()
    week_ending = today

    if not args.dry_run and not args.force and _already_ran_today(STATE_PATH, today):
        sys.stderr.write(
            f"[funnel-digest] already sent on {today.isoformat()}; refusing to re-send\n"
        )
        return 0

    totals_7d, per_day_7d, scanned_7d = _rollup(EVENTS_PATH, now_utc, window_days=7)
    totals_30d, _per_day_30d, scanned_30d = _rollup(EVENTS_PATH, now_utc, window_days=30)
    # `scanned` is reported from the 30-day rollup since it scans every
    # line in events.jsonl regardless of window (the 7-day pass scans the
    # same file but the 30-day window is the broader, more accurate count
    # for the "lines scanned" footer).
    events_scanned = max(scanned_7d, scanned_30d)

    subject = f"Orphograph weekly funnel digest — week ending {week_ending.isoformat()}"
    text_body = _format_text_body(
        week_ending=week_ending,
        totals_7d=totals_7d,
        per_day_7d=per_day_7d,
        totals_30d=totals_30d,
        events_scanned=events_scanned,
    )
    html_body = _format_html_body(
        week_ending=week_ending,
        totals_7d=totals_7d,
        per_day_7d=per_day_7d,
        totals_30d=totals_30d,
        events_scanned=events_scanned,
    )

    if args.dry_run:
        sys.stdout.write(f"SUBJECT: {subject}\n")
        sys.stdout.write(f"TO: {FOUNDER_EMAIL}\n")
        sys.stdout.write("---- TEXT ----\n")
        sys.stdout.write(text_body)
        sys.stdout.write("---- END ----\n")
        return 0

    try:
        import mailer  # type: ignore  # provided by server/ on sys.path
    except ImportError as exc:
        sys.stderr.write(f"[funnel-digest] cannot import mailer: {exc}\n")
        return 1

    try:
        ok = mailer._send(
            to=FOUNDER_EMAIL,
            subject=subject,
            text=text_body,
            html=html_body,
            transactional=True,
            category="weekly_digest",
        )
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[funnel-digest] send error: {type(exc).__name__}: {exc}\n")
        return 1

    if not ok:
        sys.stderr.write("[funnel-digest] mailer reported failure\n")
        return 1

    try:
        _write_state(STATE_PATH, today)
    except OSError as exc:
        sys.stderr.write(f"[funnel-digest] state write error: {exc}\n")
        # Send succeeded; surface as success and let the next-week run handle.
        return 0

    sys.stderr.write(f"[funnel-digest] sent to founder for week ending {week_ending.isoformat()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
