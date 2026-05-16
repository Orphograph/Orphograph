#!/usr/bin/env python3
"""og_svg.py — stdlib SVG generator for dynamic Open Graph social-share cards.

When someone shares an orphograph.com URL on X/LinkedIn/Slack/Discord,
those platforms fetch the page's `<meta property="og:image">` to render
the preview card. This module produces a 1200×630 SVG (the canonical
OG dimensions) on demand, branded for Orphograph, parameterised on
title/subtitle/kind.

Design constraints
------------------
- Stdlib only. No PIL, no font files, no third-party SVG libraries.
- Generic font families only (`serif`, `sans-serif`) — the SVG renders
  on whatever the consuming platform has installed. Most OG-card
  consumers rasterise via headless Chrome / WebKit, which has both.
- Cream-warm palette matching ``web/style.css`` (#fdfaf3 bg, #4a9a73 sage).
- Every text input is HTML-escaped so a title containing ``<``, ``>``,
  ``&``, or quote characters cannot break out of the SVG document.
- Hard-wrap titles/subtitles to two lines max — long inputs get an
  ellipsis rather than overflowing the canvas.

Privacy contract
----------------
- This module receives PUBLIC page metadata only: titles + subtitles
  pulled from blog front-matter or the static-page mapping. It must
  never be fed customer emails, receipt IDs, filenames, or any other
  data tied to a specific user. Callers are responsible for that
  filtering; this module assumes its inputs are already public.

Public API
----------
    render_og(title: str, subtitle: str = "", kind: str = "default") -> bytes
        Returns the SVG document as UTF-8 bytes.
"""
from __future__ import annotations

import html

# Canonical Open Graph image dimensions (Facebook/LinkedIn/X all agree on this).
WIDTH = 1200
HEIGHT = 630

# Cream-warm palette — kept in lockstep with ``web/style.css``.
BG_CREAM = "#fdfaf3"
PANEL_STROKE = "#ede7d8"
TEXT = "#1f1d1a"
TEXT_SOFT = "#3a3631"
MUTED = "#837e75"
SAGE = "#4a9a73"
SAGE_SOFT = "rgba(74,154,115,0.10)"
WARN_AMBER = "#c08a3e"

# Per-kind accent colour + iconography variant. New variants slot in here.
_KIND_ACCENTS: dict[str, dict[str, str]] = {
    "default": {"accent": SAGE, "label": "anchored to bitcoin · orphograph.com"},
    "blog":    {"accent": SAGE, "label": "orphograph.com / blog"},
    "article": {"accent": SAGE, "label": "orphograph.com / guides"},
    "stats":   {"accent": WARN_AMBER, "label": "orphograph.com / status"},
}


def _wrap_lines(text: str, max_chars_per_line: int, max_lines: int = 2) -> list[str]:
    """Greedy word-wrap into at most `max_lines` lines.

    Inputs longer than max_lines * max_chars_per_line get truncated with an
    ellipsis on the final line. We don't try to be clever about hyphenation;
    page titles are short enough that greedy wrapping is fine.
    """
    if not text:
        return []
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for w in words:
        candidate = f"{current} {w}".strip()
        if len(candidate) <= max_chars_per_line:
            current = candidate
            continue
        if current:
            lines.append(current)
        # If a single word is longer than the line, just keep it — wrap is
        # advisory, the SVG text element won't actually clip.
        current = w
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    # If we ran out of lines but still have unconsumed text, ellipsis the last.
    consumed = " ".join(lines)
    if consumed and len(consumed) < len(text):
        last = lines[-1]
        # Leave room for the ellipsis character.
        if len(last) > max_chars_per_line - 1:
            last = last[: max_chars_per_line - 1].rstrip()
        lines[-1] = last + "…"
    return lines


def _accent_iconography(kind: str, accent: str) -> str:
    """Bottom-right ornament that varies by kind.

    All variants share the same anchor: a small bitcoin glyph (₿) plus a
    sage-green checkmark. The kind-specific accent is conveyed through the
    label text and the accent colour, not through wildly different shapes —
    consistency reads as "same brand" across share previews.
    """
    # Bitcoin symbol + checkmark, positioned in the lower-right corner.
    # Both rendered as text glyphs so we don't need to ship font files.
    return (
        f'<g transform="translate({WIDTH - 220} {HEIGHT - 120})">'
        f'<text x="0" y="40" font-family="serif" font-size="56" '
        f'font-weight="700" fill="{accent}">₿</text>'
        f'<text x="70" y="40" font-family="sans-serif" font-size="56" '
        f'fill="{accent}">✓</text>'
        f'</g>'
    )


def render_og(title: str, subtitle: str = "", kind: str = "default") -> bytes:
    """Render a 1200×630 OG card SVG as UTF-8 bytes.

    Args:
        title:    Page title. Wrapped to ≤ 2 lines (~24 chars each at 72px).
        subtitle: Optional secondary line. Wrapped to ≤ 2 lines.
        kind:     "default" | "blog" | "article" | "stats".
                  Unknown kinds fall back to "default".

    Returns:
        SVG document as ``bytes`` (UTF-8 encoded). The output starts with
        ``<?xml`` so callers can sniff it.
    """
    accent_meta = _KIND_ACCENTS.get(kind, _KIND_ACCENTS["default"])
    accent = accent_meta["accent"]
    footer_label = accent_meta["label"]

    # Escape every text fragment before it lands inside the SVG. This is
    # the only barrier between a (theoretically public) page title and the
    # SVG document structure — be paranoid about it.
    safe_title_lines = [html.escape(line) for line in _wrap_lines(title, 24, max_lines=2)]
    safe_subtitle_lines = [html.escape(line) for line in _wrap_lines(subtitle, 56, max_lines=2)]
    safe_footer = html.escape(footer_label)

    # Vertical centring of the title block: we want the title group
    # visually centred between the top wordmark and the bottom footer.
    # 72px lines at ~1.1 line-height = ~80px per line.
    title_line_height = 84
    title_block_height = title_line_height * max(len(safe_title_lines), 1)
    # Anchor the first baseline so the block sits roughly mid-canvas.
    title_top = (HEIGHT - title_block_height) // 2 + 60

    subtitle_line_height = 38
    subtitle_top = title_top + title_block_height + 20

    parts: list[str] = []
    parts.append('<?xml version="1.0" encoding="utf-8"?>')
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" '
        f'role="img" aria-label="Orphograph">'
    )
    # Cream-warm background.
    parts.append(f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{BG_CREAM}"/>')
    # Subtle accent stripe along the top edge for brand identification at
    # thumbnail size — the card is often shown at ~300px wide in feeds.
    parts.append(
        f'<rect x="0" y="0" width="{WIDTH}" height="6" fill="{accent}"/>'
    )

    # Wordmark — top-left, 64px serif (Orphograph's editorial register).
    parts.append(
        f'<text x="70" y="110" font-family="serif" font-size="64" '
        f'font-weight="400" fill="{TEXT}" letter-spacing="-0.01em">orphograph</text>'
    )

    # Thin separator under the wordmark.
    parts.append(
        f'<line x1="70" y1="140" x2="{WIDTH - 70}" y2="140" '
        f'stroke="{PANEL_STROKE}" stroke-width="1"/>'
    )

    # Title — centred horizontally, ~72px sans-serif, wrap to ≤ 2 lines.
    for i, line in enumerate(safe_title_lines):
        y = title_top + i * title_line_height
        parts.append(
            f'<text x="{WIDTH // 2}" y="{y}" font-family="sans-serif" '
            f'font-size="72" font-weight="300" fill="{TEXT}" '
            f'text-anchor="middle" letter-spacing="-0.015em">{line}</text>'
        )

    # Subtitle — same centring rule, lighter weight, smaller.
    for i, line in enumerate(safe_subtitle_lines):
        y = subtitle_top + i * subtitle_line_height
        parts.append(
            f'<text x="{WIDTH // 2}" y="{y}" font-family="sans-serif" '
            f'font-size="28" font-weight="400" fill="{TEXT_SOFT}" '
            f'text-anchor="middle">{line}</text>'
        )

    # Bottom-right ornament (₿ + ✓) — kind-aware accent colour.
    parts.append(_accent_iconography(kind, accent))

    # Footer label, bottom-left.
    parts.append(
        f'<text x="70" y="{HEIGHT - 60}" font-family="sans-serif" '
        f'font-size="22" font-weight="400" fill="{MUTED}">{safe_footer}</text>'
    )

    parts.append('</svg>')
    return "".join(parts).encode("utf-8")


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.stdout.buffer.write(render_og(
        "Prove your art existed before the bots saw it",
        "Anchor any file to Bitcoin in 10 seconds.",
        "default",
    ))
    sys.stdout.buffer.write(b"\n")
