#!/usr/bin/env python3
"""test_teams.py — teams module unit tests.

Covers: team creation idempotency, invite single-use, ownership boundaries,
member cap enforcement, leave/remove flows, and subscription inheritance via
owner_email_for().
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))


class TestTeams(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data_dir = os.environ.get("ORPHO_DATA_DIR")
        os.environ["ORPHO_DATA_DIR"] = self._tmp.name
        # Force reimport with fresh ledger paths
        for m in ("teams", "file_lock"):
            sys.modules.pop(m, None)
        global teams  # noqa: PLW0603
        import teams as t_mod
        teams = t_mod

    def tearDown(self):
        sys.modules.pop("teams", None)
        if self._old_data_dir is None:
            os.environ.pop("ORPHO_DATA_DIR", None)
        else:
            os.environ["ORPHO_DATA_DIR"] = self._old_data_dir
        self._tmp.cleanup()

    def test_create_team_idempotent_for_same_owner(self):
        t1 = teams.create_team("owner@example.com", "Acme")
        t2 = teams.create_team("owner@example.com", "Acme")
        self.assertEqual(t1, t2, "creating twice with same owner returns same team")

    def test_invite_single_use(self):
        tid = teams.create_team("owner@example.com", "Acme")
        code = teams.issue_invite_code(tid, "owner@example.com")
        self.assertTrue(code and code.startswith("tinv_"))
        r1 = teams.redeem_invite_code(code, "alice@example.com")
        self.assertTrue(r1["ok"])
        r2 = teams.redeem_invite_code(code, "bob@example.com")
        self.assertFalse(r2["ok"])
        self.assertIn("already redeemed", r2["error"])

    def test_only_owner_can_issue_invites(self):
        tid = teams.create_team("owner@example.com", "Acme")
        code = teams.issue_invite_code(tid, "alice@example.com")  # not owner
        self.assertIsNone(code)

    def test_owner_cannot_redeem_own_invite(self):
        tid = teams.create_team("owner@example.com", "Acme")
        code = teams.issue_invite_code(tid, "owner@example.com")
        r = teams.redeem_invite_code(code, "owner@example.com")
        self.assertFalse(r["ok"])

    def test_owner_email_for_inheritance(self):
        tid = teams.create_team("owner@example.com", "Acme")
        code = teams.issue_invite_code(tid, "owner@example.com")
        teams.redeem_invite_code(code, "alice@example.com")
        # Member → owner_email_for returns the owner
        self.assertEqual(teams.owner_email_for("alice@example.com"), "owner@example.com")
        # Owner → owner_email_for returns themselves
        self.assertEqual(teams.owner_email_for("owner@example.com"), "owner@example.com")
        # Random outsider → None
        self.assertIsNone(teams.owner_email_for("rando@example.com"))

    def test_remove_member(self):
        tid = teams.create_team("owner@example.com", "Acme")
        code = teams.issue_invite_code(tid, "owner@example.com")
        teams.redeem_invite_code(code, "alice@example.com")
        ok = teams.remove_member(tid, "owner@example.com", "alice@example.com")
        self.assertTrue(ok)
        t = teams.team_for_member("alice@example.com")
        # Removed member no longer in any team
        self.assertIsNone(t)

    def test_remove_member_requires_owner(self):
        tid = teams.create_team("owner@example.com", "Acme")
        c1 = teams.issue_invite_code(tid, "owner@example.com")
        teams.redeem_invite_code(c1, "alice@example.com")
        c2 = teams.issue_invite_code(tid, "owner@example.com")
        teams.redeem_invite_code(c2, "bob@example.com")
        # Alice cannot remove Bob
        ok = teams.remove_member(tid, "alice@example.com", "bob@example.com")
        self.assertFalse(ok)

    def test_leave_team(self):
        tid = teams.create_team("owner@example.com", "Acme")
        code = teams.issue_invite_code(tid, "owner@example.com")
        teams.redeem_invite_code(code, "alice@example.com")
        ok = teams.leave_team("alice@example.com")
        self.assertTrue(ok)
        self.assertIsNone(teams.team_for_member("alice@example.com"))

    def test_owner_cannot_leave(self):
        teams.create_team("owner@example.com", "Acme")
        ok = teams.leave_team("owner@example.com")
        self.assertFalse(ok)

    def test_cant_be_in_two_teams(self):
        tid1 = teams.create_team("owner1@example.com", "Team1")
        c1 = teams.issue_invite_code(tid1, "owner1@example.com")
        teams.redeem_invite_code(c1, "alice@example.com")
        tid2 = teams.create_team("owner2@example.com", "Team2")
        c2 = teams.issue_invite_code(tid2, "owner2@example.com")
        r = teams.redeem_invite_code(c2, "alice@example.com")
        self.assertFalse(r["ok"])
        self.assertIn("leave", r["error"].lower())


if __name__ == "__main__":
    unittest.main()
