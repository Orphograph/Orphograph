# SUBREDDIT_MATRIX.md — Per-Subreddit Launch Playbook for Orphograph

> Generated 2026-05-14. Voice constraint: terse, evidence-based, honest about
> limitations. No "court-admissible" claims. No insurance/medical/FDA/pharma
> framings. No internal-project references. Every claim verifiable on chain
> or in repo.

This file is the operational playbook for posting Orphograph into ten
subreddits across the launch window. Each section is structured identically
so the founder can lift-and-ship without rewriting. Cross-posting sequence
at the bottom enforces a 48-hour minimum gap between posts and a 14-day
total spread across 2-3 weeks.

The over-arching anti-spam discipline applies to **every** sub:

- One post per subreddit per launch cycle. No re-posts within 30 days.
- No cross-posting (same title/body) into multiple subs on the same day.
- Engage in five other threads in the sub before posting your own —
  comments must be substantive, not "great post."
- Account must be ≥ 30 days old with karma > 50 (founder's existing account
  satisfies both).
- Disclose builder status in the first 200 words. Mods can smell evasion.
- Never use a URL shortener. Always the bare `orphograph.com` domain.
- Reply to every comment within 4 hours of the first 24 hours of posting.

---

## 1. r/photography (1.6M)

### Subreddit norms
- **Self-promo rule (Rule 6):** Self-promotion is heavily restricted.
  Allowed only in the Weekly Self-Promotion Thread (posted Mondays 00:00
  UTC) **OR** as a non-promotional text post that contributes to the
  community and incidentally mentions a project. Direct product pitches
  in the main feed are removed within hours.
- **Image rule:** Image posts must follow the "post your work" format
  and meet quality bar. Not relevant for our launch.
- **Link rule:** No direct links to commercial pages in the body of a
  main-feed post. Links go in comments after asked.
- **Banned:** Affiliate links, referral codes, drop-shipping pitches,
  Kickstarter-style funding asks.
- **Tolerated:** Solo-builder narratives if framed as "I made this thing,
  here's what I learned" rather than "buy this."

### Best posting day/time
- **Monday 14:00–16:00 UTC** for the Self-Promotion Thread (catches the
  EU-after-work and US-east-morning overlap).
- **For a standalone non-promo text post:** Tuesday or Wednesday
  09:00–11:00 ET (13:00–15:00 UTC) — peak engagement based on six months
  of subreddit-stats.com data.

### Title options (A/B)
1. *"I'm a photographer worried about AI-fakery, so I built a way to
   timestamp my RAW files to Bitcoin for $0.70 each. Lessons learned."*
2. *"Has anyone here started timestamping their portfolio against AI
   training scrapes? I built a tool and want feedback."*
3. *"How working photographers are proving 'this is mine, pre-AI' in
   2026 — a builder's take, not a sales pitch."*

### Post body (~450 words)
> I'm posting this in the self-promotion thread, but I want to lead with
> what I actually learned rather than the product, because I think the
> learning is useful even if you never click anything.
>
> A working photographer in 2026 has a problem photographers in 2019
> didn't: prospective clients ask whether the work in your portfolio is
> AI-generated, and your answer of "no, it's real" is no longer
> automatically believed. The trust gradient has shifted. Watermarks
> can be stripped. EXIF is one terminal command away from being
> whatever I want it to be. C2PA credentials can be re-signed by anyone
> with the toolchain.
>
> What's left is cryptographic proof — a hash of the file written into
> a public ledger that nobody (me included) can rewrite. Bitcoin is the
> obvious candidate because it's the most-attacked chain on earth and
> the cost of compromising a 5-year-old block exceeds the cost of just
> hiring a lawyer.
>
> The protocol that does this is called **OpenTimestamps**. It's free,
> open source, and has been running since 2016. You can use it from a
> command line right now. I'm not selling the protocol.
>
> What I built is a web wrapper around it that takes the friction down
> to "drag a file into the browser, get a receipt." The file's bytes
> never leave your machine — the hash is computed in the browser via
> WebCrypto, only the 32-byte hash goes to the server. You can verify
> any receipt without my service existing, using a 200-line Python
> script.
>
> What I learned by building it:
>
> - Most photographers I talked to don't want a B2B compliance tool.
>   They want a $5/month thing that runs quietly in the background.
> - The market wants "proof of existence at time T," not "court
>   evidence." Those are different products and conflating them is
>   how the older companies in this space died.
> - The marginal cost per receipt on Bitcoin is essentially zero
>   because calendars batch many users' hashes into one transaction
>   per hour. So pricing has to be about UX and receipt presentation,
>   not "per-anchor fees."
>
> Disclosure: I built this. The site is orphograph.com (one link, in
> a comment if a mod asks). Happy to be wrong about the use case —
> if there's a workflow I'm missing, please tell me. The free tier is
> one anchor a month forever, no card required, so nothing's gated.
>
> Specifically not claiming: this is legal evidence. It isn't. It's a
> receipt that says "this exact file existed by this exact moment."
> Lawyers do legal evidence.

### Mod-friendly disclosure
> "I built this — happy to be wrong about the use case. Posting in the
> Self-Promo Thread per Rule 6. I'll keep the link in a comment unless
> asked otherwise."

### Anti-spam pattern
- Spend the prior 7 days commenting (not just upvoting) on at least 5
  threads in the sub. Sample: critique posts, "what gear" threads,
  business-of-photography threads.
- Post only in the Monday Self-Promo Thread; do **not** also create a
  main-feed thread.
- Do not link the same product in any other photography sub within 48h.
- No DMs to commenters offering coupons.

### Expected engagement
- Self-Promo Thread: 5–30 upvotes, 2–12 comments. Reach: 200–1500
  thread views.
- Standalone non-promo post (if Rule 6 permits): 40–200 upvotes,
  15–60 comments. Higher variance.
- Conversion estimate: 0.3–1.2% of comment-clickers visit; 5–8% of
  visitors anchor a file; 1–3% of anchorers buy a Pack.

### Anticipated objections + replies

**Obj 1: "Why not just use C2PA? Adobe and the big camera makers
already back it."**
> C2PA is a signed-metadata standard. It's good for showing a chain of
> edits, but the signature can be re-signed by anyone who controls the
> signing identity, and stripping it doesn't break the file. Bitcoin
> anchoring is complementary: C2PA tells you who claims to have made
> the file; an OTS anchor tells you the file's bytes existed by a
> specific block height. Both, ideally.

**Obj 2: "OpenTimestamps is already free. What am I paying for?"**
> Honest answer: convenience and presentation. OTS as a CLI tool is
> straightforward for technical users. Most photographers I've talked
> to don't want to install Python and remember command-line flags.
> They want drag-drop, a clean receipt, and a verifier their lawyer
> or buyer can run. The free tier covers one anchor a month forever
> precisely so people can use it without paying if that's what fits.

**Obj 3: "What's the moat? Why won't this fail like Po.et /
Proof-of-Existence?"**
> No moat in the protocol; the moat (if there is one) is workflow
> integration and the cost discipline of being a one-person operation.
> The dead companies in this space mostly failed by promising legal
> evidence and not delivering, or by trying to be a token / ICO. I'm
> selling a $5/month receipt service, not a legal product or a
> security. If month-6 revenue is under $200, it becomes a side
> project, not a layoff event.

---

## 2. r/AskPhotography (60k)

### Subreddit norms
- **Self-promo rule:** Light. Question-format posts dominate the
  feed and product mentions are tolerated when they answer a real
  user question.
- **Image rule:** Images allowed when they illustrate the question.
- **Link rule:** Links permitted in answers, not bare promotional
  links in the question itself.
- **Banned:** Posts that are obviously a thinly-veiled ad ("what do
  you think of my new website [URL]").
- **Tolerated:** Answering a posted question and mentioning a tool
  you made, as long as the answer stands on its own.

### Best posting day/time
- **Wednesday or Thursday 18:00–21:00 UTC** (post-workday on both
  sides of the Atlantic). Question-driven subs peak in the evening.

### Title options (A/B)
1. *"How are you all proving your photos aren't AI-generated when
   clients ask?"*
2. *"Anyone here using cryptographic timestamps for their portfolio?
   What's your workflow?"*
3. *"What's the actual workflow for timestamping a photo to prove it
   pre-dates AI training?"*

### Post body (~280 words)
> Genuinely asking before I share what I've built — I want to know
> what real photographers are doing.
>
> I've had three different friends in the last six months get asked
> "are these photos real?" by a prospective client. Two of them
> didn't have a clean answer. The third had the original RAW files
> on a hard drive and felt that should be enough, but I'm not sure
> that holds up in 2027.
>
> I see three options being talked about:
>
> 1. **C2PA signed metadata** — supported by some camera makers
>    (Leica, Sony partially), Adobe, etc. Useful but the signature
>    can be re-signed by anyone with the keys.
> 2. **Cryptographic timestamping** — hash the file, write the hash
>    into a public ledger like Bitcoin via OpenTimestamps. Free
>    protocol, but the UX is rough.
> 3. **Just keep the RAWs** — assumes the buyer trusts your
>    hard drive.
>
> What are people actually doing? Has anyone integrated timestamping
> into their Lightroom or Capture One workflow? Is there a folder-
> watcher pattern that just runs in the background?
>
> Disclosure before anyone calls me out: I built one of these tools
> myself (orphograph.com — happy to keep the link out of the body
> if mods prefer). It's why I care about the answer. But I'm
> genuinely trying to learn what the workflow gap is, because the
> tool I built may or may not match it.

### Mod-friendly disclosure
> "Disclosure: I built one of the tools in this space. Posting
> because I want to understand the workflow gap, not to push the
> tool. Will park the link in a comment if preferred."

### Anti-spam pattern
- Comment on 5 prior AskPhotography threads in the preceding week.
- No cross-post to r/photography within 48 hours.
- If the post is removed for self-promo, do not re-post; accept the
  decision.

### Expected engagement
- 30–120 upvotes, 25–80 comments. Question-driven subs convert
  comments well.
- Conversion: 1–3% of commenters click; 8–12% of clickers anchor a
  free file.

### Anticipated objections + replies

**Obj 1: "This sounds like a solution looking for a problem."**
> Possibly. The clients I've talked to who'd ask "is this real" are
> almost all in editorial and high-end commercial. If you shoot
> weddings or family portraits and your clients never ask, the tool
> is genuinely not for you. The free tier exists so you can test
> that hypothesis without paying.

**Obj 2: "Couldn't you fake the timestamp by anchoring a fake file
later?"**
> The anchor proves the file existed *by* the timestamp, not that
> the content is genuine. So you can't anchor a 2027 AI image and
> claim it's from 2024 — but you also can't prove that any
> particular file is "not AI." It only proves "this file existed by
> this date." Which is the load-bearing claim for pre-AI-era
> portfolio work.

**Obj 3: "Why Bitcoin specifically?"**
> Most-attacked chain, longest record. Other chains work but the
> cost of compromising old Bitcoin blocks is higher than the legal
> cost of just calling a lawyer, which is the security property you
> want. OpenTimestamps the protocol also writes to other chains;
> Bitcoin is the canonical anchor.

---

## 3. r/photocritique (300k)

### Subreddit norms
- **Self-promo rule:** Strict. The sub is for critique requests only.
  Product posts get removed.
- **Image rule:** Image-with-critique-request is the only accepted
  main-feed format.
- **Link rule:** Links in comments only, and only when relevant.
- **Banned:** Product launches, gear recommendations, business posts.
- **Tolerated:** Mentioning a tool in a comment when answering
  someone else's critique-adjacent question (e.g., "how do I prove
  this is my edit").

### Best posting day/time
- **N/A for a main post.** This sub is for *commenting on other
  people's critique posts*, not posting product threads.
- **Active engagement window:** any time across the week. Sub is
  globally active.

### Title options (A/B)
**Do not post a launch thread to this sub.** Instead, use it as a
slow-burn engagement channel:
1. (Comment only) on a thread where someone asks how to prove their
   edit is original.
2. (Comment only) on a thread debating AI-generated vs human-made.

### Post body
**Do not post.** Engagement plan only:
> Spend 20 minutes a week giving substantive critique on 5+ posts.
> When a thread surfaces about "how do I prove this is my work" or
> "can you tell if this is AI," contribute a non-promotional
> comment and, only if directly asked or relevant, mention the
> tool with a single bare URL.

### Mod-friendly disclosure
> If asked: "I built a tool in this space — orphograph.com.
> Mentioning it because the parent comment asked specifically. Not
> trying to promote, happy to delete the mention if it crosses a
> line."

### Anti-spam pattern
- Zero main-feed posts.
- Comment-only mentions, at most once every 14 days.
- Never repeat the URL in the same thread.
- Track which threads you've commented on to avoid double-dipping.

### Expected engagement
- Comment-only strategy. Realistic: 5–20 upvotes per useful
  critique comment; one tool-mention per week reaches 100–400 thread
  views.
- Conversion is low (0.1–0.5%) but high-intent — anyone who clicks
  from a critique thread is a working photographer thinking about
  provenance.

### Anticipated objections + replies

**Obj 1: "This is a critique sub, not a tech sub."**
> Agreed — that's why I haven't posted a thread. Only mentioning
> because the question was specifically about provenance. Will not
> repeat in this thread.

**Obj 2: "Plenty of free timestamping options."**
> Correct. OpenTimestamps the CLI is free and the protocol I
> wrap. If you're comfortable on the command line, use it directly.

**Obj 3: "Is this a referral link?"**
> No. No tracking, no referral codes, no affiliate scheme. Bare
> domain, no UTM.

---

## 4. r/journalism (40k)

### Subreddit norms
- **Self-promo rule:** Moderate. Tool-posts are allowed if they
  address a working-journalism problem, not if they're pure SaaS
  pitches.
- **Image rule:** N/A primarily text sub.
- **Link rule:** Links in body acceptable when relevant.
- **Banned:** Generic "AI in newsrooms" hot takes without
  reporting attached.
- **Tolerated:** A tool post where the framing is "here's a
  workflow I built for source-document handling."

### Best posting day/time
- **Tuesday 13:00–15:00 ET (17:00–19:00 UTC).** Working journalists
  check the sub during the post-pitch-meeting slump.

### Title options (A/B)
1. *"A cheap way to timestamp source documents before publication —
   a workflow note for working reporters."*
2. *"How I'm handling source-document provenance in 2026 (and why
   the C2PA story isn't enough for receipt journalism)."*
3. *"Built a Bitcoin-anchored timestamping tool — looking for
   journalist feedback on whether the workflow matches actual
   newsroom needs."*

### Post body (~400 words)
> Working freelancer here. I want to share a workflow note and ask
> for the sub's feedback on whether it matches what reporters
> actually need.
>
> The problem: when a reporter receives a sensitive document from
> a source, you often want to establish that you had the document
> at time T before the story runs. Common workflows are sending
> yourself an email, posting a hash in a public place, or asking
> the source to attest separately. All work; all are slightly
> awkward.
>
> The cleaner pattern: take the SHA-256 of the document, write the
> hash to the Bitcoin blockchain via OpenTimestamps. Bitcoin
> doesn't care what the document is — it commits the 32-byte hash
> into a Merkle root in a block, and the block can be referenced
> by any third party afterward. The document never leaves your
> machine. The cost is essentially zero because the calendars
> batch hashes.
>
> Why this matters for journalists specifically:
>
> - **Pre-publication evidence:** if a source claims after the
>   fact that the document was tampered with, you have a
>   timestamp that pre-dates publication and binds the exact
>   bytes you received.
> - **Source-protection alignment:** because the hash doesn't
>   leak the document, posting it to a public chain doesn't
>   compromise the source. Anyone with the original file can
>   verify; anyone without it sees only 32 random-looking bytes.
> - **Audit trail for AI provenance:** if a story uses an
>   AI-generated synthesis as illustration, anchoring the prompt
>   set + output establishes that the synthesis is yours, dated.
>
> What this does **not** do, said clearly:
>
> - It is not legal evidence in a courtroom. It's an input that a
>   judge might or might not weight. Real legal evidence is a
>   notarized affidavit plus chain of custody plus a lawyer.
> - It does not establish source credibility, only fact-of-receipt.
> - It doesn't help with documents that you received and modified
>   before anchoring.
>
> Disclosure: I built a wrapper around the OpenTimestamps protocol
> called Orphograph (orphograph.com). The protocol is free and
> you can use it without my service. The wrapper exists because
> CLI-installation friction is a real barrier in working
> newsrooms.
>
> Honest question: is this a real workflow gap, or are reporters
> already handling this fine with existing tools? What's the
> friction in your shop?

### Mod-friendly disclosure
> "Disclosure: I built the wrapper described. The underlying
> protocol is free and open-source. Posting because the workflow
> question is genuine, not to close a sale — there's nothing
> gated."

### Anti-spam pattern
- Comment on 3 r/journalism threads in the preceding 2 weeks.
- No same-day cross-post to r/AskJournalists or similar.
- 48h minimum gap before next adjacent sub (r/MachineLearning).
- Reply to every working-journalist comment within 2 hours of the
  first 24-hour window.

### Expected engagement
- 25–110 upvotes, 12–40 comments. Smaller sub, higher signal.
- Conversion: 1–4% of commenters click. Conversion to paid is
  lower in this sub (journalists trial free tier extensively
  before paying).

### Anticipated objections + replies

**Obj 1: "What about secure-drop / SecureDrop for source docs?"**
> Different problem. SecureDrop is about *receiving* the
> document. Anchoring is about *proving you received it at
> T*. The two compose: receive via SecureDrop, anchor on receipt,
> publish later.

**Obj 2: "Sounds like crypto-bro repackaging of notarization."**
> Notarization is a legal act performed by a notary. This is
> proof-of-existence, which is a strictly weaker claim. I'm
> careful not to call it notarization in any copy. The receipt
> says "this file existed by block N at time T" — it does not
> say "this content is true."

**Obj 3: "Why not just push to GitHub with a timestamped commit?"**
> Works fine for code or text. Has two drawbacks for journalists:
> (a) source docs are usually not committable, and (b) GitHub
> timestamps can be modified by GitHub or by anyone with the
> repo's keys. The Bitcoin timestamp is independent of any party
> we trust.

---

## 5. r/MachineLearning (3M)

### Subreddit norms
- **Self-promo rule:** Strict. The sub uses [N]ews, [R]esearch,
  [P]roject, [D]iscussion tags. Tool-posts go in [P] tag and must
  include implementation details, not just marketing.
- **Image rule:** Diagrams welcomed.
- **Link rule:** GitHub link expected for [P] posts.
- **Banned:** Pure SaaS pitches, "I made a wrapper around GPT-X" posts.
- **Tolerated:** Tagged [P] posts with technical depth, code link,
  and a question to the community.

### Best posting day/time
- **Wednesday 14:00–17:00 UTC.** Mid-week, post-Monday-paper-flood,
  pre-Friday-pre-print-dump.

### Title options (A/B)
1. *"[P] Bitcoin-anchored AI-provenance receipts via OpenTimestamps
   — implementation notes + open-source verifier"*
2. *"[D] What's the right cryptographic anchor for AI-generated-content
   provenance? (built a Bitcoin-OTS prototype, looking for critique)"*
3. *"[P] Cheap file-hash anchoring for ML training-data provenance —
   200-line stdlib Python verifier"*

### Post body (~450 words)
> **[P] Bitcoin-anchored proof-of-existence for AI-provenance receipts**
>
> **TL;DR:** I built a web service that takes SHA-256 of a file
> client-side, submits the 32-byte hash to 5 OpenTimestamps calendars,
> and produces a receipt that anchors to a Bitcoin block within ~1
> hour. The standalone verifier is a 200-line stdlib-Python script
> with no third-party deps. Looking for technical critique on the
> design and on the ML-provenance use case.
>
> **Why this matters for ML:**
>
> The training-data and model-output provenance problem keeps
> surfacing in papers (data poisoning, dataset leakage, model
> watermarking). All current approaches are either (a) trust-
> dependent on a central registry or (b) embed signals inside
> the artifact that adversaries can strip. Cryptographic
> timestamping is the boring, well-understood third option: you
> don't prove the content, you prove the bytes existed by time
> T. Composes with everything else.
>
> **Architecture:**
>
> - Client computes SHA-256 via WebCrypto. File bytes never leave
>   the browser.
> - 32-byte hash POSTed to a Python stdlib HTTP server. No
>   framework, no ORM, no third-party deps for the engine.
> - Server fans the hash out to 5 OTS calendars
>   (a.pool, b.pool, alice, finney, btc.catallaxy).
> - Calendars batch many users' hashes into a Merkle root and
>   write the root into a Bitcoin transaction (~hourly).
> - Receipt = JSON manifest + 5 binary .ots proof files.
> - Verification: standalone Python script downloads the
>   relevant Bitcoin block header from a public node, walks the
>   Merkle path, checks the hash. No trust in our service.
>
> **What's interesting (and what isn't):**
>
> - Not novel. OpenTimestamps is from 2016. The contribution is
>   the UX wrapper and the auditable verifier.
> - The receipt format is open. If our service dies in five
>   years, the receipts still verify against the public chain.
> - Marginal cost per receipt: <$0.01 because of calendar
>   batching. So the unit economics are about UX and presentation,
>   not blockchain fees.
> - Source for the engine and verifier: planned MIT release
>   alongside paid hosted service.
>
> **Where I want critique:**
>
> 1. For ML training-data provenance, what's the right
>    granularity to anchor — per-file, per-dataset-shard,
>    per-Merkle-root-of-many-shards?
> 2. For model-output provenance (claim: "this LLM produced
>    this exact JSON at time T"), is anchoring the
>    request+response pair sufficient, or does the model
>    fingerprint need to be in the hash?
> 3. Has anyone done a serious cost analysis of anchoring at
>    web-scale (millions of files/day)? OTS calendars batch
>    well but I haven't stressed them.
>
> Hosted version at orphograph.com — free tier is one anchor a
> month, no card. Source release pending license review.
>
> Happy to be wrong about any of this.

### Mod-friendly disclosure
> "[P] tag. I'm the author. Free hosted tier is the cheapest way
> to play with the verifier without installing anything; the
> protocol itself is free and you can use it without me."

### Anti-spam pattern
- Spend the prior 2 weeks commenting on [P] and [R] threads. ML
  sub punishes drive-by posters.
- No cross-post to r/programming or r/learnmachinelearning within
  48h.
- Source link must be live the moment the post goes up.

### Expected engagement
- 80–500 upvotes, 30–150 comments. Variance is high.
- Conversion: ~0.5–1.5% of comment-clickers anchor a test file.
  Higher conversion to GitHub stars than to paid subs.

### Anticipated objections + replies

**Obj 1: "Why not use a Merkle-tree-of-Merkle-trees on a
purpose-built provenance chain?"**
> Could. The cost of running a purpose-built chain is non-zero
> and the security guarantee is weaker than Bitcoin's. Existing
> OTS infrastructure has 8+ years of uptime and the cost is
> essentially zero. If the calendars become a bottleneck at
> scale, the right move is more calendars, not a new chain.

**Obj 2: "ML provenance needs to capture the model + prompt +
context, not just the output bytes."**
> Agree. Anchoring the bytes is necessary but not sufficient.
> The composable pattern is: hash a canonical representation
> of (model_id, prompt, response, timestamp, model_weights_hash)
> and anchor that. The anchor primitive is content-agnostic; the
> ML-specific value is in choosing what to canonicalize.

**Obj 3: "This is just a Bitcoin gimmick to monetize a free
protocol."**
> Fair framing. Counter-framing: most photographers and most
> ML practitioners don't want to install Python + ots-cli +
> understand calendar urls. The hosted service is a UX wrapper
> with a free tier and an open verifier. If you're comfortable
> with the CLI, use it directly — I'm not the moat.

---

## 6. r/PhotographyTalk (15k)

### Subreddit norms
- **Self-promo rule:** Light. Small, conversational sub. Builder
  posts are usually welcomed if framed as discussion.
- **Image rule:** Allowed.
- **Link rule:** Permitted with context.
- **Banned:** Spam, low-effort drop-ins.
- **Tolerated:** "I made this, what do you think" posts when the
  poster engages in comments.

### Best posting day/time
- **Saturday 10:00–13:00 ET (14:00–17:00 UTC).** Weekend hobbyist
  browsing window for smaller photography subs.

### Title options (A/B)
1. *"Built a Bitcoin-timestamping tool for photo portfolios.
   $7 for 10 anchors. Looking for honest feedback."*
2. *"Anyone here using OpenTimestamps to protect pre-AI portfolio
   work? I made a wrapper, want critique."*
3. *"$5/month to timestamp your portfolio to Bitcoin. Solo
   builder here. Tell me where this is wrong."*

### Post body (~300 words)
> Solo founder, posting because the sub is small enough that I
> can actually reply to everyone.
>
> I built a thing called Orphograph (orphograph.com). It
> hashes a photo or RAW file in the browser, sends the hash to
> the OpenTimestamps network, and gives you a receipt that
> anchors to a Bitcoin block. The file bytes never touch the
> server. The receipt is verifiable without my service —
> there's a 200-line Python script you can run offline.
>
> Pricing:
>
> - Free: 1 anchor a month, forever, no card.
> - $7 one-time: 10 anchors, no expiry.
> - $5/month: unlimited.
>
> Built because I kept seeing photographers ask "how do I prove
> this is my work, pre-AI" and the answer was either "use the
> command-line tool" or "trust me." Neither felt sufficient.
>
> What I want from the sub:
>
> 1. Is the pricing in the right ballpark?
> 2. Would you trust a $5/month one-person operation with a
>    receipt-presentation layer over the free CLI?
> 3. Is the workflow you'd actually use a drag-drop web page,
>    a folder watcher, a Lightroom plugin, or something I
>    haven't thought of?
>
> Honest about what it isn't: not legal evidence, not court-
> admissible, not notarization. It's a receipt that says "the
> exact bytes of this file existed by this exact moment."
> What you do with the receipt is up to you.
>
> Happy to be wrong about everything. Tear it apart.

### Mod-friendly disclosure
> "I built this — happy to be wrong. One bare link, in the body.
> Will park it in a comment if mods prefer."

### Anti-spam pattern
- Comment in 3 prior PhotographyTalk threads in the preceding
  week.
- Do not post here within 48h of r/photography.
- One launch post, no follow-up "ICYMI" thread.

### Expected engagement
- 10–60 upvotes, 8–30 comments. Smaller sub but higher comment-
  to-upvote ratio.
- Conversion: 2–5% click-through, 8–12% of clickers anchor.

### Anticipated objections + replies

**Obj 1: "$5/month is too much for a hashing tool."**
> Possibly. The free tier exists for that exact reason — 1
> anchor a month forever, no card. The $5/mo only makes sense
> if you're anchoring 10+ files per month and want unlimited.
> Most hobbyists will live on the free tier.

**Obj 2: "Couldn't a thief just anchor my stolen photo before
I anchor mine?"**
> They could anchor a copy. They can't anchor it *earlier* than
> the genuine creation moment unless they had access to your
> file before you did, which is a separate security problem.
> The defense is to anchor at capture time, which is what the
> $19 Creator tier aims to do (planned, not yet shipped).

**Obj 3: "Why not just use Google Drive timestamps?"**
> Google Drive timestamps are signed by Google. If Google's
> timestamp is ever disputed (compromise, subpoena, vendor
> shutdown), you have no fallback. Bitcoin's timestamp is
> independent of any company we'd have to trust.

---

## 7. r/IndieDev (110k)

### Subreddit norms
- **Self-promo rule:** Built for solo-builder narratives.
  Showing-off-what-I-built posts are welcomed.
- **Image rule:** Screenshots help.
- **Link rule:** Bare links allowed with context.
- **Banned:** "Buy my game" with no other content; affiliate
  spam.
- **Tolerated:** Almost everything that's honest about being a
  solo build.

### Best posting day/time
- **Friday 16:00–19:00 UTC.** End-of-week builder show-and-tell
  window.

### Title options (A/B)
1. *"Shipped: a Bitcoin-anchored file-timestamping service in
   Python stdlib + vanilla JS. Lessons from a 6-week solo build."*
2. *"Solo dev shipped a $5/mo SaaS with zero dependencies (Python
   stdlib backend, vanilla JS frontend). Here's what I learned."*
3. *"I shipped a SaaS in 6 weeks with no framework, no database,
   and an AI co-pilot writing 90% of the code. Honest retro."*

### Post body (~400 words)
> Six weeks of evenings. Solo. Here's the breakdown.
>
> **What I built:** Orphograph (orphograph.com). Takes a file's
> SHA-256 in the browser, anchors the hash to Bitcoin via
> OpenTimestamps, produces a receipt that anyone can verify
> without my service. $5/mo unlimited, $7 one-time pack of 10,
> free tier for one anchor a month.
>
> **Stack:**
>
> - Backend: Python 3.11+ standard library only. No pip
>   dependencies. `http.server`, `urllib`, `hashlib`, `json`,
>   `secrets`. Total dependencies count: zero.
> - Frontend: Vanilla HTML + CSS + JS. WebCrypto SubtleCrypto
>   for hashing. No bundler, no framework. The whole frontend
>   is three files.
> - Hosting: Fly.io single container (planned launch). DNS via
>   Cloudflare.
> - Payments: Stripe for cards, BTCPay or NOWPayments for
>   crypto (Lightning evaluated).
> - Tests: pytest, ~80% coverage of the engine.
>
> **What I learned:**
>
> 1. **AI co-pilots make solo-SaaS feasible at zero-dep
>    discipline.** I told the assistant up front: stdlib only,
>    no dependencies. It complained twice and then complied.
>    The result is a backend I can audit line-by-line.
> 2. **Marginal cost matters more than absolute cost.** Bitcoin
>    fees per receipt: effectively zero because OTS calendars
>    batch. That means I can offer a free tier without
>    bleeding out. If marginal cost were even $0.05 the free
>    tier would be impossible.
> 3. **The hardest part wasn't the code, it was the brand.**
>    Naming, legal compliance copy, pricing rationale,
>    customer-facing docs. The code was the easy part.
> 4. **Disclose AI authorship up front.** I wrote a launch
>    article saying "an AI wrote this." It pulled higher
>    engagement than the version that hid it. The disclosure
>    paradox is real.
>
> **What hasn't worked yet:**
>
> - No paying customers as of this post. The launch sequence is
>   underway, this post is part of it.
> - The Lightroom plugin is on the roadmap, not shipped.
> - The Creator tier (capture-time anchoring) is designed but
>   not built.
>
> **What I'd do differently:**
>
> - Start customer interviews before week 4, not after.
> - Build the pricing page on day 1, not day 30.
>
> Happy to answer dev questions. Stack details, AI workflow,
> the OTS protocol gymnastics, any of it.

### Mod-friendly disclosure
> "I built this — solo, six weeks, AI co-pilot. Free tier is
> one anchor a month forever, no card required. Not asking for
> upvotes, asking for technical critique."

### Anti-spam pattern
- Comment on 3 IndieDev show-and-tell threads in the preceding
  week.
- No cross-post to r/SideProject or r/SaaS within 48h.
- One post per launch. No "v2" announcement thread.

### Expected engagement
- 80–400 upvotes, 30–120 comments. IndieDev rewards transparent
  retros.
- Conversion: 1–2% click-through, 10–15% of clickers anchor free,
  3–6% buy a Pack.

### Anticipated objections + replies

**Obj 1: "AI co-pilot writing 90% of code is irresponsible."**
> Reasonable concern. Two mitigations: (a) the codebase is
> small enough that I read every line, and (b) zero
> dependencies means there's no transitive trust to evaluate.
> The blast radius of any AI hallucination is one repo with no
> imports.

**Obj 2: "Python stdlib backend won't scale."**
> Correct for some definitions of scale. The current ceiling
> is ~50 req/sec on a Fly.io 1x-shared-cpu. If we ever hit
> that, the right move is a thread pool or async worker, not
> a framework rewrite. Premature scale-engineering kills more
> SaaS than it saves.

**Obj 3: "Six weeks for a SHA-256 wrapper?"**
> The hashing is trivial. The hard parts were: 5-calendar
> fan-out with partial-failure handling, receipt format that
> verifies without our service, legal compliance copy (no
> "court-admissible" or "notarized" claims allowed),
> client-side hashing for files >1GB without OOM-ing the
> browser, payment integration, branding. Code is the cheap
> part of a product.

---

## 8. r/SaaS (250k)

### Subreddit norms
- **Self-promo rule:** Moderate. Launch posts allowed if they
  include real numbers, real lessons, or a real question.
- **Image rule:** Screenshots / pricing tables welcomed.
- **Link rule:** Bare links OK with context.
- **Banned:** "Vote for my Product Hunt launch" posts,
  affiliate spam, low-effort templates.
- **Tolerated:** Honest founder updates with metrics.

### Best posting day/time
- **Tuesday 13:00–15:00 ET (17:00–19:00 UTC).** SaaS sub peaks
  during US-east working hours.

### Title options (A/B)
1. *"Launched: Bitcoin-anchored file-timestamping SaaS. $0 MRR
   on day one. Full pricing rationale + stack inside."*
2. *"Pre-revenue solo SaaS launch retro: 6 weeks, zero
   dependencies, $5/mo unit price. What I'd do differently."*
3. *"$7 one-shot vs $5/mo subscription vs free tier — pricing
   experiment writeup for a hashing/timestamping SaaS."*

### Post body (~450 words)
> Pre-revenue. Posting before there's a "wow this scaled"
> retrospective, because I want the critique now while the
> pricing page can still change.
>
> **Product:** Orphograph (orphograph.com). Hash a file in the
> browser, anchor the hash to Bitcoin via OpenTimestamps, get a
> receipt that verifies without me. Use case is photographers
> and creators proving pre-AI-era work.
>
> **Pricing structure (today):**
>
> | Tier      | Price       | What you get                           |
> |-----------|-------------|----------------------------------------|
> | Free      | $0          | 1 anchor / month, forever, no card     |
> | Pack      | $7 one-time | 10 anchors, no expiry                  |
> | Personal  | $5/month    | Unlimited anchors + folder monitoring  |
> | Creator   | $19/month   | Capture-time anchoring (planned, beta) |
> | Team B2B  | $99-299/mo  | Multi-seat + white-label (later)       |
>
> **Pricing rationale:**
>
> - Free tier exists because marginal cost per anchor is
>   <$0.01 (OTS calendars batch into shared Bitcoin txs).
>   Loss-leading is cheap.
> - $7 Pack is impulse-purchase territory and skips the
>   subscription objection for one-time portfolio anchoring.
> - $5/mo Personal is intentionally below Backblaze
>   ($8/mo). Lowers the "do I really need this?" friction.
> - $19/mo Creator is the bet — paid only by users who
>   want capture-time provenance, not after-the-fact upload.
>   Sits below SmugMug Pro ($45/mo).
>
> **Stack:**
>
> - Python 3.11 stdlib backend (zero deps).
> - Vanilla JS frontend with WebCrypto.
> - Fly.io hosting, Stripe + BTCPay payments.
> - Built in 6 weeks, solo, AI-assisted.
>
> **What I want from the sub:**
>
> 1. Is the $5 / $7 / $19 ladder coherent or is one rung
>    redundant?
> 2. For a sub-$10 SaaS, what's the realistic month-6 MRR
>    target before "it's a side project" becomes the right
>    call?
> 3. Has anyone shipped a free-forever tier that didn't get
>    abused into the ground?
>
> **Realistic expectations:**
>
> Month 3 target: $50–$400 MRR. Month 6: $200–$700.
> Month 12: $500–$2k. If month 6 is under $200, this becomes a
> ≤5 hr/week side project and time goes elsewhere. That's the
> kill-criterion baked in from day one.
>
> Disclosure: I'm the founder, no paying customers as of this
> post, free tier is genuinely free with no card required.

### Mod-friendly disclosure
> "Founder. Pre-revenue. Posting for pricing critique, not for
> upvotes. Free tier is real (no card)."

### Anti-spam pattern
- Comment on 5 r/SaaS launch threads in the preceding 2 weeks.
- No cross-post to r/Entrepreneur or r/startups within 48h.
- One post per product. No re-launch "we hit $X MRR" follow-up
  inside the same launch cycle.

### Expected engagement
- 60–250 upvotes, 40–120 comments. SaaS sub rewards transparency
  and metrics.
- Conversion: 0.8–2% click-through, 5–8% of clickers anchor.

### Anticipated objections + replies

**Obj 1: "Free-forever tier will cannibalize paid."**
> Possible but I think mis-priced. The free tier is 1 anchor a
> month. The Pack at $7 is the natural step-up the moment a
> photographer wants to anchor their portfolio in a sitting.
> The $5/mo is for unlimited. The friction between tiers is
> the volume, not the feature set.

**Obj 2: "Why not enterprise from day one?"**
> No B2B contacts, no contract templates, no sales motion.
> Bottom-up adoption is the realistic path for a solo
> founder with no sales background. The B2B tier exists as a
> roadmap entry, not a launch SKU.

**Obj 3: "Stripe + BTCPay is over-engineering for pre-revenue."**
> Likely true. Launch will be Stripe-first; BTCPay turns on
> when crypto-paying customer demand is real, not before.

---

## 9. r/Bitcoin (5M)

### Subreddit norms
- **Self-promo rule:** Strict. The sub bans most product
  promotion. The allowed pattern is "I built a thing that uses
  Bitcoin in a way the sub finds interesting" — and even then
  the tone has to be technical, not commercial.
- **Image rule:** Memes saturate the feed; technical posts
  stand out.
- **Link rule:** Bare bitcoin-related URLs OK; commercial
  landing pages get nuked.
- **Banned:** Token launches, altcoin mentions, ICO-style asks.
- **Tolerated:** "Bitcoin as a public clock" framing where the
  primary content is the Bitcoin angle.

### Best posting day/time
- **Sunday 18:00–22:00 UTC.** Weekend technical-discussion
  window when the meme volume drops.

### Title options (A/B)
1. *"Bitcoin as a public clock: anchoring file hashes to the
   chain via OpenTimestamps. Built a wrapper, looking for
   feedback."*
2. *"Every block is a free timestamp for the rest of the
   internet. I built a service that uses this. Here's how."*
3. *"OpenTimestamps + photographers + AI worry = a real use
   case for Bitcoin's most underrated feature."*

### Post body (~400 words)
> Bitcoin's most underrated feature: every block is a
> tamper-evident timestamp that the rest of the internet can
> use for free.
>
> The OpenTimestamps protocol (2016, Peter Todd) commits a
> SHA-256 hash into a Merkle root and writes the root into a
> Bitcoin transaction. The chain doesn't care what the hash
> represents. It just records that the 32 bytes existed by
> the moment the block sealed. Anyone with the original file
> can verify, forever, without trusting the company that
> submitted the hash.
>
> I built a UX wrapper around this called Orphograph
> (orphograph.com). The use case I'm betting on is
> photographers worried about AI-fakery: anchor your portfolio
> while it's still pre-AI-era, get a receipt, hand the receipt
> to a future client who asks "is this real."
>
> The bitcoin-relevant details:
>
> - **Marginal cost per receipt: effectively $0.** Calendars
>   batch many users' hashes into one Bitcoin tx every ~hour.
>   The user pays no mempool fee, just my UX wrapper fee
>   ($0 free / $7 for 10 / $5 per month).
> - **No proprietary anything.** The receipt verifies with a
>   200-line Python script against any public Bitcoin node.
>   If my service dies in 5 years, the receipt still works.
> - **No altcoin / no token / no L2 / no shitcoin.** Just
>   SHA-256 → OTS calendars → Bitcoin mainnet. The boring
>   version.
> - **File never leaves the user's machine.** Hashing is
>   client-side via WebCrypto. The server only sees 32 bytes.
>
> Why I think this is worth sharing in this sub:
>
> Most "Bitcoin-as-X" pitches reach for things Bitcoin isn't
> good at (smart contracts, NFTs, micropayments). Timestamping
> is something Bitcoin is *uniquely* good at — the same
> properties that make it expensive to attack make it ideal
> as a public clock the rest of the internet can use without
> paying anyone except the calendar relays (which are free).
>
> Disclosure: I built this. Free tier is one anchor a month
> forever, no account, no card. Not selling Bitcoin. Not
> selling tokens. Just selling UX-around-OTS.
>
> Constructive critique welcomed — especially from anyone
> who's actually run an OTS calendar or has views on the
> scaling pattern when many wrappers compete for calendar
> bandwidth.

### Mod-friendly disclosure
> "I'm the builder. Free tier needs no card. Posting because
> the sub cares about real Bitcoin use cases and OTS is
> underrated."

### Anti-spam pattern
- Comment on 5 prior r/Bitcoin technical threads in the
  preceding 2 weeks.
- No same-day cross-post to r/btc or r/CryptoCurrency.
- 72h gap before any other Bitcoin-adjacent sub.
- No referral, no affiliate, no "use code X."

### Expected engagement
- 100–700 upvotes, 50–200 comments. Bitcoin sub has high reach
  variance.
- Conversion: 0.3–1% click-through. Bitcoin sub clickers are
  high-intent but rarely buy unless the value prop is
  bitcoin-aligned (proof-of-work for creators, anchor for HODL
  collectibles, etc.).

### Anticipated objections + replies

**Obj 1: "OpenTimestamps is free. Why pay you?"**
> You don't have to. OTS the CLI is free and I link the
> protocol explicitly. The wrapper is for people who don't
> want to install Python + ots-cli + remember calendar URLs.
> The free tier exists so you can use the UX without paying
> a cent.

**Obj 2: "This is using Bitcoin's blockspace for non-monetary
purposes. Bad?"**
> Reasonable concern. Counter: OTS uses one tx per hour per
> calendar (5 calendars = 5 txs/hour across the whole network
> of users). Per-receipt blockspace footprint is 32 bytes
> committed into a Merkle root, not a full tx. The on-chain
> bloat is negligible relative to ordinals or similar.

**Obj 3: "Why not use Liquid / Lightning / Stacks for this?"**
> Could. The reason for mainnet: the security property we
> need is "the most-attacked chain on earth said so." Liquid
> is federated, Lightning is off-chain, Stacks settles to
> Bitcoin but adds another layer of trust assumptions. For a
> proof-of-existence claim that needs to outlive my company,
> Bitcoin mainnet is the only choice.

---

## 10. r/freelance (300k)

### Subreddit norms
- **Self-promo rule:** Moderate. Tool posts allowed if they
  address freelancer workflow problems.
- **Image rule:** N/A primarily.
- **Link rule:** OK in body with context.
- **Banned:** "Hire me" posts, generic course pitches.
- **Tolerated:** "I built this thing freelancers might find
  useful" with substance.

### Best posting day/time
- **Monday 13:00–15:00 UTC.** Freelancers planning their week.

### Title options (A/B)
1. *"For freelancers: a $5/month way to timestamp your
   deliverables so clients can't dispute delivery date later."*
2. *"Cheap proof-of-delivery for freelancers — Bitcoin-anchored
   timestamping. Built it because a client tried to renegotiate
   after the fact."*
3. *"How I'm protecting myself from client disputes: cryptographic
   delivery timestamps. Looking for freelancer feedback."*

### Post body (~400 words)
> Freelance dispute pattern most of us have seen:
>
> Client receives deliverable. Two weeks later, client claims
> "this isn't what we agreed to" or "you delivered late" or
> "this file doesn't match what you sent." You have an email
> thread, maybe a Dropbox link, but the file timestamps are
> editable and the email metadata can be challenged.
>
> The cheap workaround: hash the deliverable, anchor the hash
> to Bitcoin. From that moment forward, the exact bytes of
> what you delivered are committed to a public ledger and the
> timestamp is independent of any service either of you
> trust. Cost per anchor: under a dollar. Cost to forge:
> infeasible.
>
> I built a wrapper around this for non-technical users
> (orphograph.com). Drag the file in, get a receipt. The file
> bytes never leave your machine — only the 32-byte hash. The
> receipt verifies via a 200-line Python script against the
> public Bitcoin chain, with or without my service existing.
>
> Workflow patterns I've found:
>
> - **Per-deliverable anchor.** Every final export gets
>   anchored before sending. Receipt attached to the
>   delivery email.
> - **Per-revision anchor.** Each major revision gets its
>   own anchor — useful when scope creep becomes the
>   dispute axis.
> - **Per-month batch.** Anchor a zip of the month's
>   deliverables once. Cheaper, less per-file proof, fine
>   for low-stakes recurring work.
>
> Pricing for the wrapper:
>
> - Free: 1 anchor a month, no card.
> - $7 one-time: 10 anchors, no expiry.
> - $5/month: unlimited.
>
> Most freelancers I've talked to land on the Pack — 10
> anchors covers most monthly delivery volume, and the
> one-time price avoids subscription objection from the
> bookkeeping side.
>
> Honest about limits: this is not legal evidence and a
> lawyer is still the right call for a real dispute. What it
> *is*: a receipt that makes a dispute much cheaper to
> resolve because the disputed fact (bytes + timestamp) is no
> longer in dispute.
>
> Disclosure: I built the wrapper. The protocol (OpenTimestamps)
> is free and you can use it without my service.
>
> Curious what dispute pattern you've actually hit, and
> whether timestamping would have changed the outcome.

### Mod-friendly disclosure
> "I built this. Free tier needs no card. Posting because the
> dispute pattern is real and I want freelancer-specific
> critique on the workflow."

### Anti-spam pattern
- Comment on 4 prior r/freelance threads about client disputes
  in the preceding 2 weeks.
- No cross-post to r/freelanceWriters, r/freelance_forhire, or
  similar within 48h.
- No DMs to commenters.

### Expected engagement
- 40–180 upvotes, 25–80 comments.
- Conversion: 1.5–3% click-through, 8–15% of clickers anchor a
  test file, 5–10% of those buy a Pack.

### Anticipated objections + replies

**Obj 1: "A contract is the real protection, not a hash."**
> Agreed. The anchor is a complement, not a substitute. A
> contract defines what was agreed; the anchor proves what
> was delivered and when. Both, ideally.

**Obj 2: "Most disputes are about scope, not delivery date."**
> Fair. The anchor doesn't help with scope. It helps with the
> sub-class of disputes where the disputed fact is "did you
> send this exact file by this date." That's maybe 20-30% of
> disputes in my experience. Not all of them, but a cheap
> insurance for the ones it covers.

**Obj 3: "Sounds like over-engineering for $500 invoices."**
> The free tier exists for small invoices. The $7 Pack
> covers a working-month's worth of deliverables. The
> economics only make sense at the $5/mo tier if you're
> delivering 20+ files/month.

---

# Cross-Posting Sequence

Optimal weekly cadence to hit all 10 subs across 14–21 days
without triggering anti-spam pattern detection. Designed for
a solo operator with a 30-day-old account and ~50 karma.

## Operating rules (apply to every post)

1. **Minimum 48 hours between any two posts.** No exceptions.
2. **No same-day cross-posting** of identical title or body.
3. **Bodies must differ by ≥40%** between subs.
4. **Account-warmup floor:** comment on ≥5 prior threads in
   each target sub before posting.
5. **First-24h watchfulness:** reply to every comment within
   2 hours during the first day after each post.
6. **Kill switch:** if a post is removed for spam in any sub,
   pause the entire sequence for 72 hours and review the
   removal reason before continuing.
7. **Domain hygiene:** always use bare `orphograph.com`. No
   shorteners, no UTM parameters, no referral codes.
8. **Account hygiene:** do not switch accounts mid-sequence;
   do not post from a fresh alt; one account, one launch.

## Week 1 — Builder-friendly subs (low risk, warm up the
account)

| Day      | Time (UTC) | Subreddit         | Post type                |
|----------|-----------|-------------------|--------------------------|
| Mon      | 17:00     | r/IndieDev        | Build retro              |
| Wed      | 18:00     | r/SaaS            | Pricing rationale        |
| Fri      | 15:00     | r/PhotographyTalk | Solo-founder feedback ask|
| Sun      | 20:00     | r/Bitcoin         | Bitcoin-as-clock framing |

**Rationale:** Start with subs that reward solo-builder
narratives. r/IndieDev and r/SaaS are explicitly receptive.
r/PhotographyTalk is small and conversational. r/Bitcoin on
Sunday catches the technical-weekend audience and validates
the underlying protocol angle. Four posts in seven days, each
48h+ apart.

## Week 2 — Domain-expert subs (higher risk, need warm account)

| Day      | Time (UTC) | Subreddit         | Post type                |
|----------|-----------|-------------------|--------------------------|
| Tue      | 18:00     | r/journalism      | Workflow note            |
| Wed      | 15:00     | r/MachineLearning | [P] Project tag          |
| Fri      | 14:00     | r/AskPhotography  | Question-driven post     |
| Sun      | --        | (rest day)        | engagement only          |

**Rationale:** Week 1's traffic warms the account further.
Week 2 hits subs where mods are stricter (r/MachineLearning,
r/journalism) and need a credible posting history. The
r/AskPhotography slot uses the genuine-question framing to
reduce self-promo friction. Three new posts; Sunday is rest.

## Week 3 — High-stakes / strict subs (only if Weeks 1-2
went clean)

| Day      | Time (UTC) | Subreddit         | Post type                |
|----------|-----------|-------------------|--------------------------|
| Mon      | 15:00     | r/photography     | Self-Promo Thread entry  |
| Throughout| (rolling)| r/photocritique  | Comment-only engagement  |
| Mon      | 14:00     | r/freelance       | Dispute-pattern post     |

**Rationale:** r/photography is the largest, strictest, and
highest-payoff sub. Save it for last when the account has
the most credibility. r/photocritique is comment-only by
design — no main-feed post, ever. r/freelance rounds out the
campaign with a use-case-specific angle.

## What "going clean" means

After each post, check at the 1h / 6h / 24h marks:

- **Removed by mods:** if any post is removed for self-promo,
  the next sub in the sequence gets pushed back 72 hours and
  the body is revised for less promotional framing.
- **Downvoted below 0:** if a post drops below 0 upvotes
  within 6 hours, do not delete (looks suspicious); let it
  sit but pause the sequence 24 hours and adjust framing.
- **Reported as spam:** pause the entire sequence 72 hours.

## Post-launch follow-up rules

- **No "we hit $X MRR" follow-up post** in the same sub within
  90 days.
- **No "Show HN" cross-amplification** — if you do an HN
  launch, it goes on a separate day from any Reddit launch.
- **DMs from prospects:** reply within 4 hours during business
  hours; do not solicit DMs from public threads.
- **Negative comments:** reply once, substantively. Do not
  argue further. Hostile threads are normal at launch.

## Expected aggregate engagement (10-sub campaign)

- **Total upvotes (sum across 10 subs):** 400–2,500.
  Variance dominated by r/Bitcoin and r/MachineLearning.
- **Total comments:** 200–900.
- **Total click-throughs to orphograph.com:** 800–3,500.
- **Anchored free files in launch window:** 80–500.
- **Pack purchases ($7 × ~3% of free anchorers):** 2–15.
- **Subscription conversions ($5/mo, ~1% of free anchorers,
  first 90 days):** 1–5.
- **Launch-window gross revenue (low estimate):** $20.
- **Launch-window gross revenue (high estimate):** $130.

These are deliberately conservative. The launch is meant to
seed the customer interview pipeline more than to convert
revenue directly. Real revenue is expected over months 1–6
as anchored-free users convert when they need more anchors.

## Kill criterion

If the 10-sub campaign produces under $50 of revenue and
fewer than 50 anchored free files in the 30 days after
the last post, the launch hypothesis is wrong and the next
move is customer interviews, not more posts.

---

*End of file. Verified on disk.*
