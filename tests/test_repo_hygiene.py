#!/usr/bin/env python3
"""test_repo_hygiene.py — two guards for defect CLASSES this repo keeps
re-growing one instance at a time.

Both were found on 2026-08-20 by a Stage 3e drift sweep, and both were
already sitting in the working tree unnoticed:

1. `tools/test_gate_read.py` called sys.exit() at module scope. pytest
   IMPORTS files named test_*.py during collection, so that aborts the whole
   run with INTERNALERROR -- not a failing test, a dead run. CI survived only
   because it scopes itself to `pytest tests/`; anyone typing `pytest` at the
   repo root would have tripped it.

2. `scouting-drafts/` held unposted outreach drafts in a PUBLIC repo and was
   not in .gitignore, while every sibling location holding the same content
   class (outreach/, outbox/, research_*/, docs/audits/) was. Nothing had
   leaked -- the files were untracked -- but the protection was one
   `git add -A` from failing, and it failed by omission rather than by
   decision.

Neither guard is written against the instance. `.gitignore` is a
hand-maintained per-directory enumeration and a code-review checklist is a
per-file one; both fail the same way, silently, whenever something new is
created and nobody remembers. These tests are what remembers.
"""
from __future__ import annotations

import ast
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Phrases that only appear in internal outreach/scouting drafts. Deliberately
# narrow: a marker set broad enough to catch everything would flag the press
# kit and the docs. Narrow and true beats broad and flaky -- and the negative
# control below proves this set can still hit.
_INTERNAL_MARKERS = [
    re.compile(r"drafts?\s+only[.,]?\s+nothing\s+posted", re.I),
    re.compile(r"nothing\s+posted,?\s+nothing\s+queued\s+to\s+post", re.I),
    re.compile(r"demand\s+scouting:", re.I),
    re.compile(r"review\s+before\s+any\s+send", re.I),
]

_TEXT_SUFFIXES = {".md", ".txt", ".py", ".html", ".json", ".yml", ".yaml", ".sh"}


def _tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [ROOT / p for p in out.split("\0") if p]


class TestNoModuleScopeExitInTestFiles(unittest.TestCase):
    """A test file that exits at import kills the collector, not just itself."""

    @staticmethod
    def _module_scope_exits(source: str) -> list[int]:
        """Line numbers of top-level sys.exit()/exit() calls that are NOT
        inside a function, a class, or an `if __name__ == ...` guard."""
        tree = ast.parse(source)
        hits = []
        for node in tree.body:
            if isinstance(node, ast.If):
                continue  # `if __name__ == "__main__":` and friends are fine
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                f = sub.func
                name = (f.attr if isinstance(f, ast.Attribute)
                        else f.id if isinstance(f, ast.Name) else None)
                if name in ("exit", "_exit"):
                    hits.append(sub.lineno)
        return hits

    def test_negative_control_the_detector_can_hit(self):
        """A scan that has never been shown to fire is not a passing scan."""
        bad = "import sys\nprint('x')\nsys.exit(1)\n"
        good = "import sys\nif __name__ == '__main__':\n    sys.exit(0)\n"
        self.assertTrue(self._module_scope_exits(bad),
                        "detector failed to flag a module-scope sys.exit")
        self.assertFalse(self._module_scope_exits(good),
                         "detector flagged a properly guarded exit")

    def test_no_test_file_exits_during_collection(self):
        offenders = []
        for path in _tracked_files():
            if path.suffix != ".py" or not path.name.startswith("test_"):
                continue
            try:
                lines = self._module_scope_exits(path.read_text())
            except (SyntaxError, UnicodeDecodeError):
                continue
            if lines:
                offenders.append(f"{path.relative_to(ROOT)}:{lines[0]}")
        self.assertEqual(
            offenders, [],
            "these test files call sys.exit() at module scope; pytest imports "
            "them during collection and the run dies with INTERNALERROR "
            "instead of reporting a failure: " + ", ".join(offenders))


class TestInternalDraftsNotCommitted(unittest.TestCase):
    """Internal outreach drafts must never be tracked in a public repo."""

    @staticmethod
    def _markers_in(text: str) -> list[str]:
        return [m.pattern for m in _INTERNAL_MARKERS if m.search(text)]

    def test_negative_control_the_marker_set_can_hit(self):
        """Runs everywhere, including CI where the drafts do not exist.

        Without this, a marker set that had rotted into matching nothing would
        report a clean repo forever -- the exact 'deny list that matches
        nothing reports CLEAN' failure this project has already been bitten by.
        """
        sample = ("# Orphograph — demand scouting: `agent_operators` (2026-08-08)\n"
                  "\nDrafts only. Nothing posted. Review before any send.\n")
        self.assertGreaterEqual(
            len(self._markers_in(sample)), 3,
            "the internal-draft marker set no longer matches a known internal "
            "draft; it has rotted and now reports every repo clean")

    def test_no_tracked_file_carries_internal_draft_markers(self):
        offenders = []
        for path in _tracked_files():
            if path.suffix.lower() not in _TEXT_SUFFIXES or not path.is_file():
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            if path.resolve() == Path(__file__).resolve():
                continue  # this file quotes the markers on purpose
            hit = self._markers_in(text)
            if hit:
                offenders.append(f"{path.relative_to(ROOT)} ({hit[0]})")
        self.assertEqual(
            offenders, [],
            "internal outreach/scouting drafts are tracked in a PUBLIC repo: "
            + ", ".join(offenders))



# Outbound scripts that still carry a browser-spoofing User-Agent as of
# 2026-08-20. This is a FROZEN debt list, not an approval: the rule
# ("never a browser-spoofing UA") is a hard rule, and every entry here
# violates it. They are listed rather than fixed because each one is a LIVE
# job talking to a third party, and changing an outbound UA can get a job
# blocked -- a behaviour change on working production jobs is the founder's
# call, not an in-loop fix. The list may only ever SHRINK.
_SPOOFED_UA_DEBT = {
    "dataset-provenance/provenance.py",
    "scripts/auto_anchor_repo.py",
    "scripts/canary_scan.py",
    "scripts/morning_check.py",
    "scripts/orphograph_watchdog.py",
    "scripts/publish_watcher.py",
    "scripts/slo_monitor.py",
    "scripts/usb_offline_anchor_submit.py",
    "sdk-python/orphograph/_client.py",
}
# Related, tracked separately because it is a different shape:
# tests/test_usb_airgap.py:231 ASSERTS a spoofed UA is present
# (`"Mozilla/5.0" in ua and "Chrome/" in ua`), i.e. a test that pins the
# violation of a hard rule. It carries no spoof string of its own, so it does
# not belong in the list above; it belongs in the same founder decision.

# A string is IMPERSONATION when it claims a real browser PRODUCT or engine.
# A bare "Mozilla/5.0" is NOT enough on its own: the conventional honest bot
# form is `Mozilla/5.0 (compatible; <YourName>/1.0; +https://your.site)` --
# the same shape Googlebot uses -- where "Mozilla/5.0" is a vestigial
# compatibility token and the agent still says who it really is. Four files in
# server/ and scripts/ use exactly that form and are correctly NOT flagged; a
# detector that failed to tell them apart from a fake Chrome would be noise,
# and noise gets an allowlist bolted on until it means nothing.
_SPOOF_TOKENS = re.compile(
    r"Chrome/[0-9]|Safari/[0-9]|Brave/[0-9]|Firefox/[0-9]|Edg/[0-9]|AppleWebKit/[0-9]")

# Files whose browser strings are INBOUND fixtures -- they simulate a visitor
# arriving at our server, which is the opposite of impersonating a client.
_INBOUND_FIXTURES = {"tests/test_ab_home.py"}


class TestNoNewSpoofedUserAgents(unittest.TestCase):
    """The "never a browser-spoofing UA" rule, enforced instead of remembered.

    Rationale, from the rule itself: spoofing hides the exact blocking the
    check exists to find. A self-check that pretends to be Brave cannot tell
    you that Cloudflare is blocking your real agent.

    Found 2026-08-20 by the Stage 3f review gate, which caught a fresh
    violation in scripts/cf_purge.sh on its way into a commit -- and then, on
    hunting the class, found six more already tracked and NO guard anywhere.
    """

    def test_negative_control_the_token_set_can_hit(self):
        self.assertTrue(_SPOOF_TOKENS.search(
            "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Chrome/124.0.0.0"))
        self.assertIsNone(_SPOOF_TOKENS.search(
            "Orphograph-selfcheck/1.0 (+https://orphograph.com)"))

    def test_no_new_file_spoofs_a_browser(self):
        found = set()
        for path in _tracked_files():
            if path.suffix not in (".py", ".sh") or not path.is_file():
                continue
            rel = str(path.relative_to(ROOT))
            if rel in _INBOUND_FIXTURES or rel == str(
                    Path(__file__).resolve().relative_to(ROOT)):
                continue
            try:
                if _SPOOF_TOKENS.search(path.read_text(errors="ignore")):
                    found.add(rel)
            except OSError:
                continue
        new = sorted(found - _SPOOFED_UA_DEBT)
        self.assertEqual(
            new, [],
            "new browser-spoofing User-Agent(s) introduced -- this is a hard "
            "rule, and a spoofed agent hides the blocking the check exists to "
            "find: " + ", ".join(new))

    def test_the_debt_list_only_shrinks(self):
        """A fixed file must be REMOVED from the debt list, so the list stays
        an honest count of what is still wrong rather than decoration."""
        stale = set()
        for rel in _SPOOFED_UA_DEBT:
            f = ROOT / rel
            if not f.exists():
                continue
            if not _SPOOF_TOKENS.search(f.read_text(errors="ignore")):
                stale.add(rel)
        self.assertEqual(
            sorted(stale), [],
            "these files no longer spoof a UA but are still on the debt list; "
            "remove them so the list keeps meaning something: "
            + ", ".join(sorted(stale)))


if __name__ == "__main__":
    unittest.main()
