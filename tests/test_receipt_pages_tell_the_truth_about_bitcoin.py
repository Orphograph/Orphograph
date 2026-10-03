"""What the certificate and receipt pages say about Bitcoin, held against what happens.

Review round 4 of the rescue branch (#283):
  * A receipt no calendar accepted (calendars_ok 0) has no Bitcoin commitment
    and never gets one. The dataset certificate page still called it
    "Pending Bitcoin confirmation" and promised block-pinning "within ~1 hour".
  * "Within ~1 hour" is not what happens even for a good receipt: launch-week
    receipts were pinned after 1.3 h, 34 h, 46 h and 128 h. The homepage, the
    receipt page and the certificate page all said it.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
CERT_JS = WEB / "certificate.js"

# Promises that a receipt is pinned in about an hour. Review round 5: the
# first pattern missed "within roughly an hour" and "~1 hour" forms. Not
# matched, because they are true: "6 subsequent blocks (~1 hour)" (block
# depth), "batch-broadcast roughly hourly" (calendar cadence), a payment
# confirming "~1 hour on-chain".
HOUR_PROMISE = re.compile(
    r"\b(within|in) (about |roughly |around |~ ?|approximately )?(1|one|an) hour"
    r"|pending \(~ ?1 ?hour\)|~ ?1 ?hour after anchoring|wait ~ ?1 ?hour"
    r"|upgrade after (1|one|an) hour")


def _friendly_status(rec: dict) -> str:
    """Run the real friendlyStatus from certificate.js under node."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH: this check runs the real certificate.js")
    src = CERT_JS.read_text(encoding="utf-8")
    m = re.search(r"^function friendlyStatus\(rec\) \{.*?^\}", src, re.S | re.M)
    assert m, "friendlyStatus not found in certificate.js"
    driver = m.group(0) + "\nprocess.stdout.write(friendlyStatus(JSON.parse(process.argv[1])));\n"
    proc = subprocess.run([node, "-e", driver, json.dumps(rec)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_a_root_no_calendar_accepted_is_not_called_pending():
    text = _friendly_status({"status": "pending", "calendars_ok": 0, "calendars_total": 0})
    assert "pending" not in text.lower(), text
    assert "no bitcoin commitment" in text.lower(), text


def test_a_root_one_calendar_accepted_is_still_pending():
    # The honest record on the same path.
    text = _friendly_status({"status": "pending", "calendars_ok": 1, "calendars_total": 5})
    assert text.startswith("Pending Bitcoin confirmation"), text


def test_no_page_or_script_promises_pinning_within_an_hour():
    offenders = []
    # content/blog/*.md too: /blog/<slug> renders it when no static page
    # exists, and /blog/atom.xml is always built from it.
    for p in sorted(list(WEB.rglob("*.js")) + list(WEB.rglob("*.html"))
                    + list((ROOT / "content").rglob("*.md"))):
        rel = p.relative_to(ROOT).as_posix()
        if "/_mockups/" in rel or rel.endswith("index-legacy.html") or "/dist/" in rel:
            continue
        text = re.sub(r"\s+", " ", p.read_text(encoding="utf-8", errors="replace")).lower()
        if HOUR_PROMISE.search(text):
            offenders.append(rel)
    assert not offenders, ("Bitcoin pinning took 1.3 h to 128 h at launch; these still "
                           "promise it within an hour: " + ", ".join(offenders))


def test_the_hour_scan_sees_the_sentence_it_was_written_for():
    # Positive control on the shipped wording.
    for old in ("Bitcoin confirmation arrives within ~1 hour.",
                "pending — block-pinning happens within ~1 hour",
                "a real Bitcoin transaction (usually within ~1 hour)",
                "Within roughly an hour, it is committed inside a Bitcoin block.",
                "receipts upgrade to a Bitcoin commitment in roughly one hour.",
                "Bitcoin commitment: pending (~1 hour)",
                "# Wait ~1 hour for the calendar to publish its Bitcoin tx.",
                "# upgrade after 1 hour to get the full Bitcoin merkle proof:",
                "(~1 hour after anchoring)"):
        assert HOUR_PROMISE.search(old.lower()), old



def test_the_hour_scan_leaves_true_hour_statements_alone():
    for true in ("the block has accumulated 6 subsequent blocks (~1 hour)",
                 "Calendars batch-broadcast roughly hourly.",
                 "The claim code is emailed the moment your payment confirms (~1 hour on-chain)."):
        assert not HOUR_PROMISE.search(true.lower()), true
