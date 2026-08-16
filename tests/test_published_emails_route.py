#!/usr/bin/env python3
"""test_published_emails_route.py — every address we publish must be provisioned.

DEFECT (2026-08-15 Stage 3e drift check)
-----------------------------------------
The site published SEVEN @orphograph.com addresses. Cloudflare Email Routing
had rules for TWO (`hello@`, `dmarc@`). The rest bounced.

    hello@      66 refs   routed
    legal@      23 refs   NOT routed
    security@   16 refs   NOT routed  <-- .well-known/security.txt
    privacy@     4 refs   NOT routed
    press@       4 refs   NOT routed
    abuse@       2 refs   NOT routed
    support@     1 ref    NOT routed

The worst is `security@`: it is the Contact: line in
`web/.well-known/security.txt`, so a researcher who finds a flaw in a
Bitcoin-anchored TRUST product follows the documented disclosure channel and
gets a bounce. They give up, or they go public.

Two separate faults produced that:
  1. The Cloudflare rules were never created (founder-side, dashboard).
  2. `scripts/setup_email.py` — the script that provisions them — did not
     know `security@` or `press@` existed at all. So even a clean re-run
     would have left the disclosure channel dead. That is the ROOT cause,
     and it is what this test guards.

This test cannot see Cloudflare (no credentials, and DNS/routing state is not
in the repo). It enforces the half that IS checkable and that makes the other
half self-correcting: if we publish an address, the provisioner knows about it.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOMAIN = "orphograph.com"

ADDR = re.compile(r"\b([a-z0-9][a-z0-9._-]*)@" + re.escape(DOMAIN) + r"\b", re.I)

# Surfaces a customer, a researcher, or a scanner actually reads.
SCAN_DIRS = ("web", "server", "mcp")
SCAN_SUFFIXES = (".html", ".txt", ".py", ".json", ".md", ".js")
SKIP_PARTS = ("node_modules", "/data/", "/tests/", ".min.js", "_mockups",
              "/receipts/", "/sample-")

# `hello@` is the primary inbox and `dmarc@` the DMARC rua target; both are
# provisioned outside the alias loop, so they are not expected in the tuple.
PROVISIONED_ELSEWHERE = {"hello", "dmarc"}

# Addresses that appear only as EXAMPLES, never as a channel we promise to
# answer. Kept explicit so the exemption is auditable rather than a silent
# regex carve-out.
EXAMPLE_ONLY: set[str] = set()


def _alias_tuple() -> tuple[str, ...]:
    """ALIAS_ADDRESSES from the provisioner, read as source (no import: the
    script has side effects and expects CLI args)."""
    src = (ROOT / "scripts" / "setup_email.py").read_text()
    m = re.search(r"ALIAS_ADDRESSES\s*=\s*\((.*?)\)", src, re.S)
    if not m:
        raise AssertionError(
            "ALIAS_ADDRESSES not found in scripts/setup_email.py — the "
            "provisioner no longer declares which addresses it creates, so "
            "nothing can check that published addresses get routed.")
    return tuple(re.findall(r'"([a-z0-9_-]+)"', m.group(1), re.I))


def _published() -> dict[str, list[str]]:
    """local-part -> files that publish it."""
    found: dict[str, list[str]] = {}
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in SCAN_SUFFIXES:
                continue
            if any(part in str(p) for part in SKIP_PARTS):
                continue
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            for local in {m.group(1).lower() for m in ADDR.finditer(text)}:
                found.setdefault(local, []).append(
                    p.relative_to(ROOT).as_posix())
    return found


class TestPublishedAddressesAreProvisioned(unittest.TestCase):

    def test_every_published_address_is_in_the_provisioner(self):
        published = _published()
        aliases = set(_alias_tuple()) | PROVISIONED_ELSEWHERE | EXAMPLE_ONLY
        missing = {a: f for a, f in published.items() if a not in aliases}
        self.assertEqual(
            missing, {},
            "these addresses are published but the provisioner never creates "
            "a routing rule for them, so mail to them BOUNCES:\n  "
            + "\n  ".join(f"{a}@{DOMAIN} — published in {', '.join(f[:3])}"
                          for a, f in sorted(missing.items())))

    def test_the_security_txt_contact_is_provisioned(self):
        """Called out separately because it is the one address whose failure
        costs an unreported vulnerability rather than a missed email."""
        st = ROOT / "web" / ".well-known" / "security.txt"
        self.assertTrue(st.is_file(), "security.txt is missing entirely")
        contacts = ADDR.findall(st.read_text())
        self.assertTrue(contacts,
                        "security.txt names no @orphograph.com Contact:")
        aliases = set(_alias_tuple()) | PROVISIONED_ELSEWHERE
        for local in {c.lower() for c in contacts}:
            self.assertIn(
                local, aliases,
                f"security.txt tells researchers to mail {local}@{DOMAIN}, "
                f"which the provisioner does not route. A disclosure would "
                f"bounce.")

    def test_security_txt_links_are_canonical_not_redirects(self):
        """security.txt named /security.html, which 301s to /security.

        It works in a browser — clients follow redirects — but RFC 9116 asks
        for canonical URIs, and scanners that do NOT follow redirects see a
        broken Policy link on the one file whose whole job is telling a
        researcher where to go. Checked statically (no network in CI): the
        site canonicalises .html -> extensionless everywhere, so a .html URI
        here is a redirect by construction.
        """
        st = (ROOT / "web" / ".well-known" / "security.txt").read_text()
        stale = [ln.strip() for ln in st.splitlines()
                 if re.search(r"https://[^\s]*\.html", ln)]
        self.assertEqual(
            stale, [],
            "security.txt points at .html URLs, which this site 301s to their "
            f"extensionless form — name the destination instead: {stale}")

    def test_security_txt_has_the_fields_a_researcher_needs(self):
        """A Contact that routes is necessary but not sufficient: an expired
        or absent Expires makes the whole file advisory per RFC 9116."""
        st = (ROOT / "web" / ".well-known" / "security.txt").read_text()
        for field in ("Contact:", "Expires:", "Canonical:", "Policy:"):
            self.assertIn(field, st, f"security.txt lacks {field}")
        m = re.search(r"Expires:\s*(\d{4})-", st)
        self.assertIsNotNone(m, "Expires: is not an ISO date")
        self.assertGreaterEqual(
            int(m.group(1)), 2026,
            "security.txt Expires is in the past — RFC 9116 says the file "
            "should then be considered stale and may be ignored entirely")

    def test_the_provisioner_declares_no_unused_aliases(self):
        """The reverse drift: an alias nobody publishes is a rule nobody
        needs. Not a failure on its own — `billing` is deliberately kept for
        checkout correspondence — so this only asserts the exemption is
        documented in the source rather than silent."""
        src = (ROOT / "scripts" / "setup_email.py").read_text()
        published = set(_published())
        unused = [a for a in _alias_tuple() if a not in published]
        for a in unused:
            self.assertRegex(
                src, rf'"{a}",\s*#',
                f"alias {a!r} is provisioned but nothing publishes it, and it "
                f"carries no comment explaining why it is kept")


if __name__ == "__main__":
    unittest.main()
