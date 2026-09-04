"""Growth package (2026-09-03): three doors + live counter on the homepage,
install-first /mcp, ACP listing facts on /docs/agents, #share on receipts.
Static assertions on the shipped HTML/JS — every link target must exist as a
page, every counter id must be one v2.js actually fills, and no count may be
typed by hand."""
import base64
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
INDEX = (WEB / "index.html").read_text()
V2 = (WEB / "v2.js").read_text()


def _page_exists(href: str) -> bool:
    path, _, frag = href.partition("#")
    path = path.split("?")[0]
    if path in ("", "/"):
        # A fragment-only link must land on an element of the homepage.
        return not frag or f'id="{frag}"' in INDEX
    rel = path.lstrip("/")
    return ((WEB / rel).is_file() or (WEB / f"{rel}.html").is_file()
            or (WEB / rel / "index.html").is_file())


class HomepageDoors(unittest.TestCase):
    def test_three_doors_and_nav_routes_resolve(self):
        for href in ("#drop", "/mcp", "/anchor-output", "/lp/agent-receipts", "/accept", "/verify/", "/learn",
                     "/docs/agents", "/lp/"):
            self.assertIn(f'href="{href}"', INDEX, href)
            self.assertTrue(_page_exists(href), f"{href} has no page")
        self.assertEqual(INDEX.count('class="orpho-door"'), 3)

    def test_counter_is_hidden_until_the_api_answers(self):
        self.assertIn('id="home-stats" hidden', INDEX)
        self.assertIn('$("home-stats").hidden = false', V2)
        m = re.search(r'id="c-anchors">([^<]*)<', INDEX)
        self.assertEqual(m.group(1).strip(), "—", "no hand-typed count on the homepage")
        # Every counter id on the page must be one the API can feed: v2.js
        # reads anchors.total, and stats.snapshot() emits it.
        self.assertIn('"total"', (ROOT / "server" / "stats.py").read_text())
        self.assertNotIn('id="c-blocks"', INDEX, "no stats key feeds c-blocks")

    def test_situation_strip_links_only_to_existing_guides(self):
        block = INDEX.split('class="orpho-situations"', 1)[1].split("</ul>", 1)[0]
        hrefs = re.findall(r'href="(/lp/[^"]+)"', block)
        self.assertGreaterEqual(len(hrefs), 5)
        for h in hrefs:
            self.assertTrue(_page_exists(h), h)


class McpInstallFirst(unittest.TestCase):
    def test_fastest_path_and_cursor_deeplink(self):
        html = (WEB / "mcp.html").read_text()
        self.assertIn("pip install orphograph-mcp", html)
        self.assertIn("claude mcp add orphograph -- orphograph-mcp", html)
        m = re.search(r'cursor://anysphere\.cursor-deeplink/mcp/install\?name=orphograph&amp;config=([A-Za-z0-9+/=]+)', html)
        self.assertIsNotNone(m, "Cursor deeplink missing")
        self.assertEqual(json.loads(base64.b64decode(m.group(1))), {"command": "orphograph-mcp"})
        # The console script the deeplink relies on really is declared.
        self.assertIn('orphograph-mcp = "orphograph_mcp:main"', (ROOT / "mcp" / "pyproject.toml").read_text())


class AgentsDocsAcp(unittest.TestCase):
    def test_listing_facts_match_the_handler(self):
        html = (WEB / "docs" / "agents.html").read_text()
        self.assertIn("0x73113714cae2e351e2e0146b2f9b55c316b93f14", html)
        for name in ("bitcoin_timestamp_receipt", "verify_receipt"):
            self.assertIn(f"<code>{name}</code>", html)
        for price in ("0.50 USDC", "0.25 USDC"):
            self.assertIn(price, html)
        self.assertNotIn("parent_receipt_id", html, "not accepted by the handler")
        # No availability, uptime or turnaround promise on a surface the
        # office cannot guarantee while its session can expire.
        for word in ("24/7", "always on", "guaranteed", "within 5 minutes"):
            self.assertNotIn(word, html.lower())


class ReceiptShareHash(unittest.TestCase):
    def test_share_hash_opens_the_drawer(self):
        js = (WEB / "receipt.js").read_text()
        self.assertIn('window.location.hash !== "#share"', js)
        self.assertIn('details.open = true', js)
        # Runs after the share block is initialised, never on DOMContentLoaded
        # (the block's contents are not rendered yet then).
        self.assertNotIn('DOMContentLoaded", openShareFromHash', js)
        self.assertIn("  openShareFromHash();\n", js)
        self.assertIn('/receipt.js?v=7', (WEB / "receipt.html").read_text())


if __name__ == "__main__":
    unittest.main()
