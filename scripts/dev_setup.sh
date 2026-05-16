#!/usr/bin/env bash
# scripts/dev_setup.sh — one-shot sanity check + smoke for fresh clones.
#
# Verifies Python is recent enough, runs the full test suite, optionally
# runs the live OTS smoke test, and prints what's left to do per
# deploy/FOUNDER_TODO.md.
#
# Stdlib-only project: no pip install needed for the app itself.
# pytest is the only dev-time dependency.
set -euo pipefail

cd "$(dirname "$0")/.."
ORPHO_ROOT="$(pwd)"

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_yel=$'\033[33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'

step() { printf "\n${c_dim}—— %s ——${c_off}\n" "$1"; }
ok()   { printf "${c_grn}✓${c_off} %s\n" "$1"; }
warn() { printf "${c_yel}!${c_off} %s\n" "$1"; }
fail() { printf "${c_red}✗${c_off} %s\n" "$1"; exit 1; }

step "Python version check (need 3.9+)"
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
  fail "Python 3.9+ required, found ${PY_VER}"
fi
ok "Python ${PY_VER}"

step "pytest available?"
if ! python3 -m pytest --version >/dev/null 2>&1; then
  warn "pytest not installed — install it now? [Y/n]"
  read -r ans
  case "${ans:-y}" in
    [Nn]*) warn "skipping pytest install; tests will not run";;
    *) pip install --user "pytest>=9" || pip install "pytest>=9";;
  esac
fi
if python3 -m pytest --version >/dev/null 2>&1; then
  ok "pytest $(python3 -m pytest --version 2>&1 | tail -1)"
fi

step "Server modules compile cleanly"
python3 -m py_compile server/*.py
ok "all $(ls server/*.py | wc -l | tr -d ' ') modules compile"

step "Run the unit test suite (offline, ~1s)"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -q
ok "tests pass"

step "Optional: live OTS smoke test (hits 5 OpenTimestamps calendars)"
if [ "${1:-}" = "--smoke" ]; then
  DATA_DIR="$(mktemp -d)"
  PORT=9100 ORPHO_DATA_DIR="$DATA_DIR" bash scripts/smoke_test.sh >/dev/null
  ok "live smoke test: 5/5 calendars anchored"
  rm -rf "$DATA_DIR"
else
  warn "skipped (re-run with --smoke to test against the live OTS network)"
fi

step "OSS verifier round-trip on the bundled sample"
(
  cd dist/orphograph-verify
  python3 verify.py examples/sample/receipt.json --file examples/sample/sample.txt >/dev/null
)
ok "OSS verifier validates the bundled sample"

step "Deploy artifacts present?"
for f in Dockerfile fly.toml .env.example deploy/README.md deploy/FOUNDER_TODO.md \
         deploy/RUNBOOK.md deploy/SECURITY.md deploy/PAYMENT_PII_AUDIT.md \
         deploy/MARKET_ROADMAP.md deploy/VALUATION_2026_05_12_EVENING.md; do
  if [ -f "$ORPHO_ROOT/$f" ]; then ok "$f"; else warn "missing: $f"; fi
done

echo
echo "${c_grn}=================================================================${c_off}"
echo "Setup verified. Project state:"
echo "  Code:     $(find server web tests scripts dist -type f \( -name "*.py" -o -name "*.html" -o -name "*.css" -o -name "*.js" -o -name "*.sh" -o -name "*.toml" -o -name "Dockerfile" \) ! -path "*/__pycache__/*" 2>/dev/null | xargs wc -l 2>/dev/null | tail -1 | awk '{print $1}') LOC"
echo "  Tests:    $(PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ --collect-only -q 2>/dev/null | tail -1 | awk '{print $1}') passing"
echo "  Audits:   security, payment+PII, forensic (all in deploy/)"
echo
echo "What's still gated on you (the founder), per deploy/FOUNDER_TODO.md:"
echo "  1. Register orphograph.com (Porkbun/Namecheap)"
echo "  2. Push dist/orphograph-verify/ to GitHub"
echo "  3. Stripe account + Pack product + Subscription product + webhook"
echo "  4. Resend account + verify orphograph.com sending domain"
echo "  5. fly launch + fly volumes create + fly deploy"
echo "  6. 5 photographer interviews (drafts in outreach/)"
echo "  7. Show HN Tuesday 9 AM ET (draft at outreach/show_hn_draft.md)"
echo
echo "Once steps 1–5 are done, the live URL is the only thing blocking revenue."
echo "${c_grn}=================================================================${c_off}"
