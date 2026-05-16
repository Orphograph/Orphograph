# Receipt PDF — stdlib-only path (no weasyprint, no headless deps in container)

**Why this approach:** orphograph's principle #5 keeps the engine stdlib-only.
Bringing in `weasyprint`, `WeasyPrint`, or any `pdfkit` family library would add
~80MB of system deps (cairo, pango, fontconfig). For a feature most users
generate locally on their machine, that's overkill.

The win: every modern browser already has world-class HTML→PDF. We use it.

---

## The flow (3 paths)

### Path A — End-user, in-browser (recommended for customers)

1. Visit `https://orphograph.com/r/<receipt-id>`
2. Tap **"print / save as PDF"** in the header (or Cmd-P).
3. In the print dialog: Destination → **Save as PDF**.
4. Done. `.pdf` lives on the user's device.

This works in every browser. The receipt.css has `@media print` rules so the
output is ink-friendly (white background, dark text, no nav chrome).

### Path B — Curl + headless Brave (for automation / CLI tools)

```bash
# macOS Brave
"/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" \
  --headless \
  --disable-gpu \
  --no-pdf-header-footer \
  --print-to-pdf=receipt.pdf \
  "https://orphograph.com/r/<receipt-id>?print=1"
```

The `?print=1` query trigger auto-fires `window.print()` 400ms after the
explorer grid finishes rendering — see `web/receipt.js:`. In headless mode,
`window.print()` writes the PDF to the path specified by `--print-to-pdf`.

For headless Chrome/Chromium: same flags. For Firefox: `--screenshot` does
not support PDF; use the launcher with `dom.printing.print_silent=true` set
in profile prefs, or stick with Brave/Chrome for this path.

### Path C — Server-side via a sidecar (only if you really need it)

If you want `GET /api/receipt/<id>.pdf` to return a binary PDF, a sidecar
container running `gotenberg` (chromium-as-a-service) is the right call.

- Add to fly.toml as a second app: `fly launch --image gotenberg/gotenberg:8`
- Update server/app.py to add a `/api/receipt/<id>.pdf` route that:
  1. Builds the public receipt URL
  2. POSTs to the gotenberg sidecar: `POST /forms/chromium/convert/url` with `url=https://orphograph.com/r/<id>?print=1`
  3. Streams the binary PDF response back to the client

Cost: $2-3/mo for a 256MB gotenberg machine. Latency: ~800ms per PDF.

**We do NOT ship this by default.** Path A covers 99% of use cases. Build the
sidecar only when a paying B2B customer asks for an API endpoint that returns
PDFs server-side (which they will, eventually — that's a $99/mo tier feature).

---

## Testing Path B locally

```bash
# 1. Server up
nohup python3 ~/orphograph/server/app.py > ~/orphograph/logs/server.out 2>&1 &

# 2. Anchor a sample file
HASH=$(echo "test" | shasum -a 256 | awk '{print $1}')
SHA512=$(echo "test" | shasum -a 512 | awk '{print $1}')
RID=$(curl -s -X POST http://127.0.0.1:8989/api/anchor \
  -H "Content-Type: application/json" \
  -d "{\"hash_hex\":\"$HASH\",\"sha512_hex\":\"$SHA512\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['receipt_id'])")

# 3. Generate PDF
"/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" \
  --headless --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="/tmp/receipt_${RID}.pdf" \
  "http://127.0.0.1:8989/r/${RID}?print=1"

# 4. Open the result
open "/tmp/receipt_${RID}.pdf"
```

Expected: a 1-page PDF showing the receipt card with all the explorer links
intact. No nav, no footer chrome. Print-friendly.

---

## What this WON'T do

- **Won't embed a digital signature inside the PDF.** PDF signatures need
  PKCS#7 + a cert chain + a TSA — that's a different product, and the whole
  point of Orphograph is that the *Bitcoin chain* IS the signature. The PDF
  is for human handoff, not as cryptographic evidence.

- **Won't generate eIDAS-qualified PDFs.** Per principle #5 (honest copy),
  we don't claim qualified-timestamp status anywhere. A PDF receipt is a
  printable view of the JSON receipt; it has the same evidentiary weight as
  the JSON, no more.

- **Won't replace the JSON receipt as the canonical artifact.** If a customer
  ever asks "what's the authoritative version of this anchor?", the answer
  is the receipt JSON + the 5 `.ots` files at `/api/receipt/<id>`, NOT the
  PDF. The PDF is a derivative for human consumption.

---

## Helper script

```bash
~/orphograph/scripts/receipt_to_pdf.sh <receipt-id> [output.pdf]
```

Wraps Path B with sane defaults. See file for details.
