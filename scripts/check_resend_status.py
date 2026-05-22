#!/usr/bin/env python3
"""check_resend_status.py — query Resend recent-emails for delivery confirmation."""
import json
import os
import urllib.error
import urllib.request


def main() -> int:
    key = os.environ.get("RESEND_API_KEY", "")
    if not key:
        print("RESEND_API_KEY not set")
        return 1
    req = urllib.request.Request(
        "https://api.resend.com/emails?limit=15",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; OrphographMailer/0.1; +https://orphograph.com)",
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")
        return 1
    rows = data.get("data", []) if isinstance(data, dict) else []
    if not rows:
        print("no emails in response")
        print(json.dumps(data, indent=2)[:1000])
        return 0
    for e in rows[:15]:
        print(
            f"{e.get('created_at', '?')[:19]}  "
            f"to={e.get('to')}  "
            f"subj={(e.get('subject') or '?')[:55]}  "
            f"status={e.get('last_event', '?')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
