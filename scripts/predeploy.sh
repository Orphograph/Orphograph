#!/usr/bin/env bash
# predeploy.sh — local pre-deploy gate. Run BEFORE `fly deploy`.
#
# Sibling of preflight.sh, which probes the LIVE site AFTER deploy. This
# script gates the local repo state: tests, leak scans, regression checks
# on the 2026-05-22 funnel-fix batch.
#
# Exit 0 = safe to deploy. Non-zero = at least one check failed.
#
# Usage: bash scripts/predeploy.sh

set -u
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT" || { echo "cannot cd to repo root"; exit 2; }

fail=0
check() {
  local name="$1"; shift
  printf "  %-58s " "$name"
  if "$@" >/tmp/predeploy_check.out 2>&1; then
    echo "PASS"
  else
    echo "FAIL"
    sed 's/^/      /' /tmp/predeploy_check.out | head -8
    fail=$((fail + 1))
  fi
}

echo "predeploy: orphograph local deploy gate"
echo "  repo: $REPO_ROOT"
echo

# The DEPLOY-GATE command, not a hand-derived subset: the old line ran
# tests/ only (no capture/, gate reader, zk-provenance, sdk) and judged a
# tail'd summary line instead of pytest's exit code.
check "1. pytest suite (deploy gate)" "$REPO_ROOT/scripts/run_gate_tests.sh"

check "2. no founder-PII / brand-lineage in externals" bash -c '
  ! grep -rIn -E "rodriguezrivera|hyperliquid|\bhydroboro\b|boroscope|thermohydro|trail-audit" \
    web/ outbox/COLD_OUTREACH_*.md 2>/dev/null | grep -v "^Binary"
'

check "3. no literal secrets in web/ or outbox/" bash -c '
  ! grep -rIn -E "sk_live_[A-Za-z0-9]|pk_live_[A-Za-z0-9]|re_live_[A-Za-z0-9]" \
    web/ outbox/ 2>/dev/null | grep -v "^Binary"
'

check "4. sitemap parses and all URLs resolve" python3 -c '
import sys, xml.etree.ElementTree as ET, pathlib
root = ET.parse("web/sitemap.xml").getroot()
ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
missing = []
for url in root.iter(ns + "loc"):
    p = url.text.replace("https://orphograph.com", "").lstrip("/")
    if not p or p.endswith("/"):
        p = p + "index.html"
    if not (pathlib.Path("web") / p).exists():
        missing.append(url.text)
if missing:
    print("missing:", *missing[:5], sep="\n  ")
    sys.exit(1)
'

check "5. no dead /#how /#pricing /#verify /#faq anchors" bash -c '
  ! grep -rn -E "href=\"/#(how|pricing|verify|faq)\"" web/ 2>/dev/null
'

check "6. no alert( calls in v2.js (funnel finding 5)" bash -c '
  ! grep -nE "^[^/]*[^/_a-zA-Z]alert\(" web/v2.js
'

check "7. no third-party trackers in web/" bash -c '
  ! grep -rIn -iE "google-analytics|googletagmanager|gtag\(|posthog\.com|plausible\.io|umami\.is|fullstory|segment\.com|mixpanel" web/ 2>/dev/null
'

check "8. analytics script tag in every web/*.html" python3 -c '
import sys, pathlib
# Excluded: press-kit/ assets are offline-downloadable (open in journalist'"'"'s
# browser without internet) — phoning home from those would be wrong.
# Excluded: _mockups/ are founder-private staging.
EXCLUDE_PREFIXES = ("web/press-kit/", "web/_mockups/", "web/index-legacy.html")
missing = []
for p in pathlib.Path("web").rglob("*.html"):
    sp = str(p)
    if any(sp.startswith(pfx) for pfx in EXCLUDE_PREFIXES):
        continue
    s = p.read_text()
    if "</body>" in s and "event.js" not in s:
        missing.append(sp)
if missing:
    print("pages missing event.js include:", *missing[:8], sep="\n  ")
    sys.exit(1)
'

check "9. sample receipt card present on homepage" bash -c '
  grep -q "sample-receipt-card" web/index.html
'

check "10. writers.html Purchase routes to /pay/crypto.html" bash -c '
  grep -q "href=\"/pay/crypto.html\".*Purchase\|Purchase.*href=\"/pay/crypto.html\"" web/writers.html
'

check "11. all 16 blog posts exist on disk" python3 -c '
import re, sys, pathlib
idx = pathlib.Path("web/blog/index.html").read_text()
slugs = re.findall(r"href=\"([^\"]+\.html)\"", idx)
slugs = [s for s in slugs if not s.startswith("/") or s.startswith("/blog/")]
missing = []
for s in slugs:
    if s.startswith("/blog/"): s = s.replace("/blog/", "")
    if s == "index.html": continue
    p = pathlib.Path("web/blog") / s
    if not p.exists(): missing.append(s)
if missing:
    print("missing posts:", *missing, sep="\n  ")
    sys.exit(1)
'

check "12. plist templates have no hardcoded founder paths" bash -c '
  ! grep -l "rodriguezrivera\|/Users/francisco" scripts/*.plist.template 2>/dev/null
'

check "13. cold outreach drafts scan clean" bash -c '
  test -z "$(grep -l -i \"hydroboro\|hyperliquid\|core/signals\|referee/\" outbox/COLD_OUTREACH_*.md 2>/dev/null | grep -v legacy_drafts)"
'

echo
if [ $fail -eq 0 ]; then
  echo "predeploy: ALL 13 CHECKS PASSED — safe to fly deploy."
  exit 0
else
  echo "predeploy: $fail check(s) FAILED — do NOT deploy until resolved."
  exit 1
fi
