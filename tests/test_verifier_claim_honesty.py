#!/usr/bin/env python3
"""test_verifier_claim_honesty.py — the published verifier must never be
described as checking Bitcoin.

WHY THIS EXISTS
---------------
2026-08-05: six surfaces were found claiming the MIT-licensed verifier
validates receipts "against the public Bitcoin chain". It does not — it
makes ZERO network calls and is a structural checker. Proven at the time by
forging a bundle (five 65-byte fake .ots files, a receipt backdated to 2020)
which the tool passed with "all receipts valid", exit 0.

2026-08-06: a follow-up sweep found NINE MORE surfaces carrying the same
claim, including three inside the shipped verifier bundle itself and the MCP
tool descriptor that AI agents read to decide whether the tool answers "is
this on Bitcoin?". The phrase-matching grep used the first time missed all
nine. That is the failure this file exists to prevent: fixing instances
instead of the class.

On a product whose promise is "you don't have to trust us", the instrument
of that independence must not overstate what it checks. A false claim here
is a product defect, not a copy nit.

The rule enforced: if a sentence names the published verifier AND makes a
chain-verification claim, it must also carry a disclaimer making clear the
verifier's check is structural/offline. Naming the OpenTimestamps client in
the same sentence is NOT sufficient — badge.html did exactly that while
still attributing the chain check to the verifier.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Surfaces a customer, an auditor, or an AI agent actually reads.
SCAN_DIRS = ("web", "server", "mcp", "dist/orphograph-verify", "capture")
SCAN_ROOT_FILES = ("README.md", "RELEASE_NOTES_V0.1.0.md", "DOCTRINE.md")
SCAN_SUFFIXES = (".html", ".py", ".md", ".txt", ".json", ".js")
SKIP_PARTS = ("node_modules", "/data/", "/tests/", ".min.js", "/sample-",
              "/receipts/")

# Names for the thing that does NOT touch the network.
VERIFIER = re.compile(
    r"\b(?:"
    r"MIT[- ]licen[cs]ed verifier|MIT verifier|open[- ]source verifier|"
    r"our verifier|standalone verifier|published (?:MIT )?verifier|"
    r"the verifier on GitHub|verify\.py"
    r")\b", re.I)

# Claims that the chain itself was consulted.
CHAIN_CLAIM = re.compile(
    r"(?:"
    r"against\s+(?:the\s+|public\s+|Bitcoin's\s+)*(?:Bitcoin|chain|blockchain)|"
    r"Bitcoin'?s\s+(?:immutable\s+)?(?:ledger|chain|blockchain)|"
    r"Bitcoin[- ](?:chain|path)\s+(?:check|verification)|"
    r"OTS.{0,3}Bitcoin\s+path|"
    r"verif\w+\s+it\s+against|"
    r"confirm\s+the\s+receipt\s+against"
    r")", re.I)

# Tokens that make the pairing honest: they scope the verifier's work to
# structure/offline, or explicitly deny the chain check.
DISCLAIMER = re.compile(
    r"(?:"
    r"structur(?:e|al)|offline|no network|makes no network|zero network|"
    r"does NOT|does not (?:consult|verify|check|touch)|neither does|"
    r"not a chain check|not with verify\.py|NOT with verify\.py"
    r")", re.I)

# Split on sentence enders and on block-level tag boundaries, so one bad
# clause in a paragraph cannot hide behind a good clause elsewhere.
SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|</(?:p|li|td|h[1-6])>|\n\n")


def _iter_files():
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in SCAN_SUFFIXES:
                continue
            s = str(p)
            if any(part in s for part in SKIP_PARTS):
                continue
            yield p
    for name in SCAN_ROOT_FILES:
        p = ROOT / name
        if p.is_file():
            yield p


def offending_sentences(text: str) -> list[str]:
    """Sentences that credit the published verifier with a chain check."""
    out = []
    for raw in SENTENCE_SPLIT.split(text):
        if not raw:
            continue
        s = " ".join(raw.split())
        if not s:
            continue
        if VERIFIER.search(s) and CHAIN_CLAIM.search(s) and not DISCLAIMER.search(s):
            out.append(s)
    return out


class TestVerifierClaimHonesty(unittest.TestCase):

    def test_no_surface_credits_the_verifier_with_a_chain_check(self):
        violations = []
        for p in _iter_files():
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            for s in offending_sentences(text):
                violations.append(f"{p.relative_to(ROOT)}: {s[:180]}")
        self.assertEqual(
            violations, [],
            "These surfaces credit the published verifier with verifying "
            "against Bitcoin. It makes no network calls — it is a structural "
            "checker. Either scope the claim (say the verifier checks "
            "structure offline) or attribute the chain step to the "
            "OpenTimestamps client:\n  " + "\n  ".join(violations))

    def test_the_detector_catches_every_string_it_was_built_from(self):
        """The 2026-08-05 grep missed nine real surfaces. A detector that
        cannot re-find the defects it was written for is worth nothing, so
        every original false string is replayed here."""
        originals = [
            # 2026-08-06 sweep — the nine the phrase-grep missed.
            "Use the open-source verifier at https://github.com/orphograph/"
            "verifier to check this receipt against Bitcoin's immutable ledger.",
            "You receive a receipt that anyone can verify against Bitcoin's "
            "chain using the open-source verifier on GitHub.",
            "For end-to-end verification against the chain, the receipt's .ots "
            "files are checked by the standalone verifier published at "
            "github.com/Orphograph/Orphograph",
            "an OpenTimestamps client or the MIT-licensed verifier may be used "
            "to confirm the receipt against Bitcoin directly, without "
            "contacting the office.",
            "Retrieves the receipt by identifier and verifies it against the "
            "recorded Bitcoin commitment using the same procedure as the "
            "published MIT verifier.",
            "the receipt may be verified against Bitcoin using the published "
            "MIT verifier or any OpenTimestamps client.",
            "the only load-bearing time bound remains the OTS-Bitcoin path - "
            "verify that with verify.py",
            "When --ots-dir is given, the Bitcoin-path check is delegated to "
            "verify.py",
            "Verify against Bitcoin's public chain using our open-source "
            "verifier or command-line tool",
        ]
        for original in originals:
            with self.subTest(original=original[:60]):
                self.assertTrue(
                    offending_sentences(original),
                    f"detector failed to flag a known-false claim: {original}")

    def test_honest_rewrites_are_not_flagged(self):
        """Precision guard: the fixed phrasings must pass, or the gate is
        noise and the next author will disable it."""
        honest = [
            "The published MIT verifier checks the receipt's structure "
            "offline; any OpenTimestamps client confirms the commitment "
            "against Bitcoin itself.",
            "the open-source verifier on GitHub checks the receipt's structure "
            "offline, and the OpenTimestamps client checks the commitment "
            "against Bitcoin's chain itself.",
            "Neither does the standalone verifier published at "
            "github.com/Orphograph/Orphograph - that tool checks structure "
            "offline and makes no network calls.",
            "Run the OpenTimestamps client (ots verify) to confirm the "
            "commitment against Bitcoin.",
        ]
        for s in honest:
            with self.subTest(s=s[:60]):
                self.assertEqual(offending_sentences(s), [],
                                 f"false positive on honest copy: {s}")

    def test_the_browser_side_verifier_makes_no_network_calls(self):
        """There are TWO verifiers named verify.py and they differ — a
        distinction the 2026-08-05 copy fix flattened.

        web/verify/verify.py (served at /verify/) is stdlib-only and cannot
        reach the network at all. dist/orphograph-verify/verify.py is the
        vendorable bundle: structural by default, but `--ots` deliberately
        shells out to the OpenTimestamps client, which DOES consult the
        chain. Copy must not claim the browser-side tool checks Bitcoin, and
        must not claim the bundle can never do so.
        """
        net = re.compile(
            r"^\s*(?:import|from)\s+"
            r"(?:socket|ssl|urllib|http|requests|httpx|ftplib|telnetlib)\b",
            re.M)
        p = ROOT / "web" / "verify" / "verify.py"
        self.assertTrue(p.is_file(), "the served verifier is missing")
        src = p.read_text()
        self.assertIsNone(
            net.search(src),
            "web/verify/verify.py imports a network module. It is documented "
            "as making zero network calls; if that changed, every surface "
            "describing it must change too.")
        self.assertNotIn(
            "subprocess", src,
            "web/verify/verify.py uses subprocess — it could shell out to "
            "`ots` and reach the network indirectly.")

    def test_the_bundle_reaches_the_chain_only_through_otscheck(self):
        """The bundle's one chain path must stay funnelled through the module
        that reports the client's own verdict — see otscheck.py's banner."""
        p = ROOT / "dist" / "orphograph-verify" / "verify.py"
        if not p.is_file():
            self.skipTest("no bundle verifier")
        src = p.read_text()
        self.assertIn("import otscheck", src)
        self.assertNotIn(
            '"ots", "verify"', src,
            "the bundle verifier invokes the ots client directly again "
            "instead of going through otscheck.chain_verdict")

    def test_no_surface_links_the_dead_verifier_repo(self):
        """github.com/orphograph/verifier 404s. It shipped inside every
        readable-JSON export as the customer's instruction for how to
        verify independently."""
        bad = []
        for p in _iter_files():
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            if re.search(r"github\.com/orphograph/verifier", text, re.I):
                bad.append(str(p.relative_to(ROOT)))
        self.assertEqual(bad, [],
                         f"dead repo URL (404) handed to customers: {bad}")


if __name__ == "__main__":
    unittest.main()
