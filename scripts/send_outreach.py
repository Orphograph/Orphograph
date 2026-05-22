#!/usr/bin/env python3
"""send_outreach.py — dispatch a cold-outreach draft to one recipient.

Reads a vertical template from outbox/COLD_OUTREACH_<vertical>.md, optionally
personalizes the first line, and sends via server/mailer._send() — which adds
the CAN-SPAM footer, List-Unsubscribe header, and DKIM-signs via Resend.

The send only fires if RESEND_API_KEY is in env. Otherwise _send() logs
"[email:inert] would send to=..." and returns False — useful for local
dry-runs. To actually send: run inside a Fly machine where RESEND_API_KEY
is set, via `fly ssh console`.

Every successful send is logged to outbox/sent/<recipient>_<vertical>_<date>.md
for the 3-touch follow-up cadence.

Usage:
  python3 scripts/send_outreach.py <vertical> <to_email> [--name <first>] [--detail <one_sentence>]

  vertical: accounting | construction | legal_solos
  --name:   recipient first name for line-1 personalization
  --detail: one observed detail from recipient's public footprint
            (prepended as: "<first>, noticed <detail> — that's why I wrote.")

Hard caps (per outbox/COLD_OUTREACH_README.md):
  - 20 sends per day per sending domain
  - Tuesday-Thursday, 9-11 a.m. recipient local — caller's responsibility
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import sys

# Allow importing server/mailer.py
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "server"))

VALID_VERTICALS = ["accounting", "construction", "legal_solos"]
DRAFTS_DIR = REPO / "outbox"
SENT_DIR = REPO / "outbox" / "sent"
DAILY_CAP = 20


def _parse_template(vertical: str) -> tuple[str, str]:
    """Return (subject, body) extracted from the markdown template."""
    md = (DRAFTS_DIR / f"COLD_OUTREACH_{vertical}.md").read_text(encoding="utf-8")
    subj_m = re.search(r"^Subject:\s*(.+)$", md, re.MULTILINE)
    if not subj_m:
        raise SystemExit(f"no Subject: line in COLD_OUTREACH_{vertical}.md")
    subject = subj_m.group(1).strip()
    body_m = re.search(
        r"^Subject:.*?\n\nTo:.*?\n\n(.+?)\n\n## Follow-up cadence",
        md,
        re.DOTALL | re.MULTILINE,
    )
    if not body_m:
        raise SystemExit(f"could not extract body block from COLD_OUTREACH_{vertical}.md")
    body = body_m.group(1).strip()
    return subject, body


def _personalize(body: str, first_name: str | None, detail: str | None) -> str:
    if not first_name:
        return body
    if detail:
        opener = f"{first_name.strip()}, noticed {detail.strip().rstrip('.')} — that's why I wrote."
    else:
        opener = f"{first_name.strip()},"
    return opener + "\n\n" + body


def _check_daily_cap() -> int:
    today = datetime.date.today().isoformat()
    count = 0
    if SENT_DIR.exists():
        for p in SENT_DIR.iterdir():
            if today in p.name:
                count += 1
    return count


def _log_send(vertical: str, to: str, subject: str, body: str) -> pathlib.Path:
    SENT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    safe_to = re.sub(r"[^a-zA-Z0-9._-]", "_", to)
    log_path = SENT_DIR / f"{safe_to}_{vertical}_{today}.md"
    log_path.write_text(
        f"# Sent {today} · vertical={vertical} · to={to}\n\n"
        f"Subject: {subject}\n\n"
        f"---\n\n{body}\n",
        encoding="utf-8",
    )
    return log_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("vertical", choices=VALID_VERTICALS)
    ap.add_argument("to", help="recipient email")
    ap.add_argument("--name", help="recipient first name (optional)")
    ap.add_argument("--detail", help="one observed public-footprint detail (optional)")
    ap.add_argument(
        "--ignore-cap", action="store_true", help="bypass the 20/day cap (use sparingly)"
    )
    args = ap.parse_args()

    # Daily cap check (best-effort — based on sent log file count).
    sent_today = _check_daily_cap()
    if sent_today >= DAILY_CAP and not args.ignore_cap:
        print(
            f"daily cap reached: {sent_today}/{DAILY_CAP} already sent today. "
            f"Use --ignore-cap to override.",
            file=sys.stderr,
        )
        return 2

    subject, body = _parse_template(args.vertical)
    body = _personalize(body, args.name, args.detail)

    # Lazy import — mailer.py reads env on import.
    import mailer  # type: ignore  # noqa: E402

    # Render plain-text into HTML by wrapping paragraphs in <p>.
    html_paragraphs = "\n".join(
        f"<p>{p.strip()}</p>" for p in body.split("\n\n") if p.strip()
    )

    ok = mailer._send(
        to=args.to,
        subject=subject,
        text=body,
        html=html_paragraphs,
        transactional=False,  # cold outreach is bulk → triggers List-Unsubscribe
        category="cold_outreach",
    )

    if ok:
        log = _log_send(args.vertical, args.to, subject, body)
        print(f"sent · vertical={args.vertical} · to={args.to} · log={log.name}")
        return 0
    else:
        print(
            f"NOT sent (RESEND_API_KEY unset or API rejected) · "
            f"vertical={args.vertical} · to={args.to}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
