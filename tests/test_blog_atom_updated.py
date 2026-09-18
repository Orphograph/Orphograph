"""A corrected post must tell feed readers it changed.

2026-09-18: two posts were corrected (they said the five calendar servers were
five independent calendars). The Atom feed derived <updated> from `date`, so a
reader keyed on it would never re-fetch and would keep the old text.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import blog  # noqa: E402

SLUG = "why-5-opentimestamps-calendars-not-1"


def _entry(feed: str, slug: str) -> str:
    return next(e for e in feed.split("<entry>")[1:] if f"/blog/{slug}<" in e)


def test_a_corrected_post_reports_its_correction_date() -> None:
    assert "<updated>2026-09-18T00:00:00Z</updated>" in _entry(blog.atom_feed_xml(), SLUG)


def test_an_uncorrected_post_keeps_its_publication_date() -> None:
    plain = next(p for p in blog.list_posts() if not p.get("updated"))
    entry = _entry(blog.atom_feed_xml(), plain["slug"])
    assert f"<updated>{plain['date']}T00:00:00Z</updated>" in entry


def test_the_feed_is_as_new_as_its_newest_change() -> None:
    head = blog.atom_feed_xml().split("<entry>")[0]
    newest = max(p.get("updated") or p["date"] for p in blog.list_posts())
    assert f"<updated>{newest}T00:00:00Z</updated>" in head
