"""_sitetext.py — ONE way to read repo text the way a reader, or a crawler, sees it.

Two copy guards need the same flattening (test_calendar_independence_claim,
test_no_insurance_vertical_on_site). The second was first written as a copy of
the first and had drifted within the hour, so it lives here.
"""
from __future__ import annotations

import html
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MARKUP = {".html", ".xml", ".svg"}
TEXT = MARKUP | {".md", ".txt", ".json", ".js", ".ts", ".py", ".yml", ".yaml",
                 ".toml", ".css", ".sh"}

_READABLE_ATTRS = re.compile(
    r"""\b(?:content|alt|title|aria-label|placeholder|value)\s*=\s*(?:"([^"]*)"|'([^']*)')""",
    re.I)
_RAW_BLOCK = re.compile(r"<(script|style)\b[^>]*>(.*?)</\1\s*>", re.I | re.S)


def flat(text: str, suffix: str = ".html") -> str:
    """One line of what is readable in `text`.

    Markup only: tags are dropped, but text a person reads inside attributes
    (meta/og descriptions, alt, title) is kept, and <script>/<style> bodies are
    kept whole — a bare `<` in JavaScript is not a tag, and stripping from it
    to the next `>` deletes the very copy being looked for. Source files (.js,
    .py, .md, ...) are never tag-stripped for the same reason.
    Always: entities decoded, no-break spaces and all whitespace collapsed, so a
    phrase that wraps across lines is still one phrase.
    """
    if suffix.lower() in MARKUP:
        attrs = " ".join(a or b for a, b in _READABLE_ATTRS.findall(text))
        raw = " ".join(m.group(2) for m in _RAW_BLOCK.finditer(text))
        body = re.sub(r"<[^>]+>", " ", _RAW_BLOCK.sub(" ", text))
        text = f"{body} {attrs} {raw}"
    return re.sub(r"\s+", " ", html.unescape(text).replace(" ", " "))


def tracked_text_files(exclude: tuple[str, ...] = ("tests/",)) -> list[Path]:
    """Every tracked text file that still exists, minus `exclude` prefixes."""
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split("\n")
    return [ROOT / f for f in out
            if f and not f.startswith(exclude)
            and Path(f).suffix.lower() in TEXT and (ROOT / f).is_file()]


def read_flat(path: Path) -> str:
    # errors="replace", never skip: a page saved in the wrong encoding is still
    # a page somebody reads.
    return flat(path.read_text(encoding="utf-8", errors="replace"), path.suffix)
