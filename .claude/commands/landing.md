---
description: Deep audit of landing page copy and conversion flow
---

Read CLAUDE.md. Then read every file under `web/` (especially
`web/index.html`, `web/style.css`, `web/app.js`).

Audit the landing page on these dimensions, line by line:

1. **5-second test:** Within 5 seconds, can a visitor tell who this is for
   and why they should care? If not, propose H1 + subhead rewrites.

2. **Buyer match:** Does the copy speak to the buyer hypothesis in CLAUDE.md
   (photographers worried about AI scraping)? If not, rewrite for that persona.

3. **Trust signals present:** Sample receipt visible? Verifier demo?
   Open-source link (to `verify_cli.py`)? Founder identity? Social proof?
   List missing items.

4. **Conversion friction:** Walk the "drop file → first hash → save receipt
   → paid upgrade" flow. Identify every click, decision, and confusion point.

5. **Honest claim audit:** Flag any claim that isn't backed by what's in
   the code (especially "private," "court-admissible," "legally binding,"
   "notarized," "differentiated method"). Rewrite to be defensible.
   Current copy says "Your file is hashed in your browser. Only the
   32-byte fingerprint reaches our server" — verify this is still true
   in `web/app.js`.

6. **OpenTimestamps objection:** Does the page explain why someone should
   pay us when OpenTimestamps is free? If not, add a section.

7. **FAQ gaps:** What questions would a skeptical buyer have that we don't
   address? (e.g. "what if your domain dies?", "how do I prove it in
   court?", "why not just use OpenTimestamps directly?")

Output a single markdown report with: current copy quoted → problem →
specific rewrite. Save to `docs/audits/landing-$(date +%Y-%m-%d).md`.
