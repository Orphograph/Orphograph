#!/usr/bin/env bash
# Plan B: Cloudflare Tunnel for orphograph.com -> 127.0.0.1:8989
#
# Why this exists: home ISP is throttling GitHub-routed downloads (~227 B/s),
# blocking Fly.io install. Cloudflare Tunnel ships orphograph.com directly to
# the local server with zero inbound port forwarding.
#
# This script:
#   - detects cloudflared,
#   - installs it via a NON-GitHub path when possible (brew bottle = ghcr/Homebrew CDN),
#   - prints the exact interactive commands the founder runs (browser-gated login).
#
# Safe to re-run. No sudo. No automatic auth/create/route (those need a browser).

set -euo pipefail

LOCAL_URL="http://127.0.0.1:8989"
HEALTH_PATH="/api/health"
TUNNEL_NAME="orphograph"
HOSTNAME="orphograph.com"
LOCAL_BIN="${HOME}/.local/bin"
BREW_BIN="/opt/homebrew/bin/cloudflared"
CONFIG_DIR="${HOME}/.cloudflared"

c_green() { printf "\033[32m%s\033[0m\n" "$*"; }
c_yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
c_red() { printf "\033[31m%s\033[0m\n" "$*"; }
c_bold() { printf "\033[1m%s\033[0m\n" "$*"; }

step() { printf "\n"; c_bold "==> $*"; }

# ---------------------------------------------------------------------------
step "1. Pre-flight: local orphograph server"
if curl -fsS --max-time 5 "${LOCAL_URL}${HEALTH_PATH}" >/dev/null; then
  c_green "OK: ${LOCAL_URL}${HEALTH_PATH} responds 200"
else
  c_red "FAIL: ${LOCAL_URL}${HEALTH_PATH} not reachable. Start the server first:"
  echo "      cd ~/orphograph && ./scripts/launch.sh   (or whichever boot script)"
  exit 1
fi

# ---------------------------------------------------------------------------
step "2. Locate or install cloudflared (NON-GitHub path)"

CF_BIN=""
if command -v cloudflared >/dev/null 2>&1; then
  CF_BIN="$(command -v cloudflared)"
elif [[ -x "${BREW_BIN}" ]]; then
  CF_BIN="${BREW_BIN}"
elif [[ -x "${LOCAL_BIN}/cloudflared" ]]; then
  CF_BIN="${LOCAL_BIN}/cloudflared"
fi

if [[ -n "${CF_BIN}" ]]; then
  c_green "Found cloudflared: ${CF_BIN}"
  "${CF_BIN}" --version || true
else
  c_yellow "cloudflared not installed."
  cat <<'EOF'

INSTALL OPTIONS (in order of preference for a GitHub-throttled link):

  A) Homebrew bottle (RECOMMENDED — fetched from Homebrew's CDN / ghcr.io,
     NOT github.com/cloudflare/cloudflared/releases):

         brew install cloudflared

     Bottles for cloudflared on darwin-arm64 are mirrored by Homebrew, so
     this avoids the throttled github.com release host.

  B) Cloudflare APT/RPM repo at https://pkg.cloudflare.com/cloudflared
     -- LINUX ONLY. There is no macOS package on pkg.cloudflare.com
     (verified 2026-05-14: index lists Debian/Ubuntu/RHEL/CentOS only).

  C) Last resort, direct GitHub release (will be throttled on this network):

         mkdir -p ~/.local/bin
         curl -L -o ~/.local/bin/cloudflared \
           https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz
         # then untar/chmod +x. Expect ~227 B/s on the throttled link.

  D) If WARP is up, GitHub route may unblock — re-try (A) or (C) under WARP.

Run option (A) now, then re-run this script:

    brew install cloudflared && bash "$0"

EOF
  exit 2
fi

# ---------------------------------------------------------------------------
step "3. Interactive steps (you run these — browser required for step 3a)"

cat <<EOF

3a) LOGIN (opens Brave/default browser — pick the Cloudflare account that
    owns orphograph.com, then authorize the zone):

        ${CF_BIN} tunnel login

    This writes ${CONFIG_DIR}/cert.pem. Re-uses on every later command.

3b) CREATE the named tunnel (one-time; writes a credentials JSON to
    ${CONFIG_DIR}/<UUID>.json):

        ${CF_BIN} tunnel create ${TUNNEL_NAME}

3c) POINT DNS at the tunnel (creates a proxied CNAME on orphograph.com):

        ${CF_BIN} tunnel route dns ${TUNNEL_NAME} ${HOSTNAME}

3d) RUN the tunnel in the foreground, mapping ${HOSTNAME} -> ${LOCAL_URL}:

        ${CF_BIN} tunnel --hostname ${HOSTNAME} --url ${LOCAL_URL} run ${TUNNEL_NAME}

    (Leave this terminal open. Ctrl-C stops the tunnel. For a background
    run: nohup ${CF_BIN} tunnel ... run ${TUNNEL_NAME} >/tmp/cf_tunnel.log 2>&1 &)

3e) PREVENT SLEEP (laptop is now the origin server — sleep = downtime):

        caffeinate -dimsu &      # or: caffeinate -d in a dedicated terminal

EOF

# ---------------------------------------------------------------------------
step "4. Post-tunnel verification (run AFTER step 3d is live)"

cat <<EOF

Wait ~30-60s for DNS propagation, then:

        curl -sS https://${HOSTNAME}${HEALTH_PATH}

Expected: 200 with JSON body identical to:
        curl -sS ${LOCAL_URL}${HEALTH_PATH}

Tail tunnel logs in another shell:
        tail -f /tmp/cf_tunnel.log   # if you ran in background
        # or watch the foreground terminal from step 3d

EOF

c_green "Plan B prep complete. Run the four interactive steps above to go live."
