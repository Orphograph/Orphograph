#!/usr/bin/env bash
# seed_demo.sh — anchor 5 sample files so the dashboard has real receipts.
#
# Useful for:
#   • Launch demos / screenshots (the empty-state account page looks dead)
#   • Verifying the full anchor + email + dashboard pipeline end-to-end
#   • Quick sanity check after a code change
#
# Each anchor consumes free-tier rate-limit budget unless you set
# ORPHO_API_KEY in env.
#
# Usage:
#   bash ~/orphograph/scripts/seed_demo.sh
#   ORPHO_API_KEY=rk_live_... bash ~/orphograph/scripts/seed_demo.sh
#   ORPHO_BASE=http://127.0.0.1:8989 bash ~/orphograph/scripts/seed_demo.sh   # local
set -eu

BASE="${ORPHO_BASE:-https://orphograph.com}"
API_KEY="${ORPHO_API_KEY:-}"

AMBER=$'\033[38;2;192;138;62m'
SAGE=$'\033[38;2;74;154;115m'
MUTED=$'\033[38;2;131;126;117m'
RESET=$'\033[0m'

echo "${AMBER}seeding demo receipts at $BASE${RESET}"
echo

# Inline sample content so we don't need real photos around. SHA-256 of each
# content is what gets anchored. Content varies to produce 5 distinct hashes.
SAMPLES=(
    "Orphograph demo file 1 — receipt of the receipt that anchors a receipt."
    "Orphograph demo file 2 — pre-AI-era reference photo placeholder."
    "Orphograph demo file 3 — journalist's source document checksum."
    "Orphograph demo file 4 — unreleased music draft hash."
    "Orphograph demo file 5 — manuscript revision $(date -u +%Y%m%d)."
)

CREATED=0
FAILED=0
HEADERS=(-H "Content-Type: application/json" -H "User-Agent: orphograph-seed-demo/0.1")
if [ -n "$API_KEY" ]; then
    HEADERS+=(-H "X-Orpho-Api-Key: $API_KEY")
fi

for content in "${SAMPLES[@]}"; do
    SHA256=$(printf "%s" "$content" | shasum -a 256 | awk '{print $1}')
    SHA512=$(printf "%s" "$content" | shasum -a 512 | awk '{print $1}')
    BODY=$(printf '{"hash_hex":"%s","sha512_hex":"%s","client_label":"demo"}' "$SHA256" "$SHA512")

    RESPONSE=$(curl -s -X POST "${BASE}/api/anchor" "${HEADERS[@]}" -d "$BODY")
    RID=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('receipt_id',''))" 2>/dev/null || echo "")

    if [ -n "$RID" ]; then
        CALENDARS=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{d.get(\"calendars_ok\",0)}/{d.get(\"calendars_total\",0)}')")
        echo "  ${SAGE}✓${RESET} ${RID}  (${CALENDARS} OTS)  ${MUTED}sha256=${SHA256:0:16}…${RESET}"
        CREATED=$((CREATED + 1))
    else
        ERR_MSG=$(echo "$RESPONSE" | head -c 200)
        echo "  ✗ FAILED: $ERR_MSG"
        FAILED=$((FAILED + 1))
    fi
    # Gentle: don't blast the rate limiter.
    sleep 1
done

echo
echo "summary: ${SAGE}${CREATED} anchored${RESET}, ${MUTED}${FAILED} failed${RESET}"

if [ "$CREATED" -gt 0 ]; then
    echo
    echo "${MUTED}Sign in at ${BASE}/signin.html — anchors appear under your email${RESET}"
    echo "${MUTED}(only if you used an API key tied to an active subscription;${RESET}"
    echo "${MUTED} free-tier anchors are not associated with any account)${RESET}"
fi
