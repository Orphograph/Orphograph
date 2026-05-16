# Landing copy A/B variants

The current landing leads with **"Prove your art existed before the
bots saw it."** — the photographer-fear framing. If the first 7 days
of traffic show conversion below 1.5% (the audit's conservative
floor), swap to one of the variants below.

Each variant is the H1 + sub-head pair that goes in `web/index.html`
inside `<section class="hero">`. Everything else on the page stays.

The instructions at the end show the founder how to swap one for
another in <60 seconds without breaking anything.

---

## Current (V0) — photographer-fear

```html
<h1>Prove your art existed before the bots saw it.</h1>
<p class="lede">
  Anchor any file to Bitcoin in 10 seconds. SHA-256 is computed in your browser —
  your image never leaves your machine. You get a receipt anyone can verify against
  Bitcoin's chain, with or without us.
</p>
```

**Persona:** photographers actively worried about AI training scraping
their portfolio.
**Conversion theory:** fear motivates faster than aspiration. Names the
specific antagonist ("bots"). Activates the AI-disputes worry already
in the audience's head.
**When to use:** as the default. Most defensible if photographer-fear
is the modal buyer state.

---

## Variant A — photographer-pride

```html
<h1>Lock the date to your work — forever, on Bitcoin's chain.</h1>
<p class="lede">
  Every photo you make becomes harder to dispute the moment you anchor it.
  Hashing happens in your browser (no upload), and the receipt verifies
  against the public Bitcoin chain — with or without us.
</p>
```

**Persona:** photographers who think of themselves as professionals
defending their archive, not victims defending against AI.
**Conversion theory:** pride converts higher in well-established
identity groups. Names a positive action ("lock the date") instead of
a defensive one ("prove before bots").
**When to use:** if A/B data shows photographers older than 35 or who
identify as "pro" are clicking through more.

---

## Variant B — crypto-curious tinkerer

```html
<h1>Anchor any file to Bitcoin. No wallet, no gas, no upload.</h1>
<p class="lede">
  SHA-256 is computed in your browser, then submitted to 5 OpenTimestamps
  calendars that batch into a single Bitcoin transaction. Your receipt
  verifies forever against the public chain. $7 buys you 10 anchors.
</p>
```

**Persona:** people on HN, in /r/Bitcoin, on indie-hacker Twitter — who
already understand BTC + OTS and just want the price + the wedge.
**Conversion theory:** technical specificity converts crypto-curious
audiences faster than emotional appeal. Names the protocol explicitly.
**When to use:** for Show HN traffic, /r/Bitcoin community shares, and
the GitHub README front-door. Less effective for cold photographer
traffic.

---

## Variant C — single-use utility (notary replacement framing)

```html
<h1>Time-stamp any file. For when "trust me" isn't enough.</h1>
<p class="lede">
  Anchor a file's SHA-256 to Bitcoin via OpenTimestamps and get a
  cryptographic receipt that proves it existed at a specific moment
  in time. Forever. Verifiable by anyone with the open-source verifier
  on GitHub.
</p>
```

**Persona:** broad utility audience — IP attorneys, journalists
protecting sources (without anonymity needs), independent
contractors documenting deliverables, will-and-testament uses.
**Conversion theory:** broader category = broader audience but
shallower hook. Useful as a fallback if photographer-targeting
underperforms.
**When to use:** if the first 30 days suggest photographers don't
convert and the actual buyers are coming from B2B utility cases.
Most likely candidate for "Path D — pivot away from photographers"
in the audit's segment match table.

---

## How to swap a variant in (60 seconds)

1. Open `web/index.html` in any editor.
2. Find `<section class="hero">` near the top of `<main>`.
3. Replace the `<h1>` and `<p class="lede">` blocks with the variant
   you want to test.
4. **Do not change** the trust strip, drop zone, status div, or any
   element ID. Only the headline + sub-head changes.
5. Save. Restart the server (or wait for the next deploy on prod).
6. The change is reversible — keep this file as the master.

## What to measure after swapping

The first-party analytics endpoint records `page_view` and the
following conversion events: `buy_pack_click`, `buy_personal_click`,
`anchor_done`, `verify_sample_click`, `signin_request`.

After a swap, watch:

- Bounce rate proxy: `page_view` count vs `anchor_start` count over
  the next 48 hours.
- Conversion to purchase: `buy_pack_click` rate per `page_view`.
- Activation: `anchor_done` rate per `page_view`.

Don't switch variants more than once a week — too noisy. If a
variant is clearly worse after 200+ `page_view` events, switch back.

## What we deliberately don't A/B test

- **Pricing.** Stripe coupon code (`LAUNCH20`) is the launch lever.
  Price-point experiments wait until 100+ paying customers exist.
- **The "Open source verifier" trust badge.** Always on. Removing
  this would undermine the entire positioning.
- **"Files never leave your browser" claim.** Always on. Privacy
  is structural, not optional.
- **"$7 for 10 anchors" framing.** This is the headline price; do
  not bury it in tier comparisons.

## What's NOT in this file (and why)

- **Spanish-language variant.** Bilingual copy is supported.
  A Spanish landing is on the roadmap but not until first 100
  customers. Don't translate the variants above without first
  proving the English ones convert.
- **B2B / agency variants.** Wait for inbound from agencies. The
  enterprise tier copy lives in `deploy/compliance/` (DPA,
  questionnaire, sub-processor list) — different surface, different
  audience, different page.
- **Capture-time provenance teaser.** Roadmapped but not built.
  Don't promise Orphograph Capture in a headline until it's beta.
