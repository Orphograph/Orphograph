"""@stats-cold-start: the first /api/stats after a boot paid the cold ledger scan.

Measured in production 2026-11-16 (PR #314's slow log): the first /api/stats on
a fresh process took 10.769 s, all of it in the ledger scan (health 0.000 s,
423 rows) — a per-receipt manifest probe on a cold volume. The post-deploy
route check (10 s timeout) arrives about 100 s after boot and was always that
first caller, so every deploy since 11-09 needed a re-run. The server now runs
the scan once in the background at boot, before any request.
"""
from __future__ import annotations

import json
import re
import time

import pytest

import _srv

ROWS = 50


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("stats_warm")
    with (data_dir / "ledger.jsonl").open("w") as f:
        for i in range(ROWS):
            f.write(json.dumps({"receipt_id": f"WarmRow{i:04d}", "source": "free",
                                "created_at": "2026-11-01T00:00:00+00:00"}) + "\n")
    for base in _srv.server_processes(data_dir, stub_calendars=True):
        yield base, data_dir


def _log(data_dir):
    logs = list(data_dir.glob("server-*.log"))
    assert len(logs) == 1, logs
    return logs[0].read_text(errors="replace")


def test_stats_are_warmed_at_boot_without_a_request(server):
    _, data_dir = server
    deadline = time.time() + 20
    text = ""
    while time.time() < deadline:
        text = _log(data_dir)
        if "[stats] warmed" in text:
            break
        time.sleep(0.2)
    m = re.search(r"\[stats\] warmed in [0-9.]+s \(rows ([0-9]+)\)", text)
    assert m, text[-800:]
    assert int(m.group(1)) == ROWS
    assert "/api/stats" not in text  # nobody asked; the server did it itself
