#!/usr/bin/env python3
"""reply_router.py — classify inbound replies + maintain suppression list.

Two modes:

  process <path>   — read an .eml file or plain text from <path> (or "-" for
                     stdin), classify the reply, append to data/replies.jsonl,
                     and if it's a STOP add to data/suppressions.jsonl.

  drain <dir>      — process every file in <dir> (typically outbox/replies_in/),
                     then move the file to outbox/replies_processed/<date>/.

Classification:
  STOP        — body contains any of: STOP, unsubscribe, remove me, opt out,
                no further, do not contact, "stop emailing"
  INTERESTED  — body contains any of: yes, interested, more info, demo, pricing,
                book a call, send details, "tell me more", "what's the price",
                schedule, available
  AUTOREPLY   — From/headers contain: out of office, vacation, auto-reply
  NEUTRAL     — anything else

INTERESTED replies trigger a one-line notification (stderr — pipe to telegram
in launchd wrapper) so the founder sees them within minutes.

Usage:
  python3 scripts/reply_router.py process path/to/reply.eml
  python3 scripts/reply_router.py drain outbox/replies_in/
  cat reply.txt | python3 scripts/reply_router.py process -
"""

from __future__ import annotations

import argparse
import datetime
import email
import json
import pathlib
import re
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
REPLIES_LOG = DATA_DIR / "replies.jsonl"
SUPPRESSIONS = DATA_DIR / "suppressions.jsonl"
INTERESTED_LOG = DATA_DIR / "interested.jsonl"

STOP_PATTERNS = [
    r"\bstop\b",
    r"\bunsubscribe\b",
    r"\bremove\s*me\b",
    r"\bopt\s*out\b",
    r"\bno\s*further\b",
    r"\bdo\s*not\s*contact\b",
    r"\bstop\s*emailing\b",
    r"\bplease\s*remove\b",
    r"\btake\s*me\s*off\b",
]
INTERESTED_PATTERNS = [
    r"\bcan\s*we\s*talk\b",
    r"\binterested\b",
    r"\bmore\s*info(?:rmation)?\b",
    r"\bdemo\b",
    r"\bpricing\b",
    r"\bhow\s*much\b",
    r"\bbook\s*a\s*call\b",
    r"\bsend\s*(?:me\s*)?details\b",
    r"\btell\s*me\s*more\b",
    r"\bwhat'?s\s*the\s*price\b",
    r"\bschedule\b",
    r"\bavailable\b",
    r"\bquote\b",
    r"\btrial\b",
    r"\bsign\s*up\b",
    r"\bget\s*started\b",
    r"\bcuriou(?:s|sity)\b",
]
AUTOREPLY_HEADERS = ["auto-submitted", "x-autoreply", "x-autorespond"]
AUTOREPLY_BODY_PATTERNS = [
    r"\bout\s*of\s*(?:the\s*)?office\b",
    r"\bvacation\b",
    r"\bauto[\s-]?reply\b",
    r"\bauto[\s-]?response\b",
    r"\bI\s*am\s*currently\s*away\b",
]


def _classify(headers: dict[str, str], body: str) -> str:
    body_lc = body.lower()
    # Auto-reply takes precedence — never treat it as STOP or INTERESTED.
    for h in AUTOREPLY_HEADERS:
        if headers.get(h):
            return "AUTOREPLY"
    for pat in AUTOREPLY_BODY_PATTERNS:
        if re.search(pat, body_lc):
            return "AUTOREPLY"
    for pat in STOP_PATTERNS:
        if re.search(pat, body_lc):
            return "STOP"
    for pat in INTERESTED_PATTERNS:
        if re.search(pat, body_lc):
            return "INTERESTED"
    return "NEUTRAL"


def _parse_message(raw: bytes | str) -> tuple[dict[str, str], str]:
    """Parse an .eml-style message OR plain text. Returns (headers, body)."""
    if isinstance(raw, bytes):
        try:
            raw_str = raw.decode("utf-8")
        except UnicodeDecodeError:
            raw_str = raw.decode("utf-8", errors="replace")
    else:
        raw_str = raw

    # Heuristic: if it has a From: header in the first 1KB, parse as RFC 5322.
    head = raw_str[:1024].lower()
    if "from:" in head or "subject:" in head:
        msg = email.message_from_string(raw_str)
        headers = {k.lower(): v for k, v in msg.items()}
        body_parts: list[str] = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        body_parts.append(part.get_payload(decode=True).decode(errors="replace"))
                    except Exception:
                        pass
        else:
            body_parts.append(str(msg.get_payload()))
        return headers, "\n".join(body_parts)
    return {}, raw_str


def _append_jsonl(path: pathlib.Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _from_address(headers: dict[str, str]) -> str:
    raw_from = headers.get("from", "")
    m = re.search(r"<([^>]+@[^>]+)>", raw_from)
    if m:
        return m.group(1).lower().strip()
    m = re.search(r"([\w.+-]+@[\w.-]+)", raw_from)
    return (m.group(1) if m else "").lower().strip()


def _process_one(raw: bytes | str, source_label: str) -> dict:
    headers, body = _parse_message(raw)
    classification = _classify(headers, body)
    sender = _from_address(headers)
    subject = headers.get("subject", "(no subject)")[:200]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    row = {
        "ts": now,
        "source": source_label,
        "from": sender,
        "subject": subject,
        "classification": classification,
        "body_excerpt": body.strip()[:280].replace("\n", " "),
    }
    _append_jsonl(REPLIES_LOG, row)

    if classification == "STOP" and sender:
        _append_jsonl(SUPPRESSIONS, {
            "email": sender, "reason": "STOP_REPLY", "ts": now, "source": source_label,
        })
        sys.stderr.write(f"[reply] STOP — added {sender} to suppressions\n")
    elif classification == "INTERESTED" and sender:
        _append_jsonl(INTERESTED_LOG, row)
        sys.stderr.write(
            f"[reply] INTERESTED — from={sender} subj={subject!r} — "
            f"surface to founder ASAP\n"
        )
    elif classification == "AUTOREPLY":
        sys.stderr.write(f"[reply] AUTOREPLY — from={sender} (ignored)\n")
    else:
        sys.stderr.write(f"[reply] NEUTRAL — from={sender} subj={subject!r}\n")
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_process = sub.add_parser("process", help="classify one reply")
    p_process.add_argument("path", help="path to .eml or text file, or '-' for stdin")

    p_drain = sub.add_parser("drain", help="process every file in a directory")
    p_drain.add_argument("dir", help="directory of .eml or .txt files to process")

    args = ap.parse_args()

    if args.cmd == "process":
        if args.path == "-":
            raw = sys.stdin.read()
            row = _process_one(raw, "stdin")
        else:
            p = pathlib.Path(args.path)
            row = _process_one(p.read_bytes(), p.name)
        print(json.dumps(row, indent=2))
        return 0

    if args.cmd == "drain":
        d = pathlib.Path(args.dir)
        if not d.exists():
            print(f"no such directory: {d}", file=sys.stderr)
            return 1
        today = datetime.date.today().isoformat()
        processed_dir = REPO / "outbox" / "replies_processed" / today
        processed_dir.mkdir(parents=True, exist_ok=True)
        n_total = 0
        n_by_class = {"STOP": 0, "INTERESTED": 0, "AUTOREPLY": 0, "NEUTRAL": 0}
        for f in sorted(d.iterdir()):
            if not f.is_file():
                continue
            row = _process_one(f.read_bytes(), f.name)
            n_total += 1
            n_by_class[row["classification"]] += 1
            shutil.move(str(f), str(processed_dir / f.name))
        print(f"drained={n_total} {' '.join(f'{k}={v}' for k, v in n_by_class.items())}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
