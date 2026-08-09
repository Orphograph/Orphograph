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


class TestModuleDocstringListsEveryTool(unittest.TestCase):
    """The module docstring is what a human reads before installing, and it
    listed 4 of the 6 tools — omitting anchor_output and verify_lineage. Same
    drift class as the descriptor counts above, different surface."""

    def test_docstring_documents_every_implemented_tool(self):
        src = SOURCE.read_text()
        doc = src.split('"""')[1]
        missing = [t for t in implemented_tools() if t not in doc]
        self.assertEqual(missing, [],
                         f"module docstring omits {missing}; a reader deciding "
                         f"whether to install cannot see these exist")

    def test_anchor_output_forwards_zk_proof(self):
        """llms.txt names the MCP tools as a way to attach a zk_proof, so the
        tool must actually forward it — it silently dropped the field."""
        src = SOURCE.read_text()
        start = src.index("def tool_anchor_output")
        body = src[start:src.index("\ndef ", start + 10)]
        self.assertIn('payload["zk_proof"]', body,
                      "anchor_output drops zk_proof; agents cannot reach the "
                      "one feature built for them")
        schema = src[src.index('"name": "orphograph_anchor_output"'):]
        schema = schema[:schema.index('"name": "orphograph_verify_receipt"')]
        self.assertIn('"zk_proof"', schema,
                      "zk_proof is forwarded but not declared in inputSchema, "
                      "so no host will ever pass it")


class TestReadmeToolTables(unittest.TestCase):
    """Both READMEs listed a stale tool set — README.md had 5 of 6, mcp/README
    had 3 of 6 — and both described verify_receipt as checking "the Bitcoin
    chain", which it does not do. A reader picking tools works from these."""

    TABLES = ("README.md", "mcp/README.md")

    def test_readme_tables_list_every_implemented_tool(self):
        want = implemented_tools()
        for rel in self.TABLES:
            p = ROOT / rel
            if not p.is_file():
                continue
            text = p.read_text()
            missing = [t for t in want if t not in text]
            self.assertEqual(missing, [], f"{rel} omits {missing}")

    def test_readme_tables_do_not_claim_verify_receipt_checks_the_chain(self):
        bad = re.compile(
            r"verify_receipt[^|\n]*\|[^|\n]*(?:against the (?:calendars and )?"
            r"(?:the )?(?:Bitcoin )?chain|and the Bitcoin chain)", re.I)
        for rel in self.TABLES:
            p = ROOT / rel
            if not p.is_file():
                continue
            m = bad.search(p.read_text())
            self.assertIsNone(
                m, f"{rel} says verify_receipt verifies against the chain. It "
                   f"performs a lookup of the office's own record: {m.group(0) if m else ''}")
