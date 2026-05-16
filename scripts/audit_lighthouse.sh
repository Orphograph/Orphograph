#!/usr/bin/env bash
# audit_lighthouse.sh — Lighthouse audit of every public Orphograph page.
#
# Targets: Performance ≥95, SEO ≥95, Accessibility ≥90, Best Practices ≥100.
# Output: /tmp/orpho-lighthouse-YYYYMMDD/ + console summary. Nonzero exit
# if any threshold fails so CI can gate on it.
#
# Requires Node + lighthouse CLI: `npm install -g lighthouse`

set -eu
cd "$(dirname "$0")/.."

BASE="${ORPHO_BASE:-http://127.0.0.1:8989}"
OUT_DIR="/tmp/orpho-lighthouse-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$OUT_DIR"

AMBER=$'\033[38;2;192;138;62m'
SAGE=$'\033[38;2;74;154;115m'
ERR=$'\033[38;2;178;80;80m'
MUTED=$'\033[38;2;131;126;117m'
RESET=$'\033[0m'

echo "${AMBER}orphograph — Lighthouse audit${RESET}"
echo "${MUTED}─────────────────────────────────${RESET}"
echo "Base: $BASE"
echo "Reports: $OUT_DIR"
echo

if ! command -v lighthouse >/dev/null 2>&1; then
    echo "${ERR}error: lighthouse CLI not installed${RESET}" >&2
    echo "  npm install -g lighthouse" >&2
    echo "  or: brew install lighthouse" >&2
    exit 1
fi

PAGES=(
    "/"
    "/about.html"
    "/buy.html"
    "/blog/"
    "/blog/written-by-an-ai"
    "/blog/prove-photo-existed-before-ai"
    "/lp/index.html"
    "/lp/prove-photo-pre-ai.html"
    "/lp/wedding-photographer-proof.html"
    "/lp/journalist-source-timestamp.html"
    "/terms.html"
    "/privacy.html"
    "/status.html"
)

PASS=0
FAIL=0

for page in "${PAGES[@]}"; do
    url="${BASE}${page}"
    safe_name=$(echo "${page}" | sed 's|/|_|g' | sed 's|^_||' | sed 's|_$||')
    [ -z "$safe_name" ] && safe_name="home"
    report="${OUT_DIR}/${safe_name}.json"

    echo "${MUTED}auditing ${page}…${RESET}"
    if ! lighthouse "$url" \
         --output=json --output-path="$report" \
         --quiet --chrome-flags="--headless --no-sandbox" \
         --only-categories=performance,seo,accessibility,best-practices \
         2>>"${OUT_DIR}/lighthouse.log"; then
        echo "  ${ERR}✗ lighthouse failed for $page${RESET}"
        FAIL=$((FAIL + 1))
        continue
    fi

    PERF=$(python3 -c "import json; print(int(json.load(open('$report'))['categories']['performance']['score']*100))" 2>/dev/null || echo "0")
    SEO=$(python3 -c "import json; print(int(json.load(open('$report'))['categories']['seo']['score']*100))" 2>/dev/null || echo "0")
    A11Y=$(python3 -c "import json; print(int(json.load(open('$report'))['categories']['accessibility']['score']*100))" 2>/dev/null || echo "0")
    BEST=$(python3 -c "import json; print(int(json.load(open('$report'))['categories']['best-practices']['score']*100))" 2>/dev/null || echo "0")

    page_pass=true
    [ "$PERF" -lt 95 ] && page_pass=false
    [ "$SEO" -lt 95 ] && page_pass=false
    [ "$A11Y" -lt 90 ] && page_pass=false
    [ "$BEST" -lt 100 ] && page_pass=false

    if $page_pass; then
        echo "  ${SAGE}✓${RESET} ${page} — perf:${PERF} seo:${SEO} a11y:${A11Y} best:${BEST}"
        PASS=$((PASS + 1))
    else
        echo "  ${ERR}✗${RESET} ${page} — perf:${PERF} seo:${SEO} a11y:${A11Y} best:${BEST}"
        FAIL=$((FAIL + 1))
    fi
done

echo
echo "${AMBER}Summary:${RESET}  ${SAGE}${PASS} passed${RESET}   ${ERR}${FAIL} failed${RESET}"
echo "Reports: ${OUT_DIR}/"
[ $FAIL -gt 0 ] && exit 1
exit 0
