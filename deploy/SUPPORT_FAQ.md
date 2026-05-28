# Support FAQ — Orphograph

**Common questions from users, with honest answers.**

---

## General: What Is Orphograph?

**Q: What does Orphograph actually do?**

A: Orphograph creates a timestamp proof that a specific file existed by a specific date. Here's how:

1. You drop a file in your browser
2. We compute its SHA-256 hash (in your browser, not on our server)
3. We submit that hash to 5 independent Bitcoin timestamp services (OpenTimestamps calendars)
4. They include your hash in a Bitcoin transaction (~1 hour)
5. You get a receipt with your hash, the timestamp, and the Bitcoin proof

The receipt proves: "This file hash existed on Bitcoin on [date]."

**Q: Why would I want this?**

A: You need proof that you created / owned / had a file before a specific date:
- **Photographer:** "This photo is mine, from before AI training data [date]"
- **Designer:** "This artwork is original, created by me on [date]"
- **Journalist:** "I had this source material on [date]"
- **Developer:** "I wrote this code before it was copied"

Orphograph doesn't prove you made it, but it proves you had it on a specific date.

**Q: Is this a blockchain?**

A: No. Bitcoin is a blockchain, but Orphograph uses Bitcoin. You're anchoring to Bitcoin's ledger, which is public and immutable. We just provide a user-friendly interface for it.

---

## Technical: How It Works

**Q: Do you upload my file?**

A: No. Your browser computes the hash locally using WebCrypto. Only the 32-byte hash is sent to our server, never the file itself. We can't recover your file from the hash — it's cryptographically impossible.

**Q: What's a SHA-256 hash?**

A: A hash is a fingerprint. SHA-256 creates a 64-character unique identifier for any file:
- Change one byte in the file → hash completely changes
- Same file → same hash always
- Different files → different hashes (with cryptographic certainty)

Example:
```
File: cat.jpg
Hash: a3f0e42b7c1d9e4f...8b2c3a1d (unique fingerprint)

File: cat.jpg (with 1 pixel changed)
Hash: x9f1b3c2e7d4a6f8... (completely different)
```

**Q: Can you recover the file from the hash?**

A: No. SHA-256 is a one-way function. Given a hash, you cannot reverse-engineer the original file. This is why it's safe for privacy — we see the hash, not the file.

**Q: Why 5 calendars instead of 1?**

A: Redundancy. If one calendar service goes down, your proof is still valid on the other 4. Bitcoin itself is still immutable — we just anchor to multiple paths for safety.

**Q: What if I lose my receipt?**

A: The receipt is a JSON file + 5 binary .ots files. If you lose it, you can't prove what you anchored (without it). Keep backups. We plan to add a vault feature (Month 2) so you don't lose receipts.

---

## Legal: What This Proves & Doesn't Prove

**Q: Is this court-admissible?**

A: No. Orphograph is not a qualified trust-service provider. We don't comply with eIDAS or any government standard for legal evidence. 

If you need court-admissible timestamps, consult:
- A digital evidence specialist
- A regulated notary service
- An eIDAS-compliant provider

**That said:** A Bitcoin-anchored receipt is *strong evidence* that you had the file on the date shown. It's just not guaranteed to be accepted by a court. Use it as supporting evidence, not as the sole proof.

**Q: Does this prove I created the file?**

A: No. It proves the file existed on the date shown. It doesn't prove you created it, you own it, or you have permission to use it.

Example: If you copy my photo and anchor it, your receipt proves you had the file, but not that you took the photo.

**Q: Does this stop my work from being stolen?**

A: No. The receipt proves you had it first, which helps in disputes, but it doesn't technically prevent copying.

Example:
- You anchor a photo on Jan 1
- Someone steals it on Feb 1
- Your receipt proves you had it first
- A court might rule in your favor
- But: they still have a copy, and the receipt doesn't delete it

**Q: If AI training happened after my anchor date, does that help?**

A: Maybe. If you can prove:
1. You anchored a work on [date]
2. AI training data only included works published before [date]
3. The AI-generated output is suspiciously similar

Then your receipt is evidence that you had the original. But proving causation in court is complex. This is not a silver bullet.

---

## AI & Synthesis: Specific Questions

**Q: Can AI-generated images be anchored?**

A: Yes. But your receipt doesn't claim "I made this" — it claims "I had this hash on [date]."

If you anchor an AI-generated image:
- Proof: "I had this specific image on [date]"
- NOT proof: "I am the creator" or "I made it"

If you want to disclose AI generation, we're adding an optional metadata field (Month 2).

**Q: Can AI make a file with the same hash as mine?**

A: In practice, never. SHA-256 is cryptographically secure. The odds of a collision are 1 in 2^256 (effectively impossible).

But: AI can make a *visually similar* file with a *different hash*. That's why the anchor proves the specific file, not the concept.

Example:
```
Your original photo:         hash: a3f0e42b7c1d9e4f
AI makes similar photo:      hash: x9f1b3c2e7d4a6f8

Different hashes = different files
Your receipt proves YOU had the original
```

**Q: Does Orphograph detect AI-generated images?**

A: No. We can't tell if an image was created by a human or AI. We just timestamp the file's hash.

You can use external tools (e.g., Hive Moderation API, or AI detection services) to check if an image is likely AI-generated, but we don't do that.

**Q: What if someone anchors stolen content?**

A: We can't see the file, so we can't police it. But:
1. Anchoring doesn't grant ownership
2. If the content is stolen, the receipt doesn't help the thief in court
3. We'll comply with legal takedown notices (DMCA, etc.) if served

**Q: Does the timestamp prove originality?**

A: No. It proves existence at a time. If the file existed before your anchor date, the receipt doesn't prove it's original.

Example:
- Bob creates a photo on Jan 1
- Alice steals it on Feb 1 and anchors it on Feb 1
- Alice's receipt proves she had it on Feb 1
- But it doesn't prove she created it (Bob's date is earlier)

---

## Payments & Ownership

**Q: How much does Orphograph cost?**

A: 
- **Free:** 1 anchor per calendar month (rate-limited)
- **Writer Pack:** $19 one-time for 10 anchor credits (never expire)
- **Standing Order:** $9/month for unlimited anchors (planned, not yet live)
- **Creator:** $19/month for capture-time provenance app + API (planned, not yet live)

**Q: What's a Pack credit?**

A: One credit = one anchor. Buy a Pack, get 10 credits. Use them anytime. Credits never expire.

**Q: Can I share a Pack with others?**

A: No. Each Pack is tied to a claim code (like a gift card number). Anyone with the code can use the credits, so keep it private. Pack sharing coming in Month 2.

**Q: What if I don't use all my Pack credits?**

A: You keep them forever. No expiration. We'll remind you if you have unused credits after 30 days (coming Month 2).

**Q: Can I get a refund?**

A: Yes. If you buy a Pack and don't use any credits, we'll refund it within 7 days. Email support@orphograph.com with your receipt.

Partially-used Packs are refunded at our discretion (usually pro-rata based on unused credits).

**Q: Why Bitcoin instead of traditional payment?**

A: We don't offer Bitcoin payment yet (coming later). Current payments are via Stripe (Visa, Mastercard, etc.).

---

## Privacy & Data

**Q: What data do you collect?**

A: Only:
1. **Your file hash** (the 32-byte SHA-256)
2. **Your IP prefix** (first 3 octets, e.g., 1.2.3.0/24 — not full IP)
3. **Your email** (if you buy a Pack)
4. **The receipt itself** (JSON metadata, not the file)

We do NOT collect:
- Your full IP address
- Your file contents
- Analytics data
- Third-party tracking cookies
- Your browser fingerprint

**Q: Can you see my file?**

A: No. You hash it in your browser. Only the hash reaches us.

**Q: How long do you keep my data?**

A: 
- **Receipt data:** Indefinitely (it's the product)
- **Free-tier receipts:** May be pruned after 30 days (your local copy is still valid)
- **Email addresses:** 7 years (for tax records and refunds)
- **IP prefixes:** 24 hours, then deleted

**Q: Can I delete my data?**

A: Yes. Email support@orphograph.com or use the account deletion endpoint if you're signed in:
- GET /api/me/export — download all your data
- POST /api/me/delete — delete your account

We respond within 30 days (usually same-day).

**Q: Are you GDPR compliant?**

A: Yes. We offer data export and deletion. If you're in the EU, UK, or California, you have explicit rights to access and deletion.

---

## Security & Verification

**Q: How do I verify my receipt?**

A: Two ways:

1. **Online verifier:** Go to https://orphograph.com/verify, upload your receipt.json + .ots files, click verify.
2. **Offline (local):** Download our open-source verifier from https://github.com/..., run it locally with your receipt.

The offline verifier uses only standard Python libraries and Bitcoin's public ledger. You don't need to trust us.

**Q: What if Orphograph shuts down?**

A: Your receipt is still valid forever. The Bitcoin timestamp is immutable. You can verify against Bitcoin's ledger directly using the open-source verifier.

**Q: How do I know the receipt is really mine?**

A: The receipt includes:
- Your file's hash (you can recompute it and verify it matches)
- The Bitcoin transaction ID (you can verify it on blockchain.com)
- The timestamp (you can check the block date on blockchain.com)

All verifiable without trusting us.

**Q: Can someone forge a receipt?**

A: In theory, someone could create a fake JSON file claiming to be a receipt. But:
1. When verified, it won't match Bitcoin's actual ledger
2. The verification will fail, exposing it as fake
3. Bitcoin's ledger is immutable — you can't change it

**Q: Is my receipt encrypted?**

A: The receipt JSON is not encrypted. It's the same format as OpenTimestamps uses (publicly documented). Don't anchor secrets you wouldn't share with a peer.

If the file is sensitive, don't share the receipt ID publicly (coming Month 2: private receipts).

---

## Troubleshooting

**Q: I anchored a file but didn't save the receipt. Can you resend it?**

A: Yes, but only if you're signed in and bought a Pack. Sign in, go to your account, and look for the receipt in your history.

If you used the free anchor (not signed in), we might have pruned it after 30 days. Sorry. Always save your receipt locally.

**Q: The verify page says "Pending proof"? What does that mean?**

A: Your hash was submitted but hasn't appeared in a Bitcoin block yet. Bitcoin blocks happen ~10 minutes apart, and OpenTimestamps batches many hashes together, so expect:
- "Pending": first 1-2 hours
- "Final": once it's confirmed on Bitcoin (6+ block confirmations)

This is normal.

**Q: I got an error message "Rate limit exceeded." What?**

A: You've created more than 10 anchors per hour from your IP address. Try again in 1 hour, or buy a Pack (no rate limit for Pack users).

This prevents bot attacks while letting humans anchor freely.

**Q: The receipt page won't load. Is the server down?**

A: Check https://orphograph.com/status.html for server status. If it says "all systems operational," try:
1. Refresh the page
2. Clear your browser cache
3. Try a different browser
4. Try on your phone

If still broken, email support@orphograph.com with your receipt ID.

**Q: My receipt says my file hash is wrong. Did I get scammed?**

A: Unlikely. The receipt shows the hash of the file you submitted. If you later modify the file even by 1 byte, the hash changes.

To verify:
```bash
sha256sum myfile.jpg
# Compare output to receipt.json hash field
```

If they match, the receipt is correct.

---

## Feedback & Feature Requests

**Q: Can you add [feature]?**

A: Maybe. Email us at hello@orphograph.com with your feature request. We read all feedback.

**Current roadmap (Month 2-3):**
- Standing Order tier ($9/mo for unlimited anchors)
- Creator Capture app (capture-time provenance)
- Private receipts (only you can view)
- Receipt vault (save all receipts)
- Lightroom plugin (for photographers)
- Browser extension (right-click anchor)

**Q: Can you help me prove my work in court?**

A: No. We provide the timestamp proof, but we can't provide legal advice. Consult a lawyer or digital evidence specialist.

Our receipt is evidence, not proof of ownership or originality.

**Q: Can I use Orphograph for [use case]?**

A: **Yes to:**
- Proving file existence at a date
- Creating provenance records
- Building a timeline of work
- Copyright / authorship disputes (as supporting evidence)

**No to:**
- Replacing legal notarization
- Replacing qualified timestamp services (eIDAS)
- Proving you made a file
- Claiming legal ownership
- Copyright registration (use official services like US Copyright Office)

---

## Contact & Support

**Email:** support@orphograph.com  
**Response time:** Within 24 hours (usually same-day)

**For urgent issues:** Try Twitter @orphograph_app (not guaranteed)

**What we can help with:**
- Account access issues
- Refunds & billing
- Technical troubleshooting
- General questions

**What we can't help with:**
- Legal advice (consult a lawyer)
- Court cases (consult a lawyer)
- Proving originality (not in scope)
- Disputes over who created a work (beyond our remit)

---

**Last updated:** 2026-05-15  
**Version:** 1.0  
**For internal use + customer support**
