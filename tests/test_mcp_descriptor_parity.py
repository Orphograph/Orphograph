#!/usr/bin/env python3
"""test_mcp_descriptor_parity.py — every MCP descriptor must match the code.

Stage 3e drift sweep, 2026-08-05: five descriptors carried FOUR different
tool counts (server.json 3, manifest.json 3, server-card 6, README 5,
implementation 6), and the file served at the documented curl-install path
was a Jul-5 build with 5 tools while the source had 6. The server card
therefore advertised `orphograph_verify_lineage`, which no installable
build implemented — a host would offer the tool and the call would fail.
"""
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "mcp" / "orphograph_mcp.py"


def implemented_tools() -> list[str]:
    return sorted(set(re.findall(r'"name":\s*"(orphograph_[a-z_]+)"',
                                 SOURCE.read_text())))


def _tools_of(doc: dict) -> list[str]:
    raw = doc.get("tools")
    if raw is None:
        raw = (doc.get("_meta") or {}).get("tools")
    if not isinstance(raw, list):
        return []
    return sorted(t["name"] if isinstance(t, dict) else t for t in raw)


class TestMcpDescriptorParity(unittest.TestCase):
    def test_every_descriptor_lists_exactly_the_implemented_tools(self):
        want = implemented_tools()
        self.assertTrue(want, "no tools parsed from the MCP source")
        for rel in ("mcp/server.json", "mcp/manifest.json",
                    "web/.well-known/mcp/server-card.json"):
            p = ROOT / rel
            if not p.is_file():
                continue
            got = _tools_of(json.loads(p.read_text()))
            self.assertEqual(got, want,
                             f"{rel} advertises {got} but the implementation "
                             f"provides {want}. A descriptor promising a tool "
                             f"no build implements makes the call fail in the "
                             f"host.")

    def test_published_download_is_the_current_server(self):
        """web/mcp/orphograph_mcp.py is the documented curl-install target.
        A stale copy there ships users an older tool set under the SAME
        SERVER_VERSION, so nothing can tell them apart."""
        shipped = ROOT / "web" / "mcp" / "orphograph_mcp.py"
        if not shipped.is_file():
            self.skipTest("no published copy in web/mcp/")
        self.assertEqual(
            hashlib.sha256(shipped.read_bytes()).hexdigest(),
            hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "the published MCP server differs from mcp/orphograph_mcp.py — "
            "re-copy it; users install this file")

    def test_llms_txt_tool_list_matches(self):
        txt = (ROOT / "web" / "llms.txt").read_text()
        # Scope to the "Tools:" block — a bare repo-wide regex also matches
        # `orphograph_mcp` in the mcp/orphograph_mcp.py path reference.
        m = re.search(r"^- Tools:(.+?)(?=\n\n|\n##)", txt, re.S | re.M)
        self.assertIsNotNone(m, "llms.txt has no '- Tools:' line to check")
        listed = sorted(set(re.findall(r'orphograph_[a-z_]+', m.group(1))))
        self.assertEqual(listed, implemented_tools(),
                         "llms.txt advertises a different tool set to agents")


if __name__ == "__main__":
    unittest.main()
