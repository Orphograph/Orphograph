# Go live now — copy/paste sequence

You just saw `orphograph.com → ERR_NAME_NOT_RESOLVED` on your phone.
That's normal: registering the domain reserves the name, but you
haven't yet hosted the site anywhere with a public IP. This doc is
the shortest path from "domain registered" to "phone-loads-the-site."

**You'll run these commands in your own Mac terminal** (not the
Claude Code session — flyctl can't run in there due to sandbox).
Open Terminal.app, paste each block, watch the output.

Total time: **45 minutes of clock time, ~10 min of typing**. Most
of the wait is DNS propagation and Fly's cert provisioning.

---

## What's already done

- `fly.toml` configured for app `orphograph`, region `iad`, 256MB VM,
  HTTPS forced, healthcheck on `/api/health`
- `Dockerfile` ready: Python 3.11 stdlib, non-root user, `/app/data` volume mount
- `Makefile` has the `first-deploy` target that drives the whole thing
- The service is RUNNING locally on `127.0.0.1:8989` (verify in your Mac browser)

## Step 0 — install flyctl (Homebrew is the cleanest path)

The previous curl-pipe install hit a truncated-download bug and
produced a binary that macOS killed on launch. Use Homebrew instead —
it handles the code-signing and integrity checks correctly.

```bash
brew install flyctl
```

That takes 1–2 minutes. Then verify:

```bash
cd ~/orphograph
make fly-check
```

Expected output:
```
flyctl path: /opt/homebrew/bin/flyctl
flyctl v0.4.51 ...
auth: NOT logged in (run: /opt/homebrew/bin/flyctl auth login)
```

If you see a Gatekeeper warning on first launch, right-click the
binary in Finder → Open → "Open Anyway." Brew-installed binaries
usually skip this since they're signed via the Homebrew bottle pipeline.

## Step 1 — sign up for Fly

```bash
fly auth signup
```

Your browser opens. Use a fresh email if you want maximum separation
from Hydroboro (Cloudflare alias on `orphograph.com` is cleanest
since the domain is registered; failing that, a ProtonMail).

Fly **requires a credit card** even for the free tier. The free
tier is enough for Orphograph at launch volume (~$0–5/month).
Add your card. **This is the only ID disclosure to Fly** — they
don't ask for SSN, LLC, or business docs.

## Step 2 — one-button bring-up

```bash
cd ~/orphograph
make first-deploy
```

This walks through:

1. **Verify flyctl** (already done in step 0)
2. **Auth** (already done in step 1; skips if logged in)
3. **`fly launch`** — creates the app on Fly using your existing
   `fly.toml`. Picks region `iad`. Doesn't deploy yet.
4. **`fly volumes create orphograph_data`** — 1GB persistent volume
   in `iad` (the receipts + ledgers live here).
5. **Run pytest** — refuses to deploy if any test is red.
6. **`fly deploy`** — builds the Docker image, pushes, starts a
   machine, runs the healthcheck.
7. **`fly certs create orphograph.com`** — prints the DNS records
   you need to add at your registrar.
8. **Preflight probe** against `https://orphograph.com` — will
   fail initially because DNS hasn't propagated; that's normal.

Total command time: ~5 min. The deploy is fastest.

## Step 3 — add DNS records at your registrar

After `make first-deploy`, the `fly certs` step printed something
like:

```
The following DNS records are needed:
  A     orphograph.com.     66.241.xxx.xxx
  AAAA  orphograph.com.     2a09:8280:1::xxx
```

Log into wherever you registered the domain (Porkbun, Namecheap,
GoDaddy, etc.). Find the DNS records panel. Add an A record and
an AAAA record exactly as shown — usually you set:

- **Type:** `A` / **Host:** `@` (or blank, or `orphograph.com.`) / **Value:** the IPv4 / **TTL:** 600
- **Type:** `AAAA` / **Host:** `@` / **Value:** the IPv6 / **TTL:** 600

If you want `www.orphograph.com` to redirect too, add the same
records with Host = `www`.

DNS propagates in **minutes to a few hours**. Most registrars are
under 15 minutes globally.

## Step 4 — wait for the cert, then verify

```bash
fly certs check orphograph.com -a orphograph
```

Run this every few minutes until you see `Configured = true`. Once
it says `READY`, Fly has issued the TLS certificate.

Now try from your phone:

```
https://orphograph.com
```

You should see the dark-glassmorphism Orphograph landing. Drop a
photo, anchor it, see a real receipt.

## Step 5 (optional) — wire crypto revenue

Once the site is live, set up the receive address as documented in
`deploy/BTC_OPERATOR.md`:

```bash
# Generate a fresh bc1q... address on your hardware wallet.
# Then:
fly secrets set BTC_RECEIVE_ADDRESS=bc1qXXXXX -a orphograph
```

That's it. BTC payments work the next time the settle worker fires
on Fly (every 5 min). You can place a tiny test order on the live
site, send the sats, watch the email arrive.

## Step 6 (optional, anytime later) — wire Stripe + Resend

When you're ready for card payments + automated emails, follow
`deploy/LAUNCH_WALKTHROUGH.md` §2–§3. These can come weeks after
the site is live — they're additive, not blocking.

---

## What can go wrong + fixes

**`fly auth login` browser doesn't open.**
Copy the URL flyctl printed and paste it into Brave manually.

**`fly launch` says "app exists."**
You already ran it. Skip to `fly deploy`.

**`fly deploy` complains about no Dockerfile.**
You're not in the right directory. `cd ~/orphograph` and retry.

**`fly certs check` shows `Configured = false` after an hour.**
DNS records didn't take. Verify at your registrar that the records
are saved AND have a low TTL. Use `dig orphograph.com` from your
Mac terminal — should return the Fly IP.

**Site loads but `/api/buy-btc` returns 503.**
Expected — `BTC_RECEIVE_ADDRESS` not set yet. Run step 5.

**Site loads but Stripe button shows "launching soon."**
Expected — Stripe keys not set yet. Run `deploy/LAUNCH_WALKTHROUGH.md` §3.

**`fly deploy` says "out of free tier."**
You can use Fly's paid tier (~$5/month for the 256MB VM + 1GB volume),
or downsize the VM by editing `fly.toml` (`cpu_kind`, `cpus`, `memory_mb`).

---

## Honest expectations

- DNS propagation: ~minutes to a few hours
- Fly cert issuance: ~1 minute after DNS lands
- Total clock time from "make first-deploy" to "phone loads the site":
  realistically **15–60 minutes**, dominated by DNS propagation
- After that, the site stays up. Fly auto-restarts the VM if it
  crashes (healthcheck on `/api/health`).
- I (Claude) monitor via the local launchd `com.orphograph.health`
  agent that polls every minute and pings Telegram on outages —
  that's for your LOCAL service. For the public site, Fly has its
  own healthchecks and will email you on incidents.

---

## "Why is this so manual?"

I can't run `flyctl` from inside this Claude Code session because
the sandbox blocks executing fresh binaries. I can write everything
else, install flyctl, validate the configs, and walk you through.
The actual `fly auth login` + `fly deploy` need to happen in a
real terminal you control. Total typing on your side: ~5 lines of
commands. The Makefile target wraps it into one.
