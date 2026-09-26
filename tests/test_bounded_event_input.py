"""Public analytics inputs and the shared, bounded best-effort event ledger."""
import concurrent.futures
import json
import sys
from pathlib import Path

import pytest
import _srv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import analytics


def test_internal_writer_retains_newest_complete_rows(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    monkeypatch.setattr(analytics, 'EVENTS_PATH', path)
    monkeypatch.setenv('ORPHO_EVENTS_MAX_BYTES', '1024')
    for i in range(50):
        assert analytics.record('page_view', 'landing', str(i))
        assert path.stat().st_size <= 1024
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    assert rows[-1]['ip_prefix'] == '49'
    assert len(rows) < 50
    assert not list(tmp_path.glob('events.jsonl.*'))


def test_concurrent_internal_writers(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    monkeypatch.setattr(analytics, 'EVENTS_PATH', path)
    monkeypatch.setenv('ORPHO_EVENTS_MAX_BYTES', '2048')
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(lambda i: analytics.record('page_view', 'landing', str(i)), range(120)))
    assert path.stat().st_size <= 2048
    assert path.read_bytes().endswith(b'\n')
    assert all(isinstance(json.loads(line), dict) for line in path.read_bytes().splitlines())


@pytest.fixture
def server(tmp_path):
    for base in _srv.server_processes(tmp_path, stub_calendars=True,
                                     ORPHO_EVENTS_MAX_BYTES='4096',
                                     PYTHONINTMAXSTRDIGITS='640'):
        yield base, tmp_path


@pytest.mark.parametrize('route', ['/api/event', '/api/waitlist'])
@pytest.mark.parametrize('raw', [b'[]', b'null', b'[' * 1500 + b'0' + b']' * 1500,
                                b'{"email":' + b'9' * 900 + b'}'],
                         ids=['list', 'null', 'deep', 'large-number'])
def test_malformed_json_is_400(server, route, raw):
    base, _ = server
    status, _, _ = _srv.request(base, route, method='POST', body=raw,
                               headers={'Content-Type': 'application/json'})
    assert status == 400


def test_http_unicode_retention(server):
    base, data = server
    for i in range(35):
        body = json.dumps({'event': 'page_view', 'page': '/' + str(i) + '😀' * 255}).encode()
        assert _srv.request(base, '/api/event', method='POST', body=body,
                            headers={'Content-Type': 'application/json'})[0] == 204
        assert (data / 'events.jsonl').stat().st_size <= 4096
    rows = [json.loads(line) for line in (data / 'events.jsonl').read_bytes().splitlines()]
    assert rows[-1]['page'].startswith('/34')
    assert all(len(row['page'].encode('utf-8')) <= 256 for row in rows)


def test_sink_repairs_partial_tail_and_retains_exact_boundary(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    row = {'event': 'x'}
    encoded = b'{"event":"x"}\n'
    monkeypatch.setenv('ORPHO_EVENTS_MAX_BYTES', str(2 * len(encoded)))
    path.write_bytes(encoded + b'{"partial"')
    assert analytics.append_event(row, path=path)
    assert path.read_bytes() == encoded * 2
    assert analytics.append_event(row, path=path)
    assert path.read_bytes() == encoded * 2


def test_sink_rejects_oversized_row_and_survives_io_error(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    monkeypatch.setenv('ORPHO_EVENTS_MAX_BYTES', '64')
    assert not analytics.append_event({'event': 'x' * 100}, path=path)
    assert not path.exists()
    assert not analytics.append_event({'event': 'x'}, path=tmp_path)


def test_sink_drops_invalid_old_rows_on_compaction(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    monkeypatch.setenv('ORPHO_EVENTS_MAX_BYTES', '64')
    path.write_bytes(b'x' * 100 + b'\ninvalid\n{"event":"old"}\n')
    assert analytics.append_event({'event': 'new'}, path=path)
    assert [json.loads(line)['event'] for line in path.read_bytes().splitlines()] == ['old', 'new']
    assert path.stat().st_size <= 64


@pytest.fixture
def shared_servers(tmp_path):
    yield from _srv.server_processes(tmp_path, n=2, stub_calendars=True,
                                    ORPHO_EVENTS_MAX_BYTES='2048')


def test_http_concurrent_processes_share_event_cap(tmp_path, monkeypatch, shared_servers):
    monkeypatch.setattr(analytics, 'EVENTS_PATH', tmp_path / 'events.jsonl')
    monkeypatch.setenv('ORPHO_EVENTS_MAX_BYTES', '2048')
    def post(i):
        if i % 3 == 0:
            return 204 if analytics.record('page_view', 'landing', str(i)) else 500
        return _srv.request(shared_servers[i % 2], '/api/event', method='POST',
            body=json.dumps({'event': 'page_view', 'page': '/' + str(i)}).encode(),
            headers={'Content-Type': 'application/json'})[0]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        assert all(status == 204 for status in pool.map(post, range(50)))
    path = tmp_path / 'events.jsonl'
    assert path.stat().st_size <= 2048
    assert path.read_bytes().endswith(b'\n')
    assert all(isinstance(json.loads(line), dict) for line in path.read_bytes().splitlines())


def test_http_rejects_unpaired_surrogate_page(server):
    base, _ = server
    assert _srv.request(base, '/api/event', method='POST',
        body=b'{"event":"page_view","page":"\\ud800"}',
        headers={'Content-Type': 'application/json'})[0] == 400


def test_invalid_cap_setting_uses_safe_default(tmp_path, monkeypatch):
    monkeypatch.setenv('ORPHO_EVENTS_MAX_BYTES', 'not-an-integer')
    assert analytics.append_event({'event': 'x'}, path=tmp_path / 'events.jsonl')
