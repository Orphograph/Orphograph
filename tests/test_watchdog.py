"""test_watchdog — unit tests for scripts/orphograph_watchdog.py.

All network and subprocess calls are mocked. The tests never touch the
real fly CLI or the production server.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
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
        # Each test gets its own log dir so _recent_failures sees a clean
        # slate. A temp directory, removed with everything in it: the old one
        # was inside the checkout. The patches are undone by addCleanup, so
        # nothing here ever names a file through the unpatched LOG_DIR.
        logs = tempfile.TemporaryDirectory(prefix="orpho_watchdog_")
        self.addCleanup(logs.cleanup)
        for name, value in (
            ("LOG_DIR", logs.name),
            ("LOG_PATH", os.path.join(logs.name, "orphograph_watchdog.jsonl")),
            ("ALERT_PATH", os.path.join(logs.name, "orphograph_watchdog_ALERT.txt")),
        ):
            patch = mock.patch.object(watchdog, name, value)
            patch.start()
            self.addCleanup(patch.stop)

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
        # Two-strike rule: seed the log with one prior UNHEALTHY tick so
        # the current tick is the SECOND consecutive failure and recovery
        # action fires (single transient failures no longer trigger).
        with open(watchdog.LOG_PATH, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "timestamp_utc": "2026-05-20T00:00:00Z",
                "status": "UNHEALTHY",
                "response_codes": {"https://orphograph.com/": 503,
                                   "https://orphograph.com/api/health": 503},
                "action_taken": "deferred:first_unhealthy_tick",
            }) + "\n")
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
        # Two-strike rule: seed prior UNHEALTHY so this is the SECOND consecutive.
        with open(watchdog.LOG_PATH, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "timestamp_utc": "2026-05-20T00:00:00Z",
                "status": "UNHEALTHY",
                "response_codes": {"https://orphograph.com/": 502,
                                   "https://orphograph.com/api/health": 502},
                "action_taken": "deferred:first_unhealthy_tick",
            }) + "\n")
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


def test_the_real_log_directory_is_left_alone(tmp_path):
    """The tests above run against a log directory of their own. tearDown used
    to stop its patches first and delete the log and the alert file after, by
    which time LOG_DIR was the real directory again: every run of this file
    removed the watchdog's own history, and with it the count of consecutive
    failures that decides when to alert."""
    log = tmp_path / "orphograph_watchdog.jsonl"
    alert = tmp_path / "orphograph_watchdog_ALERT.txt"
    log.write_text('{"status":"ok"}\n')
    alert.write_text("an alert the founder has not read yet\n")
    env = dict(os.environ, ORPHOGRAPH_WATCHDOG_LOG_DIR=str(tmp_path),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--color=no", "-p", "no:cacheprovider",
         __file__, "-k", "WatchdogTests"],
        capture_output=True, text=True, timeout=120, cwd=ROOT, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert " passed" in proc.stdout, proc.stdout
    assert log.read_text() == '{"status":"ok"}\n'
    assert alert.read_text() == "an alert the founder has not read yet\n"


if __name__ == "__main__":
    unittest.main()
