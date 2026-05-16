"""test_anchors_pagination.py — cursor pagination for /api/me/anchors helper."""
from __future__ import annotations

import json

import app
import auth
import engine


def _write_receipt(email_source: str, receipt_id: str, created_at: str) -> None:
    d = engine.RECEIPTS_DIR / receipt_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "receipt.json").write_text(json.dumps({
        "receipt_id": receipt_id,
        "source": email_source,
        "created_at": created_at,
        "client_label": f"label-{receipt_id}",
        "hash_hex": "00" * 32,
        "sha512_hex": "00" * 64,
        "calendars_ok": 5,
        "calendars_total": 5,
        "status": "ok",
    }))


def _seed(tmp_path, monkeypatch, n: int):
    monkeypatch.setattr(engine, "RECEIPTS_DIR", tmp_path / "receipts")
    monkeypatch.setattr(auth, "HMAC_SECRET_PATH", tmp_path / ".hmac_secret")
    monkeypatch.setattr(auth, "_HMAC_SECRET_CACHE", None)
    email = "p@example.com"
    src = "sub:" + auth.email_id(email)
    for i in range(n):
        _write_receipt(src, f"rcpt_{i:03d}", f"2026-05-01T00:00:{i:02d}Z")
    return email


def test_default_page_returns_50_and_has_more(tmp_path, monkeypatch):
    email = _seed(tmp_path, monkeypatch, n=55)
    rows, has_more = app._list_anchors_for_email(email, limit=50, with_more_flag=True)
    assert len(rows) == 50
    assert has_more is True
    # Sorted newest-first
    assert rows[0]["created_at"] > rows[-1]["created_at"]


def test_no_more_when_under_limit(tmp_path, monkeypatch):
    email = _seed(tmp_path, monkeypatch, n=3)
    rows, has_more = app._list_anchors_for_email(email, limit=50, with_more_flag=True)
    assert len(rows) == 3
    assert has_more is False


def test_cursor_skips_at_or_after_before(tmp_path, monkeypatch):
    email = _seed(tmp_path, monkeypatch, n=10)
    # First page
    page1, more1 = app._list_anchors_for_email(email, limit=4, with_more_flag=True)
    assert len(page1) == 4
    assert more1 is True
    cursor = page1[-1]["created_at"]
    # Second page strictly older than cursor
    page2, more2 = app._list_anchors_for_email(email, limit=4, before=cursor, with_more_flag=True)
    assert len(page2) == 4
    # No overlap with page1
    p1_ids = {r["receipt_id"] for r in page1}
    p2_ids = {r["receipt_id"] for r in page2}
    assert p1_ids.isdisjoint(p2_ids)
    assert more2 is True  # still 2 remaining after this page


def test_backward_compat_returns_list(tmp_path, monkeypatch):
    """Old call sites (csv export) still get a plain list."""
    email = _seed(tmp_path, monkeypatch, n=3)
    rows = app._list_anchors_for_email(email, limit=50)
    assert isinstance(rows, list)
    assert len(rows) == 3


def test_other_users_anchors_excluded(tmp_path, monkeypatch):
    email = _seed(tmp_path, monkeypatch, n=2)
    # Inject an anchor for a different user
    _write_receipt("sub:other_user_id", "rcpt_other", "2026-05-01T00:00:99Z")
    rows = app._list_anchors_for_email(email, limit=50)
    assert all(r["receipt_id"] != "rcpt_other" for r in rows)
    assert len(rows) == 2
