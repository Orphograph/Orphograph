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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import engine  # noqa: E402
import _sitetext  # noqa: E402

_COUNT = r"(?:five|5)"
_CAL = r"calendars?(?! servers?)"          # "five different calendar servers" is true
FALSE_CLAIMS = re.compile(
    # "five independent [up to four words] calendars|servers|...", hyphens allowed
    rf"{_COUNT} independent(?:ly)?(?:[ -]operated)? (?:[\w-]+ ){{0,4}}?"
    rf"(?:calendars?|servers?|operators?|services|timestamps|teams)"
    # "five separate|different|distinct ... calendars|operators|teams" (not servers)
    rf"|{_COUNT} (?:separate|different|distinct) (?:[\w-]+ ){{0,4}}?(?:{_CAL}|operators?|teams)"
    # "(the five) calendars ... are run by different teams"
    rf"|calendars? [^.]{{0,40}}?run by (?:independent|different|separate) (?:operators|teams|organi[sz]ations)"
    rf"|{_COUNT} (?:[\w-]+ ){{0,3}}?calendars?,? {_COUNT} (?:[\w-]+ )?operators"
    r"|four of the five calendar operators"
    r"|five separate on-chain anchors"
    r"|anchored five times"
    r"|different calendar's batch and a different bitcoin transaction",
    re.I)

POOL_UPSTREAM = {
    "https://a.pool.opentimestamps.org": "https://alice.btc.calendar.opentimestamps.org",
    "https://b.pool.opentimestamps.org": "https://bob.btc.calendar.opentimestamps.org",
}

def test_no_public_text_claims_five_independent_calendars() -> None:
    read, hits = 0, []
    for p in _sitetext.tracked_text_files(exclude=("tests/", "outreach/")):
        flat = _sitetext.read_flat(p)
        read += 1
        hits += [(str(p.relative_to(ROOT)), m.group(0)) for m in FALSE_CLAIMS.finditer(flat)]
    assert read > 300, f"only {read} files READ — the scan is not seeing the tree"
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
        # shapes the first version of this scan could not see (review, 2026-09-18)
        "<p>Each one references a different calendar&#x27;s batch and a different Bitcoin transaction.</p>",
        '<meta name="description" content="Anchored via five independent OpenTimestamps calendars.">',
        '<img src="x.png" alt="5 independent calendars">',
        "five&nbsp;independent calendars",
        "The five calendars Orphograph uses are run by different teams, hosted in different jurisdictions",
        "Five OpenTimestamps calendars, five separate operators",
        "If five succeed, the proof is anchored five times.",
        "five independent Bitcoin-timestamp services",
    ):
        assert FALSE_CLAIMS.search(_sitetext.flat(planted)), planted
    for fine in ("verifiable independently of this office",
                 "five calendars across three operators",
                 "five different OpenTimestamps calendar servers, not one",
                 "four distinct calendars under three separately run domains",
                 "Any one of the five files verifies independently.",
                 "five OpenTimestamps calendar servers",
                 "Redundancy only works where the calendars are genuinely independent."):
        assert not FALSE_CLAIMS.search(_sitetext.flat(fine)), fine


def test_the_corrected_count_still_matches_the_code() -> None:
    """The copy now says "four distinct calendars". That is a fact about
    engine.CALENDARS; if the list changes, the sentence must be re-examined."""
    assert len(engine.CALENDARS) == 5
    distinct = {POOL_UPSTREAM.get(u, u) for u in engine.CALENDARS}
    assert len(distinct) == 4, distinct
    domains = {".".join(u.split("/")[2].split(".")[-2:]) for u in distinct}
    assert domains == {"opentimestamps.org", "eternitywall.com", "catallaxy.com"}
    learn = _sitetext.read_flat(ROOT / "web" / "learn.html")
    assert "four distinct calendars" in learn and "three separately run domains" in learn
