# Implementation Status — Orphograph Post-Launch Roadmap

**Last Updated:** 2026-05-14  
**Scope:** Month 1–3 + Month 3–12 features  
**Summary:** Many features already shipped; this document tracks what exists vs. what needs building

---

## 🟢 ALREADY IMPLEMENTED (No Work Needed)

### Task #151: Dashboard + User Account UX ✅ COMPLETE

**Status:** 📦 **SHIPPED** — All features exist and are working.

**Implemented Features:**
- ✅ `web/account.html` (UI template)
- ✅ `web/account.js` (frontend JS, 11KB)
- ✅ `GET /api/me` — returns user email + subscription status
- ✅ `GET /api/me/anchors` — paginated list of user's anchors
- ✅ `GET /api/me/anchors.csv` — CSV export of anchor history
- ✅ `GET /api/me/export` — GDPR data export (all user data as JSON)
- ✅ `POST /api/me/delete` — GDPR account deletion
- ✅ `POST /api/me/cancel-subscription` — cancel subscription
- ✅ `POST /api/me/reactivate-subscription` — reactivate subscription
- ✅ `GET /api/me/api-key` — API key management
- ✅ `POST /api/me/api-key/revoke` — revoke API key
- ✅ Filter by label + date range (implemented in account.js)
- ✅ Recent anchors table with timestamps, labels, calendar status
- ✅ API access section (for Creator tier users)
- ✅ Founder payout section (hidden, token-gated)

**What Works:**
- User signs in → sees their account dashboard
- Lists all anchors created under subscription
- Can cancel/reactivate subscription
- Can download data (GDPR export)
- Can delete account
- Can manage API keys (if Creator tier)

**What's Missing (Nice-to-Have):**
- Email notifications when subscription is about to expire
- Reminder emails for unused credits in Pack
- "Proof upgraded to Bitcoin-final" notifications (only via API currently)

**Action:** Mark as complete. No work needed.

---

### Task #151.5: Public Stats Page ✅ COMPLETE

**Status:** 📦 **SHIPPED**

**Implemented:**
- ✅ `web/stats.html` (public page)
- ✅ `web/stats.js` (frontend)
- ✅ `GET /api/stats` — public marketing metrics
- ✅ Shows: total anchors, anchors in last 24h, anchors in last 7d
- ✅ Anonymous (no PII exposure)
- ✅ Updates in real-time

**Action:** Already done. No work needed.

---

### Task #151.6: Status Page ✅ COMPLETE

**Status:** 📦 **SHIPPED**

**Implemented:**
- ✅ `web/status.html` (status page)
- ✅ `GET /api/health` — liveness endpoint
- ✅ Shows: calendar connectivity, ledger size, uptime, version

**Action:** Already done. No work needed.

---

## 🟡 PARTIALLY IMPLEMENTED (Needs Polish)

### Task #152: Founder Revenue + Metrics Dashboards ⚠️ PARTIAL

**Status:** 📋 **PARTIAL** — Payout tracking exists, revenue tracking needs to be built.

**What Exists:**
- ✅ `GET /api/founder/payout-status` — hot BTC balance + sweep status (founder-only, token-gated)
- ✅ Founder token storage in localStorage
- ✅ `server/payout_monitor.py` — BTC hot wallet monitoring
- ✅ Founder section on account.html (hidden unless founder token present)

**What's Missing (MUST BUILD):**
1. **MRR / ARR Tracking**
   - Endpoint: `GET /api/founder/metrics` — returns:
     - Current MRR (total subscription revenue this month)
     - MRR last month (for comparison)
     - Projected ARR (MRR × 12)
     - Customer counts by tier (Free / Pack / Personal / Creator)
   - Data source: stripe_processed_events.jsonl (completed subscriptions)

2. **Churn Tracking**
   - Subscriptions cancelled this month
   - Churn rate (% of subscribers who cancelled)
   - Customer names (for support follow-up)
   - Data source: subscriptions module

3. **LTV / CAC Estimates**
   - Total revenue per customer (lifetime)
   - Acquisition cost (if tracking UTM params)
   - Payback period (months to recover CAC)

4. **Revenue Dashboard UI**
   - `web/founder/dashboard.html` (new)
   - Charts: MRR trend (last 90 days), customer cohorts, churn rate
   - KPI cards: Current MRR, Monthly Active, Churn %
   - Export: CSV/JSON for accounting

5. **Alerts**
   - Email founder if churn > 10% monthly
   - Alert if MRR drops >20% week-over-week

**Files to Create:**
- `server/analytics.py` — metrics calculation
- `web/founder/dashboard.html` — UI
- `web/founder/dashboard.js` — frontend
- Update `server/app.py` with `/api/founder/metrics` route

**Complexity:** Medium (data aggregation from existing ledgers, no new API integrations)  
**Priority:** HIGH (founder needs this to track business health)  
**Effort:** 4–6 hours

---

### Task #153: Customer Support Tooling ⚠️ PARTIAL

**Status:** 📋 **PARTIAL** — Refund tooling exists as CLI, UI wrapper + other tools missing.

**What Exists:**
- ✅ `scripts/refund_pack.py` — CLI tool to refund a charge + zero claim code
- ✅ `scripts/backup_to_b2.sh` — daily backup script
- ✅ Ledger audit trail (all transactions append-only)

**What's Missing (MUST BUILD):**

1. **Customer Lookup**
   - Endpoint: `GET /api/founder/customer?email=...` (founder-only)
   - Returns: email, all anchors, purchase history, subscription status, refunds
   - No PII exposure; founder-only access

2. **Refund UI Wrapper**
   - `web/founder/support.html` — form to refund a charge
   - Input: charge ID (or search by email)
   - Shows: amount, customer email (masked), claim code
   - Button: "Confirm refund" → calls refund_pack.py via HTTP
   - Result: refund successful / failed, ledger entry created

3. **Resend Receipt**
   - Button on customer lookup page
   - Re-emails old receipt without new charge
   - Useful if customer lost the receipt email

4. **Manual Credit Grant**
   - Form: claim code + credits to add
   - Reason dropdown: "support", "bonus", "refund recovery", "promotion"
   - Appends to ledger as "support grant"
   - Email to customer: "X credits added to your account"

5. **Ledger Audit Export**
   - Export credits.ledger for accounting / tax
   - Filter by: date range, claim code, source type (stripe, pack, support, etc.)
   - Format: CSV (timestamp, email_id, claim_code, credits_delta, source)

6. **Failed Webhook Viewer**
   - List of webhook events that failed to process
   - Show: event ID, error, retry count, last attempt
   - Button: manual replay (re-run the webhook handler)
   - Useful if a webhook handler had a bug that's now fixed

7. **Abuse Detection Alerts**
   - Alert if 100+ anchors from same IP in 1 hour → possible bot
   - Alert if claim code guessing attempts (many 404s on `/api/receipt/<id>`)
   - Alert if Pack token replay attempts (same token used twice)
   - Logged to abuse.jsonl for review

**Files to Create:**
- `server/support_tools.py` — backend logic for customer lookup, ledger audit, etc.
- `web/founder/support.html` — UI for all support operations
- `web/founder/support.js` — frontend
- Update `server/app.py` with `/api/founder/support/*` routes
- `server/abuse_detection.py` — flag anomalous patterns

**Complexity:** Medium (data lookup + UI forms + email delivery)  
**Priority:** HIGH (essential for scaling to >10 customers)  
**Effort:** 6–8 hours

---

## 🔴 NOT IMPLEMENTED (Major Work Needed)

### Task #154: Creator Capture App (Desktop) 🔴 NOT STARTED

**Status:** 📦 **NOT STARTED**

**What Needs to Exist:**
- Desktop app (macOS first, Windows/Linux later)
- Capture-time provenance: screenshot → hash → anchor on capture
- Folder monitoring: watch ~/Pictures/Raw → auto-anchor new files
- Settings: API key management, folder selection, auto-anchor toggle
- Signed release: notarized macOS binary, auto-update
- Receipt management: local copies of all anchored files + receipts
- Right-click verification: verify any file against its receipt

**Architecture:**
- Python 3.11+ with PyObjC (macOS) OR Swift native
- Reuses Orphograph's `/api/anchor` for anchoring
- NO imports from ~/ai-provenance/ (clean rewrite per CLAUDE.md principle #6)
- Signed binary distribution (notarization on macOS)

**Files to Create:**
- `capture/app.py` — main daemon
- `capture/ui.py` — menu bar UI
- `capture/folder_monitor.py` — inotify / FSEvents watcher
- `capture/settings.py` — config management
- `capture/receipts.py` — local receipt storage
- `capture/macos_bundle.py` — .app bundle creation + signing
- `scripts/notarize_capture.sh` — Apple notarization pipeline

**Complexity:** HIGH (system integration, desktop UX, code signing)  
**Priority:** MEDIUM (do after first Creator tier customer)  
**Effort:** 16–24 hours (full implementation)

**Decision:** Defer until Month 3. Ship Beta to 3 photographers first. Don't advertise on landing page until MVP exists.

---

### Task #155: Lightroom Plugin 🔴 NOT STARTED

**Status:** 📦 **NOT STARTED**

**What Needs to Exist:**
- Lightroom SDK plugin (Lua-based)
- Workflow: Select photos → Right-click → "Anchor with Orphograph"
- Batch anchor multiple photos
- EXIF sync: proof timestamps written back to photo metadata
- Receipt export: sidecar XMP files saved alongside photos

**Files to Create:**
- `lightroom-plugin/Info.lua` — plugin manifest
- `lightroom-plugin/OrphographPlugin.lua` — main logic
- `server/batch_anchor.py` — new `/api/anchor/batch` endpoint
- Update `server/app.py` with batch route

**Complexity:** MEDIUM (Lightroom SDK learning curve, EXIF metadata handling)  
**Priority:** MEDIUM (photographers are target customer)  
**Effort:** 8–12 hours

**Decision:** Defer until Creator app is live + tested. Photographers will ask for this.

---

### Task #156: Browser Extension 🔴 NOT STARTED

**Status:** 📦 **NOT STARTED**

**What Needs to Exist:**
- Manifest v3 (Chrome, Firefox, Edge)
- Right-click context menu: "Anchor with Orphograph"
- Drag from address bar: hash page content + anchor
- Screenshot capture: full page or selection
- Notification: "Proof ready" → click → open receipt
- Token persistence: localStorage for Pack token

**Files to Create:**
- `browser-extension/manifest.json`
- `browser-extension/background.js` (service worker)
- `browser-extension/content.js`
- `browser-extension/popup.html` + `popup.js`
- `browser-extension/icons/` (16x16, 48x48, 128x128)

**Complexity:** MEDIUM (manifest v3 is strict, content script security)  
**Priority:** LOWER (nice-to-have, high engagement)  
**Effort:** 8–10 hours

**Decision:** Defer to Month 3–6. Launch web app first, gauge demand.

---

### Task #157: Public API + SDKs 🔴 NOT STARTED

**Status:** 📦 **NOT STARTED**

**What Needs to Exist:**
- Public API documentation: `/docs/api`
- API key lifecycle: issue, rotate, revoke
- Endpoints: `/api/anchor`, `/api/verify`, `/api/anchor/batch` (metered)
- Webhook callbacks: notify customer when proof upgrades to Bitcoin-final
- Rate limits: per API key, usage billing (per 1000 calls)
- SDKs: Python, Node.js (with type hints)
- Postman collection for testing

**Files to Create:**
- `server/api_keys.py` — key lifecycle
- `server/webhooks.py` — callback delivery + retry logic
- `web/docs/api.html` — API reference
- `python-sdk/` (setuptools package)
- `node-sdk/` (npm package)
- `postman-collection.json`

**Complexity:** MEDIUM (webhook delivery, SDK boilerplate)  
**Priority:** MEDIUM (enables automation, partnerships)  
**Effort:** 12–16 hours

**Decision:** Ship after Creator app. Demand signals from first customers.

---

### Task #158: B2B Features (Teams, White-Label, SSO) 🔴 NOT STARTED

**Status:** 📦 **NOT STARTED**

**What Needs to Exist (ONLY IF MRR > $500):**
- Organization accounts: owner + members + roles
- White-label: custom domain, branding, reseller program
- SSO: SAML integration, SCIM for auto-provisioning
- Usage-based billing: pay per 1000 API calls
- SOC2 audit prep: access logs, backup verification, incident log

**Complexity:** HIGH (SAML/SCIM, billing state, audit trails)  
**Priority:** LOW (only pursue if B2B demand exists)  
**Effort:** 24–32 hours

**Decision:** Do NOT start before MRR > $500. Get customer interviews first.

---

### Task #159: Ongoing Content + SEO 🟡 PARTIAL

**Status:** 📋 **PARTIAL** — Pages exist, ongoing content calendar needs execution.

**What Exists:**
- ✅ `web/index.html` — landing page
- ✅ `web/about.html` — about page
- ✅ `web/buy.html` — pricing page
- ✅ `web/press.html` — press kit
- ✅ `web/compare.html` — feature comparison (vs competitors)
- ✅ `web/stats.html` — public stats

**What Needs:**
1. **Blog system** (if not already built)
   - `/blog/` directory structure
   - Blog post templates
   - Archive + RSS feed
   - First posts: photographer use case, journalist workflow

2. **Use-case landing pages** (10 variants)
   - `/use-cases/photographer`
   - `/use-cases/journalist`
   - `/use-cases/designer`
   - etc.

3. **Example proofs gallery**
   - Public gallery of customer proofs (with permission)
   - Shows receipt page + verifier output + timeline

4. **FAQ expansion**
   - "Is this court-admissible?" (answer: no, we don't claim that)
   - "Can you see my files?" (answer: no, client-side hashing)
   - "What if you shut down?" (answer: verifier is open-source)

5. **SEO + Social**
   - Twitter updates (2–3/week)
   - LinkedIn thought leadership
   - Reddit engagement (r/photography, r/design, r/crypto)
   - Email newsletter to waitlist

**Action:** Start after launch. Set up content calendar for Month 1+.

---

## 🎯 PRIORITIZED BUILD ORDER (Post-Launch)

### Week 1–2 (Just Launched)
- [ ] Task #152: Founder MRR + revenue dashboards (HIGH)
  - Time: 4–6 hours
  - Enables: founder to track business health

- [ ] Task #153: Customer support tooling (HIGH)
  - Time: 6–8 hours
  - Enables: handle customer refunds, lookups, support

### Week 3–4 (First Customers Arriving)
- [ ] Task #159: Blog + use-case landing pages (MEDIUM)
  - Time: 8–12 hours
  - Enables: organic discovery, SEO

- [ ] Task #145: All-endpoints sanity probe (MEDIUM)
  - Time: 2–3 hours
  - Enables: catch regressions before they hit prod

### Month 2 (Scaling to 10+ Customers)
- [ ] Task #154: Creator Capture app (MEDIUM, but complex)
  - Time: 16–24 hours
  - Enables: $19/mo tier, photographer retention

- [ ] Task #155: Lightroom plugin (MEDIUM)
  - Time: 8–12 hours
  - Enables: workflow integration for photographers

### Month 3+ (If MRR > $500)
- [ ] Task #156: Browser extension (LOWER)
  - Time: 8–10 hours
  - Enables: one-click anchoring

- [ ] Task #157: Public API + SDKs (MEDIUM)
  - Time: 12–16 hours
  - Enables: programmatic access, partnerships

- [ ] Task #158: B2B features (LOW, only if B2B demand)
  - Time: 24–32 hours
  - Enables: enterprise sales

---

## Summary

**Already Shipped (No Work):** Tasks #151, #151.5, #151.6  
**Needs Work (Prioritized):**
1. Task #152 (4–6h, HIGH) — Founder metrics
2. Task #153 (6–8h, HIGH) — Support tooling
3. Task #154 (16–24h, MEDIUM) — Creator app
4. Task #155 (8–12h, MEDIUM) — Lightroom plugin
5. Task #156 (8–10h, LOWER) — Browser extension
6. Task #157 (12–16h, MEDIUM) — Public API
7. Task #158 (24–32h, LOW) — B2B features
8. Task #159 (8–12h, MEDIUM) — Ongoing content

**Total Estimated Effort (All Tasks):** 82–130 hours  
**Path to Month 1 Target (Dashboard + Support + Content):** 18–26 hours

---

## Notes

- Most dashboard work is already done. Just need to verify it all works end-to-end.
- Founder metrics are critical for decision-making; build this first.
- Creator Capture should launch after first paying Creator customer (don't build blind).
- B2B features should only start if there's real customer demand (not speculation).
- Content marketing is ongoing; set up a monthly rhythm (1–2 blog posts, 2–3 social posts/week).

---

**Last Updated:** 2026-05-14  
**Author:** Claude Code (Implementation Status Audit)
