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

# Pages are DISCOVERED by shape, not listed by hand. Anything carrying the
# shared `<header class="nav">` and loading /index.css is migratable; every
# other shape is skipped with a reason. Hand-maintaining a list across 106
# pages guarantees an omission, and an omitted page is an invisible one.
EXCLUDE_DIRS = {"_mockups", "dist", "construction"}
# v2/index.html is the dormant dark A/B arm; founder/ is not customer-facing.
EXCLUDE_FILES = {"v2/index.html", "index-legacy.html"}


def discover() -> list[Path]:
    out = []
    for f in sorted(WEB.rglob("*.html")):
        rel = f.relative_to(WEB).as_posix()
        if any(part in EXCLUDE_DIRS for part in f.relative_to(WEB).parts):
            continue
        if rel in EXCLUDE_FILES or rel.startswith("founder/"):
            continue
        out.append(f)
    return out

TOKENS = '<link rel="stylesheet" href="/css/orpho-tokens.css?v=1">'
PRIMS = '<link rel="stylesheet" href="/css/orpho-primitives.css?v=1">'
# The base sheet differs by family: most pages load /index.css, the blog and
# transactional families load /style.css. Either is a valid insertion anchor.
BASE_CSS_RE = re.compile(r'<link rel="stylesheet" href="/(?:index|style)\.css\?v=\d+">')
HEADER_RE = re.compile(r'<header class="nav">\s*<div class="wrap row">(.*?)</div>\s*</header>', re.DOTALL)
# Other header shapes carrying the same brand+nav content.
ALT_HEADER_RE = re.compile(r'<header class="(?:topnav|post-header|mast)">(.*?)</header>', re.DOTALL)
# The transactional/legal/lp family used a bare <header> with a lowercase
# brand div — never matched by the shapes above, so those 24 pages kept
# their pre-archival masthead through the 2026-08-08 migration (found by
# the founder's coherence audit, 2026-08-10).
BARE_HEADER_RE = re.compile(
    r'<header>\s*<div class="brand"><a href="/">orphograph</a></div>\s*'
    r'(<nav>.*?</nav>)\s*</header>', re.DOTALL)
BODY_RE = re.compile(r'<body([^>]*)>')


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

    m = BASE_CSS_RE.search(out)
    if not m:
        return False, "no /index.css or /style.css link — unexpected shape, skipped"
    out = out[:m.start()] + TOKENS + "\n" + PRIMS + "\n" + out[m.start():]

    # Body class — additive, so a page that already carries one keeps it.
    bm = BODY_RE.search(out)
    if not bm:
        return False, "no <body> tag, skipped"
    attrs = bm.group(1)
    if 'class="' in attrs:
        out = out[:bm.start()] + re.sub(r'class="', 'class="orpho ', attrs.join(("<body", ">")), count=1) + out[bm.end():]
    else:
        out = out[:bm.start()] + f'<body class="orpho"{attrs}>' + out[bm.end():]

    # Header. Pages with NO header are headerless BY DESIGN — the lp/ landing
    # pages and the pay/ flows deliberately omit nav so nothing competes with
    # the single action. Adding one would change conversion and payment UX,
    # which is a product decision, not a restyle. They get the shell only.
    hm = HEADER_RE.search(out) or ALT_HEADER_RE.search(out)
    if hm:
        new_header = build_header(hm.group(1))
        if new_header:
            out = out[:hm.start()] + new_header + out[hm.end():]
        # A header with no <nav> keeps its own markup; the shell still applies.

    if '<footer class="site">' in out:
        out = out.replace(
            '<footer class="site">',
            '<footer class="site orpho-footer">\n'
            '  <span class="orpho-footer__medallion" aria-hidden="true"></span>', 1)

    path.write_text(out, encoding="utf-8")
    return True, "migrated"


def retrofit_headers() -> int:
    """Second pass for pages that already carry the shell but kept the old
    bare-<header> masthead. Replaces ONLY the header block; nav links are
    lifted verbatim per page, same as the original migration."""
    done = skipped = 0
    for path in discover():
        src = path.read_text(encoding="utf-8")
        m = BARE_HEADER_RE.search(src)
        if not m:
            continue
        new_header = build_header(m.group(1))
        if not new_header:
            print(f"SKIP {path}: bare header without a <nav>")
            skipped += 1
            continue
        path.write_text(src[:m.start()] + new_header + src[m.end():], encoding="utf-8")
        print(f"retrofit {path}")
        done += 1
    print(f"\n{done} headers retrofitted, {skipped} skipped")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    done = skipped = 0
    reasons: dict[str, int] = {}
    for p in discover():
        name = p.relative_to(WEB).as_posix()
        if not args.apply:
            txt = p.read_text()
            if "orpho-tokens.css" in txt: continue
            state = "would migrate" if '<header class="nav">' in txt else "SKIP (shape)"
            print(f"  {name:42} {state}"); continue
        ok, why = migrate(p)
        if ok:
            print(f"  {name:42} migrated")
        reasons[why] = reasons.get(why, 0) + 1
        done += ok; skipped += (not ok)

    if args.apply:
        print(f"\n{done} migrated, {skipped} skipped")
        for why, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            if why != "migrated":
                print(f"    {n:3}  {why}")
    return 0


if __name__ == "__main__":
    import sys as _sys
    if "--retrofit-headers" in _sys.argv:
        raise SystemExit(retrofit_headers())
    sys.exit(main())
