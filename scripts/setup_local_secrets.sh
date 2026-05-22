#!/usr/bin/env bash
# setup_local_secrets.sh — one-shot rebuild of the founder's laptop-side ops
# state for Orphograph. Idempotent: safe to run any number of times.
#
# What it sets up:
#   1. ~/.orphograph_secrets.env       (mode 0600)
#   2. ~/.orphograph_canary.txt        (mode 0600) — leak-canary string
#   3. tmutil exclusions on both       (so Time Machine never copies them)
#   4. ~/Orphograph/secrets.sparseimage encrypted vault (AES-256, 10 MB)
#   5. Keychain entry "orphograph-secrets-vault" holding the vault passphrase
#   6. launchd agents (morning_check + canary_scan) bootstrapped if not loaded
#
# What it does NOT do (founder must do these manually):
#   • Populate ORPHO_FOUNDER_TOKEN inside ~/.orphograph_secrets.env. After
#     this script runs, run `scripts/rotate_founder_token.sh` to mint one.
#   • Copy the encrypted vault to a USB stick for off-machine recovery.
#   • Add `~/.orphograph_secrets.env` to Time Machine via the System Settings
#     GUI — the CLI exclusion this script applies is functionally equivalent
#     and survives moves/renames, but not delete+recreate.
#
# No secret ever appears in this script's stdout. The vault passphrase is
# generated, stored in the macOS Keychain, and immediately unset from the
# shell's memory.

set -euo pipefail

SECRETS_FILE="$HOME/.orphograph_secrets.env"
CANARY_FILE="$HOME/.orphograph_canary.txt"
VAULT_DIR="$HOME/Orphograph"
VAULT_FILE="$VAULT_DIR/secrets.sparseimage"
KEYCHAIN_SERVICE="orphograph-secrets-vault"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '  %s\n' "$*"; }
ok()  { printf '  ✓ %s\n' "$*"; }
warn(){ printf '  ! %s\n' "$*" >&2; }

step() { printf '\n[%s] %s\n' "$1" "$2"; }

# ─────────────────────────────────────────────────────────────────────────
step 1 "secrets file"
umask 077
if [ ! -f "$SECRETS_FILE" ]; then
  : > "$SECRETS_FILE"
  ok "created $SECRETS_FILE"
else
  ok "$SECRETS_FILE already exists"
fi
chmod 0600 "$SECRETS_FILE"
ok "mode 0600 enforced"

if ! grep -q "^ORPHO_FOUNDER_TOKEN=" "$SECRETS_FILE"; then
  warn "ORPHO_FOUNDER_TOKEN is missing — run scripts/rotate_founder_token.sh to mint one"
else
  ok "ORPHO_FOUNDER_TOKEN entry present"
fi

# ─────────────────────────────────────────────────────────────────────────
step 2 "leak canary"
if [ ! -f "$CANARY_FILE" ] || ! grep -q "^ORPHO-CANARY-" "$CANARY_FILE"; then
  CANARY=$(python3 -c "import secrets; print('ORPHO-CANARY-' + secrets.token_hex(8) + '-DO-NOT-REMOVE')")
  printf '%s\n' "$CANARY" > "$CANARY_FILE"
  chmod 0600 "$CANARY_FILE"
  if ! grep -q "ORPHO-CANARY-" "$SECRETS_FILE"; then
    {
      printf '\n# Leak canary — daily scan greps the public web for this string.\n'
      printf '# If alerted, your secrets file has leaked. Rotate immediately.\n'
      printf '# %s\n' "$CANARY"
    } >> "$SECRETS_FILE"
  fi
  ok "canary planted ($(printf '%s' "$CANARY" | cut -c1-25)…)"
  unset CANARY
else
  ok "canary already present ($(head -c 25 "$CANARY_FILE")…)"
fi

# ─────────────────────────────────────────────────────────────────────────
step 3 "Time Machine exclusions"
for f in "$SECRETS_FILE" "$CANARY_FILE"; do
  if tmutil isexcluded "$f" 2>/dev/null | grep -q '\[Excluded\]'; then
    ok "$(basename "$f") already excluded"
  else
    tmutil addexclusion "$f" 2>/dev/null || warn "addexclusion failed for $f"
    if tmutil isexcluded "$f" 2>/dev/null | grep -q '\[Excluded\]'; then
      ok "excluded $(basename "$f")"
    else
      warn "could not exclude $(basename "$f") — check Full Disk Access for Terminal"
    fi
  fi
done
log "(sparseimage at $VAULT_FILE stays included — AES-256-encrypted, Time Machine is its recovery path)"

# ─────────────────────────────────────────────────────────────────────────
step 4 "encrypted vault"
mkdir -p "$VAULT_DIR"
if [ -f "$VAULT_FILE" ]; then
  ok "vault already exists at $VAULT_FILE"
  ok "Keychain entry should already hold the passphrase (service=$KEYCHAIN_SERVICE)"
else
  PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(40))")
  security delete-generic-password -s "$KEYCHAIN_SERVICE" >/dev/null 2>&1 || true
  security add-generic-password -a "$USER" -s "$KEYCHAIN_SERVICE" -w "$PASS" -U
  printf '%s' "$PASS" | hdiutil create -type SPARSE -size 10m -fs APFS \
    -encryption AES-256 -stdinpass -volname "OrphographSecrets" \
    "$VAULT_FILE" >/dev/null
  MOUNT_POINT="/tmp/orpho_vault_setup_$$"
  printf '%s' "$PASS" | hdiutil attach -stdinpass -nobrowse \
    -mountpoint "$MOUNT_POINT" "$VAULT_FILE" >/dev/null
  cp "$SECRETS_FILE" "$MOUNT_POINT/orphograph_secrets.env"
  sync
  hdiutil detach "$MOUNT_POINT" -quiet
  unset PASS
  ok "vault created and locked"
  ok "Keychain entry stored (service=$KEYCHAIN_SERVICE)"
fi

# ─────────────────────────────────────────────────────────────────────────
step 5 "launchd agents"
for label in com.orphograph.morning_check com.orphograph.canary_scan; do
  TEMPLATE="$REPO_ROOT/scripts/${label}.plist.template"
  TARGET="$HOME/Library/LaunchAgents/${label}.plist"
  if [ ! -f "$TEMPLATE" ]; then
    warn "template missing: $TEMPLATE — skipping"
    continue
  fi
  if [ ! -f "$TARGET" ]; then
    cp "$TEMPLATE" "$TARGET"
    ok "installed plist: $label"
  fi
  if ! launchctl list | grep -q "$label"; then
    launchctl bootstrap "gui/$UID" "$TARGET" 2>/dev/null \
      && launchctl enable "gui/$UID/$label" 2>/dev/null \
      && ok "bootstrapped: $label" \
      || warn "$label bootstrap failed (Sequoia IO error 5 is common; re-run after a logout)"
  else
    ok "already loaded: $label"
  fi
done

# ─────────────────────────────────────────────────────────────────────────
step 6 "verification"
log "secrets file:    $(ls -la "$SECRETS_FILE"  | awk '{print $1, $5, "bytes"}')"
log "canary file:     $(ls -la "$CANARY_FILE"   | awk '{print $1, $5, "bytes"}')"
log "vault file:      $(ls -la "$VAULT_FILE"    2>/dev/null | awk '{print $1, $5, "bytes"}' || echo "missing")"
log "tm excluded:     secrets=$(tmutil isexcluded "$SECRETS_FILE" | grep -o '\[Excluded\]\|\[Included\]')  canary=$(tmutil isexcluded "$CANARY_FILE" | grep -o '\[Excluded\]\|\[Included\]')"
log "keychain entry:  $(security find-generic-password -s "$KEYCHAIN_SERVICE" >/dev/null 2>&1 && echo present || echo MISSING)"
log "launchd loaded:  $(launchctl list | grep -c orphograph) orphograph agents"

printf '\nDone. Next manual steps:\n'
printf '  1. If ORPHO_FOUNDER_TOKEN is missing, run scripts/rotate_founder_token.sh\n'
printf '  2. When you buy a USB stick, copy %s to it\n' "$VAULT_FILE"
printf '  3. Smoke-test with: python3 scripts/morning_check.py\n'
