"""/unsubscribe must never reflect markup from its `e` parameter (2026-09-19).

EMAIL_RE is a shape check (`^[^@\\s,]{1,64}@[^@\\s,]{1,255}$`): it accepts
`<svg/onload=alert(1)>@x.co`. The GET handler interpolated that value straight
into an HTML page AND stored it in the suppression list. script-src 'self'
stopped script execution, but attacker-chosen markup still rendered on our
origin (a crafted link could show a fake "re-subscribe" anchor).

Found by enumerating every server-built HTML string, not by a report. These
tests drive the real handler and assert on the bytes a visitor receives.
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import app  # noqa: E402
import unsubscribe  # noqa: E402

PAYLOADS = (
    "<svg/onload=alert(1)>@x.co",
    "<a/href=//evil.example>re-subscribe</a>@x.co",
    "a<b>@x.co",
)


def _drive(raw_email: str):
    h = app.Handler.__new__(app.Handler)
    h.path = "/unsubscribe?e=" + quote(raw_email)
    h.command = "GET"
    h.headers = {}
    h.wfile = io.BytesIO()
    seen = {"status": None, "headers": []}
    h.send_response = lambda code, *a: seen.__setitem__("status", code)
    h.send_error = lambda code, *a: seen.__setitem__("status", code)
    h.send_header = lambda k, v: seen["headers"].append((k, v))
    h.end_headers = lambda: None
    with mock.patch.object(unsubscribe, "add", return_value=True) as add:
        h._handle_unsubscribe_get()
    return seen, h.wfile.getvalue().decode("utf-8"), add


class TestUnsubscribeReflectsNoMarkup(unittest.TestCase):
    def test_markup_payloads_are_refused_and_never_stored(self):
        for p in PAYLOADS:
            with self.subTest(payload=p):
                seen, body, add = _drive(p)
                self.assertEqual(seen["status"], 400)
                self.assertFalse(add.called,
                                 "a markup payload reached the suppression list")
                self.assertNotIn(p.split("@")[0], body)

    def test_output_is_escaped_even_for_an_accepted_address(self):
        """Second guard, independent of the first: `&`, `'` and `"` are legal
        in the shape check and must come back encoded."""
        seen, body, add = _drive("o'brien&\"co\"@example.com")
        self.assertEqual(seen["status"], 200)
        self.assertNotIn("o'brien&\"co\"@example.com", body)
        self.assertIn("o&#x27;brien&amp;&quot;co&quot;@example.com", body)

    def test_ordinary_address_still_works(self):
        """CONTROL: the fix must not break the one thing the page is for."""
        seen, body, add = _drive("reader@example.com")
        self.assertEqual(seen["status"], 200)
        add.assert_called_once_with("reader@example.com", source="link_get")
        self.assertIn("<strong>reader@example.com</strong>", body)

    def test_page_carries_no_inline_style_and_links_the_site_sheets(self):
        """The inline <style> was dropped by style-src 'self', so the page
        rendered bare. It now links the same pinned sheets as the error page."""
        _, body, _ = _drive("reader@example.com")
        self.assertNotIn("<style", body.lower())
        self.assertNotIn(" style=", body.lower())
        self.assertIn('<link rel="stylesheet" href="/style.css?v=', body)


if __name__ == "__main__":
    unittest.main()
