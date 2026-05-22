#!/usr/bin/env bash
# rotate_founder_token.sh — generate a new ORPHO_FOUNDER_TOKEN, write it to
# ~/.orphograph_secrets.env (mode 0600), push to Fly. The token value is
# NEVER echoed to stdout or stderr — only a SHA-256 fingerprint of the
# *new* value is printed so the founder can confirm rotation succeeded
# without the secret entering the terminal scrollback or any log.
set -euo pipefail
ENV_FILE="$HOME/.orphograph_secrets.env"
APP="${1:-orphograph}"
NEW=$(python3 -c "import secrets; print(secrets.token_hex(32))")
FP=$(printf '%s' "$NEW" | shasum -a 256 | cut -c1-12)

umask 077
touch "$ENV_FILE"
chmod 0600 "$ENV_FILE"
python3 - "$ENV_FILE" "$NEW" <<'PY'
import re,sys
p,v=sys.argv[1],sys.argv[2]
try: t=open(p).read()
except FileNotFoundError: t=""
if re.search(r'^ORPHO_FOUNDER_TOKEN=', t, flags=re.M):
    t=re.sub(r'^ORPHO_FOUNDER_TOKEN=.*$', f'ORPHO_FOUNDER_TOKEN={v}', t, flags=re.M)
else:
    if t and not t.endswith("\n"): t+="\n"
    t+=f"ORPHO_FOUNDER_TOKEN={v}\n"
open(p,'w').write(t)
PY

# Pipe the token into fly via stdin so it never appears in process args.
printf 'ORPHO_FOUNDER_TOKEN=%s\n' "$NEW" | fly secrets import -a "$APP" >/dev/null 2>&1
unset NEW
echo "rotated. new fingerprint sha256:${FP}…"
