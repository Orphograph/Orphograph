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

Current pricing truth (ladder of 2026-11-04, founder decision):
  - server/nowpayments_api.py PLANS: writer_pack = $5 / 25 credits,
    pack_50 = $20 / 200 credits (label "Pack of 200").
  - web/pricing.html: Writer Pack $5 (25 anchors), Pack of Two Hundred $20
    (200 anchors), Standing Order $9/month.

Before that the ladder was Writer Pack $19 / 10 and Pack of Fifty $29 / 50.
On 2026-09-19 the drift class was a mismatched pairing (a *10-anchor* pack
priced at *$29*). Each pattern below is either such a (price, count)
mismatch for the current ladder, or a string that only the retired ladder
used ("Pack of Fifty", "$19"/"$29" beside a pack). None has a legitimate
use in a post today, so the gate cannot fire on correct copy (e.g. "$5 for
a 25-anchor pack" or "Pack of Two Hundred: $20").
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
        re.compile(r"\$20\s+for\s+a\s+25-(?:anchor\s+)?pack"),
        "$20 is the Pack of Two Hundred price (200 credits) per "
        "server/nowpayments_api.py PLANS['pack_50'] and web/pricing.html. "
        "A 25-anchor pack is the Writer Pack at $5 (PLANS['writer_pack']).",
    ),
    (
        re.compile(r"\$5\s+for\s+a\s+200-(?:anchor\s+)?pack"),
        "$5 is the Writer Pack price (25 credits) per "
        "server/nowpayments_api.py PLANS['writer_pack']. A 200-anchor pack "
        "is the Pack of Two Hundred at $20 (PLANS['pack_50']).",
    ),
    (
        re.compile(r"Pack\s+of\s+(?:Fifty|50)\b"),
        "The Pack of Fifty was renamed the Pack of Two Hundred ($20, 200 "
        "credits) on 2026-11-04.",
    ),
    (
        re.compile(r"\$(?:19|29)\b[^.\n]{0,40}\b(?:[Pp]ack|anchors?)\b"
                   r"|(?:Writer\s+Pack|[Pp]ack)\b[^.\n]{0,30}\$(?:19|29)\b"),
        "$19 / $29 are the retired pack prices (before 2026-11-04). The "
        "Writer Pack is $5 for 25, the Pack of Two Hundred $20 for 200.",
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
