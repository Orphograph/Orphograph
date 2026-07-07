#!/usr/bin/env python3
"""blog.py — render markdown posts from content/blog/*.md as HTML.

Stdlib only. Implements a deliberately small markdown subset that
covers what we actually write: headers, paragraphs, code blocks,
inline code, links, lists, bold/italic, horizontal rules,
blockquotes, hr. No tables, no images (we don't use them in the
posts we have today). When we need more, extend here.

Posts have YAML-ish front-matter at the top:

    ---
    title: How to prove your photo existed before AI scraped it
    slug: prove-photo-existed-before-ai
    date: 2026-05-12
    author: Orphograph
    summary: A practical guide for photographers...
    tags: [photographers, ai-disputes]
    ---

    # Body in markdown follows...

In-memory caching with mtime-based invalidation so we never reread
unchanged files but also pick up edits without restart.

Public API:
    list_posts() -> list[dict]   # post metadata, sorted by date desc
    get_post(slug) -> dict | None
    render_index_html() -> str   # full HTML page for /blog/
    render_post_html(slug) -> str | None  # full HTML page for /blog/<slug>
    atom_feed_xml() -> str       # /blog/atom.xml
"""
from __future__ import annotations

import html
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = ROOT / "content" / "blog"

_cache_lock = threading.Lock()
_post_cache: dict[str, tuple[float, dict]] = {}  # slug -> (mtime, post)

SITE_URL = os.environ.get("SITE_URL", "https://orphograph.com")


# ── Front-matter parsing ────────────────────────────────────────────────

def _parse_front_matter(text: str) -> tuple[dict, str]:
    """Extract the YAML-ish header block + return (meta, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    header = text[3:end].strip()
    body = text[end + len("\n---"):].lstrip("\n")

    meta: dict = {}
    for line in header.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes if any.
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        # Arrays in the tiny `[a, b, c]` form.
        if value.startswith("[") and value.endswith("]"):
            value = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        meta[key] = value
    return meta, body


# ── Markdown → HTML (deliberately tiny subset) ──────────────────────────

_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^```(\S*)\s*$")
_HR_RE = re.compile(r"^(?:-{3,}|_{3,}|\*{3,})\s*$")
_OL_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
_UL_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_BLOCKQUOTE_RE = re.compile(r"^>\s*(.*)$")

_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")


def _render_inline(text: str) -> str:
    """Inline markdown transforms. Escape first, then re-introduce safe tags."""
    out = html.escape(text)
    # Inline code first so its braces don't trip link/bold parsing.
    out = _INLINE_CODE_RE.sub(r"<code>\1</code>", out)
    # Links — careful with the captured URL, escape it as an attribute.
    def _link(m):
        label = m.group(1)
        url = m.group(2)
        # Allow only http(s), mailto, and relative links.
        if not (url.startswith(("http://", "https://", "mailto:", "/", "#"))):
            return html.escape(m.group(0))
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>'
    out = _LINK_RE.sub(_link, out)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _ITALIC_RE.sub(r"<em>\1</em>", out)
    return out


def _render_markdown(md: str) -> str:
    lines = md.splitlines()
    html_out: list[str] = []
    i = 0
    n = len(lines)
    in_para: list[str] = []

    def flush_para():
        if in_para:
            html_out.append("<p>" + _render_inline(" ".join(in_para)) + "</p>")
            in_para.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Fenced code block.
        m_fence = _FENCE_RE.match(line)
        if m_fence:
            flush_para()
            lang = m_fence.group(1) or ""
            i += 1
            buf: list[str] = []
            while i < n and not _FENCE_RE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            lang_attr = f' class="lang-{html.escape(lang)}"' if lang else ""
            html_out.append(
                f'<pre><code{lang_attr}>' +
                html.escape("\n".join(buf)) +
                "</code></pre>"
            )
            i += 1
            continue

        # Header.
        m = _HEADER_RE.match(line)
        if m:
            flush_para()
            level = len(m.group(1))
            text = _render_inline(m.group(2).strip())
            html_out.append(f"<h{level}>{text}</h{level}>")
            i += 1
            continue

        # Horizontal rule.
        if _HR_RE.match(line):
            flush_para()
            html_out.append("<hr>")
            i += 1
            continue

        # Blank line — end paragraph.
        if not stripped:
            flush_para()
            i += 1
            continue

        # Lists (ordered/unordered) — gather consecutive list items.
        m_ul = _UL_RE.match(line)
        m_ol = _OL_RE.match(line)
        if m_ul or m_ol:
            flush_para()
            tag = "ul" if m_ul else "ol"
            list_re = _UL_RE if m_ul else _OL_RE
            items: list[str] = []
            while i < n:
                m_item = list_re.match(lines[i])
                if not m_item:
                    break
                items.append(_render_inline(m_item.group(1)))
                i += 1
            html_out.append(f"<{tag}>")
            for it in items:
                html_out.append(f"  <li>{it}</li>")
            html_out.append(f"</{tag}>")
            continue

        # Blockquote.
        m_bq = _BLOCKQUOTE_RE.match(line)
        if m_bq:
            flush_para()
            quote_lines: list[str] = []
            while i < n:
                m_q = _BLOCKQUOTE_RE.match(lines[i])
                if not m_q:
                    break
                quote_lines.append(m_q.group(1))
                i += 1
            html_out.append("<blockquote>")
            html_out.append("<p>" + _render_inline(" ".join(l for l in quote_lines if l)) + "</p>")
            html_out.append("</blockquote>")
            continue

        # Default — accumulate into paragraph.
        in_para.append(stripped)
        i += 1

    flush_para()
    return "\n".join(html_out)


# ── Post loading + caching ──────────────────────────────────────────────

def _load_post(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_front_matter(text)
    slug = meta.get("slug") or path.stem
    return {
        "slug": slug,
        "title": meta.get("title", slug),
        "date": meta.get("date", ""),
        "author": meta.get("author", "Orphograph"),
        "summary": meta.get("summary", ""),
        "tags": meta.get("tags", []) if isinstance(meta.get("tags"), list) else [],
        "body_md": body,
        "html": _render_markdown(body),
        "path": path,
    }


def _all_posts() -> list[dict]:
    """Walk BLOG_DIR, refresh cache by mtime, return sorted list."""
    posts: list[dict] = []
    if not BLOG_DIR.exists():
        return posts
    with _cache_lock:
        for path in BLOG_DIR.glob("*.md"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            cached = _post_cache.get(path.stem)
            if cached and cached[0] == mtime:
                posts.append(cached[1])
                continue
            try:
                post = _load_post(path)
            except (OSError, UnicodeDecodeError):
                continue
            _post_cache[path.stem] = (mtime, post)
            posts.append(post)
    posts.sort(key=lambda p: p.get("date", ""), reverse=True)
    return posts


# ── Public API ──────────────────────────────────────────────────────────

def list_posts() -> list[dict]:
    """Return post metadata (no full HTML)."""
    return [
        {k: v for k, v in p.items() if k not in ("html", "body_md", "path")}
        for p in _all_posts()
    ]


def get_post(slug: str) -> dict | None:
    for p in _all_posts():
        if p["slug"] == slug:
            return p
    return None


# ── HTML page renderers ─────────────────────────────────────────────────

_SHELL_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{description}">
<link rel="icon" type="image/png" href="/favicon.png?v=8">
<link rel="alternate" type="application/atom+xml" title="Orphograph blog" href="/blog/atom.xml">
<link rel="stylesheet" href="/style.css">
<link rel="stylesheet" href="/blog.css">
{og_tags}
</head>
<body>
<header>
  <div class="brand"><a href="/">orphograph</a></div>
  <nav>
    <a href="/">home</a>
    <a href="/blog/">blog</a>
    <a href="/verify/">verifier</a>
    <a href="/status.html">status</a>
  </nav>
</header>
<main>"""

_SHELL_FOOT = """</main>
<footer>
  <small>
    <a href="/">home</a> ·
    <a href="/blog/">blog</a> ·
    <a href="/blog/atom.xml">RSS</a> ·
    <a href="/terms.html">Terms</a> ·
    <a href="/privacy.html">Privacy</a> ·
    <a href="/status.html">Status</a>
  </small>
</footer>
</body>
</html>"""


def _og_tags(title: str, description: str, url: str) -> str:
    return (
        f'<meta property="og:title" content="{html.escape(title)}">\n'
        f'<meta property="og:description" content="{html.escape(description)}">\n'
        f'<meta property="og:url" content="{html.escape(url)}">\n'
        f'<meta property="og:type" content="article">\n'
        f'<meta property="og:image" content="{SITE_URL}/og-image.png?v=8">\n'
        f'<meta name="twitter:card" content="summary_large_image">\n'
    )


def render_index_html() -> str:
    posts = list_posts()
    head = _SHELL_HEAD.format(
        title="Blog — Orphograph",
        description="Honest writing about photo provenance, OpenTimestamps, and the AI-disputes era. Updated when there's something to say.",
        og_tags=_og_tags("Orphograph blog",
                        "Honest writing about photo provenance and AI disputes.",
                        f"{SITE_URL}/blog/"),
    )
    body = ['<section class="blog-index">',
            '<h1>Blog</h1>',
            '<p class="lede">Honest writing about photo provenance, OpenTimestamps, '
            'and the AI-disputes era. Updated when there is something to say.</p>',
            '<ul class="post-list">']
    if not posts:
        body.append('<li class="muted">(no posts yet)</li>')
    for p in posts:
        slug = html.escape(p["slug"])
        title = html.escape(p["title"])
        date = html.escape(str(p.get("date", "")))
        summary = html.escape(p.get("summary", ""))
        body.append(
            f'<li class="post-entry">'
            f'<a href="/blog/{slug}"><h2>{title}</h2></a>'
            f'<p class="post-date muted">{date}</p>'
            f'<p class="post-summary">{summary}</p>'
            f'</li>'
        )
    body.append('</ul>')
    body.append('</section>')
    return head + "\n".join(body) + _SHELL_FOOT


def render_post_html(slug: str) -> str | None:
    post = get_post(slug)
    if not post:
        return None
    title = html.escape(post["title"])
    date = html.escape(str(post.get("date", "")))
    author = html.escape(post.get("author", "Orphograph"))
    summary = post.get("summary", "")
    head = _SHELL_HEAD.format(
        title=title + " — Orphograph",
        description=html.escape(summary),
        og_tags=_og_tags(post["title"], summary, f"{SITE_URL}/blog/{slug}"),
    )
    body = [
        '<article class="blog-post">',
        f'<header class="post-header"><h1>{title}</h1>'
        f'<p class="post-meta muted">By {author} · {date}</p></header>',
        '<div class="post-body">',
        post["html"],
        '</div>',
        '<footer class="post-footer">'
        '<p class="muted">'
        '<a href="/blog/">← back to blog</a> · '
        f'<a href="/blog/atom.xml">subscribe via RSS</a>'
        '</p></footer>',
        '</article>',
    ]
    return head + "\n".join(body) + _SHELL_FOOT


# ── Atom feed ───────────────────────────────────────────────────────────

def atom_feed_xml() -> str:
    posts = _all_posts()
    updated = posts[0].get("date", "") if posts else ""
    if updated:
        # Pad bare YYYY-MM-DD to a full timestamp.
        if len(updated) == 10:
            updated = updated + "T00:00:00Z"

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        '  <title>Orphograph</title>',
        f'  <link href="{SITE_URL}/blog/atom.xml" rel="self"/>',
        f'  <link href="{SITE_URL}/blog/"/>',
        f'  <id>{SITE_URL}/blog/</id>',
        f'  <updated>{updated or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}</updated>',
        '  <author><name>Orphograph</name></author>',
    ]
    for p in posts:
        slug = p["slug"]
        title = html.escape(p["title"])
        summary = html.escape(p.get("summary", ""))
        post_date = p.get("date", "")
        post_updated = post_date + "T00:00:00Z" if len(post_date) == 10 else post_date
        lines += [
            '  <entry>',
            f'    <title>{title}</title>',
            f'    <link href="{SITE_URL}/blog/{slug}"/>',
            f'    <id>{SITE_URL}/blog/{slug}</id>',
            f'    <updated>{post_updated}</updated>',
            f'    <summary>{summary}</summary>',
            '  </entry>',
        ]
    lines.append('</feed>')
    return "\n".join(lines)
