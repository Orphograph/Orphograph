#!/usr/bin/env bash
# End-to-end proof of the "log out of all devices" loop, loopback only.
# Manual/integration harness (not run by the pytest gate). The trap-cleanup
# pattern below (direct background -> $! = real pid; trap cleanup EXIT INT TERM)
# is statically guarded by tests/test_harness_cleanup.py.
set -u
# Repo root = two levels up from tests/manual/, so this runs from any checkout/tree.
WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PORT=8991
BASE="http://127.0.0.1:${PORT}"
DATA=$(mktemp -d /tmp/orpho_logout_proof.XXXXXX)
PASS=0; FAIL=0
say(){ printf '%s\n' "$*"; }
chk(){ # chk "label" actual expected
  if [ "$2" = "$3" ]; then say "  ✅ $1: $2"; PASS=$((PASS+1)); else say "  ❌ $1: got $2, expected $3"; FAIL=$((FAIL+1)); fi
}

# ---- start server (loopback, temp data, dev cookies over http) ----
# Background python3 DIRECTLY (no subshell / no `cmd && cmd &` wrapper) so $! is
# the server's REAL pid. The old `( cd && python3 & )` form set $! to the
# subshell, leaving python3 orphaned (and still listening) when the trap fired.
# cd in the main shell instead (harness is throwaway); app.py resolves WEB_DIR /
# DATA_DIR from __file__ + env, so cwd is otherwise irrelevant.
cd "$WT" || { echo "cannot cd $WT"; exit 1; }
HOST=127.0.0.1 PORT=$PORT ORPHO_DATA_DIR="$DATA" ORPHO_COOKIE_SECURE=0 \
    python3 server/app.py >"$DATA/server.log" 2>&1 &
SRVPID=$!
cleanup() {
  if [ -n "${SRVPID:-}" ] && kill -0 "$SRVPID" 2>/dev/null; then
    pkill -P "$SRVPID" 2>/dev/null      # reap any transient children first
    kill -TERM "$SRVPID" 2>/dev/null
    wait "$SRVPID" 2>/dev/null           # block until it actually exits (reap)
    kill -0 "$SRVPID" 2>/dev/null && kill -KILL "$SRVPID" 2>/dev/null
  fi
  rm -rf "$DATA"
}
trap cleanup EXIT INT TERM

say "== waiting for server (pid $SRVPID, $BASE) =="
if ! curl -s --retry 40 --retry-delay 1 --retry-connrefused -o /dev/null "$BASE/api/health"; then
  say "SERVER DID NOT COME UP — log tail:"; tail -20 "$DATA/server.log"; exit 1
fi
say "  up. bind check:"; lsof -nP -iTCP:$PORT -sTCP:LISTEN 2>/dev/null | awk 'NR==1||/LISTEN/{print "    "$0}'

mint(){ ( cd "$WT" && ORPHO_DATA_DIR="$DATA" python3 -c "import sys;sys.path.insert(0,'server');import auth;print(auth.issue_link_token('$1')[0])" ); }
redeem(){ curl -s -D "$DATA/h_$2" -c "$DATA/$2" -o /dev/null "$BASE/a/$1"; }   # $1=token $2=jarname
me(){ curl -s -o /dev/null -w "%{http_code}" -b "$DATA/$1" "$BASE/api/me"; }    # -> http code
mebody(){ curl -s -b "$DATA/$1" "$BASE/api/me"; }

say ""; say "== sign in: alice on 2 devices + bob on 1 (real magic-link → cookie) =="
T1=$(mint alice@example.com); redeem "$T1" jarA1
T2=$(mint alice@example.com); redeem "$T2" jarA2
T3=$(mint bob@example.com);   redeem "$T3" jarB1
say "  Set-Cookie (device A1): $(grep -i '^set-cookie' "$DATA/h_jarA1" | tr -d '\r')"

say ""; say "== BEFORE logout-all =="
chk "alice device A1 /api/me" "$(me jarA1)" "200"
chk "alice device A2 /api/me" "$(me jarA2)" "200"
chk "bob   device B1 /api/me" "$(me jarB1)" "200"
say "  (A1 identity: $(mebody jarA1 | python3 -c 'import sys,json;print(json.load(sys.stdin).get("email"))' 2>/dev/null))"

say ""; say "== THE CLICK: POST /api/me/logout-all from alice device A1 =="
LO=$(curl -s -b "$DATA/jarA1" -X POST "$BASE/api/me/logout-all")
say "  response: $LO"
REVOKED=$(printf '%s' "$LO" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("sessions_revoked"))' 2>/dev/null)
chk "sessions_revoked" "$REVOKED" "2"

say ""; say "== AFTER logout-all =="
chk "alice device A1 (the one that clicked) -> 401" "$(me jarA1)" "401"
chk "alice device A2 (OTHER device)        -> 401" "$(me jarA2)" "401"
chk "bob   device B1 (different user)      -> 200" "$(me jarB1)" "200"

say ""; say "== idempotency: click again from a now-dead device =="
LO2=$(curl -s -o /dev/null -w "%{http_code}" -b "$DATA/jarA1" -X POST "$BASE/api/me/logout-all")
chk "second logout-all on dead session -> 401" "$LO2" "401"

say ""; say "================= RESULT: $PASS passed, $FAIL failed ================="
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
