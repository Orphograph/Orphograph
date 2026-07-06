"""Regression guards for the 2026-05-25 design-critique landing-page fixes.

Locks in the visible changes so a future template edit can't silently undo:

  1. The header nav badge default label is "status", not the half-built-looking
     "checking" — JS still overwrites to "live"/"degraded" on /api/health.
  2. The Writer Pack ($19 / 10 anchors) is a real tier card, not a footnote.
  3. All four canonical SKUs (Free, Writer Pack, Pack of Fifty, Standing Order)
     are present and priced per the founder-confirmed canon.
  4. The hero price-anchor strip surfaces every SKU before the long scroll.
  5. Tier CTAs are crypto-only while the card rails are unconfigured: the free
     tier reads "Begin"; every paid tier is a single "Pay with crypto" button
     routing to /pay/crypto.html. No live card hooks (data-checkout) remain.
     (Updated 2026-05-31: founder decision — crypto-only until Stripe is set.)
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
CRYPTO_JS = REPO / "web" / "pay" / "crypto.js"


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
    """Crypto-only CTA ladder (founder decision 2026-05-31, card rails
    unconfigured): the free tier reads "Begin"; every PAID tier is a single
    "Pay with crypto" button. The old card verbs (Buy / Subscribe) and the
    data-checkout card hooks are gone — these tests lock that in so a dead
    card button can't silently reappear before Stripe is configured."""

    def test_free_begins_and_paid_tiers_are_crypto(self):
        html = _html()
        m = re.search(r'id="tiers".*?</section>', html, re.DOTALL)
        self.assertIsNotNone(m)
        tiers_block = m.group(0)
        # Free tier invites without a charge.
        self.assertRegex(
            tiers_block, r'>\s*Begin\s*<',
            "Free-tier CTA verb 'Begin' missing from tiers block.",
        )
        # Each of the three paid tiers routes to the crypto checkout.
        pay_crypto = re.findall(r'>\s*Pay with crypto\s*<', tiers_block)
        self.assertEqual(
            len(pay_crypto), 3,
            "Expected exactly 3 'Pay with crypto' CTAs (one per paid tier), "
            f"found {len(pay_crypto)}.",
        )

    def test_no_dead_card_buttons(self):
        """No live card-checkout hook may sit on the homepage while the card
        rails are unconfigured — neither a data-checkout button nor the old
        Buy/Subscribe card verbs as CTAs (a dead button 'looks bad' and 503s)."""
        html = _html()
        m = re.search(r'id="tiers".*?</section>', html, re.DOTALL)
        self.assertIsNotNone(m)
        # Strip HTML comments first: the Pack-of-Fifty card-enable note mentions
        # data-checkout in prose — documentation, not an active button.
        tiers_block = re.sub(r"<!--.*?-->", "", m.group(0), flags=re.DOTALL)
        self.assertNotIn(
            "data-checkout", tiers_block,
            "A live card-checkout hook (data-checkout) reappeared on the "
            "homepage; tiers are crypto-only until Stripe is configured.",
        )
        for card_verb in (r'>\s*Buy\s*<', r'>\s*Subscribe\s*<'):
            self.assertNotRegex(
                tiers_block, card_verb,
                "An old card CTA verb (Buy/Subscribe) reappeared; "
                "tiers are crypto-only.",
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


class TestCheckoutWiringMatchesSKU(unittest.TestCase):
    """Money-critical SKU-wiring guard. The homepage is crypto-only as of
    2026-05-31 (card rails unconfigured), so no tier carries a card button
    today. This guard preserves the rule for whenever a card button returns:
    the card `data-checkout="pack"` plan charges the single STRIPE_PRICE_PACK
    and grants PACK_CREDIT_COUNT (=10) credits, so it may ONLY ever back a
    10-credit tier (Writer Pack). Wiring it to the 50-credit "Pack of Fifty"
    under-delivers (buyer pays for 50, receives 10) — the bug this prevents
    from ever shipping. Pack of Fifty needs its own $29 / 50-credit Stripe
    price (STRIPE_PRICE_PACK50) before it gets a card button. crypto.js
    separately honors the ?plan=pack_50 query param; see TestCryptoPlanDeepLink.
    """

    def _tier_chunk(self, name: str) -> str:
        # Strip HTML comments (not rendered/active) then split on the tier
        # delimiter so each chunk is exactly one tier card's markup.
        html = re.sub(r"<!--.*?-->", "", _html(), flags=re.DOTALL)
        chunks = re.split(r'<div class="tier', html)
        marker = f'<div class="t-name">{name}</div>'
        for c in chunks:
            if marker in c:
                return c
        self.fail(f"tier card {name!r} not found")

    def test_fifty_credit_tier_is_not_wired_to_the_ten_credit_pack(self):
        fifty = self._tier_chunk("Pack of Fifty")
        self.assertIn("50 anchor credits", fifty, "sanity: this is the 50-credit tier")
        self.assertNotIn(
            "plan=writer_pack",
            fifty,
            "Pack of Fifty must NOT deep-link to the writer_pack plan: that SKU "
            "grants 10 credits and would under-deliver the 50-pack.",
        )
        self.assertRegex(
            fifty,
            r'/pay/crypto(?:\.html)?\?plan=pack_50',
            "Pack of Fifty should deep-link to the dedicated pack_50 plan, "
            "which grants 50 credits.",
        )

    def test_writer_pack_routes_to_crypto(self):
        """The 10-credit Writer Pack is crypto-only while the card rails are
        unconfigured: it routes to the crypto checkout with plan=writer_pack
        and carries NO card hook. (If a card button is ever re-added it must
        map to the single `pack` SKU — 10 credits / $19 — never the 50-pack.)"""
        writer = self._tier_chunk("Writer Pack")
        self.assertIn("10 anchor credits", writer)
        self.assertNotIn(
            'data-checkout="pack"', writer,
            "Writer Pack is crypto-only until Stripe is configured.",
        )
        self.assertRegex(
            writer, r'href="/pay/crypto(?:\.html)?\?plan=writer_pack"',
            "Writer Pack CTA should route to the crypto checkout "
            "(plan=writer_pack).",
        )


class TestCryptoPlanDeepLink(unittest.TestCase):
    def test_crypto_page_honors_pack50_query_param(self):
        """The landing links to /pay/crypto.html?plan=pack_50 for the 50-pack.
        The crypto page must not silently default that buyer back to the
        10-credit Writer Pack."""
        js = CRYPTO_JS.read_text(encoding="utf-8")
        self.assertIn('params.get("plan")', js)
        self.assertIn('plan === "pack_50" ? "pack_50" : "writer_pack"', js)


class TestCspInlineConsolidation(unittest.TestCase):
    """The live site serves `style-src 'self'` with NO 'unsafe-inline', so every
    inline <style> block and inline `style=` attribute is blocked by the browser
    and silently dropped. All landing-page CSS must live in the external
    /index.css (which IS 'self'-allowed). This guards both the post-redesign
    markup invariants and the CSP hygiene (no inline CSS may creep back in)."""

    def test_lockup_wordmark_present(self):
        self.assertIn('class="lockup-wordmark">Orphograph', _html())

    def test_lockup_tagline_present(self):
        self.assertIn("Proof &middot; Permanence", _html())

    def test_old_lockup_taglines_gone(self):
        html = _html()
        self.assertNotIn("Strategy", html)
        self.assertNotIn("Stewardship", html)

    def test_raster_lockup_references_gone(self):
        html = _html()
        self.assertNotIn("lockup.png", html)
        self.assertNotIn("lockup-text-img", html)

    def test_assurances_row_replaces_chips_and_lede(self):
        html = _html()
        self.assertIn('class="assurances"', html)
        self.assertEqual(html.count('class="assure"'), 3)
        self.assertNotIn('class="lede"', html)
        self.assertNotIn('class="chips"', html)

    def test_no_inline_style_block(self):
        """CSP hygiene: no <style> block may remain — style-src 'self' drops it."""
        self.assertNotIn("<style>", _html())

    def test_no_inline_style_attribute(self):
        """CSP hygiene: no inline `style=` attribute may remain — it is blocked
        by style-src 'self' (no 'unsafe-inline')."""
        self.assertNotIn(" style=", _html())


if __name__ == "__main__":
    unittest.main()
