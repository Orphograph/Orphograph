#!/usr/bin/env python3
"""migrate_design_system.py — apply the archival shell to a page.

Phase 2 of the visual migration. The ten core pages share an identical
`<header class="nav">` block and load `/index.css`, so one transform covers
all of them. Homepage-only composition (`orpho-home.css`) is deliberately NOT
linked here.

Design rules this script obeys:

  * IDEMPOTENT — running twice is a no-op. Every step checks for its own
    output before writing.
  * FAILS LOUD — a page that does not match the expected shape is SKIPPED with
    a reason, never partially transformed. A half-migrated page is worse than
    an unmigrated one.
  * PRESERVES HOOKS — `live-status-badge` and every nav href survive verbatim.
    The header is rebuilt from the page's own links, not from a template, so a
    page with a different nav keeps its own.

Usage:
    python3 scripts/migrate_design_system.py --check      # report only
    python3 scripts/migrate_design_system.py --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

# The ten pages sharing the `nav` header + /index.css base.
CORE_PAGES = [
    "verify-js.html", "pricing.html", "faq.html", "mcp.html", "access.html",
    "status.html", "security.html", "writers.html", "about-the-office.html",
    "integrations.html",
]

TOKENS = '<link rel="stylesheet" href="/css/orpho-tokens.css?v=1">'
PRIMS = '<link rel="stylesheet" href="/css/orpho-primitives.css?v=1">'
INDEX_CSS_RE = re.compile(r'<link rel="stylesheet" href="/index\.css\?v=\d+">')
HEADER_RE = re.compile(r'<header class="nav">\s*<div class="wrap row">(.*?)</div>\s*</header>', re.DOTALL)


def build_header(inner: str) -> str:
    """Rebuild the header as a brand lockup + nav, from the page's OWN links.

    The nav block is lifted verbatim, so a page whose nav differs keeps its
    difference. Only the brand anchor is replaced — the plain "Orphograph"
    text link becomes crest + wordmark + subtitle.
    """
    nav = re.search(r"<nav>(.*?)</nav>", inner, re.DOTALL)
    if not nav:
        return ""
    links = nav.group(1).strip()
    # The CTA becomes the one filled button, matching the homepage.
    links = re.sub(r'class="cta cta-btn"', 'class="orpho-btn orpho-btn--primary cta cta-btn"', links)
    return (
        '<header class="orpho-header nav">\n'
        '  <a href="/" class="orpho-brand brand">\n'
        '    <img class="orpho-brand__mark" src="/seal-display.png?v=8" alt="" width="62" height="62">\n'
        '    <span class="orpho-brand__text">\n'
        '      <span class="orpho-brand__name">Orphograph</span>\n'
        '      <span class="orpho-brand__sub">Empirical Notary</span>\n'
        '    </span>\n'
        '  </a>\n'
        '  <nav class="orpho-nav">\n'
        f'    {links}\n'
        '  </nav>\n'
        '</header>'
    )


def migrate(path: Path) -> tuple[bool, str]:
    src = path.read_text(encoding="utf-8")
    out = src

    if "orpho-tokens.css" in src:
        return False, "already migrated"

    m = INDEX_CSS_RE.search(out)
    if not m:
        return False, "no /index.css link — unexpected shape, skipped"
    out = out[:m.start()] + TOKENS + "\n" + PRIMS + "\n" + out[m.start():]

    if "<body>" not in out:
        return False, "no bare <body> — page has its own body class, skipped"
    out = out.replace("<body>", '<body class="orpho">', 1)

    hm = HEADER_RE.search(out)
    if not hm:
        return False, "header does not match the shared shape, skipped"
    new_header = build_header(hm.group(1))
    if not new_header:
        return False, "header has no <nav>, skipped"
    out = out[:hm.start()] + new_header + out[hm.end():]

    if '<footer class="site">' in out:
        out = out.replace(
            '<footer class="site">',
            '<footer class="site orpho-footer">\n'
            '  <span class="orpho-footer__medallion" aria-hidden="true"></span>', 1)

    path.write_text(out, encoding="utf-8")
    return True, "migrated"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    done = skipped = 0
    for name in CORE_PAGES:
        p = WEB / name
        if not p.exists():
            print(f"  {name:26} MISSING"); skipped += 1; continue
        if not args.apply:
            state = "already migrated" if "orpho-tokens.css" in p.read_text() else "would migrate"
            print(f"  {name:26} {state}"); continue
        ok, why = migrate(p)
        print(f"  {name:26} {why}")
        done += ok; skipped += (not ok)

    if args.apply:
        print(f"\n{done} migrated, {skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
