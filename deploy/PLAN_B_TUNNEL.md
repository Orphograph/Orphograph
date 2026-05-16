# Plan B — Cloudflare Tunnel (orphograph.com → laptop:8989)

Fallback if Plan A (Fly.io via Cloudflare WARP) doesn't unblock GitHub within
5 minutes. Routes orphograph.com directly to the already-running local server
through Cloudflare's edge — no inbound ports, no GitHub binaries required (when
installed via Homebrew bottle).

## When to invoke

Trigger Plan B if **any** of these are true 5 minutes after WARP comes up:
- `brew install flyctl` still stalls < 50 KB/s, or
- `curl -L https://fly.io/install.sh | sh` still stalls, or
- direct `curl github.com` still measures < 100 KB/s.

Otherwise stay on Plan A.

## 4-step sequence (~10 min to live)

1. **Install** (one-time):

       brew install cloudflared

   Homebrew pulls the darwin-arm64 bottle from its CDN / ghcr.io, NOT from
   github.com/cloudflare/cloudflared/releases. This is the path that survives
   the ISP throttle. (Verified 2026-05-14: pkg.cloudflare.com hosts only
   Debian/RPM packages — no macOS path there.)

2. **Login** (browser-gated — see "Brave interaction" below):

       cloudflared tunnel login

3. **Create + route**:

       cloudflared tunnel create orphograph
       cloudflared tunnel route dns orphograph orphograph.com

4. **Run** (laptop is now the origin):

       cloudflared tunnel --hostname orphograph.com --url http://127.0.0.1:8989 run orphograph
       caffeinate -dimsu &     # keep laptop awake

Verify: `curl -sS https://orphograph.com/api/health` returns the same JSON as
`curl -sS http://127.0.0.1:8989/api/health` (200, `"ok": true`).

The wrapper script `scripts/plan_b_tunnel.sh` prints these commands with the
detected binary path and runs the pre-flight + post-flight checks.

## Brave-browser interaction (one moment only)

`cloudflared tunnel login` (step 2) opens **the default browser** to
`https://dash.cloudflare.com/argotunnel?...`. Since the default is Brave, that
is where it lands. Founder must:
- be logged into the Cloudflare account that owns `orphograph.com`,
- pick the `orphograph.com` zone from the list,
- click **Authorize**.

That writes `~/.cloudflared/cert.pem` and the rest of the flow is headless.

## Tradeoffs

- **Laptop = origin.** Sleep, lid-close, or network drop = downtime.
  Mitigation: `caffeinate -dimsu` keeps the machine awake; plug in power.
- **Single point of failure** until we move to Fly.io. Acceptable for launch
  night; migrate to a real host within 72h.
- **TLS** terminates at Cloudflare's edge (Full or Flexible mode in the zone).
  Origin is `http://127.0.0.1:8989` — fine, the hop is loopback.
- **Logs** live in the foreground terminal (or `/tmp/cf_tunnel.log` if
  backgrounded). No log rotation by default.

## Cost

**$0.** Cloudflare Tunnel (formerly Argo Tunnel for named tunnels) is in the
free tier with no bandwidth cap for the tunnel itself. Standard Cloudflare
zone egress applies (free for our traffic levels).

## Estimated time to live

**~10 minutes** from `brew install cloudflared` finishing:
- install: 30-90s on Homebrew CDN (assumes WARP up OR Homebrew is unthrottled)
- login: 60s (browser click-through)
- create+route+run: 60s
- DNS propagation: 30-60s
- verification curl: 5s

## Rollback

Stop the tunnel (Ctrl-C the foreground run, or `pkill cloudflared`). The
proxied CNAME on `orphograph.com` remains but will start serving 530s. Either
delete the DNS record in the Cloudflare dashboard or leave it for the next
launch attempt.
