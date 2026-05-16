# Launch Blockers — Completion Guide (TODAY 2026-05-14)

**PRIORITY:** All 4 blockers must be COMPLETE today for launch this week.

---

## 🔴 BLOCKER #146: Publish Privacy Policy + Terms of Service

### Current Status: ⏳ NOT DONE

### What's Required:
Two pages must be published and linked in footer before orphograph.com goes live.

### Files Already Exist:
- ✅ `web/privacy.html` — Privacy Policy template
- ✅ `web/terms.html` — Terms of Service template
- Check if they're complete or just stubs

### Action Items (90 minutes):
1. **Review existing docs:**
   ```bash
   cat web/privacy.html | head -50
   cat web/terms.html | head -50
   ```

2. **If complete:** Just verify they're linked in footer
   - Open `web/index.html`
   - Check if footer has links to `/privacy.html` and `/terms.html`
   - If not, add: `<a href="/privacy.html">Privacy</a> · <a href="/terms.html">Terms</a>`

3. **If incomplete:** Fill in key sections:
   - **Privacy Policy** must cover:
     - No file uploads (hashing is client-side)
     - IP truncation to /24 for rate limiting
     - GDPR data export + deletion paths
     - No third-party analytics/tracking
     - Stripe payment processing (link to Stripe's privacy)
     - Email delivery (explain Resend/Mailgun)
     - No cookies for tracking (only session cookie for auth)

   - **Terms of Service** must cover:
     - What Orphograph is (not legal evidence, not court-admissible)
     - Limitation of liability
     - Refund policy (how long to request refund)
     - Dispute resolution (email to support)
     - User responsibilities (don't upload illegal content)
     - Service availability (best-effort, not guaranteed SLA)
     - Changes to terms (we can modify with notice)

4. **Quick template** (if starting from scratch, 30 min write):
   ```markdown
   # Privacy Policy
   
   Last Updated: 2026-05-14
   
   ## What We Collect
   - Email (for subscriptions + password-less sign-in)
   - File hashes (SHA-256, computed client-side)
   - IP address (truncated to /24 for rate limiting, never stored full)
   - Anchor metadata (timestamp, calendar submission status)
   
   ## What We Don't Collect
   - Your files (never uploaded to us)
   - Full IP addresses (truncated before logging)
   - Browser fingerprints
   - Third-party analytics cookies
   
   ## Your Rights
   - GDPR right to access: GET /api/me/export
   - GDPR right to deletion: DELETE /api/me
   - Both are instant, no manual review needed
   
   ## Contact
   Email: support@orphograph.com
   ```

### ✅ Definition of Done:
- Privacy Policy published at `/privacy.html`
- Terms of Service published at `/terms.html`
- Both linked in footer of web/index.html
- Both pages load without errors (check Console)
- No "TODO" or placeholder text remaining

### Time: 90 minutes max

---

## 🔴 BLOCKER #147: Verify orphograph.com DNS + Email Delivery

### Current Status: ⏳ NOT DONE

### What's Required:
Verify domain is live + emails actually arrive in real inboxes.

### Action Items (60 minutes):

1. **DNS Verification:**
   ```bash
   # Test from command line
   nslookup orphograph.com
   dig orphograph.com +short
   
   # Should show Fly's IP (IPv4) + IPv6 addresses
   # Example output:
   # A: 1.2.3.4
   # AAAA: 2001:db8::1
   ```

2. **HTTPS Verification:**
   ```bash
   # Confirm certificate is valid
   curl -v https://orphograph.com/ 2>&1 | grep -E "Certificate|Subject:|Issuer:"
   
   # Should show valid Let's Encrypt certificate
   ```

3. **Magic-Link Email Test:**
   ```
   a. Go to https://orphograph.com/signin.html
   b. Enter YOUR email (use Gmail/Outlook, something you can check)
   c. Click "Send me a sign-in link"
   d. Check inbox (wait 10 seconds)
   e. If email arrives: ✓ PASS
      Click link, verify you can sign in
   f. If email doesn't arrive:
      - Check spam folder
      - Check Mailer.py logs (stderr)
      - Debug: is Resend API key set? (RESEND_API_KEY env var?)
      - Is SMTP configured?
   ```

4. **Receipt Email Test:**
   ```
   a. Sign in to your account
   b. Drop a test file on landing page
   c. Anchor it (wait for receipt)
   d. Watch for receipt email
   e. If email arrives: ✓ PASS
   f. If not: debug as above
   ```

5. **Mobile Test:**
   ```
   a. Open https://orphograph.com on iPhone/Android
   b. Layout should be responsive (no horizontal scroll)
   c. Drop zone should work
   d. Buttons should be touch-friendly (44px+)
   ```

### ✅ Definition of Done:
- [ ] `nslookup orphograph.com` returns valid IPs
- [ ] `curl https://orphograph.com` returns 200 + valid certificate
- [ ] Magic-link email arrives in inbox within 30 seconds
- [ ] Can click link, sign in, see account page
- [ ] Receipt email arrives after anchoring
- [ ] Mobile layout is responsive
- [ ] No CORS errors in browser console

### Time: 60 minutes max

### Troubleshooting:
- **DNS not resolving:** Wait 24h for propagation, or manually set `/etc/hosts` for testing
- **Email not arriving:** Check `.env.local` for RESEND_API_KEY or SMTP config
- **Certificate error:** Fly should auto-issue; wait 5 min, clear browser cache
- **Slow anchor:** If orphograph.com is resolving but doesn't load, check Fly instance status

---

## 🔴 BLOCKER #148: Run Plagiarism Check on Marketing Copy

### Current Status: ⏳ NOT DONE

### What's Required:
Verify landing page copy is original (no uncredited paraphrases from competitors).

### Competitors to Check:
- OpenTimestamps (opentimestamps.org)
- OriginStamp (originstamp.com)
- WordProof (wordproof.io)
- Signl (signl.app) — newer competitor

### Action Items (45 minutes):

1. **Extract landing copy:**
   ```bash
   # Get all text from web/index.html (ignore HTML tags)
   grep -oP '>[^<]+<' web/index.html | sed 's/[<>]//g' | grep -v '^ *$' > /tmp/orpho_copy.txt
   ```

2. **Manual comparison (fastest, 15 min):**
   - Read 3 competitor landing pages
   - Visually scan for similar phrases
   - Check: do we use their exact words?
   - Expected: almost all original (unless credited)

3. **Automated check (optional, 5 min):**
   - Use Copyscape.com (free version)
   - Paste orphograph.com landing URL
   - Reports: % original vs. matched text
   - Threshold: >95% original is acceptable

4. **Specific check points (10 min):**
   - "Prove your art existed" — original to us? YES (our phrasing)
   - "Your file never leaves your browser" — original? YES
   - "Bitcoin blockchain" — generic phrase, OK to use
   - Any phrases that start "Orphograph is..." vs "OpenTimestamps is..." — check for paraphrase

5. **If issues found (20 min):**
   - Rewrite problematic sentences
   - Add citations where needed ("Similar to [competitor], Orphograph does X")
   - Ensure critique is respectful (attack ideas, not founders)

### ✅ Definition of Done:
- [ ] Reviewed 3 competitor landing pages
- [ ] No uncredited paraphrases found
- [ ] All copy reads as original voice
- [ ] No "court-admissible," "legally binding," "notarized" claims (removed in prior audits)
- [ ] Any borrowed structures are credited

### Time: 45 minutes max

### Files to Review:
- `web/index.html` — landing page (main target)
- `web/about.html` — about page (secondary)
- `web/buy.html` — pricing page (tertiary)

---

## 🔴 BLOCKER #149: End-to-End Beta Test with 3 Friends

### Current Status: ⏳ NOT DONE

### What's Required:
3 real people successfully anchor a file + verify it, without your help.

### Action Items (90 minutes total, 30 min per tester):

1. **Recruit 3 testers:**
   - Email / text 3 friends with this link: `https://orphograph.com`
   - Brief instructions: "Try dropping a file, getting a receipt, verifying it. Let me know if anything breaks or confuses you."
   - Give them 20 minutes to test

2. **Per-tester checklist (do this 3x):**
   - [ ] Tester goes to landing page
   - [ ] Tester drags a file (PDF, image, anything)
   - [ ] File gets hashed + receipt created
   - [ ] Tester sees receipt JSON + 5 OTS files
   - [ ] Tester can click "verify" + see proof
   - [ ] Tester can download receipt
   - [ ] Tester tries Pack: requests magic link → clicks → signs in
   - [ ] Tester buys Pack, sees claim code, uses it
   - [ ] Error messages are clear (not "error 503")
   - [ ] Mobile layout works (if they test on phone)

3. **Collect feedback (5 min per tester):**
   - Ask: "What confused you?"
   - Ask: "What would you change?"
   - Ask: "Would you pay for this?"
   - Record: bugs + suggestions

4. **Fix showstoppers only (up to 30 min):**
   - Obvious errors (404, crash) → fix immediately
   - Confusing copy → reword if obvious
   - Slow performance → investigate
   - **Do NOT polish:** ignore "the button should be red" style feedback

### ✅ Definition of Done:
- [ ] 3 people tested (names + timestamps)
- [ ] All 3 successfully anchored without help
- [ ] All 3 could verify receipt
- [ ] No critical bugs found (or fixed immediately)
- [ ] Feedback collected + logged
- [ ] Confidence: "Users can use this without my hand-holding"

### Time: 90 minutes max

### If test fails:
- Tester can't drop file → check browser console for errors
- Tester can't anchor → check calendar connectivity
- Tester gets "error 503" → check Stripe secret is set
- Tester on mobile sees horizontal scroll → adjust CSS

---

## 🔴 BLOCKER #150: Create Git Tag v0.1.0 + GitHub Release

### Current Status: ⏳ NOT DONE (trivial, 10 min)

### Action Items:

1. **Tag current commit:**
   ```bash
   cd ~/orphograph
   git tag -a v0.1.0 -m "Launch MVP: Bitcoin-anchored proofs, GDPR-ready"
   git push origin v0.1.0
   ```

2. **Create GitHub Release:**
   ```bash
   gh release create v0.1.0 \
     --title "Orphograph v0.1.0 — MVP Launch" \
     --notes "Client-side hashing, 5 OTS calendars, GDPR export/delete. All security audits passed (SECURITY.md, PAYMENT_PII_AUDIT.md). 262 tests."
   ```

3. **Add to Release:**
   - Link to SECURITY.md
   - Link to PAYMENT_PII_AUDIT.md
   - Link to sample receipt (web/sample/)
   - Changelog (copy from git log summary)

### ✅ Definition of Done:
- [ ] Git tag `v0.1.0` exists on GitHub
- [ ] GitHub Release page shows release notes
- [ ] Release is public + searchable

### Time: 10 minutes

---

## 📅 TIMELINE FOR TODAY (2026-05-14)

| Task | Owner | Time | Start | Done |
|---|---|---|---|---|
| #146: Publish Privacy + Terms | Founder | 90m | 13:00 | 14:30 |
| #147: Verify DNS + email | Founder | 60m | 14:30 | 15:30 |
| #148: Plagiarism check | Founder | 45m | 15:30 | 16:15 |
| #149: Beta test (3 friends) | Founder | 90m | 16:15 | 17:45 |
| #150: Git tag + release | Founder | 10m | 17:45 | 17:55 |

**Total: 295 minutes (< 5 hours)**

**Target Completion: 17:55 UTC (5:55 PM)**

---

## ✅ LAUNCH GO DECISION

Once all 5 blockers complete: **👍 GREEN LIGHT TO LAUNCH**

Deploy to Fly production at 18:00 UTC (or immediately after last blocker passes).

Announce on Show HN + Twitter same day.

---

## If Something Breaks

**You still have until 2026-05-18 to fix critical issues.**

This blocker list is the minimum to go live safely. If you hit a blocker:
1. Document the issue
2. Fix the root cause (don't band-aid)
3. Test the fix
4. Continue to next blocker

We've already passed all technical gates (262 tests, 2 audits). These 5 items are the last safety checks before you scale.

