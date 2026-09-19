"""test_blog_md_price_drift.py — content/blog/*.md pack-price drift gate.

content/blog/*.md posts are rendered two ways: nine of ten are shadowed at
/blog/<slug> by a static file under web/blog/<slug>.html (server/app.py,
the "Clean URL for a static HTML post" branch), and one
(prove-what-was-in-your-training-set.md) is unshadowed and served directly
by blog.render_post_html(). Shadowing is not permanent cover: app.py falls
through to the .md renderer the moment the matching web/blog/<slug>.html is
missing, and every post's title + front-matter `summary:` is served live
today via /blog/atom.xml regardless of shadowing (blog.atom_feed_xml() reads
content/blog/*.md directly and never consults web/blog/). A stale price
string sitting in a "dead" .md source is one file deletion away from going
live, so it is gated here rather than left to be caught only when it
surfaces on a page.

Current pricing truth (verified 2026-09-19):
  - server/nowpayments_api.py PLANS: writer_pack = $19 / 10 credits,
    pack_50 = $29 / 50 credits.
  - web/pricing.html: Writer Pack $19 (10 anchors), Pack of Fifty $29
    (50 anchors), Standing Order $9/month.

The known drift class is NOT "$29" or "$19" appearing at all — both are
correct prices for their own tier ($19 for 10 credits, $29 for 50 credits).
The defect is the two paired wrong: an old boilerplate line that priced a
*10-anchor* pack at *$29* (Pack of Fifty's price, not Writer Pack's), found
verbatim in four posts on 2026-09-19 and fixed in this same change. Each
pattern below is a (price, count-word) conjunction, not a bare dollar
amount, so the gate cannot fire on correct copy (e.g. "$19 for a 10-anchor
pack" or "Pack of Fifty: $29") and cannot be satisfied by ever forbidding a
correct price from appearing in a post.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = ROOT / "content" / "blog"

# (compiled pattern, reason) — every pattern here must be a mismatched
# price/count pairing, cite the correct source, and have zero legitimate use.
STALE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\$29\s+for\s+a\s+10-(?:anchor\s+)?pack"),
        "$29 is the Pack of Fifty price (50 credits) per "
        "server/nowpayments_api.py PLANS['pack_50'] and web/pricing.html "
        "line ~133/136. A 10-anchor/10-pack claim must price at $19 "
        "(PLANS['writer_pack'], web/pricing.html line ~109/112).",
    ),
    (
        re.compile(r"\$19\s+for\s+a\s+50-(?:anchor\s+)?pack"),
        "$19 is the Writer Pack price (10 credits) per "
        "server/nowpayments_api.py PLANS['writer_pack']. A 50-anchor/"
        "50-pack claim must price at $29 (PLANS['pack_50']).",
    ),
]


def test_blog_dir_exists_and_is_not_empty() -> None:
    # A missing or empty content/blog would make every scan below vacuous —
    # collecting zero files must fail the suite, not silently pass it.
    assert BLOG_DIR.is_dir(), f"missing: {BLOG_DIR}"
    md_files = sorted(BLOG_DIR.glob("*.md"))
    assert len(md_files) > 0, f"no .md files found under {BLOG_DIR}"


def test_no_stale_pack_price_strings_in_blog_md() -> None:
    md_files = sorted(BLOG_DIR.glob("*.md"))
    assert len(md_files) > 0, f"no .md files found under {BLOG_DIR}"

    hits: list[str] = []
    for path in md_files:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for pattern, reason in STALE_PATTERNS:
            for m in pattern.finditer(text):
                lineno = text.count("\n", 0, m.start()) + 1
                snippet = lines[lineno - 1].strip() if lineno - 1 < len(lines) else ""
                hits.append(
                    f"{path.relative_to(ROOT)}:{lineno}: {snippet!r} — {reason}"
                )

    assert not hits, "stale pack-price string(s) found:\n" + "\n".join(hits)
