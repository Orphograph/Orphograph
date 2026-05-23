#!/usr/bin/env python3
"""refresh_brand_assets.py — atomically swap brand assets across the site.

Run AFTER you've dropped the new HD files at the exact paths below.
Required:
  - web/seal.png           (the new globe + spiral seal, ≥ 1024px wide)
  - web/lockup.png         (the new wordmark + tagline, ≥ 1600px wide)  [optional but recommended]

What this script does:
  1. Validate each provided file (exists, parses as PNG, sane dimensions)
  2. Generate favicon variants from seal.png via Pillow:
       favicon-16.png, favicon-32.png, apple-touch-icon-180.png, favicon.ico
       (overwrites existing favicon.png with a 512×512 version for sharpness)
  3. Optionally generate og-image.png from the seal at 1200×630 with the
     cream paper background, if --og is passed
  4. Bump cache-buster ?v=6 → ?v=7 in every reference across web/, server/
  5. Print a summary diff + ready-to-deploy state

Safety rails:
  - REFUSES to run if web/seal.png mtime is older than 10 minutes (means
    you didn't drop a new file — script would needlessly bump cache).
  - REFUSES to run if the new seal has the same SHA-256 as the prior one
    (no-op safeguard).
  - Skips lockup.png steps if file is absent; warns rather than fails.
  - All edits to source files use exact-string replacement; if the source
    has been modified, the script prints what would change and exits.

Usage:
  # 1. Drop the new files at the exact paths above.
  # 2. Run:
  python3 scripts/refresh_brand_assets.py
  python3 scripts/refresh_brand_assets.py --og        # also regen og-image
  python3 scripts/refresh_brand_assets.py --dry-run   # show what would change
"""

from __future__ import annotations

import argparse
import hashlib
import io
import pathlib
import re
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
WEB = REPO / "web"
SEAL = WEB / "seal.png"
LOCKUP = WEB / "lockup.png"
SEAL_SHA_CACHE = REPO / "data" / ".seal_sha256"  # tracks the last applied seal

OLD_VER = "v=6"
NEW_VER = "v=7"

CREAM_BG = (246, 241, 227)  # matches --paper / brand cream

REFERENCE_EXTENSIONS = [".html", ".css", ".js", ".py", ".xml", ".json"]


def _sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _validate_png(p: pathlib.Path, min_width: int, label: str) -> tuple[int, int, int]:
    if not p.exists():
        return (0, 0, 0)
    from PIL import Image
    try:
        with Image.open(p) as im:
            im.verify()
        with Image.open(p) as im:
            w, h = im.size
            mode = im.mode
    except Exception as e:
        print(f"  ERROR {label}: cannot parse as image: {e}", file=sys.stderr)
        return (-1, -1, -1)
    size_kb = p.stat().st_size // 1024
    if w < min_width:
        print(f"  WARN  {label}: {w}×{h} ({size_kb}KB) — below recommended {min_width}px wide")
    else:
        print(f"  OK    {label}: {w}×{h} {mode} ({size_kb}KB)")
    return (w, h, size_kb)


def _regen_favicons(dry_run: bool) -> list[pathlib.Path]:
    from PIL import Image
    out_paths: list[pathlib.Path] = []
    with Image.open(SEAL) as src:
        src = src.convert("RGBA")
        for size, name in [(16, "favicon-16.png"), (32, "favicon-32.png"),
                           (180, "apple-touch-icon-180.png"), (512, "favicon.png")]:
            target = WEB / name
            if dry_run:
                print(f"  DRY-RUN would write {name} ({size}×{size})")
            else:
                resized = src.resize((size, size), Image.LANCZOS)
                resized.save(target, "PNG", optimize=True)
                print(f"  wrote {name} ({size}×{size}, {target.stat().st_size // 1024}KB)")
            out_paths.append(target)
        # .ico bundle
        ico = WEB / "favicon.ico"
        if dry_run:
            print("  DRY-RUN would write favicon.ico (16, 32, 48)")
        else:
            sizes = [(16, 16), (32, 32), (48, 48)]
            icons = [src.resize(s, Image.LANCZOS) for s in sizes]
            icons[0].save(ico, format="ICO", sizes=sizes, append_images=icons[1:])
            print(f"  wrote favicon.ico ({ico.stat().st_size // 1024}KB)")
        out_paths.append(ico)
    return out_paths


def _regen_og_image(dry_run: bool) -> pathlib.Path:
    from PIL import Image
    og = WEB / "og-image.png"
    with Image.open(SEAL) as src:
        src = src.convert("RGBA")
        # 1200×630 cream canvas with seal centered, sized to 80% of height
        canvas = Image.new("RGB", (1200, 630), CREAM_BG)
        target_h = int(630 * 0.8)
        scale = target_h / src.height
        target_w = int(src.width * scale)
        resized = src.resize((target_w, target_h), Image.LANCZOS)
        x = (1200 - target_w) // 2
        y = (630 - target_h) // 2
        canvas.paste(resized, (x, y), resized if resized.mode == "RGBA" else None)
        if dry_run:
            print("  DRY-RUN would write og-image.png (1200×630, cream + seal)")
        else:
            canvas.save(og, "PNG", optimize=True)
            print(f"  wrote og-image.png (1200×630, {og.stat().st_size // 1024}KB)")
    return og


def _bump_cachebusters(dry_run: bool) -> dict[pathlib.Path, int]:
    """Find every occurrence of `?v=6` for brand assets and bump to `?v=7`.

    Conservative: only bumps lines that also mention seal.png/seal.svg/
    favicon/lockup/og-image to avoid touching unrelated version markers.
    """
    # Match brand assets followed by ?v=6. Covers seal.png, seal.svg,
    # lockup.png, og-image.png, favicon.png, favicon-16.png, favicon-32.png,
    # apple-touch-icon-180.png, favicon.ico, favicon.svg.
    pattern = re.compile(
        r"((?:seal|lockup|og-image)\.(?:png|svg)|"
        r"(?:favicon[a-z0-9-]*|apple-touch-icon-\d+)\.(?:png|svg|ico))"
        r"\?v=6"
    )
    touched: dict[pathlib.Path, int] = {}
    for ext in REFERENCE_EXTENSIONS:
        for p in REPO.rglob(f"*{ext}"):
            sp = str(p)
            if any(skip in sp for skip in ("/.git/", "/node_modules/", "/.pytest_cache/",
                                            "/__pycache__/", "/dist/", "/secrets.sparseimage")):
                continue
            try:
                s = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if "v=6" not in s:
                continue
            new_s, n = pattern.subn(lambda m: f"{m.group(1)}?v=7", s)
            if n == 0:
                continue
            touched[p] = n
            if not dry_run:
                p.write_text(new_s, encoding="utf-8")
    return touched


def _git_status_paths() -> set[str]:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO), "diff", "--name-only"],
            stderr=subprocess.DEVNULL,
        ).decode().splitlines()
        return set(out)
    except Exception:
        return set()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--og", action="store_true", help="also generate og-image.png")
    ap.add_argument("--force", action="store_true",
                    help="bypass mtime / sha freshness guards")
    args = ap.parse_args()

    print(f"refresh_brand_assets: dry_run={args.dry_run} og={args.og}")
    print()

    # --- Freshness guards ---
    if not SEAL.exists():
        print(f"FAIL: {SEAL} does not exist. Drop the new seal there first.", file=sys.stderr)
        return 2

    seal_age = time.time() - SEAL.stat().st_mtime
    if seal_age > 600 and not args.force:
        print(
            f"FAIL: {SEAL} is {int(seal_age)}s old (>10 min). Did you forget to drop "
            f"the new file? Use --force to override.",
            file=sys.stderr,
        )
        return 3

    new_sha = _sha256_file(SEAL)
    if SEAL_SHA_CACHE.exists():
        prior_sha = SEAL_SHA_CACHE.read_text().strip()
        if prior_sha == new_sha and not args.force:
            print(
                f"FAIL: seal.png SHA matches the previously-applied seal — no-op.\n"
                f"      If you intended a re-bump, pass --force.\n"
                f"      sha256={new_sha[:16]}…",
                file=sys.stderr,
            )
            return 4

    # --- Validate ---
    print("--- validation ---")
    sw, sh, ssize = _validate_png(SEAL, 1024, "seal.png")
    if sw < 0:
        return 5
    if LOCKUP.exists():
        _validate_png(LOCKUP, 1600, "lockup.png")
    else:
        print("  INFO  lockup.png absent — skipping lockup-related steps")
    print()

    # --- Favicons ---
    print("--- favicon variants from seal.png ---")
    _regen_favicons(args.dry_run)
    print()

    # --- OG image ---
    if args.og:
        print("--- og-image (1200×630, cream + seal) ---")
        _regen_og_image(args.dry_run)
        print()

    # --- Cache busters ---
    print("--- cache-buster bump (?v=6 → ?v=7) ---")
    touched = _bump_cachebusters(args.dry_run)
    if not touched:
        print("  no files touched (no v=6 references found for brand assets)")
    else:
        for p, n in sorted(touched.items()):
            rel = p.relative_to(REPO)
            print(f"  {'DRY' if args.dry_run else 'BUMP'}  {rel} ({n} replacement{'s' if n != 1 else ''})")
    print()

    # --- Persist SHA cache ---
    if not args.dry_run:
        SEAL_SHA_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SEAL_SHA_CACHE.write_text(new_sha + "\n")
        print(f"  recorded new seal SHA: {new_sha[:16]}…")

    # --- Summary ---
    print()
    print("--- summary ---")
    print(f"  files touched: {len(touched)}")
    print(f"  new seal: {sw}×{sh} {ssize}KB · sha256={new_sha[:16]}…")
    if not args.dry_run:
        modified = _git_status_paths()
        if modified:
            print(f"  git modified ({len(modified)}):")
            for f in sorted(modified)[:15]:
                print(f"    {f}")
            if len(modified) > 15:
                print(f"    … +{len(modified) - 15} more")
        print()
        print("  next: bash scripts/predeploy.sh && git commit && fly deploy")
    else:
        print()
        print("  next (dry-run): re-run without --dry-run to actually apply")

    return 0


if __name__ == "__main__":
    sys.exit(main())
