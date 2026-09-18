"""The shared request helper in tests/_srv.py and the stub server launcher.

Each test here fails on the defect it pins: a launcher that only boots thanks
to Python's implicit script-directory path entry; a helper that follows 3xx
(and would replay a POST as a GET); a JSON wrapper that hands back `null` typed
as a dict; a header dict that hides a header sent twice; a server that died
mid-module surfacing as a bare connection error with its last words discarded.
"""
from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import _srv  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _send(self, code: int, body: bytes, **headers: str):
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/hop":
            return self._send(302, b"", Location="/landed")
        if self.path == "/landed":
            return self._send(200, b"landed")
        if self.path == "/twice":
            self.send_response(200)
            self.send_header("X-Dup", "a")
            self.send_header("X-Dup", "b")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if self.path == "/obj":
            return self._send(200, b'{"k": 1}')
        if self.path == "/empty":
            return self._send(200, b"")
        if self.path == "/html":
            return self._send(200, b"<html>not json</html>")
        return self._send(404, b"nope")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path == "/hop":
            return self._send(303, b"", Location="/landed")
        if self.path == "/api/anchor":
            return self._send(200, b"null", **{"Content-Type": "application/json"})
        if self.path == "/echo-ctype":
            ctype = self.headers.get("Content-Type", "")
            return self._send(422, json.dumps({"ctype": ctype}).encode())
        return self._send(404, b"nope")


@pytest.fixture(scope="module")
def tiny():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_request_returns_the_3xx_it_was_sent(tiny):
    code, body, headers = _srv.request(tiny, "/hop")
    assert code == 302, (code, body)
    assert headers.get("Location") == "/landed"
    assert body == b""


def test_request_never_replays_a_post_as_a_get(tiny):
    code, body, _ = _srv.request(tiny, "/hop", "POST", b"{}")
    assert code == 303, (code, body)
    assert body != b"landed"


def test_request_hands_back_error_status_and_headers(tiny):
    code, body, headers = _srv.request(tiny, "/missing")
    assert code == 404 and body == b"nope"
    assert headers.get("Content-Length") == "4"


def test_anchor_wraps_non_object_json_as_raw(tiny):
    code, rec = _srv.anchor(tiny, {"hash_hex": "ab" * 32})
    assert code == 200
    assert rec == {"_raw": "null"}


def test_headers_keep_a_header_the_server_sent_twice(tiny):
    """A dict keeps one of two; an 'at most once' assertion over it cannot fail."""
    _, _, headers = _srv.request(tiny, "/twice")
    assert headers.get_all("X-Dup") == ["a", "b"]
    assert [v for k, v in headers.items() if k.lower() == "x-dup"] == ["a", "b"]


def test_header_lookup_is_case_insensitive(tiny):
    _, _, headers = _srv.request(tiny, "/hop")
    assert headers.get("location") == "/landed"


def test_post_json_sends_json_and_parses_an_error_body(tiny):
    code, rec = _srv.post_json(tiny, "/echo-ctype", {"a": 1})
    assert code == 422
    assert rec == {"ctype": "application/json"}


def test_get_json_parses_an_object(tiny):
    assert _srv.get_json(tiny, "/obj") == (200, {"k": 1})


def test_a_200_without_a_json_object_is_not_a_usable_reply(tiny):
    """An absence assertion (`"field" not in rec`) must not pass over a page
    that answered 200 with nothing, or with HTML."""
    assert _srv.get_json(tiny, "/empty") == (200, {"_raw": ""})
    for path in ("/empty", "/html"):
        with pytest.raises(AssertionError):
            _srv.ok_json(*_srv.get_json(tiny, path))
    with pytest.raises(AssertionError):
        _srv.ok_json(*_srv.get_json(tiny, "/missing"))
    assert _srv.ok_json(*_srv.get_json(tiny, "/obj")) == {"k": 1}


def test_a_server_that_died_reports_its_last_words(tmp_path):
    bases, procs, logs = _srv.spin(tmp_path, stub_calendars=True)
    try:
        _srv.wait_ready(bases, procs, logs)
        marker = "/no-such-page-last-words-marker"
        assert _srv.request(bases[0], marker)[0] == 404
        procs[0].kill()                 # dies mid-module; the fixture is still up
        procs[0].wait(timeout=10)
        with pytest.raises(_srv.ServerGone) as exc:
            _srv.request(bases[0], "/api/health")
    finally:
        _srv._kill_all(procs, logs)
    assert "--- server output ---" in str(exc.value)
    assert marker in str(exc.value), "the server's own log is not in the failure"


def test_a_server_that_hangs_reports_its_last_words(tmp_path):
    """Wedged, not dead: accepts the connection and never answers."""
    hung = socket.socket()
    hung.bind(("127.0.0.1", 0))
    hung.listen(1)
    base = f"http://127.0.0.1:{hung.getsockname()[1]}"
    log = tmp_path / "hung.log"
    log.write_text("last words of a wedged server\n")
    _srv._LOG_BY_BASE[base] = log
    try:
        with pytest.raises(_srv.ServerGone) as exc:
            _srv.request(base, "/api/health", timeout=0.5)
    finally:
        _srv._LOG_BY_BASE.pop(base, None)
        hung.close()
    assert "last words of a wedged server" in str(exc.value)


def test_a_malformed_url_is_the_tests_bug_not_a_dead_server(tmp_path):
    bases, procs, logs = _srv.spin(tmp_path, stub_calendars=True)
    try:
        _srv.wait_ready(bases, procs, logs)
        with pytest.raises(http.client.InvalidURL):
            _srv.request(bases[0], "/a b\n")
    finally:
        _srv._kill_all(procs, logs)


def test_teardown_forgets_the_log_so_a_reused_port_does_not_inherit_it(tmp_path):
    bases, procs, logs = _srv.spin(tmp_path, stub_calendars=True)
    assert bases[0] in _srv._LOG_BY_BASE
    _srv._kill_all(procs, logs)
    assert bases[0] not in _srv._LOG_BY_BASE
    with pytest.raises(urllib.error.URLError):
        _srv.request(bases[0], "/")


def test_an_unknown_base_keeps_the_plain_connection_error():
    port = _srv.reserve_ports(1)[0]
    with pytest.raises(urllib.error.URLError):
        _srv.request(f"http://127.0.0.1:{port}", "/")


def test_stub_launcher_boots_without_the_implicit_script_dir_path(tmp_path):
    """PYTHONSAFEPATH=1 (3.11+) drops the script's own directory from sys.path;
    the launcher must find its test helpers by an explicit insert, not by luck."""
    bases, procs, logs = _srv.spin(tmp_path, stub_calendars=True, PYTHONSAFEPATH="1")
    try:
        _srv.wait_ready(bases, procs, logs)
        code, body, _ = _srv.request(bases[0], "/api/health")
        assert code == 200, body
        assert json.loads(body).get("ok", True) is not False
    finally:
        _srv._kill_all(procs, logs)
