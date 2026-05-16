# Orphograph Capture — $19/mo Creator Tier Ship Plan

> **Target:** Creator tier live and accepting paid subscriptions in 4 weeks.
> **Status today (2026-05-14):** Capture daemon works headless (stdlib Python),
> launchd plist exists, README written. Everything below is the gap from
> "works on the founder's laptop" to "a stranger pays $19, downloads a .dmg,
> double-clicks an installer, and is anchoring photos by dinner."
>
> **Doctrine:** ship the smallest viable Creator tier that justifies $19. If
> any block in this plan slips, fall back to the v0.1 escape hatches at the
> bottom of this doc — never delay launch waiting on a polish item.

---

## Table of contents

1. [Why $19, why now, why these features](#why-19-why-now-why-these-features)
2. [Gap analysis — what exists vs what ships](#gap-analysis)
3. [Week-by-week schedule](#week-by-week-schedule)
4. [Founder action items (account-gated work)](#founder-action-items)
5. [Risks + mitigations](#risks--mitigations)
6. [Pricing rationale](#pricing-rationale)
7. [Marketing copy (exact landing-page block)](#marketing-copy)
8. [Internal docs: Creator vs Personal](#internal-docs-creator-vs-personal)
9. [v0.1 escape hatches if a week slips](#v01-escape-hatches)
10. [Definition of done](#definition-of-done)

---

## Why $19, why now, why these features

### Why $19
- Personal tier is $5/mo, Pack is $7 one-shot. Creator at $19 is the only tier
  that justifies building a desktop app + signed installer + auto-update
  infrastructure. Anything below $15 doesn't pay back the Apple Developer
  fee + the engineering hours on Sparkle + the tray-icon SwiftUI shell.
- $19 sits in the gap between consumer backup (Backblaze $8/mo) and
  pro-portfolio SaaS (SmugMug Pro $45/mo). Creators who already pay one of
  those will read $19 as "obviously cheaper than my portfolio host, obviously
  more serious than my backup tool."
- Per Y3 valuation row: 200 Creator subs × $19 = **$46k MRR**. That's the
  ceiling on this tier; if it caps at 50 subs the answer is to add a B2B
  tier above it, not to discount Creator below $19.

### Why now (not Q4 2026)
- The capture daemon is already written. The big-cost item (a working,
  privacy-clean watcher) is done. The remaining 4 weeks are packaging,
  installer, UI shell, server validation — not new engineering.
- "Capture-time provenance" is the only differentiator vs OpenTimestamps
  (free) and OriginStamp (enterprise). Every week we delay, the more likely
  someone else ships the obvious capture-time wrapper around OTS.
- Founder has Show HN attention from the Pack launch. The Creator tier is
  the natural "what's next" post — and HN attention has a half-life of weeks,
  not quarters.

### Why these ten features and not others
- Items 1-7 are *blocking* — without them, a stranger cannot install + pay.
- Items 8-10 (docs, copy, telemetry) are *post-install* essentials — without
  them, support cost per subscriber explodes and the founder personally
  handles every ticket.
- Explicitly NOT in v1: Windows installer, Linux installer, Lightroom-plugin
  bundling inside the .dmg, iOS Boroscope-sibling, team/white-label features.
  All deferred to v0.2+.

---

## Gap analysis

### What exists today (verified on disk 2026-05-14)
- `~/orphograph/capture/orphograph_capture.py` — 307-line stdlib daemon,
  watches folders, hashes locally, POSTs to `/api/anchor`, writes
  `.orpho.json` sidecars. Tested foreground + via launchd.
- `~/orphograph/capture/com.orphograph.capture.plist` — launchd config,
  needs `CHANGEME` substitution + API key paste.
- `~/orphograph/capture/README.md` — 110-line install + privacy + verify doc.
- `/api/anchor` endpoint live on orphograph.com — same engine that powers
  the website drop-zone.

### What's missing for $19/mo (the ten gaps)

| # | Gap | Effort | Owner | Blocking? |
|---|---|---|---|---|
| 1 | Signed installer (.pkg) — Developer ID code-sign + notarize | 1-2 days | Claude | Yes |
| 2 | Auto-update — Sparkle framework | 3 days | Claude | No (defer to v0.2) |
| 3 | Menu-bar UI — SwiftUI wrapper around daemon | 5 days | Claude | No (defer to v0.2) |
| 4 | Branded badge generator — `/api/badge/<id>.svg` | 2 days | Claude | Yes |
| 5 | Stripe Creator-tier price live ($19/mo) | 1 day | Founder + Claude | Yes |
| 6 | API-key Creator-tier validation on /api/anchor | 1 day | Claude | Yes |
| 7 | `/capture` landing page (.dmg download + walkthrough) | 1 day | Claude | Yes |
| 8 | Internal docs (Creator vs Personal matrix) | 0.5 day | Claude | Yes |
| 9 | Marketing copy (~100-word block on landing) | 0.5 day | Claude | Yes |
| 10 | Telemetry (privacy-preserving, opt-in) | 1 day | Claude | No (defer to v0.2 if slipping) |

**Total blocking effort:** ~7 engineering days + founder account work.
**Total with non-blocking items:** ~17 days.
**Buffer:** 4 weeks × 5 working days = 20 days. Margin is 3 days. Tight but
real.

---

## Week-by-week schedule

### Week 1 — Foundation (signing + first installer)

**Goal by Friday W1:** A signed .pkg installer that a stranger can
double-click on a fresh macOS install. No UI yet, no auto-update yet.

**Days 1-2 (Mon-Tue):**
- Founder: enroll in Apple Developer Program ($99/yr). Use existing Apple ID.
  Submit business name "Hydroboro Basic Industries LLC" or personal name —
  the LLC is preferred per launch-prep memory, but if LLC formation isn't
  finalized, personal Apple ID works for v0.1 (can transfer later).
- Founder: ~24h Apple approval window. Use this time productively (see Day 3).
- Claude: while waiting, draft `~/orphograph/capture/build_pkg.sh` —
  the build script that takes `orphograph_capture.py` + plist + LaunchAgent
  installer hooks + bundles into a .pkg payload. Uses `pkgbuild` and
  `productbuild` (stdlib macOS tools).
- Claude: draft `~/orphograph/capture/Resources/postinstall` — the script
  that runs after .pkg install, substitutes `$USER` into the plist, prompts
  for the API key (or reads it from a config file), and loads the
  LaunchAgent.
- Claude: draft `~/orphograph/capture/Resources/preinstall` — checks
  Python 3.11+ is present (it is on every modern macOS), exits with a clear
  error if not.

**Day 3 (Wed):**
- Founder: once Developer ID is approved, install the cert into Keychain
  via Xcode → Preferences → Accounts → Manage Certificates → Developer ID
  Installer + Developer ID Application.
- Claude: integrate code-signing into `build_pkg.sh`:
  ```bash
  codesign --force --options runtime --timestamp \
    --sign "Developer ID Application: <FOUNDER NAME> (<TEAM ID>)" \
    orphograph_capture.py  # or the bundled Python launcher
  productsign --sign "Developer ID Installer: <FOUNDER NAME> (<TEAM ID>)" \
    Orphograph-Capture-unsigned.pkg Orphograph-Capture.pkg
  ```
- Claude: notarize the .pkg with `xcrun notarytool submit` (Apple's
  automated malware scan; ~5min turnaround).
- Claude: staple the notarization ticket with `xcrun stapler staple`.

**Day 4 (Thu):**
- Test on a fresh macOS user account (create a second account on the dev
  Mac via System Settings → Users & Groups, log in, copy the .pkg over).
  Verify: double-click → Apple verifies → install completes → daemon
  running within 60s of install.
- Test the uninstall path: founder should be able to remove cleanly via a
  shipped `uninstall.sh` (calls `launchctl unload`, removes plist, removes
  state dir if --purge).

**Day 5 (Fri):**
- Buffer day. Fix anything broken in the .pkg flow. The week-1 success
  criterion: `Orphograph-Capture-0.1.0.pkg` exists on disk, opens on a fresh
  user, daemon is running. No UI, no auto-update — those are weeks 2+.
- Tag `capture-v0.1.0` in git.
- Anchor the .pkg sha256 via Orphograph's own /api/anchor (dogfooding —
  we ship our own provenance for the installer itself).

**Week 1 exit criteria:**
- [ ] Apple Developer enrollment complete
- [ ] `~/orphograph/capture/build_pkg.sh` produces signed + notarized .pkg
- [ ] `~/orphograph/capture/uninstall.sh` cleanly removes the daemon
- [ ] Fresh-user install tested (double-click → daemon running)
- [ ] Installer .pkg sha256 anchored via /api/anchor (dogfood receipt)

---

### Week 2 — UI + auto-update

**Goal by Friday W2:** Menu-bar tray icon showing status + Sparkle auto-update
wired. If either feature slips, the v0.1 escape hatch is "CLI-only Creator
tier" — see escape hatches section.

**Days 1-2 (Mon-Tue) — Menu-bar tray icon:**
- New target: `~/orphograph/capture/OrphographCaptureMenuBar/` — a thin
  SwiftUI macOS app (Xcode project) that:
  - Lives in the menu bar (NSStatusItem, no Dock icon).
  - Polls `python3 orphograph_capture.py --status` every 30s and renders:
    - Green dot: daemon running, last anchor < 1h ago
    - Yellow dot: daemon running, last anchor > 24h ago (might be no new files, that's fine — still warn)
    - Red dot: daemon not running OR last 5 anchors failed
  - Click menu:
    - "Status: X files anchored, last at HH:MM" (read-only)
    - "Open log…" → opens `~/Library/Logs/orphograph-capture.out` in Console.app
    - "Open receipts folder…" → opens `~/Pictures`
    - "Pause anchoring" → `launchctl unload`
    - "Resume anchoring" → `launchctl load`
    - Divider
    - "About Orphograph Capture vX.Y.Z"
    - "Quit menu bar app" (does NOT stop the daemon)
- The menu-bar app is a *display layer only*. The daemon runs independently
  via launchd. If the menu-bar app crashes, anchoring continues uninterrupted.
- Stdlib SwiftUI only. No CocoaPods, no SPM dependencies except Sparkle (next).
- Sign + notarize the .app the same way as the .pkg.
- Bundle the .app inside the .pkg payload so one install gets both.

**Days 3-4 (Wed-Thu) — Sparkle auto-update:**
- Add Sparkle via Swift Package Manager (the only external dep). Sparkle is
  the macOS-canonical auto-update framework, used by Transmission, Sketch,
  1Password historic, etc.
- Generate an EdDSA signing key pair: `generate_keys` tool from Sparkle.
  Private key → macOS Keychain. Public key → embedded in the .app's
  Info.plist as `SUPublicEDKey`.
- Host the appcast.xml at `https://orphograph.com/capture/appcast.xml`. The
  file is a static Sparkle-format XML pointing at the latest .pkg URL +
  signature + release notes. Update it on every release via
  `~/orphograph/capture/release.sh`.
- Configure Sparkle to check daily, prompt the user to install (don't
  silent-update — surprise restarts are a CSAT hit on prosumer tools).
- Test the upgrade path: build v0.1.0, install, build v0.1.1 with a trivial
  change, point the appcast at v0.1.1, watch Sparkle prompt + apply.

**Day 5 (Fri):**
- Smoke-test the full flow on the fresh-user account from week 1:
  1. Install the v0.1.0 .pkg
  2. Menu-bar icon appears (green dot)
  3. Drop a photo into ~/Pictures → anchor within 10s → sidecar appears
  4. Click the menu-bar icon → status shows "1 file anchored"
  5. Trigger an auto-update test → Sparkle prompts → upgrade applies
- Tag `capture-v0.2.0` in git.

**Week 2 exit criteria:**
- [ ] OrphographCaptureMenuBar.app builds + signs + notarizes
- [ ] Menu-bar icon renders status correctly (green/yellow/red)
- [ ] Pause / Resume controls work via the menu
- [ ] Sparkle integrated, EdDSA keys generated, public key embedded
- [ ] appcast.xml live at orphograph.com/capture/appcast.xml
- [ ] Auto-update tested end-to-end (v0.1 → v0.2)
- [ ] `release.sh` script produces signed .pkg + updates appcast.xml

---

### Week 3 — Server + marketing

**Goal by Friday W3:** Stripe Creator-tier checkout live, API-key validation
enforced server-side, branded badge SVG endpoint live, `/capture` landing
page published, marketing copy in production.

**Day 1 (Mon) — Stripe Creator-tier price:**
- Founder: in Stripe Dashboard → Products → Orphograph → Add new price tier:
  - Product: existing "Orphograph Subscription" product (or create new)
  - Price: $19.00 USD recurring monthly
  - Tax behavior: exclusive (Stripe Tax handles per-jurisdiction add-on)
  - Trial: optional 7-day free trial (founder decides — see pricing rationale)
  - Metadata: `tier=creator`, `rate_limit_per_day=1000`,
    `features=capture_daemon,branded_badge,api_access,lightroom_plugin`
- Founder: copy the price ID (`price_xxxxxx`) into
  `~/orphograph/server/.env.local` as `STRIPE_PRICE_CREATOR`.
- Claude: update `~/orphograph/server/stripe_handler.py` to handle the new
  price ID in the webhook handler: on `customer.subscription.created` with
  `price_id == STRIPE_PRICE_CREATOR`, set `user.tier = "creator"` in the
  ledger and generate an API key.
- Claude: update the account page `~/orphograph/web/account.html` to show
  current tier + the Creator-tier upgrade button (Stripe Checkout link).

**Day 2 (Tue) — API-key Creator-tier validation:**
- Update `~/orphograph/server/app.py`'s `/api/anchor` handler:
  - Read `X-Orpho-Api-Key` header
  - If absent or invalid → Personal-tier rate limit (1/mo free, 100/mo Personal)
  - If valid AND tier == "creator" → 1000/day rate limit + receipt gets a
    `branded: true` flag in the JSON response
  - If valid AND tier == "personal" → 100/mo rate limit, no branding
- Update the ledger schema: each receipt now records `tier` + `api_key_id`
  (not the key itself, just its hash prefix for support debugging).
- Write a quick test in `~/orphograph/tests/test_creator_tier.py`:
  - POST `/api/anchor` with no key → 200, personal-tier response
  - POST `/api/anchor` with creator key → 200, `branded: true` in response
  - POST `/api/anchor` with creator key 1001 times in a day → 429 on the
    1001st
- Verify on staging (Fly.io preview), then promote to production.

**Day 3 (Wed) — Branded badge SVG generator:**
- New endpoint: `GET /api/badge/<receipt_id>.svg`
  - Looks up the receipt
  - Renders a small SVG (~280×80px) with:
    - "ANCHORED" + Orphograph wordmark on the left
    - Date + short receipt-ID hash on the right
    - Bottom-right: tiny "verify →" linking to /r/<receipt_id>
    - Dark glassmorphism aesthetic (transparent dark bg + neon-green accent)
    - **Embed the receipt URL as both an `<a>` and a comment**, so when
      creators paste the SVG into portfolios it's still clickable.
  - 1-day cache (`Cache-Control: public, max-age=86400`); badges are
    immutable per receipt.
- Add a copy-to-clipboard "Get badge" button on the receipt page for Creator
  subscribers. Free + Personal tiers see a "Upgrade to Creator for badge"
  CTA instead.
- Test on real receipts; verify the SVG renders identically in Safari,
  Chrome, Firefox, and inline on a markdown portfolio site (GitHub Pages
  test).

**Day 4 (Thu) — `/capture` landing page:**
- New page: `~/orphograph/web/capture.html`
  - Hero: "Anchors at the shutter, not after edit." + the 100-word block
    from the marketing-copy section below.
  - One-click download button → `https://orphograph.com/capture/Orphograph-Capture-latest.pkg`
    (a stable URL that 302-redirects to the current versioned .pkg)
  - 3-step install walkthrough (matches the README):
    1. Subscribe to Creator ($19/mo) → get your API key emailed
    2. Download + install the .pkg
    3. Paste your API key into the menu-bar app's setup prompt → done
  - "How it works" section: 4 short paragraphs on the local-hash flow,
    privacy guarantees (file bytes never leave), receipt-verification path,
    architectural firewall (so the principle-6 reader can see we mean it).
  - FAQ: 8 questions (file bytes never upload, opt-in filename, what if I
    cancel the subscription, how do I verify receipts in 10 years, what
    happens if Apple Notarization revokes our cert, etc.).
  - Footer with the architectural-firewall disclaimer for the Hydroboro
    skeptic.
- Add a "Creator" row to the homepage pricing table linking to /capture.
- Push to Fly.io.

**Day 5 (Fri) — Marketing copy + waitlist email:**
- Lock the ~100-word hero block (see marketing-copy section below).
- Draft the waitlist email (founder sends Monday W4):
  - Subject: "Orphograph Capture is live — your shutter, anchored to Bitcoin"
  - 3 paragraphs: what it is, who it's for, how to upgrade. Single CTA
    button. No second CTA. No "P.S."
- Draft the Show HN follow-up post (founder posts Tuesday W4 at 10am ET):
  - Title: "Show HN: Orphograph Capture — capture-time provenance for
    photographers ($19/mo)"
  - Body: 200 words, link to /capture, link to the daemon source (MIT
    open-source on GitHub).

**Week 3 exit criteria:**
- [ ] Stripe Creator-tier price live, webhook tested
- [ ] `/api/anchor` enforces Creator rate limit + branding flag
- [ ] `/api/badge/<id>.svg` live, cached, copy-button on receipt page
- [ ] `/capture` landing page live on orphograph.com
- [ ] Marketing copy locked
- [ ] Waitlist email drafted (founder sends W4 Monday)
- [ ] Show HN post drafted (founder posts W4 Tuesday)

---

### Week 4 — Polish + launch

**Goal by Friday W4:** Soft-launch to waitlist on Monday, Show HN on Tuesday,
first 5 paid subscribers by Friday.

**Day 1 (Mon) — End-to-end testing on fresh Mac:**
- Borrow a friend's Mac (or use a VM via Tart / UTM with a clean macOS
  install). Repeat the full install + subscribe flow:
  1. Visit /capture
  2. Click "Subscribe $19/mo" → Stripe Checkout → complete payment
  3. Receive API key email
  4. Download .pkg → install
  5. Menu-bar icon appears
  6. Setup prompt → paste API key → save
  7. Drop a photo into ~/Pictures → 10s → sidecar appears + menu-bar
     counter increments
  8. Open the receipt → "Get badge" button appears (branded: true)
  9. Cancel the subscription → API key downgrades to Personal tier
- Fix any UX paper-cuts. The bar: a stranger with no founder support gets
  from /capture → first anchor in ≤10 minutes.

**Day 2 (Tue) — Founder dogfood begins (5-day smoke):**
- Founder installs the production .pkg on their main Mac (yes, the same one
  running this Claude session — the daemon is non-disruptive).
- Founder uses it as their actual provenance tool for the rest of the week.
  Any photo taken on the iPhone (which syncs to ~/Pictures via iCloud Photos
  Library or similar) auto-anchors.
- Founder logs anything weird in `~/orphograph/deploy/CAPTURE_DOGFOOD.md`
  (new file, append-only). Claude reads it daily and fixes the top issue.

**Days 3-4 (Wed-Thu) — Telemetry + final polish:**
- Add opt-in telemetry:
  - On first launch, menu-bar app shows a one-time prompt: "Help improve
    Orphograph Capture by sending anonymous daily stats? (Count of anchors,
    success rate. No file metadata, no filenames, no identifiers.)"
  - If accepted: daemon POSTs daily to `/api/telemetry/capture` with
    `{tier, anchors_today, failures_today, days_since_install}`. No user ID,
    no API key in the body — just an opaque randomly-generated install UUID
    stored in the state dir.
  - Server aggregates into a dashboard at `/admin/capture-telemetry` for
    founder support visibility.
- Verify the privacy claim with a Wireshark/Little Snitch capture: nothing
  leaves the machine except the hash POSTs + the optional telemetry POST.
  Document this in `~/orphograph/capture/PRIVACY_AUDIT.md`.

**Day 5 (Fri) — Launch readiness review:**
- Walk the full Definition of Done checklist (bottom of this doc).
- Founder + Claude sign off on go/no-go.
- If go: founder sends waitlist email Monday morning, Show HN Tuesday 10am ET.
- If no-go: identify the single blocking item, ship escape hatch, push
  launch by 1 week (max).

**Week 4 exit criteria:**
- [ ] End-to-end test on fresh Mac passes (stranger → first anchor ≤10min)
- [ ] Founder 5-day dogfood complete, top issues fixed
- [ ] Opt-in telemetry implemented + privacy audit documented
- [ ] Waitlist email sent (Monday W4)
- [ ] Show HN post live (Tuesday W4)
- [ ] First 5 Creator subscribers by Friday (target, not hard gate)

---

## Founder action items

These require founder accounts, sudo, or payment methods. Claude cannot do
them — they're booked into the schedule above but flagged here for at-a-glance
planning.

| Item | When | Cost | Effort | Notes |
|---|---|---|---|---|
| Apple Developer Program enrollment | W1 Day 1 | $99/yr | 30min + 24h Apple wait | Use existing Apple ID; LLC name optional, can transfer later |
| Developer ID certificates install (Application + Installer) | W1 Day 3 | $0 | 15min | Xcode → Preferences → Accounts |
| Apple notarization service signup | W1 Day 3 | $0 | 5min | Free with Developer ID; needs app-specific password generated at appleid.apple.com |
| First .pkg notarization submission | W1 Day 3 | $0 | 10min (5min Apple turnaround) | `xcrun notarytool submit` from terminal |
| Stripe Creator-tier price creation | W3 Day 1 | $0 | 15min | Dashboard → Products → Add price |
| Send waitlist email | W4 Monday | $0 (Resend free tier) | 10min | Pre-drafted W3 Day 5, just hit send |
| Post on Show HN | W4 Tuesday 10am ET | $0 | 5min + an hour for replies | Pre-drafted W3 Day 5 |
| Borrow / VM a fresh Mac for end-to-end test | W4 Day 1 | $0 | 1h | Tart VM or a friend's machine |
| Stripe payout setup (already done per launch-prep memory) | — | $0 | — | Verify still active before W4 |
| Optional: LLC bank account for Stripe payout | If LLC formation complete | $0 | 30min | Personal account works for v0.1 |

**Total founder time across 4 weeks:** ~5 hours, mostly clicking buttons in
Apple + Stripe dashboards. No coding required.

**Total founder cash outlay:** $99 (Apple Developer) + ~$0 elsewhere
(Stripe, Resend, Fly.io all on existing accounts).

---

## Risks + mitigations

### R1: Apple rejects the .pkg notarization
- **Pre-empted by:** code-signing + notarization in week 1, before any UI
  work. If Apple rejects, we have 3 weeks to fix vs trying to debug it
  during launch week.
- **Common rejection reasons:** unsigned Python interpreter inside the
  bundle (mitigation: ship as a stub that calls /usr/bin/python3, don't
  bundle our own), missing entitlements (mitigation: add `--options runtime`
  + a minimal entitlements.plist with `com.apple.security.network.client`).
- **Worst case:** if Apple persistently rejects, ship a .dmg with an
  unsigned binary + a Gatekeeper bypass instructions page. Hit the rate of
  ~20% lost conversions but doesn't block launch.

### R2: Sparkle auto-update breaks (the .app self-corrupts during upgrade)
- **Pre-empted by:** test the v0.1 → v0.2 upgrade path in week 2 before
  cutting v0.1.
- **Mitigation if broken at launch:** ship v0.1 *without* auto-update.
  Founder posts a "manual update" notice when v0.2 ships. Auto-update can
  be added in v0.2 itself (Sparkle reads its config from the *running*
  bundle, so v0.2 can enable it even if v0.1 didn't have it).
- **Escape hatch:** disable auto-update entirely in v0.1, rely on manual
  download notices. Cost: -10% UX, +1 week of dev time saved.

### R3: SwiftUI menu-bar app is more complex than estimated
- **Pre-empted by:** time-box the menu-bar work to 2 days max. If we hit
  day 3 and the basics aren't working, fall back to:
- **Escape hatch:** ship "CLI-only Creator tier" for v0.1. The Creator
  daemon is still the daemon, still anchors at capture-time, still gets the
  rate-limit bypass + branded badge. The user gets a Terminal command to
  check status (`orphograph-capture status`) instead of a menu-bar icon.
  We add the menu-bar in v0.2. This is *not great* but the daemon itself is
  the product — the icon is sugar.
- The README + landing copy must transparently say "v0.1 is CLI-only, GUI
  is coming in v0.2 (next 30 days)" if we take this path. Customers who
  don't want CLI get a refund.

### R4: Stripe Creator-tier webhook misfires (sub created but no API key issued)
- **Pre-empted by:** test the webhook on Stripe's test mode in W3 Day 1
  with at least 5 simulated subscription events.
- **Mitigation:** the webhook handler must be idempotent — re-running it on
  the same event produces the same state. The account page must show an
  obvious "Generate API key" button if the webhook race-conditioned and the
  user has an active sub but no key.
- **Founder support fallback:** founder can manually issue an API key from
  the admin tool for any user. SLA: ≤4h response during W4 launch window.

### R5: One of the OTS calendars goes offline during launch week
- **Pre-empted by:** we already submit to 5 calendars; 3-of-5 = success per
  the engine. Two can fail and we're fine.
- **Mitigation:** if 3+ fail simultaneously, the daemon logs the failure and
  retries. Receipts still get created but with reduced confidence. The
  menu-bar icon goes yellow.
- **Existing behavior:** the daemon already records `calendars_ok` /
  `calendars_total` per receipt. Nothing to add — just make sure the
  menu-bar UI surfaces it.

### R6: Volume during launch week overwhelms the Fly.io single instance
- **Pre-empted by:** the /api/anchor endpoint is stateless except for the
  ledger append. Fly.io autoscales horizontally.
- **Mitigation:** add a "Status: degraded" banner to /capture if the queue
  depth exceeds 10s. Customers anchor → got 202, retries in 60s. No data
  lost. Worst-case: anchors are delayed by minutes, not lost.

### R7: Customer cancels Creator sub but daemon keeps running
- **Pre-empted by:** the API-key validation is server-side. When the sub
  cancels, the key downgrades to Personal-tier rate limits. The daemon
  keeps running but only gets 100 anchors/mo instead of 1000/day.
- **UX:** menu-bar icon shows a "Subscription expired — anchoring limited"
  banner with a Re-subscribe button.
- **Daemon behavior:** never silently fails. If rate-limited, logs +
  surfaces in the menu-bar.

### R8: The architectural-firewall principle (CLAUDE.md #6) is violated by
accident
- **Pre-empted by:** every PR touching `~/orphograph/capture/` runs a grep
  in CI for `hsi_anchor`, `Hydroboro`, `HSI`, `Boroscope`, `regime`. Zero
  matches required.
- **Pre-empted by:** the capture daemon imports stdlib only, no
  cross-project imports. Linted in CI.

---

## Pricing rationale

### Why $19/mo specifically

**Anchor points the customer already has in their head:**

| Service | Price | Category |
|---|---|---|
| Backblaze | $8/mo | Consumer backup |
| iCloud+ 200GB | $3/mo | Consumer storage |
| Dropbox Plus | $12/mo | Consumer storage |
| **Orphograph Creator** | **$19/mo** | **Provenance** |
| 1Password | $36/yr ($3/mo) | Security (low anchor) |
| Adobe Photography Plan | $20/mo | Pro creative |
| SmugMug Pro | $45/mo | Pro portfolio |
| Pixieset Pro | $35/mo | Pro client gallery |

$19 is the *gap* between consumer ($3-12) and pro ($20-45). Creators in
the AI-dispute target market are already paying both: they have iCloud
(consumer) and SmugMug or Adobe (pro). Orphograph slides in below the
pro-portfolio bill and reads as cheaper-than-portfolio rather than
more-than-backup.

**What Creator gets vs Personal:**

| Feature | Personal ($5/mo) | Creator ($19/mo) |
|---|---|---|
| Web drop-zone anchoring | Unlimited | Unlimited |
| Folder watcher | Yes | Yes |
| Receipt verification (in-app) | Yes | Yes |
| Receipt verification (standalone CLI) | Yes (open-source) | Yes (open-source) |
| **Capture-time daemon** | No | **Yes** |
| Menu-bar app | No | Yes |
| Auto-update | No | Yes |
| **Branded SVG badge** | No | **Yes** |
| Receipt URL on the badge | No | Yes |
| API rate limit | 100/mo | **1,000/day** |
| Direct API access | No | **Yes** |
| Lightroom plugin pre-bundled | No | Yes (when shipped) |
| Priority anchor queue | No | Yes |
| Email support | Best-effort | 24h SLA |

The capture-time daemon is the load-bearing differentiator. Everything else
is either polish (badge, menu-bar) or scale (rate limit, API). If a customer
asks "why $14 more than Personal," the answer is one sentence: **"Orphograph
Capture anchors at the shutter, not after upload. You don't have to remember
to anchor — it happens automatically as you shoot."**

### Y3 valuation row (per memory project_orphograph_valuation)

| Year | Creator subs | MRR | ARR |
|---|---|---|---|
| Y1 (end of W4 + 12mo) | 20 | $380 | $4.6k |
| Y2 | 80 | $1.5k | $18k |
| Y3 base | 200 | $3.8k | $46k |
| Y5 venture row | 1,000 | $19k | $228k |

Creator alone doesn't hit the Y5 venture row — that requires a B2B tier
above it. But Creator is the bridge: it proves capture-time provenance is
a thing people pay for, which then de-risks the B2B tier pitch ("our
Creator subscribers do this 1000×/day per shop, scaled to your fleet of 50
photographers, that's...").

**Underwrite the base. Preserve the venture optionality.**

---

## Marketing copy

### Hero block (~100 words) for /capture landing page

> **Anchors at the shutter, not after edit.**
>
> Orphograph Capture sits quietly in your menu bar and timestamps every
> photo, video, and document the moment it lands on your disk. Your files
> never leave your machine — only their SHA-256 hashes do, and only to
> anchor them to the Bitcoin blockchain through OpenTimestamps. Cancel any
> time. Verify in ten years with or without us. Built for photographers,
> journalists, and creators who need to prove a frame is real, original,
> and pre-AI without uploading the frame itself. The daemon is open-source
> and MIT-licensed; the $19/mo Creator subscription pays for the API rate
> limit, the embeddable badge, and the auto-updating menu-bar app.

Exactly 99 words. Three sentences are doing the heavy lift:
- Sentence 1: *what* (capture-time, not upload-time)
- Sentence 4: *who* (creators in AI-dispute contexts)
- Sentence 5: *why pay vs OpenTimestamps free* (open-source daemon
  acknowledged; you're buying the polish, not the cryptography)

### Secondary copy: the 5-bullet "why $19" block

- The daemon runs while you sleep — every shot from your iPhone's iCloud
  sync, every screen recording, every Lightroom export is anchored
  automatically.
- Your file bytes never upload. Only the 32-byte hash.
- A branded SVG badge with the receipt URL drops into your portfolio
  with one paste.
- API access for studios + agencies who want their own ingestion pipeline.
- Cancel any time. Receipts already created stay verifiable on Bitcoin
  forever, with or without us.

### One-line subhead variants (A/B test in W4 if time permits)

1. "Anchors at the shutter, not after edit." (recommended default)
2. "Capture-time provenance for photographers, journalists, creators."
3. "Your shutter, timestamped to Bitcoin. While you sleep."
4. "Prove the frame is yours. Before AI has a chance to argue."

---

## Internal docs: Creator vs Personal

Create `~/orphograph/docs/CREATOR_VS_PERSONAL.md` (founder + support reference):

- One-pager comparing the two tiers (the feature matrix above)
- Common support questions:
  - "I cancelled but the daemon keeps running" → expected, rate-limit
    downgrades, menu-bar shows banner
  - "How do I get my API key back?" → /account.html → Regenerate (revokes
    old)
  - "Does my receipt still verify after I cancel?" → yes, forever, Bitcoin
  - "Can I install on multiple Macs?" → yes, same API key works on all
    machines under the same account. Rate limit is per-account.
  - "I want to anchor a non-photo file" → daemon already anchors PDFs, docs,
    audio, video. Add `--all-extensions` to anchor literally everything.
  - "Can I use this in my agency / studio?" → yes for v0.1; B2B tier
    coming with team management features (don't promise a date)
- Refund policy: pro-rated refund any time within first 30 days,
  no-questions-asked. After that: cancel, no refund on partial month.
- Architectural firewall note: the capture daemon is a clean rewrite of
  the capture-time pattern; does NOT share code with any Hydroboro-branded
  project. (For the principle-6 reader who reads the source.)

---

## v0.1 escape hatches

If the schedule slips, here's the pre-decided fallback in priority order
(cheapest to most-painful):

### Escape hatch 1: Defer auto-update to v0.2
- Cost: -1 week of dev time
- Impact: customers manually download new .pkg when they hear about it
- Triggered if: Sparkle integration fails by W2 Day 4
- v0.1 ships without auto-update; in-app notice "Check for updates →" link
  to /capture. v0.2 adds Sparkle within 30 days.

### Escape hatch 2: Defer menu-bar app to v0.2 (ship CLI-only Creator)
- Cost: -1 week of dev time, -10-20% conversion from non-CLI customers
- Impact: customers run `orphograph-capture status` in Terminal to check
- Triggered if: SwiftUI work isn't done by W2 Day 3
- v0.1 ships as a signed .pkg that installs the daemon + a CLI wrapper.
  Landing page transparently says "v0.1 is CLI-only; menu-bar app in 30
  days." Refund anyone who asks.

### Escape hatch 3: Defer telemetry to v0.2
- Cost: -1 day of dev time
- Impact: founder support visibility is worse; have to ask customers for
  logs when things break
- Triggered if: any other item slips and we need to redirect a day
- v0.1 ships without telemetry. The `--status` command + `seen.jsonl` give
  enough self-debug info.

### Escape hatch 4: Defer Lightroom-plugin bundling
- Cost: 0 days (it's already on a separate spec at LIGHTROOM_PLUGIN_SPEC.md)
- Impact: Creator-tier value prop has one less item but daemon does
  Lightroom export automatically (it's a folder watch)
- Triggered if: we want to ship a smaller v0.1
- Move "Lightroom plugin pre-bundled" off the feature matrix; replace with
  "automatic Lightroom export anchoring (watches the export folder)."

### Escape hatch 5: 1-week launch delay
- Cost: 7 days
- Impact: founder's HN momentum from Pack launch decays
- Triggered if: 2+ of the above hatches don't fix the blocking item
- Push launch to W5. Use the extra week to fix the actual blocker. Do not
  push past W6 — at that point reassess whether the Y3 trajectory was
  optimistic.

**Absolute red lines (never compromise):**
- File bytes never upload. Period. (CLAUDE.md principle 1)
- Receipts verify without us. Period. (CLAUDE.md principle 3)
- Zero Hydroboro lineage in code or strings. Period. (CLAUDE.md principle 6)
- Honest copy. No "court-admissible," "notarized," "legally binding."
  (CLAUDE.md principle 5)

---

## Definition of done

The Creator tier is "done" when all of these are true on 2026-06-11
(end of W4 Friday):

**Product:**
- [ ] Signed + notarized `Orphograph-Capture-0.2.0.pkg` available at
      `https://orphograph.com/capture/Orphograph-Capture-latest.pkg`
- [ ] Menu-bar app installs alongside daemon, shows status (or CLI-only
      escape hatch documented)
- [ ] Sparkle auto-update wired and tested (or v0.2-defer escape hatch)
- [ ] Branded SVG badge endpoint live at `/api/badge/<id>.svg`
- [ ] Opt-in telemetry implemented + privacy audit doc

**Server:**
- [ ] Stripe Creator-tier price `$19/mo` live (production, not test)
- [ ] Webhook tested: subscription created → API key issued → email sent
- [ ] `/api/anchor` enforces Creator rate limit (1000/day) + branding flag
- [ ] `/api/badge/<id>.svg` returns valid SVG for any receipt
- [ ] Cancel flow: sub cancelled → tier downgrades to Personal in ≤10s

**Customer-facing:**
- [ ] `/capture` landing page live
- [ ] 100-word hero block deployed
- [ ] Pricing-table row for Creator on homepage links to /capture
- [ ] FAQ + privacy statement live

**Internal:**
- [ ] `~/orphograph/docs/CREATOR_VS_PERSONAL.md` written
- [ ] Refund policy documented
- [ ] Founder dogfood: 5 consecutive days using Capture on personal Mac,
      no critical issues
- [ ] End-to-end fresh-Mac test passes (stranger → first anchor ≤10min)

**Launch:**
- [ ] Waitlist email sent W4 Monday morning
- [ ] Show HN post live W4 Tuesday 10am ET
- [ ] Founder watches HN replies for 4h post-launch
- [ ] First 5 Creator subscribers by W4 Friday (target, not gate)

**Compliance:**
- [ ] Privacy audit (Little Snitch or Wireshark capture) confirms no file
      bytes leave the machine
- [ ] Architectural firewall: grep for `hsi_anchor|Hydroboro|HSI|Boroscope|
      regime` in `~/orphograph/capture/**` returns 0 matches
- [ ] License: MIT clearly stated on the daemon source + the .pkg About box
- [ ] Honest copy review: no "court-admissible," "legally binding,"
      "notarized" anywhere in /capture page

---

## Post-launch (W5+)

Not in scope for this 4-week plan, but pre-decided so we don't waste cycles
debating them in W4:

- v0.2 priorities (W5-W8): close any deferred escape hatches first
  (auto-update, menu-bar, telemetry). Then Lightroom-plugin bundling.
- Windows / Linux installers: defer to Y2 unless waitlist demand demands it.
  Most Creator-target buyers are on Mac.
- iOS Boroscope-sibling capture app: separate project, defer to Y2.
- B2B tier ($99-299/mo team): start customer-development conversations in
  W6-W8 with any Creator subscribers who have studios.
- Localization: English-only for v0.1 + v0.2.

---

## Cadence + checkpoints

- **Daily (W1-W4):** Claude runs `~/orphograph/scripts/capture_smoke.sh`
  end-of-day, writes results to `~/orphograph/deploy/CAPTURE_SMOKE_LOG.md`.
- **Weekly (Friday EOD):** Founder + Claude walk the week's exit criteria.
  Anything red → triage Monday, escape-hatch decision by Tuesday EOD.
- **W4 Monday 9am:** Final go/no-go. Founder makes the call.
- **W4 Tuesday post-Show-HN:** 4h dedicated to replies; founder owns the
  voice, Claude drafts technical answers.

---

## One-line summary for the impatient

> Ship a signed .pkg installer of the capture daemon + a $19/mo Stripe price
> + a branded SVG badge + a `/capture` landing page in 4 weeks. The daemon
> already works; everything else is packaging.

— End of plan —
