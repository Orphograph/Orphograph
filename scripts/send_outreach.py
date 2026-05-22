#!/usr/bin/env python3
"""send_outreach.py — dispatch a cold-outreach draft to one recipient.

Templates are embedded inline so the script is self-contained on the Fly
image (the source outbox/ directory is .gitignored). Sends via
server/mailer._send() which adds CAN-SPAM footer + RFC 8058
List-Unsubscribe header + Resend DKIM-signing.

Locally inert without RESEND_API_KEY — useful for dry-runs. To actually
send: run inside a Fly machine via `fly ssh console -C` where
RESEND_API_KEY is in env.

Usage:
  python3 scripts/send_outreach.py <vertical> <to_email> [--name <first>] [--detail <one_sentence>]

  vertical: accounting | construction | legal_solos
  --name:   recipient first name for line-1 personalization
  --detail: one observed detail from recipient's public footprint
            (prepended as: "<first>, noticed <detail> — that's why I wrote.")

Hard caps (per outbox/COLD_OUTREACH_README.md):
  - 20 sends per day per sending domain (enforced via sent-log count)
  - Tuesday-Thursday, 9-11 a.m. recipient local — caller's responsibility
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "server"))

SENT_DIR = REPO / "outbox" / "sent"
DAILY_CAP = 20

# Embedded templates — kept verbatim from outbox/COLD_OUTREACH_<vertical>.md
# after the 2026-05-22 redaction pass. Each tuple is (subject, body).
# Body is verbatim; first line may be prepended by --name / --detail.

TEMPLATES: dict[str, tuple[str, str]] = {
    "accounting": (
        "Independent date-of-record for period-close files",
        """Lenders and auditors increasingly ask whether a close-package PDF or trial balance is the same file produced on close day, or one edited later. The question is rarely about the numbers. It is about the date of the bytes.

The office at orphograph.com publishes a method that lets a controller compute a cryptographic fingerprint of any close-package file on their own machine and anchor that fingerprint into the Bitcoin chain through a public timestamping protocol. The file is processed locally and is not transmitted to any external server. The output is a single-page receipt — with share link, embeddable badge, and a verify link — that any third party can check independently. The method does not replace a CPA review and is not an attestation product.

A short read for the period-close use case is here:

https://orphograph.com/blog/date-stamp-a-document-permanently.html

If timestamped proof-of-existence is not relevant to your practice, reply STOP and you will not be contacted again.

— the office at orphograph.com""",
    ),
    "construction": (
        "Independent date-of-record for storm-damage photos",
        """The crew already shoots a hundred photos per roof. The fight is rarely the photo. It is the carrier asking, weeks after the job closed, whether the date and the file are the originals.

The office at orphograph.com publishes a method that lets a contractor compute a cryptographic fingerprint of any photo or PDF on their own machine and anchor that fingerprint into the Bitcoin chain through a public timestamping protocol. The file is processed locally and is not transmitted to any external server. The output is a single-page receipt — with share link, an embeddable badge for the job folder, and a verify link any adjuster can open on a separate computer.

A short read for the storm-photo use case is here:

https://orphograph.com/blog/prove-a-photo-was-not-edited.html

If timestamped proof-of-existence is not relevant to your practice, reply STOP and you will not be contacted again.

— the office at orphograph.com""",
    ),
    "legal_solos": (
        "Independent date-of-record for exhibits",
        """The exhibit fight is rarely about the photo or the screenshot itself. It is opposing counsel arguing that the date or the file was altered after the fact.

The office at orphograph.com publishes a method that lets a practitioner compute a cryptographic fingerprint of any photo, PDF, or screenshot on their own machine and anchor that fingerprint into the Bitcoin chain through a public timestamping protocol. The file is processed locally and is not transmitted to any external server. The output is a single-page receipt — with share link, embeddable badge for the case file, and a verify link the court or opposing counsel can open independently.

This is not legal advice and establishes no privileged relationship.

A short read on what a cryptographic timestamp proves, and what it does not, is here:

https://orphograph.com/blog/digital-notary-vs-cryptographic-timestamp.html

If timestamped proof-of-existence is not relevant to your practice, reply STOP and you will not be contacted again.

— the office at orphograph.com""",
    ),
}


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
    ap.add_argument("vertical", choices=sorted(TEMPLATES.keys()))
    ap.add_argument("to", help="recipient email")
    ap.add_argument("--name", help="recipient first name (optional)")
    ap.add_argument("--detail", help="one observed public-footprint detail (optional)")
    ap.add_argument(
        "--ignore-cap", action="store_true", help="bypass the 20/day cap"
    )
    args = ap.parse_args()

    sent_today = _check_daily_cap()
    if sent_today >= DAILY_CAP and not args.ignore_cap:
        print(
            f"daily cap reached: {sent_today}/{DAILY_CAP} already sent today. "
            f"Use --ignore-cap to override.",
            file=sys.stderr,
        )
        return 2

    subject, body = TEMPLATES[args.vertical]
    body = _personalize(body, args.name, args.detail)

    import mailer  # type: ignore  # noqa: E402

    html_paragraphs = "\n".join(
        f"<p>{p.strip()}</p>" for p in body.split("\n\n") if p.strip()
    )

    ok = mailer._send(
        to=args.to,
        subject=subject,
        text=body,
        html=html_paragraphs,
        transactional=False,
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
