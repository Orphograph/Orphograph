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


# Directories that are copies, caches, or vendored trees. Walking them would
# flag the same file many times over and report worktree copies as new.
_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules",
              ".pytest_cache", ".mypy_cache", ".ruff_cache", "worktrees",
              "site-packages"}


def _all_test_files() -> list[Path]:
    """Every test_*.py pytest could COLLECT, found the way pytest finds them.

    Deliberately NOT `git ls-files`. pytest walks the filesystem and does not
    consult .gitignore, so an index-based scan is blind to exactly the files
    most likely to carry this defect -- untracked scratch scripts and
    gitignored working directories. The first version of this guard made that
    mistake and reported the class closed while
    `outreach/test_discovery_log.py` (gitignored) still killed collection.
    """
    out = []
    for path in ROOT.rglob("test_*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    return out


class TestNoModuleScopeExitInTestFiles(unittest.TestCase):
    """A test file that exits at import kills the collector, not just itself."""

    @staticmethod
    def _is_exit_call(node: ast.AST) -> bool:
        """True only for a real interpreter exit.

        `ast.Attribute` with attr == "exit" is NOT enough: `stack.exit()`,
        `ctx.exit()` and any other object method named exit would match, and a
        guard that fires on those gets muted within a week. Require the actual
        exit builtins and the sys/os spellings.
        """
        if not isinstance(node, ast.Call):
            return False
        f = node.func
        if isinstance(f, ast.Name):
            return f.id in ("exit", "quit")
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            return (f.value.id, f.attr) in {("sys", "exit"), ("os", "_exit"),
                                            ("os", "abort")}
        return False

    @classmethod
    def _is_main_guard(cls, node: ast.AST) -> bool:
        """`if __name__ == "__main__":` and its `!=`/tuple variants."""
        if not isinstance(node, ast.If):
            return False
        return any(isinstance(n, ast.Name) and n.id == "__name__"
                   for n in ast.walk(node.test))

    @classmethod
    def _module_scope_exits(cls, source: str) -> list[int]:
        """Line numbers of exits that fire when the module is merely IMPORTED.

        Two bugs the first version had, both caught by review:
          * it used ast.walk, which descends INTO FunctionDef/ClassDef, so a
            perfectly fine `def main(): sys.exit(2)` inside a test file was
            flagged;
          * it skipped every top-level `ast.If`, so the commoner form --
            `if not os.environ.get("X"): sys.exit(0)` -- passed silently.
        This walks statements, refuses to enter function and class bodies, and
        skips only genuine __name__ guards.
        """
        hits: list[int] = []

        def visit(node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef, ast.Lambda)):
                    continue          # runs on call, not on import
                if cls._is_main_guard(child):
                    continue          # runs on execution, not on import
                if cls._is_exit_call(child):
                    hits.append(child.lineno)
                visit(child)

        visit(ast.parse(source))
        return sorted(set(hits))

    def test_negative_control_the_detector_can_hit(self):
        """A scan that has never been shown to fire is not a passing scan.

        Each case below corresponds to a way this detector has actually been
        wrong, so the control fails if any of them regresses.
        """
        must_flag = {
            "bare call": "import sys\nprint('x')\nsys.exit(1)\n",
            # The commoner landmine; the first version MISSED this entirely.
            "conditional skip": ("import os, sys\n"
                                 "if not os.environ.get('X'):\n"
                                 "    sys.exit(0)\n"),
            "try/except tail": ("import sys\ntry:\n    pass\n"
                                "except Exception:\n    sys.exit(3)\n"),
        }
        must_not_flag = {
            "__name__ guard": ("import sys\nif __name__ == '__main__':\n"
                               "    sys.exit(0)\n"),
            # The first version FALSE-POSITIVED on both of these.
            "inside a function": "import sys\ndef main():\n    sys.exit(3)\n",
            "method named exit": "def f(stack):\n    stack.exit()\n",
            "inside a class": ("import sys\nclass C:\n    def go(self):\n"
                               "        sys.exit(1)\n"),
        }
        for label, src in must_flag.items():
            self.assertTrue(self._module_scope_exits(src),
                            f"detector failed to flag: {label}")
        for label, src in must_not_flag.items():
            self.assertFalse(self._module_scope_exits(src),
                             f"detector false-positived on: {label}")

    def test_no_test_file_exits_during_collection(self):
        offenders = []
        for path in _all_test_files():
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
    # :231 ASSERTS a spoofed UA is present (`"Mozilla/5.0" in ua and
    # "Chrome/" in ua`) -- a test that PINS the violation of a hard rule.
    # Reversing it is part of the same founder decision as the nine above.
    "tests/test_usb_airgap.py",
}

# --- Is a User-Agent string impersonating a browser? --------------------
#
# Two shapes must be told apart, and a detector that cannot do it is useless
# in both directions:
#
#   IMPERSONATION   claims a real browser product/engine ("Chrome/124.0.0.0",
#                   "AppleWebKit/605.1.15"), OR is a bare "Mozilla/5.0 (OS
#                   platform string)" that identifies nothing at all.
#   HONEST BOT      "Mozilla/5.0 (compatible; OrphographMailer/0.1;
#                   +https://orphograph.com)" -- the shape Googlebot uses,
#                   where Mozilla/5.0 is a vestigial compatibility token and
#                   the agent still says who it is and how to reach it.
#
# The first version of this guard enumerated product tokens only, which let a
# bare `Mozilla/5.0 (Windows NT 10.0; Win64; x64)` -- a browser identity with
# no self-identification anywhere in it -- pass clean. Caught by review.
_BROWSER_PRODUCT = re.compile(
    r"Chrome/[0-9]|Safari/[0-9]|Brave/[0-9]|Firefox/[0-9]|Edg/[0-9]"
    r"|AppleWebKit/[0-9]|Trident/[0-9]|OPR/[0-9]")
_MOZILLA = re.compile(r"Mozilla/[0-9]")
# Self-identification must appear near the Mozilla token, not anywhere in the
# file -- otherwise one honest UA elsewhere would launder every spoof.
_SELF_ID_WINDOW = 220


def _spoof_reason(text: str) -> str | None:
    """Return why `text` impersonates a browser, or None if it does not."""
    m = _BROWSER_PRODUCT.search(text)
    if m:
        return f"claims a browser product: {m.group(0)}"
    for m in _MOZILLA.finditer(text):
        window = text[m.start():m.start() + _SELF_ID_WINDOW]
        if "compatible;" in window and "+http" in window:
            continue          # honest bot convention
        return ("bare Mozilla/ with no `(compatible; <name>; +https://…)` "
                "self-identification")
    return None


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

    def test_negative_control_the_detector_separates_the_two_shapes(self):
        spoofs = [
            "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Chrome/124.0.0.0",
            # Bare browser identity, no self-ID. The first version MISSED this.
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        ]
        honest = [
            "Orphograph-selfcheck/1.0 (+https://orphograph.com)",
            "Mozilla/5.0 (compatible; OrphographMailer/0.1; +https://orphograph.com)",
            "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        ]
        for ua in spoofs:
            self.assertIsNotNone(_spoof_reason(ua), f"missed a spoof: {ua}")
        for ua in honest:
            self.assertIsNone(_spoof_reason(ua), f"false positive on: {ua}")

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
                if _spoof_reason(path.read_text(errors="ignore")):
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
            if not _spoof_reason(f.read_text(errors="ignore")):
                stale.add(rel)
        self.assertEqual(
            sorted(stale), [],
            "these files no longer spoof a UA but are still on the debt list; "
            "remove them so the list keeps meaning something: "
            + ", ".join(sorted(stale)))


if __name__ == "__main__":
    unittest.main()
