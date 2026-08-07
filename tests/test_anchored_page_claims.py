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
