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
    for p in sorted(list(WEB.rglob("*.js")) + list(WEB.rglob("*.html"))):
        rel = p.relative_to(ROOT).as_posix()
        if "/_mockups/" in rel or rel.endswith("index-legacy.html") or "/dist/" in rel:
            continue
        text = re.sub(r"\s+", " ", p.read_text(encoding="utf-8", errors="replace")).lower()
        if re.search(r"within (about |~|approximately )?(1|one|an) hour", text):
            offenders.append(rel)
    assert not offenders, ("Bitcoin pinning took 1.3 h to 128 h at launch; these still "
                           "promise it within an hour: " + ", ".join(offenders))


def test_the_hour_scan_sees_the_sentence_it_was_written_for():
    # Positive control on the shipped wording.
    for old in ("Bitcoin confirmation arrives within ~1 hour.",
                "pending — block-pinning happens within ~1 hour",
                "a real Bitcoin transaction (usually within ~1 hour)"):
        assert re.search(r"within (about |~|approximately )?(1|one|an) hour", old.lower()), old
