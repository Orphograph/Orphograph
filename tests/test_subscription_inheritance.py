#!/usr/bin/env python3
"""test_subscription_inheritance.py — pin the helper that decides whether a
caller has an active subscription either directly or via team membership.

This is the central authorization check for rate-limit bypass + private
receipts. A regression here would silently downgrade or upgrade users.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))


class TestSubscriptionInheritance(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data = os.environ.get("ORPHO_DATA_DIR")
        os.environ["ORPHO_DATA_DIR"] = self._tmp.name
        # Fresh imports against the new data dir
        for m in ("teams", "subscriptions", "file_lock", "auth"):
            sys.modules.pop(m, None)
        import teams
        import subscriptions
        self.teams = teams
        self.subscriptions = subscriptions

    def tearDown(self):
        for m in ("teams", "subscriptions", "file_lock", "auth"):
            sys.modules.pop(m, None)
        if self._old_data is None:
            os.environ.pop("ORPHO_DATA_DIR", None)
        else:
            os.environ["ORPHO_DATA_DIR"] = self._old_data
        self._tmp.cleanup()

    def _import_helper(self):
        """Import app to reach _subscription_active_for. We re-import every
        call so the helper sees the fresh teams/subscriptions modules."""
        # The helper lives in app.py at module scope; we need only the function.
        # Avoid booting the full app; read the function via importlib.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_orpho_app_for_test",
            str(ROOT / "server" / "app.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._subscription_active_for

    def test_returns_false_for_empty_email(self):
        helper = self._import_helper()
        self.assertFalse(helper(""))
        self.assertFalse(helper(None))

    def test_returns_true_for_direct_subscriber(self):
        """Mocked is_active returns True → helper must say True."""
        helper = self._import_helper()
        # Patch the subscriptions module the helper closed over
        with patch.object(self.subscriptions, "is_active", return_value=True):
            self.assertTrue(helper("paid@example.com"))

    def test_returns_false_for_non_subscriber_with_no_team(self):
        helper = self._import_helper()
        with patch.object(self.subscriptions, "is_active", return_value=False):
            self.assertFalse(helper("freerider@example.com"))

    def test_team_member_inherits_owner_subscription(self):
        """Member is not directly subscribed, but the team owner is.
        Helper must say True via team inheritance."""
        # Set up a team via the real teams module
        owner = "owner@example.com"
        member = "member@example.com"
        tid = self.teams.create_team(owner, "Acme")
        code = self.teams.issue_invite_code(tid, owner)
        self.teams.redeem_invite_code(code, member)

        helper = self._import_helper()
        # Owner is subscribed; member is not.
        def is_active(email):
            return email == owner
        with patch.object(self.subscriptions, "is_active", side_effect=is_active):
            self.assertTrue(helper(member),
                "team member must inherit owner's active subscription")
            self.assertTrue(helper(owner))

    def test_team_member_no_inherit_when_owner_inactive(self):
        """If the team owner's subscription lapses, members must lose access."""
        owner = "owner@example.com"
        member = "member@example.com"
        tid = self.teams.create_team(owner, "Acme")
        code = self.teams.issue_invite_code(tid, owner)
        self.teams.redeem_invite_code(code, member)

        helper = self._import_helper()
        with patch.object(self.subscriptions, "is_active", return_value=False):
            self.assertFalse(helper(member),
                "team member must NOT have access when owner's sub lapses")
            self.assertFalse(helper(owner))


if __name__ == "__main__":
    unittest.main()
