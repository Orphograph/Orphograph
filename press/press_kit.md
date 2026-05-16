# Press kit — Orphograph

**For journalists, podcasters, and trade-publication writers.**
Last updated 2026-05-13.

---

## One-paragraph product description

Orphograph is a browser-based service that anchors any file to the
Bitcoin blockchain in 10 seconds, producing a receipt that proves
the file existed at a specific moment in time. The file's
fingerprint is computed client-side (the bytes never reach the
server), then submitted to OpenTimestamps calendar servers that
batch many users' hashes into a single Bitcoin transaction roughly
once an hour. The resulting receipt can be verified by anyone — even
years later, even if Orphograph the company is gone — using an
open-source verifier on GitHub. Pricing: free for one anchor per
month, $7 for a ten-anchor pack, $5/month for unlimited anchors.

## One-sentence pitch

> "Orphograph is what photographers use when they need to prove a
> photo existed before any AI training run scraped it — Bitcoin
> as a witness, no upload, no lawyer."

## Three takes for three audiences

**For tech journalists:**
> A stdlib-only, ~6,000-LOC Python + vanilla JS service applying
> the open OpenTimestamps protocol to the consumer market — with
> three closed audits (forensic, security, payment+PII), 156
> automated tests, GDPR-functional data-rights endpoints, and an
> open-source verifier that outlives the company.

**For photography-trade press:**
> A $7 service that solves the "did I take this before AI got
> hold of it" problem photographers are quietly losing. Built by
> an anonymous solo founder after talking to photographers
> being accused of using AI for shots they actually took.

**For legal / compliance press:**
> Bitcoin-anchored proof-of-existence with an open-source verifier
> for the indie-creator market — explicitly NOT a qualified eIDAS
> timestamp, deliberately positioned below the regulated-TSA
> segment, with full GDPR data-subject endpoints from day one.

## Founder bio

**Founder:** Publishes under the pseudonym **Orphograph**.
Personal background details are kept off the record by request.
Available for written interviews via hello@orphograph.com.

(Contact details below.)

## What's interesting (story angles)

**Solo founder, stdlib-only, sub-6000-LOC.** The whole stack is
small enough to audit in an afternoon. There are zero third-party
runtime dependencies (no npm packages, no pip dependencies, no
SaaS vendor lock-in beyond Stripe + Resend + Fly). Trust through
reviewability.

**The AI-provenance market is real but nobody quite has the
right shape yet.** Truepic raised $37.2M for the enterprise
side, GPTZero hit $24M ARR on deepfake detection, but the
indie-photographer-doesn't-want-to-install-Python tier is
underserved. Originality.ai's $2.3M ARR shows there's real money
in adjacent text-AI verification — the photo equivalent is open.

**Open-source verifier as the trust mechanism.** The verifier is
MIT-licensed on GitHub. The pitch is "the receipt outlives the
company by design." This is the inverse of how most SaaS works
— companies want lock-in; we explicitly built unlock-in.

**Pricing tension: protocol is free, wrapper is paid.** The
underlying OpenTimestamps protocol is free if you'll use the
CLI. The startup thesis is that 99% of photographers won't, and
the wedge is UX + email-delivered receipts + a verifier they
don't have to maintain themselves. Question: how much of the
photographer market values that enough to pay? Real validation
risk, openly disclosed.

**Quantum hedge.** Receipts include both SHA-256 (the Bitcoin
anchor) and SHA-512 (a sibling witness). If SHA-256 is ever
broken, the SHA-512 still binds the file to the receipt. Public
commitment to forward-compatibility in case crypto-agility is
ever needed.

**The kill criteria are public.** The founder's audit document
states explicitly: if month-3 MRR is below $50, this becomes a
side project; if month-6 MRR is below $200, maintenance only;
if month-12 is below $1,000, kill or sell. Rare for a launching
startup to publish their own off-ramp.

## Available for interviews on

- The AI-provenance category and where Orphograph fits
- Indie-SaaS launch playbooks (stdlib-only, audit-first)
- Going public with kill criteria
- OpenTimestamps protocol explainer (10-min walkthrough or 60-min
  deep dive depending on slot)
- Bitcoin-anchored vs. eIDAS-qualified timestamping — when each
  matters

## Quotable paragraphs (attribute as "Orphograph founder")

> "The standard photographer's answer to 'when did you take this'
> is EXIF data plus cloud backups plus 'trust me.' That worked
> for thirty years. It does not work in 2026 when AI image
> generation makes every photo provisionally suspect. The only
> answer left is mathematics: anchor your file to a record system
> nobody can rewrite. That's all Orphograph is — a button that
> does that."

> "The verifier is open-source not because I'm a charity but
> because nobody will use this if it doesn't outlive the company.
> Photographers think about their archives in decade-long
> horizons. A receipt that requires our domain to be alive in
> 2036 is worthless. A receipt that's a JSON file plus a 100-line
> Python script is durable. We sell the convenience layer; the
> proof layer belongs to anyone who saves the files."

> "I'm explicit about what this is not. It's not court-admissible
> on its own. It's not an eIDAS qualified timestamp. It's not a
> legal opinion. It is strong cryptographic evidence that a file
> existed at a moment in time. For 95% of the actual disputes
> photographers face — 'no, I shot this in 2023, before your AI
> existed' — that is exactly the right shape of evidence."

> "There are 2.5 million working photographers globally. If 1%
> of them ever needs to prove the date of a photo, and 10% of
> those find $7 a reasonable price, that's 2,500 paying
> customers — a small but viable business for one solo founder.
> The math doesn't require everyone to care."

## Screenshots + assets

Available on request to **press@orphograph.com**:

- Hero shot of the landing page (dark glassmorphism, neon-green accent)
- Sample receipt JSON (real anchor, 5/5 calendars valid)
- The /status.html dashboard (operational transparency)
- The OSS verifier in action (terminal screenshot)
- The /r/<id> print view (the artifact a buyer takes home)
- Founder avatar (pseudonymous, no real-name photos)

All assets are CC0 with attribution requested.

## Color palette + typography (for art directors)

- Background: `#0c0e10` (deep charcoal)
- Accent: `#5bdc9b` (neon green; "Orphograph green")
- Warning: `#f7c548` (amber)
- Error: `#ef6b6b` (coral red)
- Text: `#e7e9ec` (warm white)
- Muted: `#8a9099` (cool grey)
- Typography: `-apple-system, BlinkMacSystemFont, Inter, Helvetica`
  at weight 300 (thin) for headings, 400 (regular) for body

## Contact

| Topic | Email | Response window |
|---|---|---|
| Press inquiries + interviews | press@orphograph.com | within 24h business days |
| Customer support | support@orphograph.com | within 48h |
| Security disclosures | security@orphograph.com | within 24h |
| Privacy / GDPR requests | privacy@orphograph.com | within 30 days |

**Twitter / X:** @orphograph
**GitHub:** https://github.com/orphograph
**LinkedIn:** founder reachable via the press@ inbox

---

## What we don't do

- Pay for press placement
- Offer exclusives in exchange for coverage
- Send unsolicited PR pitches
- Comment on competitors by name
- Speculate about other companies' security postures

## What we will do

- Walk you through the codebase live over a recorded Zoom (no on-camera founder)
- Provide a free account for evaluation purposes
- Respond to fact-check requests within 4 hours
- Give honest answers about traction (numbers public after 30
  days, including kill-criteria status)
- Send the DPA + sub-processors list + security questionnaire
  unprompted for B2B-shaped trade press

---

*Generated 2026-05-13 from `~/orphograph/press/press_kit.md`.
For the latest version, check the public site at
orphograph.com/press once the domain is live.*
