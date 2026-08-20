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


if __name__ == "__main__":
    unittest.main()
