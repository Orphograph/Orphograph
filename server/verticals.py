"""verticals.py — load per-vertical YAML configurations and render landing pages.

The module loads every ``config/verticals/*.yml`` at import time (not per
request) and exposes ``get(slug)`` and ``all_slugs()`` for the routing layer
in ``app.py``. Pages are rendered through a stdlib-only string template that
matches the ``/method/folder-merkle.html`` brand surface — cream background,
serif headings, formal voice.

The pages are intentionally NOT linked from the live homepage. They are
reachable only by direct URL under ``/verticals/<slug>.html``.
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

try:  # PyYAML is the preferred reader and is already installed at 6.0.3.
    import yaml as _yaml  # type: ignore[import-not-found]
    _HAVE_YAML = True
except ImportError:  # pragma: no cover - fallback path
    from . import _minimal_yaml as _yaml  # type: ignore[no-redef]
    _HAVE_YAML = False


_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _ROOT / "config" / "verticals"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load_all() -> dict[str, dict[str, Any]]:
    """Load every YAML in the config directory once at module import time."""
    loaded: dict[str, dict[str, Any]] = {}
    if not _CONFIG_DIR.is_dir():
        return loaded
    for yml in sorted(_CONFIG_DIR.glob("*.yml")):
        with yml.open("r", encoding="utf-8") as fh:
            data = _yaml.safe_load(fh)
        if not isinstance(data, dict):
            continue
        slug = data.get("slug") or yml.stem
        data["slug"] = slug
        loaded[slug] = data
    return loaded


_CONFIGS: dict[str, dict[str, Any]] = _load_all()


def get(slug: str) -> dict[str, Any] | None:
    """Return the loaded YAML for ``slug`` or ``None`` if unknown."""
    return _CONFIGS.get(slug)


def all_slugs() -> list[str]:
    """Return the sorted list of slugs the module has loaded."""
    return sorted(_CONFIGS.keys())


def reload() -> None:
    """Re-read the YAMLs from disk. Used in tests; not called per-request."""
    global _CONFIGS
    _CONFIGS = _load_all()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Orphograph — {title_esc}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#fdfaf3">
<meta name="description" content="{subhead_esc}">
<meta property="og:title" content="Orphograph — {title_esc}">
<meta property="og:description" content="{subhead_esc}">
<meta property="og:type" content="article">
<meta property="og:image" content="https://orphograph.com/seal.png?v=6">
<link rel="icon" type="image/png" href="/favicon.png?v=6">
<link rel="stylesheet" href="/index.css?v=8">
<style>
  body.vertical-page {{ background: #fdfaf3; }}
  .arch-wrap {{ max-width: 820px; margin: 0 auto; padding: 0 24px 64px; }}
  .arch-wrap h1 {{ font-family: var(--serif); font-size: 36px; font-weight: 500; margin: 32px 0 6px; }}
  .arch-wrap h2 {{ font-family: var(--serif); font-size: 22px; font-weight: 500; margin: 36px 0 4px; color: var(--ink); }}
  .arch-rule {{ border-top: 1px solid var(--ink); width: 32px; margin: 8px 0 16px; opacity: 0.6; }}
  .arch-wrap p, .arch-wrap li {{ color: var(--ink-2); line-height: 1.75; font-size: 15.5px; }}
  .arch-wrap code {{ background: rgba(74,154,115,0.08); padding: 2px 6px; border-radius: 3px; font-size: 0.92em; }}
  .arch-lede {{ font-size: 17px; line-height: 1.7; color: var(--ink-2); margin: 0 0 24px; }}
  .arch-callout {{ background: rgba(74,154,115,0.06); border-left: 3px solid var(--confirm); padding: 16px 20px; margin: 24px 0; border-radius: 0 6px 6px 0; }}
  .vertical-disclaimer {{ background: #f4ecd9; border: 1px solid #d9d2c0; padding: 18px 22px; margin: 36px 0 24px; border-radius: 6px; font-size: 14px; line-height: 1.7; color: var(--ink-2); }}
  .vertical-faq dt {{ font-weight: 600; margin-top: 14px; color: var(--ink); }}
  .vertical-faq dd {{ margin: 4px 0 0 0; color: var(--ink-2); }}
  details.technical-detail {{ margin: 24px 0; }}
  details.technical-detail summary {{ cursor: pointer; font-family: var(--serif); font-size: 17px; color: var(--ink); padding: 6px 0; }}
</style>
<meta name="copyright" content="(c) 2026 Orphograph. Code under MIT (see /LICENSE). Brand, content, and design — all rights reserved.">
</head>
<body class="vertical-page">

<header class="nav">
  <div class="wrap row">
    <a href="/" class="brand">Orphograph</a>
    <nav>
      <a href="/learn.html">Method</a>
      <a href="/faq.html">FAQ</a>
      <a href="/continuity.html">Continuity</a>
      <a href="/#tiers">Pricing</a>
      <a href="/signin.html">Sign in</a>
      <a href="/#drop" class="cta">Anchor a file</a>
    </nav>
  </div>
</header>

<main class="arch-wrap">

  <p style="font-size:12px;letter-spacing:0.18em;text-transform:uppercase;color:var(--muted);margin:48px 0 8px;">Vertical · {nav_label_esc}</p>
  <h1>{headline_esc}</h1>
  <p class="arch-lede">{subhead_esc}</p>
{audience_block}{what_means_block}{how_it_works_block}{technical_detail_block}{faq_block}
  <section>
    <h2>Disclaimer</h2>
    <div class="arch-rule"></div>
    <div class="vertical-disclaimer">{disclaimer_html}</div>
  </section>

  <section>
    <h2>Citations and verification</h2>
    <div class="arch-rule"></div>
    <ul>
      <li><a href="/method/folder-merkle.html">Folder anchoring by Merkle root</a></li>
      <li><a href="/method/architecture.html">Architecture of the protocol</a></li>
      <li><a href="/faq.html">Frequently asked</a></li>
      <li><a href="/verify-js.html">Standalone independent verifier (single HTML file)</a></li>
    </ul>
  </section>

  <p style="color:var(--muted);font-size:13px;margin-top:48px;border-top:1px solid var(--line-soft);padding-top:18px;">
    Pricing reference: <code>{pricing_placeholder_esc}</code>.
  </p>
</main>

<footer>
  <div class="wrap" style="text-align:center;color:var(--muted);font-size:13px;padding:32px 0;">
    <p>Orphograph &middot; An empirical notary.</p>
    <p style="margin-top:8px;">
      <a href="/" style="color:var(--muted);">Home</a> &middot;
      <a href="/method/folder-merkle.html" style="color:var(--muted);">Method</a> &middot;
      <a href="/faq.html" style="color:var(--muted);">FAQ</a> &middot;
      <a href="/continuity.html" style="color:var(--muted);">Continuity</a>
    </p>
  </div>
</footer>

<script src="/statusbar.js" defer></script>
</body>
</html>
"""


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _paragraphs(body: str) -> str:
    """Render a YAML block-literal as escaped paragraphs separated by blank lines."""
    if not body:
        return ""
    chunks = [chunk.strip() for chunk in str(body).split("\n\n") if chunk.strip()]
    out: list[str] = []
    for chunk in chunks:
        # Preserve intra-paragraph line breaks as single spaces.
        flat = " ".join(line.strip() for line in chunk.splitlines() if line.strip())
        out.append(f"    <p>{html.escape(flat)}</p>")
    return "\n".join(out)


def _disclaimer_html(text: str) -> str:
    chunks = [chunk.strip() for chunk in str(text).split("\n\n") if chunk.strip()]
    if not chunks:
        return html.escape(str(text))
    out = []
    for chunk in chunks:
        flat = " ".join(line.strip() for line in chunk.splitlines() if line.strip())
        out.append(f"<p style=\"margin:0 0 10px;\">{html.escape(flat)}</p>")
    return "".join(out)


def _audience_block(cfg: dict[str, Any]) -> str:
    audience = cfg.get("audience")
    if not audience:
        return ""
    return (
        "\n  <section>\n"
        "    <h2>Audience</h2>\n"
        "    <div class=\"arch-rule\"></div>\n"
        f"    <p>{_esc(audience)}</p>\n"
        "  </section>\n"
    )


def _bullet_section(title: str, items: Any) -> str:
    if not items or not isinstance(items, list):
        return ""
    li_html = "\n".join(f"      <li>{_esc(item)}</li>" for item in items)
    return (
        "\n  <section>\n"
        f"    <h2>{html.escape(title)}</h2>\n"
        "    <div class=\"arch-rule\"></div>\n"
        f"    <ul>\n{li_html}\n    </ul>\n"
        "  </section>\n"
    )


def _technical_detail_block(cfg: dict[str, Any]) -> str:
    td = cfg.get("technical_detail")
    if not isinstance(td, dict):
        return ""
    summary = td.get("summary") or "Technical detail"
    sections = td.get("sections") or []
    if not isinstance(sections, list) or not sections:
        return ""
    parts: list[str] = []
    parts.append("\n  <details class=\"technical-detail\">")
    parts.append(f"    <summary>{_esc(summary)}</summary>")
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        sec_title = sec.get("title", "")
        sec_body = sec.get("body", "")
        parts.append("    <section>")
        parts.append(f"      <h2>{_esc(sec_title)}</h2>")
        parts.append("      <div class=\"arch-rule\"></div>")
        parts.append(_paragraphs(sec_body))
        parts.append("    </section>")
    parts.append("  </details>\n")
    return "\n".join(parts)


def _faq_block(cfg: dict[str, Any]) -> str:
    faq = cfg.get("faq")
    if not isinstance(faq, list) or not faq:
        return ""
    items: list[str] = []
    for entry in faq:
        if not isinstance(entry, dict):
            continue
        q = entry.get("q", "")
        a = entry.get("a", "")
        a_flat = " ".join(line.strip() for line in str(a).splitlines() if line.strip())
        items.append(f"      <dt>{_esc(q)}</dt>")
        items.append(f"      <dd>{html.escape(a_flat)}</dd>")
    if not items:
        return ""
    body = "\n".join(items)
    return (
        "\n  <section>\n"
        "    <h2>Frequently asked</h2>\n"
        "    <div class=\"arch-rule\"></div>\n"
        f"    <dl class=\"vertical-faq\">\n{body}\n    </dl>\n"
        "  </section>\n"
    )


def render_html(slug: str) -> str | None:
    """Render the landing page for ``slug``. Returns ``None`` if unknown."""
    cfg = get(slug)
    if cfg is None:
        return None
    hero = cfg.get("hero") or {}
    title = cfg.get("title", "")
    nav_label = cfg.get("nav_label", "")
    headline = hero.get("headline", title)
    subhead = hero.get("subhead", "")
    pricing_placeholder = cfg.get("pricing_placeholder", "")
    disclaimer = cfg.get("disclaimer", "")

    return _PAGE_TEMPLATE.format(
        title_esc=_esc(title),
        nav_label_esc=_esc(nav_label),
        headline_esc=_esc(headline),
        subhead_esc=_esc(subhead),
        pricing_placeholder_esc=_esc(pricing_placeholder),
        disclaimer_html=_disclaimer_html(disclaimer),
        audience_block=_audience_block(cfg),
        what_means_block=_bullet_section("What this means", cfg.get("what_this_means")),
        how_it_works_block=_bullet_section("How it works", cfg.get("how_it_works")),
        technical_detail_block=_technical_detail_block(cfg),
        faq_block=_faq_block(cfg),
    )
