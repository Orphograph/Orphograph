"""A spun server binds its own port, and the harness learns it from that server.

reserve_ports() held sockets on :0 and released them just before the server
bound, so another process could take the port in between. That failed the
deploy of #275 on 2026-09-27 (`OSError: [Errno 98] Address already in use`,
deploy skipped). Reproducing it found worse: readiness was any answer to
/api/health, so a server that lost its port to another server was reported
ready and the test ran against a process it did not start.

The server now binds port 0 itself and writes the port it got; spin() reads
that line from the server's own log. No window, no retry.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import _srv


def test_the_harness_does_not_choose_the_port(tmp_path, monkeypatch):
    def no_reservations(n):
        raise AssertionError("spin() reserved a port the server then had to bind")
    monkeypatch.setattr(_srv, "reserve_ports", no_reservations)
    # The claim is that the SERVER picks: every child is started with PORT=0,
    # however the harness might otherwise have chosen one.
    ports_given: list[int] = []
    real_env = _srv.base_env

    def recording_env(data_dir, port, **extra):
        ports_given.append(port)
        return real_env(data_dir, port, **extra)
    monkeypatch.setattr(_srv, "base_env", recording_env)
    for base in _srv.server_processes(tmp_path, stub_calendars=True):
        assert ports_given == [0], ports_given
        port = int(base.rsplit(":", 1)[1])
        assert port > 0
        assert _srv.request(base, "/api/health")[0] == 200
        log = tmp_path / f"server-{port}.log"
        assert f"orphograph listening on http://127.0.0.1:{port}" in log.read_text(), (
            "the base must be the port the server itself reported")


def test_two_servers_on_one_data_dir_each_answer_on_their_own_port(tmp_path):
    for bases in _srv.server_processes(tmp_path, n=2, stub_calendars=True):
        assert len(set(bases)) == 2, bases
        for base in bases:
            assert _srv.request(base, "/api/health")[0] == 200
        assert sorted(p.name for p in tmp_path.glob("server-*.log")) == sorted(
            f"server-{b.rsplit(':', 1)[1]}.log" for b in bases)


def test_a_server_that_dies_before_binding_fails_at_once_with_its_output(tmp_path):
    """Control: a startup death is still a failure, with the server's words."""
    with pytest.raises(pytest.fail.Exception, match="EXITED during startup"):
        for _ in _srv.server_processes(tmp_path, stub_calendars=True, PORT="not-a-port"):
            pass


def test_the_hmac_secret_is_not_written_into_the_checkout(monkeypatch):
    """An in-process claim email derives a referral code from the installation
    secret; loading it must not create or touch a secret in the checkout.

    The cache is emptied first so the email itself must load the secret, and
    the test fails if it did not (a no-op mailer made the first version of
    this test pass while checking nothing; found by review of #276)."""
    import auth
    import mailer
    repo = Path(__file__).resolve().parent.parent
    in_checkout = [repo / ".hmac_secret", repo / "data" / ".hmac_secret"]

    def state():
        return {p: (p.stat().st_mtime_ns if p.exists() else None) for p in in_checkout}
    before = state()
    monkeypatch.setattr(auth, "_HMAC_SECRET_CACHE", None)
    mailer.send_pack_claim_email("secret-path@example.test", "pk_secretPathTest01", 10)
    assert auth._HMAC_SECRET_CACHE is not None, "the email never loaded the secret"
    assert repo not in auth.HMAC_SECRET_PATH.parents, auth.HMAC_SECRET_PATH
    assert auth.HMAC_SECRET_PATH.read_bytes() == auth._HMAC_SECRET_CACHE
    assert state() == before, "a secret file in the checkout was created or rewritten"
