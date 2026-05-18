# Safety Gaps — Non-Code Premortem Items — 2026-05-18

Scope: premortem failure modes that **cannot be fixed by editing this repo alone**. Each requires founder action, an infra/vendor change, or physical hardware. Companion documents:

- `deploy/HMAC_SECRET_AUDIT_2026_05_18.md` (sibling audit, B-8)
- `deploy/LAUNCH_CHECKLIST.md` (pre-push scrub procedure)
- `deploy/FLY_PREFLIGHT.md` (Fly machine baseline)

Eight gaps below. Cost figures are USD. Hours = founder wall-clock, not Claude time.

---

## Gap 1 — Fly capacity (premortem A-3)

**What fails.** Production runs one shared-CPU VM in `iad` (see `fly.toml`: `cpus=1`, `memory_mb=512`, `primary_region='iad'`, `min_machines_running=1`). The 256MB figure in the premortem is stale — the VM is already at 512MB. Failure modes that remain: (a) traffic spike from Show HN front page drives concurrent OTS submissions + receipt-PDF renders past 512MB → OOM kill → users see 502s until Fly restarts the machine (~10s downtime per OOM); (b) `iad` region outage takes the entire product down; (c) the single mount at `/app/data` is region-pinned, so a second machine in a different region needs a separate volume and a shared-state plan before it's safe to enable. Detection: `fly logs -a orphograph` showing `OOM killed` lines, or `/api/health` returning non-200 from the external monitor. Live `fly metrics` was not collected inside this audit (sandbox blocked `fly` CLI); founder should capture the 24h memory floor before sizing.

1. Capture current memory floor: `fly metrics -a orphograph` (look at last 24h "Memory used" panel) — record the p95 in this file before scaling.
2. Bump VM to 1GB for headroom: `fly scale memory 1024 -a orphograph`. Shared-CPU 1GB is roughly **+$3.19/mo** over 512MB (shared-1x-cpu 512MB ≈ $1.94/mo, 1GB ≈ $5.13/mo as of 2026-05).
3. Add a warm standby region: `fly volumes create orphograph_data --region ord --size 1 -a orphograph` then `fly scale count 2 --region ord -a orphograph`. Note: this needs the app to be made stateless or sticky-routed first (volumes don't replicate); ship as **standby cold-start** rather than active-active until that's solved. Second machine + volume ≈ **+$5.13/mo + $0.15/GB-mo = +$5.28/mo**.
4. Flip `auto_stop_machines = 'off'` already done; verify `min_machines_running = 1` per region after step 3 so the standby actually exists.
5. Add an external uptime monitor (UptimeRobot free tier or BetterStack free): HTTP GET `https://orphograph.com/api/health` every 60s, page on 2 consecutive fails to Telegram.

**Cost.** ~$8.50/mo recurring once both steps land. **Hours.** 1.5h (sizing + volume + monitor setup + smoke test).

---

## Gap 2 — Hardware wallet seed loss (premortem A-4)

**What fails.** All Bitcoin revenue lands at the address in `data/btc_address.txt` (current value: `bc1qclvjjmwmr294rydv4x0dc787nx9jd8j4ny4jaz`). Recovery of that address depends entirely on the BIP39 seed phrase held by the founder. Single-location seed loss modes: house fire, theft, flood, founder-incapacitation with no heir access. Server compromise is **not** in scope here — the seed never touches the Fly host; the host only holds the receive address. Detection is post-hoc: founder tries to sweep funds and the wallet won't restore. There is no remote alarm for "your steel plate is missing."

1. Pick a split scheme. Two viable paths, **do not mix them**:
   - **(A) SLIP-39 / Shamir on hardware**: Trezor Model T (~$219) or Keystone 3 Pro (~$149) natively support 3-of-5 Shamir. Generate 5 shares, distribute. Coldcard Mk4 (~$157) supports Seed XOR (different scheme, also workable).
   - **(B) Steel BIP39 plate split across geographies**: buy 3 steel plates (Blockmit, Cryptosteel Capsule, or Cobo Tablet — $40–$90 each). Stamp the **full** seed on each; store in 3 separately-controlled locations (home safe, bank SDB, trusted relative). Simpler but increases attack surface: any single plate-finder can steal the whole address.
2. **Recommended for solo-founder scale**: path (A) with Trezor Model T, 3-of-5 Shamir, shares stored as follows — 2 home (safe + hidden), 1 bank safe-deposit box, 1 sealed with attorney holding the LLC docs, 1 with out-of-state trusted family. Reconstruction requires any 3 → no single location compromise loses funds.
3. Confirm payout flow does NOT require the seed to be online: `data/btc_address.txt` is a watch-only string; the server never signs. Recovery is offline-only on a separate device.
4. Document the recovery runbook in `deploy/WALLET_QUICK.md` (already exists — extend with the share-location map; do **not** name the locations in the repo).
5. Test recovery on a brand-new wallet from 3 shares before relying on the scheme. Wipe and re-derive — must match the same xpub.

**Cost.** Trezor Model T $219 + 1 spare backup card pack $20 + bank SDB ~$75/yr + steel plate $60 (defense-in-depth) = **~$375 one-time + $75/yr**. **Hours.** 4h (purchase, init, share distribution, recovery test).

---

## Gap 3 — OTS pool calendars 404 on `/timestamp/<X>` (premortem B-6)

**What fails.** `server/engine.py:33-39` ships 5 calendars; positions 0 and 1 are `https://a.pool.opentimestamps.org` and `https://b.pool.opentimestamps.org`. These submit fine to `/digest`, but post-submission upgrade requests against `/timestamp/<commitment>` return 404 reliably, while the direct calendars (`alice.btc.calendar.opentimestamps.org`, `finney.calendar.eternitywall.com`, `btc.calendar.catallaxy.com`) return 200 once the commitment is in a Bitcoin block. The pool hosts appear to be DNS-level round-robins that don't index individual stamps — the request lands on a peer that didn't see the original submission. Net effect: a receipt with all 5 calendars submitted shows 3/5 upgrades on verify, not 5/5, even after on-chain confirmation. The receipt is still cryptographically valid — three calendar paths to Bitcoin is past the `MIN_CALENDARS_OK=3` floor — but the dashboard misleadingly shows the user as "partial."

1. File an upstream issue at `https://github.com/opentimestamps/opentimestamps-server/issues` describing the observation: pool endpoints accept `POST /digest` (200) but return 404 on subsequent `GET /timestamp/<commitment>` against the same hash. Title suggestion: "Pool endpoints (a.pool/b.pool) do not serve /timestamp/<X> for commitments they accepted." Include three example commitment hashes and the timestamps of submission vs upgrade attempt.
2. Pending upstream fix, the proposed (un-applied) repo change is to swap pool entries for the underlying operator's direct calendar — `a.pool.opentimestamps.org` → `alice.btc.calendar.opentimestamps.org` (already in the list), `b.pool.opentimestamps.org` → `bob.btc.calendar.opentimestamps.org` (operated by Peter Todd, the same upstream maintainer). This would drop to 4 unique calendars (alice is duplicated) and require adding bob to maintain 5. **Not applied here per scope; document only.**
3. Alternative: keep the pool entries for `POST /digest` redundancy (they do accept submissions reliably) and add a second `_upgrade` code path that prefers direct calendars when the pool returns 404. This is a code change for a later sprint, not infra.
4. Until then, communicate honestly on the receipt UI: "3 of 5 calendars confirmed = full receipt" — already enforced by `MIN_CALENDARS_OK=3`.

**Cost.** $0. **Hours.** 0.5h (file the GitHub issue + cross-link the response in this file when received).

---

## Gap 4 — HMAC secret rotation + history scrub (premortem B-8)

**Status.** See `deploy/HMAC_SECRET_AUDIT_2026_05_18.md` from sibling audit. Verdict in that file: **no leak** — Pattern A clean-slate `git init` was executed before first push, and `git log --all --full-history -- data/.hmac_secret` returns empty. Earliest commit `6c18d58 release: 0.1.0` authored by the pseudonymous `Orphograph <orphograph@users.noreply.github.com>`. Total history depth on all refs: 22 commits. No remediation required today.

**What still fails.** Two future-risk scenarios. (a) Operational mistake: a future engineer commits `data/.hmac_secret` to a tracked path (e.g., a debug dump in `tests/`) and pushes before noticing. (b) HMAC key reuse beyond its threat-model lifetime — even an un-leaked key should be rotated periodically because every issued token signed under it is replay-window material; current implementation has no rotation cadence. Detection: GitHub secret-scanning would catch (a) if Push Protection is enabled on the repo; (b) is silent and only detected by policy.

1. **Enable GitHub Push Protection** on `github.com/Orphograph/Orphograph` (Settings → Code security → Secret scanning → Push protection: **on**). Free for public repos.
2. **Rotate the HMAC secret on a 90-day cadence** even without a known leak. Procedure:
   ```
   fly ssh console -a orphograph
   rm /app/data/.hmac_secret
   exit
   fly machines restart -a orphograph
   ```
   This regenerates a fresh random key on next boot and invalidates all outstanding auth tokens (acceptable for current scale). Alternative: set via Fly secret instead of file — `fly secrets set ORPHO_HMAC_SECRET=$(openssl rand -hex 32) -a orphograph` — only viable after the server is taught to prefer env over file (code change, out of scope here).
3. **If a future audit DOES flip** (any `git log` returns a SHA touching `data/`), run the runbook already documented at `HMAC_SECRET_AUDIT_2026_05_18.md` §"What to do IF this audit ever flips" — rotate first, then `git filter-repo --invert-paths --path data/.hmac_secret ...` followed by `git push origin --force --all`. `git filter-repo` install: `brew install git-filter-repo`. BFG (`bfg-repo-cleaner`) is the Java alternative; either works; filter-repo is currently recommended upstream.
4. **Calendar a 90-day rotation reminder** (founder Calendar / Reminders / Todoist): "Orphograph HMAC rotation — 2026-08-18."

**Cost.** $0. **Hours.** 0.25h (toggle Push Protection + set calendar reminder); 0.25h every 90d (rotation).

---

## Gap 5 — Cloudflare dependency (premortem B-16)

**What fails.** `orphograph.com` resolves through Cloudflare DNS (and likely Cloudflare proxy for TLS edge). A CF DNS outage (precedent: 2022-06-21, 2023-11-02, both ~1-hour outages affecting `dns.cloudflare.com`) takes the apex unreachable even though Fly is healthy. CF proxy outages (more common, 2-5x/year) return 5xx pages branded as CF, not orphograph. Detection: external uptime monitor (Gap 1 step 5) reporting `orphograph.com` 5xx while `<app-name>.fly.dev` direct returns 200.

1. **Lower TTLs now**, before any outage, so a manual cutover finishes inside one TTL window:
   - Cloudflare dashboard → orphograph.com → DNS → Records → for each A/AAAA/CNAME on the apex and `www`: edit → TTL → **Auto → 60 seconds** (CF allows "Auto" or a specific seconds value when proxy is off; if proxied/orange-cloud, TTL is fixed at Auto and the cutover instead requires turning off proxy first).
2. **Document the fallback A record**. Pull Fly's dedicated IPv4 (if allocated): `fly ips list -a orphograph` → record the `v4 dedicated` line. If only shared IPv4 exists, allocate dedicated: `fly ips allocate-v4 -a orphograph` (one-time +$2/mo). Write the IP into `deploy/PLAN_B_TUNNEL.md` or a new `deploy/DNS_FALLBACK.md` with the literal A record value.
3. **Manual cutover runbook** (also written into the fallback doc):
   - Symptom confirmed: external monitor red AND `dig @1.1.1.1 orphograph.com` times out.
   - Log into a secondary DNS provider already pre-configured: register orphograph.com as a **secondary zone** at deSEC.io (free, EU-hosted, primary-secondary supported) OR pre-stage the zone at Bunny DNS (free, $1/mo for premium).
   - Change registrar (where the domain itself is registered, e.g., Porkbun/Cloudflare Registrar) nameservers from `*.ns.cloudflare.com` to the secondary provider's NS records. Propagation: 60s if TTLs are pre-lowered per step 1; up to registrar's NS TTL (often 86400s) at the parent zone — this is the slow link, not CF's record TTL.
   - Validate: `dig @8.8.8.8 orphograph.com` returns the Fly dedicated IP.
4. **Cloudflare Registrar lock-in note**: if the domain is also registered AT Cloudflare (not just DNS-hosted), the registrar side of the cutover is itself dependent on CF being up. Mitigation: transfer registration to a separate registrar (Porkbun ~$10/yr, Namecheap ~$13/yr) and keep CF for DNS+proxy only. This is the single highest-leverage fix in this gap.

**Cost.** $2/mo dedicated IPv4 + ~$10/yr secondary registrar (if transferred) = **~$34/yr**. **Hours.** 2h (TTL lower + IP allocation + secondary zone pre-stage + runbook write-up + registrar transfer initiation).

---

## Gap 6 — No Show HN / launch posts shipped (premortem B-19)

**What fails.** Site is live since 2026-05-16, anchored, working. Zero inbound traffic except direct-typed URL. Without a coordinated launch the product silently times out: low signups → no anchors → no revenue → loss of motivation to maintain. This is pure founder-execution; no code or infra unblocks it. Detection: `/api/stats` showing flat anchor count week-over-week.

Drafts already on disk in `outreach/` (line counts in parentheses):

| File | Lines | Channel | Status |
|---|---|---|---|
| `show_hn_v2.md` | 37 | Hacker News Show HN | ready, shortest cleanest draft |
| `show_hn_draft.md` | 108 | Hacker News (long) | superseded by v2 |
| `show_hn_research_brief.clean.md` | 91 | HN comment prep | use as Q&A cheat sheet |
| `twitter_launch_v2.md` | 102 | X/Twitter | ready |
| `twitter_launch_thread.md` | 169 | X/Twitter (long thread) | superseded by v2 |
| `linkedin_launch.md` | 119 | LinkedIn | ready |
| `reddit_photography_v2.md` | 60 | r/photography | ready |
| `reddit_r_photography.md` | 78 | r/photography (long) | superseded by v2 |
| `indie_hackers_post.md` | 96 | indiehackers.com | ready |
| `cold_dm_twitter.md` | 102 | DM script | use after Show HN lands |
| `beta_recruitment_kit.md` | 126 | press-kit / replies | reference, not a post |

1. **Tuesday 09:00–10:00 ET** — submit `outreach/show_hn_v2.md` to news.ycombinator.com/submit. Tuesday morning ET is the highest-density window for Show HN front-page reach. Title under 80 chars, URL = `https://orphograph.com`. Have `show_hn_research_brief.clean.md` open in another tab for Q&A.
2. **Same day +15 minutes** — post `outreach/twitter_launch_v2.md` as a thread on X. First reply: the HN link. This drives the early upvote velocity HN ranks on.
3. **Same day +1 hour** — post `outreach/linkedin_launch.md` to LinkedIn personal. Tag relevant photography / provenance contacts.
4. **Day +1 (Wed)** — submit `outreach/reddit_photography_v2.md` to r/photography. Check subreddit self-promo rules first (most allow 1-in-10).
5. **Day +2 (Thu)** — submit `outreach/indie_hackers_post.md`. Lower ceiling than HN but converts B2B traffic.
6. **Day +3–7** — work the DM script from `cold_dm_twitter.md` against the top 25 names in `outreach/INFLUENCER_TARGETS.md` (file is in `deploy/`, not `outreach/` — confirm before sending).
7. **Anti-spam rule**: do NOT cross-post HN + Reddit + LinkedIn in the same hour; HN moderators detect coordinated drops and flag.

**Cost.** $0. **Hours.** 4h on launch day + 1h/day for 7 days = **~11h total**.

---

## Gap 7 — Refund-not-revoking-credits (premortem A-1, in flight)

**What fails.** A Stripe refund issued via the Dashboard credits the customer's card but does not currently zero out the credits balance they already spent into the product (anchors stay valid, account stays funded). Net effect: chargeback abuse = free anchors. **Sibling agent is implementing the fix in parallel** — the credit-revocation hook on `charge.refunded` is being added to `server/btc_payments.py` / Stripe webhook handler. Detection post-fix: webhook log line `credits_revoked refund=<re_*> user=<email> credits=<n>`.

1. **Cross-reference**: the implementing commit lands on the same branch as the launch fixes — search `git log --oneline -- server/btc_payments.py` or `server/app.py` for a commit message containing "refund" or "revoke" within the 2026-05-18 window. Update this section with the SHA once visible.
2. **Founder-side action** is to verify post-deploy: issue a $1 test charge via Stripe → anchor a file → refund via Dashboard → confirm credits balance drops to zero on `/account` and the ledger appends a `revoke` event. This is the only acceptance test that catches webhook-signature mistakes.
3. **Stripe Dashboard setting**: confirm `charge.refunded` is in the webhook endpoint's subscribed events at dashboard.stripe.com → Developers → Webhooks → orphograph endpoint. If missing, click Edit → check `charge.refunded` and `charge.dispute.created`.

**Cost.** $0 (Stripe test mode). **Hours.** 0.5h (manual test refund + webhook event verification, post-merge).

---

## Gap 8 — No reconciliation cron (premortem B-18, in flight)

**What fails.** Daily there is no automated job comparing (a) Stripe payouts → (b) issued credits in `data/ledger.jsonl` → (c) on-chain BTC received at `data/btc_address.txt`. A silent drift (under-credit, over-credit, double-anchor) can persist for weeks. **Sibling agent is implementing the daily reconciliation job.** Detection post-fix: a `data/reconciliation.jsonl` line per day, plus Telegram ping on any mismatch ≥ 1 cent or any anchor count off by ≥ 1.

1. **Cross-reference**: search `git log --oneline -- scripts/` and `git log --oneline -- server/` for "reconcil" within the 2026-05-18 window; update SHA here.
2. **Infra side** (founder action regardless of sibling implementation): the cron has to actually run. Two viable hosts:
   - **Fly cron-equivalent**: add a `[processes]` entry in `fly.toml` for `reconcile = "python -m server.reconcile"` and a separate machine that wakes via `fly machine run --schedule daily`. Currently `fly.toml` has no `[processes]` block — would need extension.
   - **Founder-laptop launchd** (already a paved path on this Mac per global CLAUDE.md): `~/Library/LaunchAgents/com.orphograph.reconcile.plist` invoking `curl -fsS https://orphograph.com/admin/reconcile -H "Authorization: Bearer <ADMIN_TOKEN>"` daily at 09:00. Pros: zero Fly cost, alerts route through existing Telegram bridge. Cons: laptop must be on; misses days if travelling.
3. **Telegram alerting** routes through existing `~/.claude/notifier` (per `project_notifier.md` in memory). Reconciliation script just needs to POST a one-line summary to the unified Telegram bridge on mismatch.

**Cost.** $0 (laptop launchd) or ~$2/mo (Fly cron machine, idle-billed). **Hours.** 1h (plist write + cron token wiring + first dry-run + Telegram path test).

---

## Roll-up

| Gap | $ one-time | $ recurring/mo | Hours |
|---|---:|---:|---:|
| 1 Fly capacity | 0 | 8.50 | 1.5 |
| 2 Seed split | 375 | 6.25 | 4.0 |
| 3 OTS pool 404 | 0 | 0 | 0.5 |
| 4 HMAC rotation | 0 | 0 | 0.25 + 0.25/quarter |
| 5 Cloudflare | 0 | 2.83 | 2.0 |
| 6 Launch posts | 0 | 0 | 11.0 |
| 7 Refund revoke | 0 | 0 | 0.5 |
| 8 Reconcile cron | 0 | 0 | 1.0 |
| **Total** | **$375** | **~$17.58/mo** | **~21 hours** |

Annualized ongoing: ~$211/yr. One-time $375. Founder execution: ~21h spread over 2 weeks.

---

**Re-audit by 2026-06-18.** Specifically re-check: Gap 1 (Fly memory floor after first traffic spike), Gap 3 (whether the upstream GitHub issue has been triaged), Gap 4 (90-day rotation reminder fires 2026-08-18), Gap 6 (which launch posts actually shipped vs which remained drafts), Gaps 7 + 8 (confirm the sibling-agent commits landed and the acceptance tests passed).
