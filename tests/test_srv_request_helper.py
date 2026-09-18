"""The shared request helper in tests/_srv.py and the stub server launcher.

Each test here fails on the defect it pins: a launcher that only boots thanks
to Python's implicit script-directory path entry; a helper that follows 3xx
(and would replay a POST as a GET); a JSON wrapper that hands back `null` typed
as a dict; a header dict that hides a header sent twice; a server that died
mid-module surfacing as a bare connection error with its last words discarded.
"""
from __future__ import annotations

import json
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


def test_a_server_that_died_reports_its_last_words(tmp_path):
    bases, procs, logs = _srv.spin(tmp_path, stub_calendars=True)
    try:
        _srv.wait_ready(bases, procs, logs)
        marker = "/no-such-page-last-words-marker"
        assert _srv.request(bases[0], marker)[0] == 404
    finally:
        _srv._kill_all(procs, logs)
    with pytest.raises(_srv.ServerGone) as exc:
        _srv.request(bases[0], "/api/health")
    assert "--- server output ---" in str(exc.value)
    assert marker in str(exc.value), "the server's own log is not in the failure"


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
