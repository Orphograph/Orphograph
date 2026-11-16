"""Slow-request timing in the access log, driven through a real server process.

@stats-cold-start (2026-11-16): the post-deploy route check timed out on
/api/stats at the edge on 3 deploys in a row (11-09, 11-16 twice) while the
origin answered a moment later. A cold stats computation measured 0.03 s and
the hourly upgrade pass did not slow it, so the cause is not known. The access
log printed no duration, so nobody could see where the time went. Requests
slower than ORPHO_SLOW_REQUEST_SEC now get a `slow` line with the duration, and
a slow /api/stats computation names its two phases.

Thresholds are set to 0 here so every request is "slow".
"""
from __future__ import annotations

import re

import pytest

import _srv


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("slow_log")
    for base in _srv.server_processes(data_dir, stub_calendars=True,
                                      ORPHO_SLOW_REQUEST_SEC="0", ORPHO_STATS_SLOW_SEC="0"):
        yield base, data_dir


def _log(data_dir):
    logs = list(data_dir.glob("server-*.log"))
    assert len(logs) == 1, logs
    return logs[0].read_text(errors="replace")


def test_a_slow_request_logs_its_duration(server):
    base, data_dir = server
    _srv.raw_request(base, "/api/stats")
    text = _log(data_dir)
    assert re.search(r'slow "GET /api/stats HTTP/1\.1" [0-9]+\.[0-9]{3}s', text), text[-800:]


def test_a_slow_stats_computation_names_its_phases(server):
    base, data_dir = server
    _srv.raw_request(base, "/api/stats")
    assert re.search(r"\[stats\] compute [0-9.]+s \(health [0-9.]+s, ledger [0-9.]+s, rows [0-9]+\)",
                     _log(data_dir))


def test_the_slow_line_keeps_the_access_log_redaction(server):
    base, data_dir = server
    _srv.raw_request(base, "/r/ReceiptIdCanarySlow01")
    text = _log(data_dir)
    assert "ReceiptIdCanarySlow01" not in text
    assert 'slow "GET /r/{id} HTTP/1.1"' in text
