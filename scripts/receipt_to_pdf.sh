#!/usr/bin/env bash
# receipt_to_pdf.sh — render an Orphograph receipt to PDF via headless Brave.
#
# Usage:
#   receipt_to_pdf.sh <receipt-id> [output-path]
#   receipt_to_pdf.sh abc123_xyz  ~/Desktop/proof.pdf
#
# Defaults output to /tmp/orpho_<id>.pdf if no path given.
# Defaults to https://orphograph.com if ORPHO_BASE unset; override for local
# testing via ORPHO_BASE=http://127.0.0.1:8989.
set -eu

RID="${1:?usage: receipt_to_pdf.sh <receipt-id> [output-path]}"
OUT="${2:-/tmp/orpho_${RID}.pdf}"
BASE="${ORPHO_BASE:-https://orphograph.com}"

# Find Brave (preferred per founder), fall back to Chrome/Chromium.
BROWSER=""
for candidate in \
  "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" \
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  "/Applications/Chromium.app/Contents/MacOS/Chromium" \
  "$(command -v brave 2>/dev/null || true)" \
  "$(command -v chromium 2>/dev/null || true)"
do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    BROWSER="$candidate"
    break
  fi
done

if [ -z "$BROWSER" ]; then
  echo "error: no headless-capable browser found (Brave / Chrome / Chromium)" >&2
  exit 1
fi

URL="${BASE}/r/${RID}?print=1"
echo "rendering $URL → $OUT"

"$BROWSER" \
  --headless \
  --disable-gpu \
  --no-pdf-header-footer \
  --hide-scrollbars \
  --print-to-pdf="$OUT" \
  "$URL"

if [ -f "$OUT" ]; then
  SIZE=$(stat -f%z "$OUT" 2>/dev/null || stat -c%s "$OUT" 2>/dev/null)
  echo "✓ wrote $OUT ($SIZE bytes)"
else
  echo "✗ no PDF produced — check that the receipt-id exists at $URL"
  exit 1
fi
