"""test_watchdog — unit tests for scripts/orphograph_watchdog.py.

All network and subprocess calls are mocked. The tests never touch the
real fly CLI or the production server.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

watchdog = importlib.import_module("orphograph_watchdog")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


class _FakeResp:
    """Minimal stand-in for urlopen's context-manager response."""

    def __init__(self, status: int = 200) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _fly_status_json(state: str, mid: str = "abc123") -> str:
    return json.dumps(
        {
            "Machines": [
                {
                    "id": mid,
                    "state": state,
                    "config": {"process_group": "app"},
                }
            ]
        }
    )


def _completed(stdout: str = "", returncode: int = 0):
    return mock.Mock(stdout=stdout, stderr="", returncode=returncode)


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #


class WatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        # Each test gets its own log dir so _recent_failures sees a clean slate.
        self._tmpdir = mock.patch.object(
            watchdog,
            "LOG_DIR",
            str(ROOT / "tests" / ".watchdog_tmp_logs"),
        )
        self._tmpdir.start()
        os.makedirs(watchdog.LOG_DIR, exist_ok=True)
        self._logpath = mock.patch.object(
            watchdog,
            "LOG_PATH",
            os.path.join(watchdog.LOG_DIR, "orphograph_watchdog.jsonl"),
        )
        self._logpath.start()
        self._alertpath = mock.patch.object(
            watchdog,
            "ALERT_PATH",
            os.path.join(watchdog.LOG_DIR, "orphograph_watchdog_ALERT.txt"),
        )
        self._alertpath.start()
        # Clean log + alert files between tests.
        for p in (watchdog.LOG_PATH, watchdog.ALERT_PATH):
            if os.path.exists(p):
                os.unlink(p)

    def tearDown(self) -> None:
        self._alertpath.stop()
        self._logpath.stop()
        self._tmpdir.stop()
        for p in (
            os.path.join(watchdog.LOG_DIR, "orphograph_watchdog.jsonl"),
            os.path.join(watchdog.LOG_DIR, "orphograph_watchdog_ALERT.txt"),
        ):
            if os.path.exists(p):
                os.unlink(p)

    # ---------- 1. healthy path ---------- #
    def test_healthy_returns_zero_no_action(self) -> None:
        with mock.patch.object(
            watchdog.urllib.request, "urlopen", return_value=_FakeResp(200)
        ) as urlopen, mock.patch.object(
            watchdog.subprocess, "run"
        ) as srun:
            rc = watchdog.run_once()
        self.assertEqual(rc, 0)
        self.assertEqual(urlopen.call_count, len(watchdog.PROBE_URLS))
        srun.assert_not_called()
        # Log line written with HEALTHY status, no action.
        with open(watchdog.LOG_PATH, "r", encoding="utf-8") as fh:
            rows = [json.loads(ln) for ln in fh if ln.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "HEALTHY")
        self.assertIsNone(rows[0]["action_taken"])

    # ---------- 2. unhealthy + stopped → start ---------- #
    def test_unhealthy_stopped_invokes_machine_start(self) -> None:
        srun = mock.Mock(
            side_effect=[
                _completed(_fly_status_json("stopped"), 0),  # fly status
                _completed("", 0),  # fly machine start
            ]
        )
        with mock.patch.object(
            watchdog.urllib.request, "urlopen", return_value=_FakeResp(503)
        ), mock.patch.object(watchdog.subprocess, "run", srun):
            rc = watchdog.run_once()
        self.assertEqual(rc, 1)
        self.assertEqual(srun.call_count, 2)
        first_args = srun.call_args_list[0][0][0]
        second_args = srun.call_args_list[1][0][0]
        self.assertIn("status", first_args)
        self.assertEqual(second_args[:3], [watchdog.FLY_BIN, "machine", "start"])
        self.assertIn("abc123", second_args)

    # ---------- 3. unhealthy + started → restart --skip-health-checks ---------- #
    def test_unhealthy_started_invokes_machine_restart_skip_health(self) -> None:
        srun = mock.Mock(
            side_effect=[
                _completed(_fly_status_json("started"), 0),
                _completed("", 0),
            ]
        )
        with mock.patch.object(
            watchdog.urllib.request, "urlopen", return_value=_FakeResp(502)
        ), mock.patch.object(watchdog.subprocess, "run", srun):
            rc = watchdog.run_once()
        self.assertEqual(rc, 1)
        second_args = srun.call_args_list[1][0][0]
        self.assertEqual(
            second_args[:3], [watchdog.FLY_BIN, "machine", "restart"]
        )
        self.assertIn("abc123", second_args)
        self.assertIn("--skip-health-checks", second_args)

    # ---------- 4. three-strike alert path ---------- #
    def test_three_consecutive_failures_writes_alert(self) -> None:
        srun = mock.Mock(
            side_effect=[
                _completed(_fly_status_json("stopped"), 0),
                _completed("", 0),
                _completed(_fly_status_json("stopped"), 0),
                _completed("", 0),
                _completed(_fly_status_json("stopped"), 0),
                _completed("", 0),
            ]
        )
        # Force telegram path to fail so the file fallback is exercised.
        with mock.patch.object(
            watchdog.urllib.request, "urlopen", return_value=_FakeResp(503)
        ), mock.patch.object(watchdog.subprocess, "run", srun), mock.patch.object(
            watchdog, "_try_telegram", return_value=False
        ) as tg:
            r1 = watchdog.run_once()
            r2 = watchdog.run_once()
            r3 = watchdog.run_once()
        self.assertEqual((r1, r2, r3), (1, 1, 1))
        # Alert file must exist with at least one line.
        self.assertTrue(
            os.path.exists(watchdog.ALERT_PATH),
            f"alert file missing at {watchdog.ALERT_PATH}",
        )
        with open(watchdog.ALERT_PATH, "r", encoding="utf-8") as fh:
            txt = fh.read()
        self.assertIn("UNHEALTHY", txt)
        # Telegram was attempted (importability check).
        tg.assert_called()


if __name__ == "__main__":
    unittest.main()
