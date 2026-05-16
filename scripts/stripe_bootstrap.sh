#!/usr/bin/env bash
# stripe_bootstrap.sh — provision Orphograph's 4 Stripe products + prices +
# Payment Links via the REST API (no Stripe CLI dependency) and persist the
# resulting hosted Payment Link URLs into .env.local.
#
# What this builds (one round-trip per row):
#
#   ┌────────────────────────────────────┬──────────────┬─────────────────────┐
#   │ Name                               │ Amount       │ Lookup key (price)  │
#   ├────────────────────────────────────┼──────────────┼─────────────────────┤
#   │ Orphograph Pack — 10 anchors       │ $7   one-time│ orpho_pack_v1       │
#   │ Orphograph Personal (monthly)      │ $5/mo        │ orpho_personal_m_v1 │
#   │ Orphograph Personal (annual)       │ $60/yr       │ orpho_personal_y_v1 │
#   │ Orphograph Creator (monthly)       │ $19/mo       │ orpho_creator_m_v1  │
#   └────────────────────────────────────┴──────────────┴─────────────────────┘
#
# Idempotency: products are matched by exact `name`; prices by `lookup_key`.
# If found, they are reused — re-running the script is safe.
#
# Auth: HTTP Basic, secret key as username, empty password (Stripe convention).
#
# Usage:
#     # 1. drop STRIPE_API_KEY=sk_live_... (or sk_test_...) into .env.local
#     # 2. run:
#     bash ~/orphograph/scripts/stripe_bootstrap.sh
#     # or preview without touching the API:
#     bash ~/orphograph/scripts/stripe_bootstrap.sh --dry-run
#
# Output: STRIPE_PACK_URL / STRIPE_PERSONAL_MONTHLY_URL /
# STRIPE_PERSONAL_ANNUAL_URL / STRIPE_CREATOR_MONTHLY_URL written into
# .env.local (existing values are replaced in-place, new ones appended).

set -u
set -o pipefail

# ─────────────────────────────────────────────────────────────────
# Warm palette — same tones as scripts/setup_email.py
# ─────────────────────────────────────────────────────────────────
INK=$'\033[38;2;31;29;26m'
TEXT=$'\033[38;2;58;54;49m'
MUTED=$'\033[38;2;131;126;117m'
AMBER=$'\033[38;2;192;138;62m'
SAGE=$'\033[38;2;74;154;115m'
ERR=$'\033[38;2;178;80;80m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
RESET=$'\033[0m'

say()   { printf '%s\n' "${INK}$*${RESET}"; }
muted() { printf '%s\n' "${MUTED}$*${RESET}"; }
ok()    { printf '%s\n' "${SAGE}✓${RESET} ${INK}$*${RESET}"; }
warn()  { printf '%s\n' "${AMBER}!${RESET} ${INK}$*${RESET}"; }
die()   { printf '%s\n' "${ERR}✗${RESET} ${INK}$*${RESET}" >&2; exit 1; }
step()  { printf '\n%s\n' "${AMBER}▸${RESET} ${BOLD}${INK}$*${RESET}"; }

# ─────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_LOCAL="${ROOT}/.env.local"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) die "unknown flag: $arg (try --dry-run or --help)" ;;
  esac
done

# ─────────────────────────────────────────────────────────────────
# Preflight
# ─────────────────────────────────────────────────────────────────
command -v curl    >/dev/null 2>&1 || die "curl not found in PATH"
command -v python3 >/dev/null 2>&1 || die "python3 not found in PATH"

printf '\n%s\n' "${AMBER}╭──────────────────────────────────────────────────────╮${RESET}"
printf '%s\n'   "${AMBER}│${RESET} ${BOLD}${INK}Orphograph — Stripe bootstrap${RESET}                       ${AMBER}│${RESET}"
printf '%s\n'   "${AMBER}╰──────────────────────────────────────────────────────╯${RESET}"

STRIPE_API_KEY=""
if [[ -f "$ENV_LOCAL" ]]; then
  # Read STRIPE_API_KEY without sourcing the whole file (avoids side effects).
  STRIPE_API_KEY="$(grep -E '^[[:space:]]*STRIPE_API_KEY[[:space:]]*=' "$ENV_LOCAL" \
                    | tail -n1 \
                    | sed -E 's/^[[:space:]]*STRIPE_API_KEY[[:space:]]*=[[:space:]]*//' \
                    | sed -E 's/^"(.*)"$/\1/' \
                    | sed -E "s/^'(.*)'\$/\\1/")"
fi

if [[ -z "${STRIPE_API_KEY:-}" ]]; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    warn ".env.local has no STRIPE_API_KEY — running in dry-run mode."
  else
    say   ""
    warn  "No STRIPE_API_KEY in ${ENV_LOCAL/$HOME/~}"
    muted ""
    muted "Add a line like:"
    muted "    STRIPE_API_KEY=sk_live_xxx        # or sk_test_xxx"
    muted ""
    muted "then re-run this script. Use --dry-run to preview without the key."
    exit 1
  fi
fi

if [[ -n "${STRIPE_API_KEY:-}" ]]; then
  KEY_KIND="unknown"
  case "$STRIPE_API_KEY" in
    sk_live_*) KEY_KIND="LIVE  ⚠"  ;;
    sk_test_*) KEY_KIND="test"     ;;
    rk_live_*) KEY_KIND="restricted live ⚠" ;;
    rk_test_*) KEY_KIND="restricted test"   ;;
  esac
  muted ""
  ok    "Loaded STRIPE_API_KEY (${KEY_KIND})"
fi
if [[ "$DRY_RUN" -eq 1 ]]; then
  warn  "DRY-RUN: no API calls will be made."
fi

API="https://api.stripe.com/v1"

# ─────────────────────────────────────────────────────────────────
# Tiny JSON helper — pull a top-level scalar from a JSON blob.
# Usage: jget '<json>' some.key
# Returns empty on miss; supports dot paths via python's dict walk.
# ─────────────────────────────────────────────────────────────────
jget() {
  python3 - "$1" "$2" <<'PY' 2>/dev/null || true
import json, sys
blob, path = sys.argv[1], sys.argv[2]
try:
    d = json.loads(blob)
except Exception:
    sys.exit(0)
for k in path.split("."):
    if isinstance(d, dict) and k in d:
        d = d[k]
    elif isinstance(d, list):
        try:
            d = d[int(k)]
        except Exception:
            sys.exit(0)
    else:
        sys.exit(0)
if d is None:
    sys.exit(0)
print(d)
PY
}

# Walk a top-level "data" list, return the first item whose name matches arg.
jfind_by_name() {
  python3 - "$1" "$2" <<'PY' 2>/dev/null || true
import json, sys
blob, want = sys.argv[1], sys.argv[2]
try:
    d = json.loads(blob)
except Exception:
    sys.exit(0)
for it in d.get("data", []):
    if it.get("name") == want:
        print(it.get("id", ""))
        break
PY
}

# ─────────────────────────────────────────────────────────────────
# curl wrappers — Basic auth with empty password.
# Echo what we'd send in dry-run mode; otherwise hit the API.
# ─────────────────────────────────────────────────────────────────
stripe_post() {
  local path="$1"; shift
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '{"id":"dryrun_%s","name":"%s","url":"https://buy.stripe.com/dryrun_%s"}' \
      "$(printf '%s' "$path" | tr '/' '_')" "$*" "$(printf '%s' "$path$*" | python3 -c 'import sys,hashlib;print(hashlib.sha256(sys.stdin.read().encode()).hexdigest()[:12])')"
    return 0
  fi
  curl --silent --show-error --fail-with-body \
       -u "${STRIPE_API_KEY}:" \
       "$@" \
       "${API}${path}"
}

stripe_get() {
  local path="$1"; shift
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '{"data":[]}'
    return 0
  fi
  curl --silent --show-error --fail-with-body \
       -u "${STRIPE_API_KEY}:" \
       -G "$@" \
       "${API}${path}"
}

# ─────────────────────────────────────────────────────────────────
# Find-or-create a product by name. Echoes the product id.
# Args: name, tier_meta, extra_meta_kv (k=v repeated, optional)
# ─────────────────────────────────────────────────────────────────
ensure_product() {
  local name="$1"; shift
  local -a meta=("$@")

  # Search active products. Stripe's `search` endpoint supports `name:"..."`
  # but requires a paid plan; we fall back to a plain list + filter.
  local list resp pid
  list="$(stripe_get "/products" -d "active=true" -d "limit=100")"
  pid="$(jfind_by_name "$list" "$name")"
  if [[ -n "$pid" ]]; then
    printf '  %s reusing product ${pid}: %s\n' "${SAGE}↺${RESET}" "$name" >&2
    printf '%s' "$pid"
    return 0
  fi

  local -a fields=(-d "name=${name}")
  for kv in "${meta[@]}"; do
    fields+=(-d "metadata[${kv%%=*}]=${kv#*=}")
  done
  resp="$(stripe_post "/products" "${fields[@]}")"
  pid="$(jget "$resp" id)"
  [[ -n "$pid" ]] || die "product create failed for: $name :: $resp"
  printf '  %s created product %s: %s\n' "${SAGE}+${RESET}" "$pid" "$name" >&2
  printf '%s' "$pid"
}

# ─────────────────────────────────────────────────────────────────
# Find-or-create a price under a product. Echoes the price id.
# Args: product_id, lookup_key, amount_cents, currency, interval(optional)
# interval = "" (one-time), "month", or "year"
# ─────────────────────────────────────────────────────────────────
ensure_price() {
  local product="$1" lookup="$2" amount="$3" currency="$4" interval="${5:-}"
  local list resp price_id

  list="$(stripe_get "/prices" -d "lookup_keys[]=${lookup}" -d "active=true" -d "limit=10")"
  price_id="$(python3 - "$list" "$lookup" <<'PY' 2>/dev/null || true
import json, sys
blob, want = sys.argv[1], sys.argv[2]
try:
    d = json.loads(blob)
except Exception:
    sys.exit(0)
for p in d.get("data", []):
    if p.get("lookup_key") == want and p.get("active"):
        print(p.get("id", ""))
        break
PY
)"
  if [[ -n "$price_id" ]]; then
    printf '  %s reusing price ${price_id}: %s\n' "${SAGE}↺${RESET}" "$lookup" >&2
    printf '%s' "$price_id"
    return 0
  fi

  local -a fields=(
    -d "product=${product}"
    -d "unit_amount=${amount}"
    -d "currency=${currency}"
    -d "lookup_key=${lookup}"
  )
  if [[ -n "$interval" ]]; then
    fields+=(-d "recurring[interval]=${interval}")
  fi
  resp="$(stripe_post "/prices" "${fields[@]}")"
  price_id="$(jget "$resp" id)"
  [[ -n "$price_id" ]] || die "price create failed for: $lookup :: $resp"
  printf '  %s created price %s: %s\n' "${SAGE}+${RESET}" "$price_id" "$lookup" >&2
  printf '%s' "$price_id"
}

# ─────────────────────────────────────────────────────────────────
# Find-or-create a Payment Link for a given price. Echoes the URL.
# Idempotency note: Stripe doesn't let us name payment links, so we
# tag with metadata[orpho_lookup]=<lookup_key> and dedupe on that.
# ─────────────────────────────────────────────────────────────────
ensure_payment_link() {
  local price="$1" lookup="$2"
  local list url

  list="$(stripe_get "/payment_links" -d "active=true" -d "limit=100")"
  url="$(python3 - "$list" "$lookup" <<'PY' 2>/dev/null || true
import json, sys
blob, want = sys.argv[1], sys.argv[2]
try:
    d = json.loads(blob)
except Exception:
    sys.exit(0)
for link in d.get("data", []):
    md = link.get("metadata") or {}
    if md.get("orpho_lookup") == want:
        print(link.get("url", ""))
        break
PY
)"
  if [[ -n "$url" ]]; then
    printf '  %s reusing payment link: %s\n' "${SAGE}↺${RESET}" "$url" >&2
    printf '%s' "$url"
    return 0
  fi

  local resp
  resp="$(stripe_post "/payment_links" \
      -d "line_items[0][price]=${price}" \
      -d "line_items[0][quantity]=1" \
      -d "metadata[orpho_lookup]=${lookup}" \
      -d "after_completion[type]=redirect" \
      -d "after_completion[redirect][url]=https://orphograph.com/thanks?lookup=${lookup}")"
  url="$(jget "$resp" url)"
  [[ -n "$url" ]] || die "payment link create failed for: $lookup :: $resp"
  printf '  %s created payment link: %s\n' "${SAGE}+${RESET}" "$url" >&2
  printf '%s' "$url"
}

# ─────────────────────────────────────────────────────────────────
# Build out the 4 SKUs.
# ─────────────────────────────────────────────────────────────────
step "1/4  Pack — \$7 one-time, 10 anchors"
PROD_PACK="$(ensure_product 'Orphograph Pack — 10 anchors' \
  'tier=pack' 'anchors=10' 'product=orphograph')"
PRICE_PACK="$(ensure_price "$PROD_PACK" 'orpho_pack_v1' 700 'usd' '')"
URL_PACK="$(ensure_payment_link "$PRICE_PACK" 'orpho_pack_v1')"

step "2/4  Personal Monthly — \$5/mo"
PROD_PERSONAL="$(ensure_product 'Orphograph Personal (monthly)' \
  'tier=personal' 'billing=monthly' 'product=orphograph')"
PRICE_PERSONAL_M="$(ensure_price "$PROD_PERSONAL" 'orpho_personal_m_v1' 500 'usd' 'month')"
URL_PERSONAL_M="$(ensure_payment_link "$PRICE_PERSONAL_M" 'orpho_personal_m_v1')"

step "3/4  Personal Annual — \$60/yr"
PROD_PERSONAL_Y="$(ensure_product 'Orphograph Personal (annual)' \
  'tier=personal' 'billing=annual' 'product=orphograph')"
PRICE_PERSONAL_Y="$(ensure_price "$PROD_PERSONAL_Y" 'orpho_personal_y_v1' 6000 'usd' 'year')"
URL_PERSONAL_Y="$(ensure_payment_link "$PRICE_PERSONAL_Y" 'orpho_personal_y_v1')"

step "4/4  Creator Monthly — \$19/mo"
PROD_CREATOR="$(ensure_product 'Orphograph Creator (monthly)' \
  'tier=creator' 'billing=monthly' 'product=orphograph')"
PRICE_CREATOR_M="$(ensure_price "$PROD_CREATOR" 'orpho_creator_m_v1' 1900 'usd' 'month')"
URL_CREATOR_M="$(ensure_payment_link "$PRICE_CREATOR_M" 'orpho_creator_m_v1')"

# ─────────────────────────────────────────────────────────────────
# Write URLs into .env.local — upsert (replace existing, append new).
# ─────────────────────────────────────────────────────────────────
step "Writing Payment Link URLs into .env.local"

if [[ "$DRY_RUN" -eq 1 ]]; then
  warn "DRY-RUN: would write the following into ${ENV_LOCAL/$HOME/~}:"
  printf '%s\n'   "${MUTED}    STRIPE_PACK_URL=\"${URL_PACK}\"${RESET}"
  printf '%s\n'   "${MUTED}    STRIPE_PERSONAL_MONTHLY_URL=\"${URL_PERSONAL_M}\"${RESET}"
  printf '%s\n'   "${MUTED}    STRIPE_PERSONAL_ANNUAL_URL=\"${URL_PERSONAL_Y}\"${RESET}"
  printf '%s\n'   "${MUTED}    STRIPE_CREATOR_MONTHLY_URL=\"${URL_CREATOR_M}\"${RESET}"
  exit 0
fi

mkdir -p "$ROOT"
touch "$ENV_LOCAL"
chmod 600 "$ENV_LOCAL"

python3 - "$ENV_LOCAL" \
  "STRIPE_PACK_URL=$URL_PACK" \
  "STRIPE_PERSONAL_MONTHLY_URL=$URL_PERSONAL_M" \
  "STRIPE_PERSONAL_ANNUAL_URL=$URL_PERSONAL_Y" \
  "STRIPE_CREATOR_MONTHLY_URL=$URL_CREATOR_M" <<'PY'
import sys, pathlib, re, tempfile, os
path = pathlib.Path(sys.argv[1])
updates = {}
for kv in sys.argv[2:]:
    k, _, v = kv.partition("=")
    updates[k] = v

text = path.read_text() if path.exists() else ""
lines = text.splitlines()

# Find section header (if any) so new keys land in a tidy block.
SECTION_HEADER = "# --- Stripe (Payment Link URLs, written by stripe_bootstrap.sh) ---"
seen = set()
out = []
for line in lines:
    m = re.match(r'^\s*([A-Z][A-Z0-9_]*)\s*=', line)
    if m and m.group(1) in updates:
        key = m.group(1)
        out.append(f'{key}="{updates[key]}"')
        seen.add(key)
    else:
        out.append(line)

missing = [k for k in updates if k not in seen]
if missing:
    if out and out[-1].strip() != "":
        out.append("")
    out.append(SECTION_HEADER)
    for k in ["STRIPE_PACK_URL", "STRIPE_PERSONAL_MONTHLY_URL",
              "STRIPE_PERSONAL_ANNUAL_URL", "STRIPE_CREATOR_MONTHLY_URL"]:
        if k in missing:
            out.append(f'{k}="{updates[k]}"')

# Atomic replace, preserve 0600 perms.
fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.local.", suffix=".tmp")
try:
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(out).rstrip() + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
except Exception:
    try: os.unlink(tmp)
    except FileNotFoundError: pass
    raise
PY

ok "Wrote 4 STRIPE_*_URL entries into ${ENV_LOCAL/$HOME/~}"

printf '\n%s\n' "${SAGE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
printf '%s\n'   "${BOLD}${INK}Done. Payment Links ready:${RESET}"
printf '%s\n'   "${MUTED}  Pack             ${RESET}${INK}${URL_PACK}${RESET}"
printf '%s\n'   "${MUTED}  Personal monthly ${RESET}${INK}${URL_PERSONAL_M}${RESET}"
printf '%s\n'   "${MUTED}  Personal annual  ${RESET}${INK}${URL_PERSONAL_Y}${RESET}"
printf '%s\n'   "${MUTED}  Creator monthly  ${RESET}${INK}${URL_CREATOR_M}${RESET}"
printf '%s\n'   "${SAGE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
printf '\n%s\n' "${MUTED}Next: paste these URLs into web/index.html pricing buttons,${RESET}"
printf '%s\n\n' "${MUTED}or have app.py read them from .env.local at startup.${RESET}"
