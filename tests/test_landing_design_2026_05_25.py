"""Regression guards for the 2026-05-25 design-critique landing-page fixes.

Locks in the visible changes so a future template edit can't silently undo:

  1. The header nav badge default label is "status", not the half-built-looking
     "checking" — JS still overwrites to "live"/"degraded" on /api/health.
  2. The Writer Pack ($19 / 10 anchors) is a real tier card, not a footnote.
  3. All four canonical SKUs (Free, Writer Pack, Pack of Fifty, Standing Order)
     are present and priced per the founder-confirmed canon.
  4. The hero price-anchor strip surfaces every SKU before the long scroll.
  5. Tier CTAs use one primary verb per tier (Begin / Buy / Buy / Subscribe)
     and crypto is a secondary inline link, not a co-equal button.
  6. The "office anchors only the fingerprint" boilerplate in the examples
     block appears at most twice — the prior 5-restatement repetition is gone.
  7. The pricing tier name "First Party" (jargon) is gone in favor of "Free".

If a future copy/layout pass needs to change any of these on purpose, update
this file in the same commit so the intent is recorded.
"""
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
INDEX_HTML = REPO / "web" / "index.html"


def _html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


class TestNavBadgeLabel(unittest.TestCase):
    def test_initial_label_is_status_not_checking(self):
        """The visible label must default to 'status' — never the
        half-built-looking 'checking' word."""
        html = _html()
        # The element looks like: <span class="label">status</span>
        m = re.search(r'<span class="label">([^<]+)</span>', html)
        self.assertIsNotNone(m, "live-status-badge label span missing")
        self.assertEqual(
            m.group(1).strip().lower(),
            "status",
            "Initial badge label regressed to a placeholder-looking value; "
            "JS still updates it to live/degraded on /api/health.",
        )

    def test_no_visible_checking_word_in_nav(self):
        """The literal text 'checking' must not appear as visible nav copy.
        (data-state='checking' is fine — that's a JS-internal attribute.)"""
        html = _html()
        # Pull only the <header class="nav"> block.
        m = re.search(r'<header class="nav">(.*?)</header>', html, re.DOTALL)
        self.assertIsNotNone(m, "header.nav block missing")
        nav = m.group(1)
        # Strip the data-state attribute before searching for visible text.
        visible = re.sub(r'data-state="[^"]*"', "", nav)
        self.assertNotRegex(
            visible,
            r">\s*checking\s*<",
            "The word 'checking' should not appear as visible nav text.",
        )


class TestFourTierPricing(unittest.TestCase):
    def test_all_four_canonical_skus_present(self):
        html = _html()
        for sku in ("Free", "Writer Pack", "Pack of Fifty", "Standing Order"):
            self.assertIn(
                f">{sku}<",
                html,
                f"Canonical SKU {sku!r} missing from the tiers section.",
            )

    def test_canonical_prices_displayed(self):
        """Prices must match founder canon (2026-05-25):
        Free $0, Writer Pack $19, Pack of Fifty $29, Standing Order $9/month."""
        html = _html()
        # Free
        self.assertRegex(html, r'class="t-name">Free</div>\s*<div class="t-price">\$0')
        # Writer Pack
        self.assertRegex(
            html,
            r'class="t-name">Writer Pack</div>\s*<div class="t-price">\$19',
        )
        # Pack of Fifty
        self.assertRegex(
            html,
            r'class="t-name">Pack of Fifty</div>\s*<div class="t-price">\$29',
        )
        # Standing Order
        self.assertRegex(
            html,
            r'class="t-name">Standing Order</div>\s*<div class="t-price">\$9',
        )

    def test_first_party_jargon_removed(self):
        """'First Party' was unintelligible jargon as a tier name. Should
        be gone in favor of plain 'Free'."""
        html = _html()
        # Pull just the tiers block (id="tiers") and look there — the phrase
        # may legitimately appear elsewhere in the doc (e.g. about the office).
        m = re.search(r'id="tiers".*?</section>', html, re.DOTALL)
        self.assertIsNotNone(m)
        tiers_block = m.group(0)
        self.assertNotIn(
            "First Party",
            tiers_block,
            "Tier name 'First Party' should be 'Free' — jargon removed.",
        )


class TestPriceAnchorStrip(unittest.TestCase):
    def test_strip_renders_all_skus_inline(self):
        html = _html()
        m = re.search(r'<div class="price-anchor".*?</div>', html, re.DOTALL)
        self.assertIsNotNone(m, "Price-anchor strip missing.")
        strip = m.group(0)
        for sku in ("Free", "Writer Pack", "Pack of Fifty", "Standing Order"):
            self.assertIn(sku, strip, f"{sku!r} not in price-anchor strip.")


class TestSinglePrimaryCTA(unittest.TestCase):
    """Each paid tier should have ONE primary .cta button, not two
    competing ones (Pay-by-card / Pay-with-crypto side by side)."""

    def _extract_tier_blocks(self) -> list[str]:
        html = _html()
        # Pull each <div class="tier"...> ... </div> at depth 1 inside .tiers.
        # Simple greedy split: every `<div class="tier` opens a tier; the next
        # one closes the prior. Last one closes at `</div>\s*</div>` (the .tiers).
        # Good enough for this regression file.
        parts = re.split(r'<div class="tier(?: featured)?">', html)
        # parts[0] is everything before the first tier
        return parts[1:5]  # exactly 4 tiers expected

    def test_each_tier_has_one_primary_cta(self):
        tiers = self._extract_tier_blocks()
        self.assertEqual(len(tiers), 4, "Expected exactly 4 tier cards.")
        for i, block in enumerate(tiers):
            # Count `.cta` link elements that are NOT also `.cta-alt`
            # (the legacy outline-secondary class) and NOT `.cta-alt-link`
            # (the inline secondary link inside cta-fineprint).
            primary_ctas = re.findall(
                r'<a [^>]*class="cta"(?![^>]*cta-alt)',
                block,
            )
            self.assertEqual(
                len(primary_ctas),
                1,
                f"Tier #{i} should have exactly one primary .cta button, "
                f"found {len(primary_ctas)}. Block head: {block[:120]!r}",
            )


class TestCTAVerbs(unittest.TestCase):
    """Verbs ladder: Begin / Buy / Buy / Subscribe.
    Reads as a progression, not a wall of repeated 'Pay'."""

    def test_each_canonical_verb_appears(self):
        html = _html()
        m = re.search(r'id="tiers".*?</section>', html, re.DOTALL)
        self.assertIsNotNone(m)
        tiers_block = m.group(0)
        for verb in ("Begin", "Buy", "Subscribe"):
            self.assertRegex(
                tiers_block,
                rf'>\s*{verb}\s*<',
                f"Tier CTA verb {verb!r} missing from tiers block.",
            )


class TestExamplesRepetitionTrimmed(unittest.TestCase):
    """The pre-fix copy repeated 'the office anchors only the fingerprint;
    the file itself never leaves your device' five times across the
    examples block. Trim to at most two occurrences."""

    def test_office_boilerplate_appears_at_most_twice(self):
        html = _html()
        m = re.search(r'<details class="examples">.*?</details>', html, re.DOTALL)
        self.assertIsNotNone(m)
        examples = m.group(0)
        count = examples.count("the office anchors only the fingerprint")
        self.assertLessEqual(
            count,
            2,
            f"Privacy boilerplate repeated {count}× in examples; "
            "should be at most 2× (was 5× pre-fix).",
        )


if __name__ == "__main__":
    unittest.main()
