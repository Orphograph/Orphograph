"""A port lost between reserving it and the server binding it is retried.

reserve_ports() holds sockets on :0 and releases them just before the server
binds, so another process can take the port in between (an outgoing
connection's source port on a busy runner). That failed the deploy of #275
on 2026-09-27: `OSError: [Errno 98] Address already in use` in a server that
never started, and the deploy was skipped. Only that cause is retried; any
other startup death still fails at once with the server's own output.
"""
from __future__ import annotations

import _srv


def test_a_port_taken_before_the_server_binds_is_retried(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for held in _srv.server_processes(first, stub_calendars=True):
        taken = int(held.rsplit(":", 1)[1])
        real = _srv.reserve_ports
        calls: list[int] = []

        def first_attempt_collides(n: int) -> list[int]:
            calls.append(n)
            return [taken] if len(calls) == 1 else real(n)

        monkeypatch.setattr(_srv, "reserve_ports", first_attempt_collides)
        for base in _srv.server_processes(second, stub_calendars=True):
            assert base != held, "the retry must use a fresh port"
            assert _srv.request(base, "/api/health")[0] == 200
        assert len(calls) == 2, calls
        assert len(list(second.glob("server-*.log"))) == 1, (
            "the lost attempt's log must not linger: tests read the one log file")


def test_a_server_that_dies_for_another_reason_still_fails_at_once(tmp_path):
    """Control: the retry is for a lost port only."""
    import pytest
    with pytest.raises(pytest.fail.Exception, match="EXITED during startup"):
        for _ in _srv.server_processes(tmp_path, stub_calendars=True,
                                       CHECKOUT_RATE_PER_HOUR="10",
                                       PORT="not-a-port"):
            pass
