#!/usr/bin/env python3
"""badge_svg.py — Verifier badge SVG generator.

Renders a 300x80 cream-warm verification badge for an Orphograph receipt.
The badge is intended to be pasted on creator portfolios as an
``<img src="/api/badge/<id>.svg">`` inside a link to ``/r/<id>``. The
generated SVG ALSO wraps itself in an ``<a>`` element so that the badge
remains clickable when embedded directly via ``<object>`` or fetched
inline (the link is harmless when the SVG is loaded as an ``<img>``
since browsers ignore SVG navigation in that mode).

Privacy contract (mirrors the receipt page defaults):
    - NEVER include the client_label (filename) in the badge.
    - NEVER include the customer email.
    - NEVER include any portion of the file hash beyond the receipt_id.
    - The only customer-derived identifier rendered is the receipt_id,
      which is bearer-equivalent by design — it already appears in the
      public ``/r/<id>`` URL.

Stdlib only. No external font references, no external assets. Renders
with sans-serif/serif keywords so any browser can paint it offline.

Public API:
    render(receipt: dict, *, base_url: str = "") -> str
        Returns a self-contained SVG document string.
"""
from __future__ import annotations

# ── Palette (matches the orphograph.com cream-warm theme) ──────────────
BG = "#fdfaf3"      # warm cream
ACCENT = "#4a9a73"  # muted green (checkmark + Bitcoin glyph)
TEXT = "#1f1d1a"    # near-black ink
MUTED = "#6b665d"   # warm gray for the subtitle
BORDER = "#e8e2d2"  # hairline


# ── XML escaping ───────────────────────────────────────────────────────

def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&apos;")
    )


# ── Helpers ────────────────────────────────────────────────────────────

def _short_id(receipt_id: str) -> str:
    """Last 8 chars of the receipt id — enough to be recognizable, not enough
    to leak structure beyond the public URL the badge already links to."""
    if not isinstance(receipt_id, str):
        return ""
    rid = receipt_id.strip()
    if len(rid) <= 8:
        return rid
    return rid[-8:]


def _short_date(created_at: str | None) -> str:
    """Return the YYYY-MM-DD prefix of the ISO timestamp, or empty string.

    The receipt's ``created_at`` is an ISO-8601 timestamp like
    ``2026-05-14T12:34:56+00:00``. The badge shows the date only — the
    HH:MM is implementation noise that distracts from the headline claim.
    """
    if not isinstance(created_at, str) or len(created_at) < 10:
        return ""
    head = created_at[:10]
    # Cheap shape check so we don't render garbage on a malformed receipt.
    if head[4] != "-" or head[7] != "-":
        return ""
    if not (head[:4].isdigit() and head[5:7].isdigit() and head[8:10].isdigit()):
        return ""
    return head


# ── Public API ─────────────────────────────────────────────────────────

def render(receipt: dict, *, base_url: str = "") -> str:
    """Render the verification badge as a self-contained SVG string.

    Args:
        receipt: a verify_receipt() result OR an anchor_hash() record.
            Only ``receipt_id`` and ``created_at`` are read; any other
            fields (client_label, hash_hex, email, …) are intentionally
            ignored to preserve the privacy contract documented above.
        base_url: optional absolute prefix for the link target.
            Empty string → relative link ``/r/<id>``, which is the right
            default for same-origin embeds and standalone SVG viewers.

    Returns:
        SVG document beginning with ``<?xml`` and ending with ``</svg>``.
        Always 300x80 px. Self-contained — no <image>, no @font-face,
        no external href except the wrapping anchor to the receipt page.
    """
    rid = receipt.get("receipt_id", "") if isinstance(receipt, dict) else ""
    rid = rid if isinstance(rid, str) else ""
    created_at = receipt.get("created_at", "") if isinstance(receipt, dict) else ""
    # Folder (dataset) receipts get a dataset-aware subtitle and link straight
    # to the certificate view. kind + leaf_count are non-PII (no filenames,
    # hash, or email), so surfacing them respects the privacy contract above.
    is_folder = isinstance(receipt, dict) and receipt.get("kind") == "folder"
    leaf_count = receipt.get("leaf_count") if isinstance(receipt, dict) else None

    short_id = _short_id(rid)
    date = _short_date(created_at)

    # Build the link target. Folder receipts point at /certificate/<id>;
    # single-file receipts at /r/<id>. Honor base_url when provided, else
    # render a relative URL so the badge works regardless of host.
    page = ("/certificate/" if is_folder else "/r/") + rid
    link_target = (base_url.rstrip("/") + page) if rid else (base_url.rstrip("/") + "/")
    if not base_url:
        link_target = page if rid else "/"

    # All user-derived strings are escaped before substitution. The
    # palette constants and the layout numerics are static and safe.
    safe_link = _xml_escape(link_target)
    safe_id = _xml_escape(short_id)
    safe_date = _xml_escape(date)

    if is_folder and isinstance(leaf_count, int) and leaf_count > 0:
        subtitle = f"dataset &middot; {leaf_count} files &middot; anchored to Bitcoin"
    elif safe_date:
        subtitle = f"anchored to Bitcoin &middot; {safe_date}"
    else:
        subtitle = "anchored to Bitcoin"

    parts: list[str] = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        'viewBox="0 0 300 80" width="300" height="80" '
        'role="img" aria-label="Verified by Orphograph, anchored to Bitcoin">'
    )
    # ── Wrap the whole composition in an anchor so click-through works
    # ── for inline embeds (no-op when the SVG is loaded as <img>).
    parts.append(f'<a xlink:href="{safe_link}" target="_blank" rel="noopener">')

    # Card background + 1px border.
    parts.append(
        f'<rect x="0.5" y="0.5" width="299" height="79" rx="8" ry="8" '
        f'fill="{BG}" stroke="{BORDER}" stroke-width="1"/>'
    )

    # ── Checkmark glyph (top-left of the badge, inside a circle) ────────
    # 24x24 box centred at (28, 28). Stroke-based path so it stays crisp
    # at any DPI without external font resources.
    parts.append(
        f'<circle cx="28" cy="28" r="13" fill="none" '
        f'stroke="{ACCENT}" stroke-width="2"/>'
    )
    parts.append(
        f'<path d="M22 28 L26.5 32.5 L34.5 23.5" fill="none" '
        f'stroke="{ACCENT}" stroke-width="2.4" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )

    # ── Bitcoin "₿" glyph (top-right of the badge) ─────────────────────
    # Using the actual Unicode codepoint U+20BF in a sans-serif keyword
    # font means we don't ship a font file. Browsers fall back to their
    # default glyph; on systems without it the surrounding text still
    # reads correctly and the colour cues remain.
    parts.append(
        f'<text x="278" y="35" font-family="sans-serif" font-size="22" '
        f'font-weight="700" fill="{ACCENT}" text-anchor="middle">'
        '&#x20BF;</text>'
    )

    # ── Wordmark: "Verified by Orphograph" ─────────────────────────────
    parts.append(
        f'<text x="50" y="32" font-family="serif" font-size="16" '
        f'font-weight="600" fill="{TEXT}">Verified by Orphograph</text>'
    )

    # ── Subtitle: "anchored to Bitcoin · YYYY-MM-DD" ───────────────────
    parts.append(
        f'<text x="50" y="50" font-family="sans-serif" font-size="11" '
        f'fill="{MUTED}">{subtitle}</text>'
    )

    # ── Short receipt ID footer (last 8 chars) ─────────────────────────
    if safe_id:
        parts.append(
            f'<text x="50" y="66" font-family="monospace" font-size="10" '
            f'fill="{MUTED}">id &middot; {safe_id}</text>'
        )

    parts.append("</a>")
    parts.append("</svg>")
    return "".join(parts)


if __name__ == "__main__":
    # Quick self-check — render a fake receipt and dump to stdout.
    import sys
    sample = {
        "receipt_id": "XwTULwlh76PcCst9",
        "created_at": "2026-05-14T12:34:56+00:00",
    }
    sys.stdout.write(render(sample))
    sys.stdout.write("\n")
