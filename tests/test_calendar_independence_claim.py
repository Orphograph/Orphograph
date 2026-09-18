"""Public copy must not call the five calendar servers five independent calendars.

Found 2026-09-18 while cross-checking proofs with an outside OpenTimestamps
tool. The office submits to five servers, but two of them (a.pool, b.pool) are
aggregators in front of the alice and bob calendars. Three separate signals:

  * a fresh `a.ots` names alice in its pending attestation, `b.ots` names bob;
  * the confirmed public sample's `a.ots` and `alice.ots` end in the SAME
    Bitcoin transaction;
  * a.pool's own status page says it aggregates "for the upstream calendar
    server https://alice.btc.calendar.opentimestamps.org".

So five submissions reach four distinct calendars. About forty surfaces said
"five independent calendars", and one post said the five were run by different
teams in different jurisdictions and that each proof ended in a different
Bitcoin transaction. The upgrade worker had known about the aliases since
2026-08-30 (test_upgrade_query_attested_calendar.py); the copy never caught up.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import engine  # noqa: E402

TEXT_EXT = {"html", "md", "txt", "xml", "json", "svg"}

FALSE_CLAIMS = re.compile(
    r"(?:five|5) independent(?:ly)?(?:[ -]operated)? (?:\w+ ){0,3}?(?:calendars?|servers?|operators?|services|timestamps)"
    r"|calendars? (?:are )?run by (?:independent|different) (?:operators|teams)"
    r"|four of the five calendar operators"
    r"|five separate on-chain anchors"
    r"|different calendar's batch and a different bitcoin transaction",
    re.I)

POOL_UPSTREAM = {
    "https://a.pool.opentimestamps.org": "https://alice.btc.calendar.opentimestamps.org",
    "https://b.pool.opentimestamps.org": "https://bob.btc.calendar.opentimestamps.org",
}


def _flat(text: str) -> str:
    """Tags out, whitespace collapsed: the claim wraps across lines in HTML,
    and a line-by-line grep missed five surfaces the first time."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))


def _public_text_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split("\n")
    return [ROOT / f for f in out
            if f and f.rsplit(".", 1)[-1] in TEXT_EXT
            and not f.startswith(("tests/", "outreach/"))]


def test_no_public_text_claims_five_independent_calendars() -> None:
    files = _public_text_files()
    assert len(files) > 200, f"only {len(files)} files scanned — the scan is not seeing the tree"
    hits = []
    for p in files:
        try:
            flat = _flat(p.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        hits += [(str(p.relative_to(ROOT)), m.group(0)) for m in FALSE_CLAIMS.finditer(flat)]
    assert not hits, (
        "Five SERVERS, four distinct calendars (a.pool and b.pool feed alice and "
        "bob). These surfaces say otherwise:\n  " + "\n  ".join(f"{f}: {c!r}" for f, c in hits))


def test_the_scan_can_see_the_claim_it_hunts() -> None:
    """NEGATIVE CONTROL: every shape that was live on 2026-09-18, wrapped or not."""
    for planted in (
        "<p>submitted to five independent\n   OpenTimestamps calendars.</p>",
        "Using 5 independent OpenTimestamps calendars.",
        "five independently operated OpenTimestamps calendars",
        "Five independent timestamp servers confirm.",
        '<h2 class="t">5 Independent Timestamps</h2>',   # the homepage card: found on the wire, not by grep
        "Calendars are run by independent operators with no financial tie",
        "even if four of the five calendar operators vanish",
        "five separate Merkle paths leading to five separate\non-chain anchors",
    ):
        assert FALSE_CLAIMS.search(_flat(planted)), planted
    for fine in ("verifiable independently of this office",
                 "five OpenTimestamps calendar servers",
                 "Redundancy only works where the calendars are genuinely independent."):
        assert not FALSE_CLAIMS.search(_flat(fine)), fine


def test_the_corrected_count_still_matches_the_code() -> None:
    """The copy now says "four distinct calendars". That is a fact about
    engine.CALENDARS; if the list changes, the sentence must be re-examined."""
    assert len(engine.CALENDARS) == 5
    distinct = {POOL_UPSTREAM.get(u, u) for u in engine.CALENDARS}
    assert len(distinct) == 4, distinct
    domains = {".".join(u.split("/")[2].split(".")[-2:]) for u in distinct}
    assert domains == {"opentimestamps.org", "eternitywall.com", "catallaxy.com"}
    learn = _flat((ROOT / "web" / "learn.html").read_text(encoding="utf-8"))
    assert "four distinct calendars" in learn and "three separately run domains" in learn
