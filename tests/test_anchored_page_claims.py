#!/usr/bin/env python3
"""test_anchored_page_claims.py — a page may not promise a receipt it lacks.

DEFECT (2026-08-06 Stage 3e, claim-vs-live-data sweep)
------------------------------------------------------
Five /method pages carried a sentence of the form

    "the receipt identifier for this revision is recorded in the footer below"

and four of them had no receipt link anywhere. The fifth, /method/architecture,
had one — and it did not match the page:

    receipt sHGk_kgKi9YdlLBB  anchors 0411812b… (2026-05-18)
    the live page today hashes 02b0b72a…

21 commits touched that file after it was anchored, and the footer still read
"Publication receipt for this revision". The same pages also promised
"Subsequent revisions are anchored separately; their receipts are appended to
the same footer record" — no such appending has ever happened, and there is no
mechanism that would do it.

These are the pages whose entire stated purpose is establishing prior art on a
proof-of-existence product. They invite the reader to check, and the check
fails. That is the most expensive kind of false claim this codebase can carry.

There is a structural reason it can never be fully true as written: a per-page
receipt embedded in the page it attests changes that page, which invalidates
the attestation. The copy now says what actually holds.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METHOD = ROOT / "web" / "method"

RECEIPT_LINK = re.compile(r'href="/r/([A-Za-z0-9_-]{10,})"')

# Sentences that promise the reader a receipt id is present on this page.
PROMISES = (
    "receipt identifier for this revision is recorded in the footer",
    "receipt identifier for this revision is recorded in the footer below",
    "listed in the page footer below",
)

# A promise that no mechanism fulfils.
APPEND_PROMISE = "their receipts are appended to the same footer record"


def _pages():
    return sorted(p for p in METHOD.glob("*.html"))


class TestAnchoredPageClaims(unittest.TestCase):

    def test_a_page_promising_a_footer_receipt_must_have_one(self):
        offenders = []
        for p in _pages():
            text = p.read_text(errors="ignore")
            if any(s in text for s in PROMISES) and not RECEIPT_LINK.search(text):
                offenders.append(p.relative_to(ROOT).as_posix())
        self.assertEqual(
            offenders, [],
            "these pages tell the reader a receipt identifier is recorded in "
            "their footer, and no /r/<id> link appears on them. On a "
            "proof-of-existence product an unfulfilled invitation to verify is "
            f"a product defect: {offenders}")

    def test_no_page_promises_receipts_are_appended_on_revision(self):
        """Nothing appends receipts on revision. The daily repo anchor covers
        these files, but it produces one root for the whole tree, not a
        per-page receipt to append here."""
        offenders = [p.relative_to(ROOT).as_posix() for p in _pages()
                     if APPEND_PROMISE in p.read_text(errors="ignore")]
        self.assertEqual(offenders, [],
                         f"promise with no mechanism behind it: {offenders}")

    def test_a_presented_receipt_is_not_described_as_covering_this_revision(self):
        """A page's own receipt cannot cover the page's current bytes — adding
        the receipt id changes them. Any page showing a receipt must scope the
        claim to the revision it really attests."""
        offenders = []
        for p in _pages():
            text = p.read_text(errors="ignore")
            if not RECEIPT_LINK.search(text):
                continue
            if re.search(r"receipt for this revision", text, re.I):
                offenders.append(p.relative_to(ROOT).as_posix())
        self.assertEqual(
            offenders, [],
            "a receipt is presented as covering 'this revision'. It cannot: "
            "embedding the id changes the bytes it would have to attest. Scope "
            f"the claim to the revision actually anchored. {offenders}")


if __name__ == "__main__":
    unittest.main()


# Cycle 9 (vacuous-pass lens): the guard above matched three August wordings on
# web/method only, and passed while five method pages said "the receipt id is
# recorded once issuance completes" and /mcp, /continuity, /legal and
# /about-the-office said they were "anchored on issuance" with receipts
# "replaced accordingly". Nothing issues per-page receipts: the daily repo
# anchor (scripts/auto_anchor_repo.py) makes one private root for the whole
# tree. These are every wording of that promise, read on every page.
PER_PAGE_PROMISES = (
    "recorded once issuance completes",
    "receipt is replaced accordingly",
    "anchored on issuance",
    "anchored at issuance",
    "anchored at the time of issuance",
    "itself anchored on publication",
    "receipt identifier for this revision is recorded in the footer",
    "their receipts are appended",
    # Review of PR #284: the footer form of the same promise, on /continuity
    # and /faq.
    "listed in the footer",
    "listed in the page footer",
    "receipt for the latest revision",
    # Not "are themselves anchored": revisions ARE anchored, as part of the
    # daily repository root (/changelog, /docs/webhooks say only that).
)


def _plain(markup: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup)).lower()


def _per_page_promises_in(markup: str) -> list:
    text = _plain(markup)
    return [p for p in PER_PAGE_PROMISES if p in text]


class TestNoPagePromisesItsOwnReceipt(unittest.TestCase):

    def test_no_served_page_promises_a_per_page_receipt(self):
        offenders = {}
        for p in sorted((ROOT / "web").rglob("*.html")):
            rel = p.relative_to(ROOT).as_posix()
            if "/_mockups/" in rel or rel.endswith("index-legacy.html"):
                continue
            hits = _per_page_promises_in(p.read_text(errors="ignore"))
            if hits:
                offenders[rel] = hits
        self.assertEqual(offenders, {}, "no mechanism issues or replaces per-page receipts")

    def test_the_scan_catches_the_shipped_wordings(self):
        # Positive control: the sentences master carried on 2026-10-02.
        for old in (
            "Publication receipt for this revision: pending Bitcoin commitment "
            "(the receipt id is recorded once issuance completes).",
            "The page is published as defensive prior art and is itself anchored on issuance.",
            "This page is anchored at issuance. Updates to this page are themselves anchored, "
            "and the <br>receipt is replaced accordingly.",
            "This page is itself anchored at the time of issuance.",
            "The page is itself anchored on publication and is intended",
            "Revisions to this document are themselves anchored; the receipt for the "
            "latest revision is listed in the footer.",
            "Updates to this page are themselves anchored; the receipt is listed in the "
            "page footer of the latest revision.",
        ):
            self.assertTrue(_per_page_promises_in(old), old)

    def test_the_daily_anchor_these_pages_now_cite_is_real(self):
        # The new copy says the source is anchored daily as one private root;
        # that rests on the job's own definition. A page claim follows the code.
        src = (ROOT / "scripts" / "auto_anchor_repo.py").read_text()
        self.assertIn("daily folder anchor", src)
        self.assertIn('"private": private', src)
        # Review rounds 1-2 of PR #284: spelling checks ("web/", "*.html")
        # passed exclusions such as "*.htm*", "*html", "?eb/*" and
        # "**/*.html". Ask the matcher the job actually uses instead. Round 3:
        # reading the first EXCLUDE_PATTERNS literal missed a later `+=` and
        # patterns added at the call site, so record the exclude list the
        # job's build_manifest really passes.
        self.assertEqual(_pages_the_daily_anchor_drops(_exclude_the_job_passes(JOB)), [],
                         "the daily anchor would drop these pages, which say they are covered")

    def test_the_exclusion_check_catches_patterns_that_drop_pages(self):
        # Positive control: each of these drops at least one page.
        for pattern in ("*.html", "*.htm*", "*html", "?eb/*", "web/*", "**/*.html", "web*"):
            self.assertTrue(_pages_the_daily_anchor_drops((pattern,)), pattern)

    def test_the_recorded_exclude_list_sees_later_and_call_site_additions(self):
        # Positive control for the recording: planted copies of the job.
        import tempfile
        src = JOB.read_text()
        call = "exclude=list(EXCLUDE_PATTERNS)"
        self.assertEqual(src.count(call), 1)
        plants = {"augmented": src + '\nEXCLUDE_PATTERNS += ("*.html",)\n',
                  "call-site": src.replace(call, call + ' + ["web/*"]')}
        for name, planted in plants.items():
            with tempfile.TemporaryDirectory() as d:
                copy = Path(d) / "auto_anchor_repo.py"
                copy.write_text(planted)
                self.assertTrue(_pages_the_daily_anchor_drops(_exclude_the_job_passes(copy)), name)
        # Round 4: with no exclude passed, the job uses merkle.DEFAULT_EXCLUDE;
        # the recorder must check that list, not an empty one.
        import sys
        from unittest import mock
        sys.path.insert(0, str(ROOT / "server"))
        import merkle
        with tempfile.TemporaryDirectory() as d:
            copy = Path(d) / "auto_anchor_repo.py"
            copy.write_text(src.replace(call, "exclude=None"))
            with mock.patch.object(merkle, "DEFAULT_EXCLUDE", tuple(merkle.DEFAULT_EXCLUDE) + ("web/*",)):
                self.assertTrue(_pages_the_daily_anchor_drops(_exclude_the_job_passes(copy)), "default list")


JOB = ROOT / "scripts" / "auto_anchor_repo.py"


def _exclude_the_job_passes(job: Path) -> list:
    """Load the job and run its build_manifest with MerkleTree.from_folder
    replaced by a recorder, so nothing is walked or sent."""
    import importlib.util
    import sys
    from unittest import mock
    sys.path.insert(0, str(ROOT / "server"))
    spec = importlib.util.spec_from_file_location("_auto_anchor_repo_under_test", job)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class _Recorded(Exception):
        pass

    seen = {}

    def record(root, exclude=None, **kwargs):
        # from_folder applies DEFAULT_EXCLUDE when no list is passed.
        seen["exclude"] = list(mod.merkle.DEFAULT_EXCLUDE if exclude is None else exclude)
        raise _Recorded

    with mock.patch.object(mod.merkle.MerkleTree, "from_folder", side_effect=record):
        try:
            mod.build_manifest(ROOT)
        except _Recorded:
            pass
    assert "exclude" in seen, "build_manifest no longer calls MerkleTree.from_folder"
    return seen["exclude"]


def _pages_the_daily_anchor_drops(patterns) -> list:
    import sys
    sys.path.insert(0, str(ROOT / "server"))
    import merkle
    pages = [p.relative_to(ROOT).as_posix() for p in (ROOT / "web").rglob("*.html")
             if "/_mockups/" not in p.as_posix()]
    return [rel for rel in pages if merkle._matches_any(rel, list(patterns))]
