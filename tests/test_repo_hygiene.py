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

# Every language that can carry an outbound User-Agent. The first version of
# this guard scanned only .py and .sh, which made sdk-node/src/client.ts --
# holding the byte-identical spoof under the same disproven comment --
# STRUCTURALLY INVISIBLE, and the debt list was declared empty while it sat
# there. A deny list that cannot reach the violation reports CLEAN.
_CODE_SUFFIXES = {".py", ".sh", ".ts", ".js", ".mjs", ".cjs", ".bash", ".zsh"}


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
    # pytest's default `python_files` is `test_*.py` AND `*_test.py`, and this
    # repo has no pytest.ini/pyproject section narrowing it -- so globbing only
    # the first pattern would let a `foo_test.py` collision through, which is
    # the same defect wearing a different filename.
    for pattern in ("test_*.py", "*_test.py"):
        for path in ROOT.rglob(pattern):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            out.append(path)
    return sorted(set(out))


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
# Files still carrying a browser-spoofing User-Agent. EMPTY as of 2026-08-20.
#
# It held ten entries this morning, frozen on the assumption that changing an
# outbound UA might get a live job blocked. That assumption was never tested.
# Testing it took four minutes: every host these scripts talk to --
# orphograph.com, pypi.org, registry.npmjs.org, api.github.com and
# html.duckduckgo.com -- returns 200 to an honest self-identifying agent, and
# the CDN rule that started all of this blocks exactly one literal token,
# `Python-urllib`. A "founder decision" that dissolves under four minutes of
# measurement was never a decision; it was an unexamined premise.
#
# Keep it empty. The shrink-only test below makes a stale entry fail, and the
# growth test makes a new violation fail.
_SPOOFED_UA_DEBT: set[str] = set()

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
    """Return why this string impersonates a browser, or None if it does not.

    Applied to ONE string literal at a time (see `_string_groups`), so the
    self-identification window below can only be satisfied by the agent's own
    text -- nothing elsewhere in the file can vouch for it.
    """
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


def _string_groups(text: str, suffix: str) -> list[str]:
    """Every STRING LITERAL in the file, with adjacent literals concatenated.

    Replaces an earlier `_strip_comments` + whole-file scan, which had two
    defects found by review:

      * Prose about a past spoof re-tripped the guard, so documenting a fix
        looked like committing one -- the shape that teaches people to mute a
        guard forever.
      * Worse, the comment-stripped reconstruction could LAUNDER a real spoof.
        `_spoof_reason` forgives a bare `Mozilla/` when `compatible;` and
        `+http` appear within the next 220 characters. Removing comments (and
        the original spacing) could drag an unrelated honest UA -- there are
        three in server/ -- into that window, so a spoof added just above one
        of them scanned clean. Reproduced before fixing.

    Checking each literal on its own removes the window entirely: a UA is one
    string, and nothing outside it can vouch for it. Adjacent literals are
    joined first, because both Python implicit concatenation and `+` joining
    split one UA across several tokens, and checking the halves separately
    would flag an honest `"Mozilla/5.0 (compatible; " "Name/1.0; +https://x)"`.
    """
    groups: list[str] = []
    if suffix == ".py":
        import io, tokenize
        try:
            toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            return [text]        # unparseable -> scan everything, fail loud
        cur: list[str] = []
        JOINABLE = {tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT,
                    tokenize.INDENT, tokenize.DEDENT}
        for t in toks:
            if t.type == tokenize.STRING:
                try:
                    import ast as _ast
                    cur.append(str(_ast.literal_eval(t.string)))
                except Exception:
                    cur.append(t.string)
            elif t.type in JOINABLE or (t.type == tokenize.OP and t.string in "+("):
                continue                      # keeps a concatenation together
            else:
                if cur:
                    groups.append("".join(cur)); cur = []
        if cur:
            groups.append("".join(cur))
        return groups

    # .ts/.js/.mjs/.cjs/.sh: no tokenizer available, so walk the text once,
    # tracking whether we are inside a string or a comment. A regex over
    # quoted spans is NOT enough: a quoted PHRASE inside a `//` comment looks
    # exactly like a string literal, and this file's own fix comment -- which
    # quotes the disproven claim "only the leading Mozilla/5.0 appeases the
    # gateway" -- tripped the guard that way.
    out, buf, i, n = [], [], 0, len(text)
    line_comment = "//" if suffix != ".sh" else "#"
    while i < n:
        c = text[i]
        if c in "'\"`":
            quote, i, lit = c, i + 1, []
            while i < n and text[i] != quote:
                if text[i] == "\\" and i + 1 < n:
                    lit.append(text[i + 1]); i += 2; continue
                lit.append(text[i]); i += 1
            i += 1
            val = "".join(lit)
            # Join runs of literals separated only by whitespace or `+`, so a
            # UA split across concatenated pieces is judged whole.
            j = i
            while j < n and text[j] in " \t\r\n+":
                j += 1
            if j < n and text[j] in "'\"`":
                buf.append(val); i = j; continue
            buf.append(val); out.append("".join(buf)); buf = []
            continue
        if text.startswith(line_comment, i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if suffix != ".sh" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        i += 1
    if buf:
        out.append("".join(buf))
    return out


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

    def _file_flags(self, text: str, suffix: str = ".py") -> bool:
        return any(_spoof_reason(g) for g in _string_groups(text, suffix))

    def test_negative_control_prose_is_not_a_spoof_but_a_literal_is(self):
        commented = '# we used to send Mozilla/5.0 ... Chrome/124.0.0.0\nUA = "x/1.0"\n'
        live = 'UA = "Mozilla/5.0 (Macintosh) Chrome/124.0.0.0"\n'
        self.assertFalse(self._file_flags(commented),
                         "a comment explaining a past spoof was flagged as one")
        self.assertTrue(self._file_flags(live),
                        "a live spoof in a string literal was missed")

    def test_negative_control_an_honest_ua_cannot_launder_a_spoof(self):
        """The defect this replaced: `_spoof_reason` forgives a bare Mozilla/
        when `compatible;` and `+http` appear within 220 chars, and a
        whole-file scan let an UNRELATED honest UA further down the file
        supply them. server/ has three such honest agents, so the laundering
        path was reachable in real code."""
        laundered = (
            'SPOOF = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"\n'
            'HONEST = "Mozilla/5.0 (compatible; Mailer/0.1; +https://orphograph.com)"\n')
        self.assertTrue(
            self._file_flags(laundered),
            "an honest UA elsewhere in the file laundered a real spoof")
        # ...and the honest one alone still must not be flagged.
        self.assertFalse(self._file_flags(
            'HONEST = "Mozilla/5.0 (compatible; Mailer/0.1; +https://orphograph.com)"\n'))

    def test_negative_control_split_literals_are_judged_as_one_string(self):
        """A UA split across concatenated literals must be judged whole:
        the halves of an honest agent look bare on their own."""
        self.assertFalse(self._file_flags(
            'UA = ("Mozilla/5.0 (compatible; " "Name/1.0; +https://x.example)")\n'),
            "an honest UA split across literals was flagged")
        self.assertTrue(self._file_flags(
            'UA = ("Mozilla/5.0 (Macintosh) " "AppleWebKit/605.1.15")\n'),
            "a spoof split across literals was missed")

    def test_negative_control_typescript_is_scanned(self):
        """.ts was unscanned, which is how sdk-node/src/client.ts held the
        byte-identical spoof while the debt list was declared empty."""
        ts = ('const USER_AGENT =\n  "Mozilla/5.0 (Macintosh) " +\n'
              '  "AppleWebKit/605.1.15 Safari/605.1.15 orphograph-node/0.1.0";\n')
        self.assertTrue(self._file_flags(ts, ".ts"),
                        "a TypeScript spoof was not detected")
        self.assertIn(".ts", _CODE_SUFFIXES)

    def test_no_new_file_spoofs_a_browser(self):
        found = set()
        for path in _tracked_files():
            if path.suffix not in _CODE_SUFFIXES or not path.is_file():
                continue
            rel = str(path.relative_to(ROOT))
            if rel in _INBOUND_FIXTURES or rel == str(
                    Path(__file__).resolve().relative_to(ROOT)):
                continue
            try:
                if any(_spoof_reason(g) for g in _string_groups(
                        path.read_text(errors="ignore"), path.suffix)):
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
        an honest count of what is still wrong rather than decoration.

        NOTE, so nobody mistakes this for active protection: with
        `_SPOOFED_UA_DEBT` now EMPTY this loop has nothing to iterate and
        cannot fail. It is a latent guard that only becomes live if somebody
        re-adds an entry. The check that actually protects the rule today is
        `test_no_new_file_spoofs_a_browser` above, which fails on ANY spoof in
        any scanned language. Saying so beats letting a green tick imply a
        check that is not running.
        """
        stale = set()
        for rel in _SPOOFED_UA_DEBT:
            f = ROOT / rel
            if not f.exists():
                continue
            if not any(_spoof_reason(g) for g in _string_groups(
                    f.read_text(errors="ignore"), f.suffix)):
                stale.add(rel)
        self.assertEqual(
            sorted(stale), [],
            "these files no longer spoof a UA but are still on the debt list; "
            "remove them so the list keeps meaning something: "
            + ", ".join(sorted(stale)))


class TestNoDuplicateTestBasenames(unittest.TestCase):
    """Two test files with the same basename and no __init__.py collide.

    pytest's default import mode derives the module name from the basename
    when the directory is not a package, so the second import of `test_sdk`
    fails and takes the WHOLE collection down -- 1681 tests do not run because
    of two filenames. Found 2026-08-20: tests/test_sdk.py and
    sdk-python/tests/test_sdk.py had been colliding, invisible to CI because
    CI scopes itself to `pytest tests/`.
    """

    def test_negative_control_the_scan_finds_test_files(self):
        """A duplicate check over an empty list passes vacuously forever."""
        files = _all_test_files()
        self.assertGreater(len(files), 50,
                           "the test-file walk found almost nothing; the "
                           "duplicate check below would pass vacuously")

    def test_no_two_test_files_share_a_basename(self):
        from collections import defaultdict
        by_name = defaultdict(list)
        for path in _all_test_files():
            # A directory that IS a package gets a unique module path, so a
            # shared basename there is legitimate -- the guard's own docstring
            # names packages as the fix, and flagging them would punish it.
            if (path.parent / "__init__.py").exists():
                continue
            by_name[path.name].append(str(path.relative_to(ROOT)))
        dupes = {n: sorted(v) for n, v in by_name.items() if len(v) > 1}
        self.assertEqual(
            dupes, {},
            "these test files share a basename and will collide during "
            "collection unless their directories are packages, taking the "
            f"entire run down with them: {dupes}")


if __name__ == "__main__":
    unittest.main()
