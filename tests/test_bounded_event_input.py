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
    # No rollover files: only the one stable sidecar lock.
    assert [p.name for p in tmp_path.glob('*events.jsonl*') if p.name != 'events.jsonl'] == ['events.jsonl.lock']


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
    assert rows[0]['event'] == analytics.COMPACTED_EVENT
    assert all(len(row['page'].encode('utf-8')) <= 256 for row in rows[1:])


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


# ── review follow-ups: atomic compaction, retention order, rewrite rate ────

import fcntl
import os
import random
import signal
import subprocess
import time

SERVER_DIR = Path(__file__).resolve().parents[1] / 'server'


def _rows(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def test_compaction_keeps_the_newest_rows_contiguous_and_in_order(tmp_path, monkeypatch):
    """Varied row sizes, several compactions: what is kept must be exactly the
    newest run of rows, oldest first, with nothing skipped."""
    path = tmp_path / 'events.jsonl'
    monkeypatch.setenv('ORPHO_EVENTS_MAX_BYTES', '4096')
    rng = random.Random(7)
    replaces = []
    real_replace = os.replace
    monkeypatch.setattr(os, 'replace', lambda a, b: (replaces.append(1), real_replace(a, b))[1])
    for seq in range(600):
        assert analytics.append_event({'event': 'e', 'ts': f'2026-09-25T00:00:{seq % 60:02d}+00:00',
                                       'seq': seq, 'pad': 'x' * rng.randrange(0, 120)}, path=path)
        assert path.stat().st_size <= 4096
    assert len(replaces) >= 3, 'control: the ledger compacted several times'
    rows = _rows(path)
    assert rows[0]['event'] == analytics.COMPACTED_EVENT
    seqs = [r['seq'] for r in rows[1:]]
    assert seqs == list(range(seqs[0], 600)), seqs
    assert rows[0]['oldest_kept_ts'] == rows[1]['ts']


def test_headroom_keeps_rewrites_rare(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    monkeypatch.setenv('ORPHO_EVENTS_MAX_BYTES', '4096')
    replaces = []
    real_replace = os.replace
    monkeypatch.setattr(os, 'replace', lambda a, b: (replaces.append(1), real_replace(a, b))[1])
    for seq in range(2000):
        assert analytics.append_event({'seq': seq}, path=path)
    # Each compaction frees at least a quarter of the cap (~1 KB, ~90 rows
    # of ~12 bytes). Without the headroom every event over the cap rewrites.
    assert 0 < len(replaces) <= 2000 // 50, len(replaces)


def test_a_failed_compaction_leaves_the_ledger_untouched(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    monkeypatch.setenv('ORPHO_EVENTS_MAX_BYTES', '1024')
    for seq in range(40):
        analytics.append_event({'seq': seq}, path=path)
    before = path.read_bytes()

    def boom(a, b):
        raise OSError('disk went away')
    monkeypatch.setattr(os, 'replace', boom)
    for seq in range(40, 200):
        if not analytics.append_event({'seq': seq, 'pad': 'y' * 30}, path=path):
            break
    else:
        pytest.fail('control: no append ever needed a compaction')
    assert path.read_bytes().startswith(before)
    assert all(isinstance(r, dict) for r in _rows(path))
    assert not list(tmp_path.glob('.events.jsonl.*.tmp'))


_WRITER = r'''
import os, sys
sys.path.insert(0, sys.argv[1])
os.environ["ORPHO_EVENTS_MAX_BYTES"] = "262144"
import analytics
from pathlib import Path
path = Path(sys.argv[2])
i = 0
while True:
    analytics.append_event({"event": "e", "ts": "2026-09-25T00:00:00+00:00", "seq": i, "pad": "z" * 180}, path=path)
    i += 1
'''


def test_a_killed_writer_never_leaves_an_empty_or_torn_ledger(tmp_path):
    """SIGKILL a writer that is compacting in a loop, while an unlocked reader
    keeps reading. Every read must be a whole, non-empty ledger."""
    path = tmp_path / 'events.jsonl'
    rows = ''.join(json.dumps({'event': 'e', 'seq': -i, 'pad': 'z' * 180}) + '\n' for i in range(1300))
    path.write_text(rows)
    for trial in range(6):
        child = subprocess.Popen([sys.executable, '-c', _WRITER, str(SERVER_DIR), str(path)])
        deadline = time.monotonic() + 0.4 + 0.15 * trial
        reads = 0
        while time.monotonic() < deadline:
            data = path.read_bytes()
            assert data, 'an unlocked reader saw an empty ledger'
            assert all(isinstance(json.loads(line), dict) for line in data.splitlines()), 'torn ledger'
            reads += 1
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=10)
        data = path.read_bytes()
        assert data and data.endswith(b'\n') or data.count(b'\n') > 1000
        # At most the one line being appended at the kill can be cut short,
        # and the next append repairs it.
        assert all(isinstance(json.loads(line), dict) for line in data.splitlines()[:-1])
        assert reads > 10
    assert analytics.compaction_marker(path) is not None, 'control: the writer compacted'


def test_the_lock_is_shared_across_processes(tmp_path):
    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps({'event': 'seed'}) + '\n')
    lock = open(tmp_path / 'events.jsonl.lock', 'a')
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    child = subprocess.Popen([sys.executable, '-c',
        'import sys; sys.path.insert(0, sys.argv[1]); import analytics; from pathlib import Path; '
        'analytics.append_event({"event": "child"}, path=Path(sys.argv[2]))',
        str(SERVER_DIR), str(path)])
    time.sleep(0.8)
    assert child.poll() is None, 'the child wrote while another process held the lock'
    assert [r['event'] for r in _rows(path)] == ['seed']
    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    lock.close()
    assert child.wait(timeout=10) == 0
    assert [r['event'] for r in _rows(path)] == ['seed', 'child']


def test_a_torn_tail_under_the_cap_is_repaired_in_place(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    monkeypatch.setenv('ORPHO_EVENTS_MAX_BYTES', '4096')
    path.write_bytes(b'{"event":"a"}\n{"event":"b"')
    assert analytics.append_event({'event': 'c'}, path=path)
    assert path.read_bytes() == b'{"event":"a"}\n{"event":"c"}\n'


def test_readers_say_the_window_is_incomplete_after_compaction(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    monkeypatch.setenv('ORPHO_EVENTS_MAX_BYTES', '2048')
    for seq in range(200):
        analytics.append_event({'event': 'lp_cta_clicked', 'seq': seq,
                                'ts': '2026-09-25T00:00:00+00:00', 'ip_trunc': '1.2.3',
                                'ip_src': 'cf'}, path=path)
    marker = analytics.compaction_marker(path)
    assert marker and marker['oldest_kept_ts'] == '2026-09-25T00:00:00+00:00'

    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('gate_read_t', root / 'tools' / 'gate_read.py')
    gr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gr)
    rows, bad = gr.load(path)
    assert bad == 0 and all(r['event'] == 'lp_cta_clicked' for r in rows)
    usable, pre_fix, relay, _ = gr.partition(rows)
    legs = {'cta_clicks': gr.leg_cta(usable, pre_fix, relay, set())}
    if legs['cta_clicks']['status'] == 'NOT_MET':
        legs = gr.apply_compaction(legs, gr.compaction_marker(path))
        assert legs['cta_clicks']['status'] == gr.UNKNOWN

    spec = importlib.util.spec_from_file_location('funnel_digest_t', root / 'scripts' / 'funnel_digest.py')
    fd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fd)
    assert fd._kept_from(path) == '2026-09-25T00:00:00+00:00'
    assert '2026-09-25T00:00:00+00:00' in fd._compacted_note(fd._kept_from(path))


def test_the_funnel_endpoint_reports_a_compacted_window(tmp_path):
    ledger = tmp_path / 'events.jsonl'
    for base in _srv.server_processes(tmp_path, stub_calendars=True, ORPHO_FOUNDER_TOKEN='tok-funnel-1'):
        def funnel():
            status, body, _ = _srv.request(base, '/api/founder/funnel',
                                           headers={'X-Orpho-Founder': 'tok-funnel-1'})
            assert status == 200, body
            return json.loads(body)
        ledger.write_text(json.dumps({'event': 'drop_zone_visible',
                                      'ts': '2026-09-24T00:00:00+00:00'}) + '\n')
        before = funnel()
        assert before['window_complete'] is True and before['ledger_compacted'] is None
        ledger.write_text(json.dumps({'event': '_compacted', 'ts': '2026-09-25T00:00:00+00:00',
                                      'oldest_kept_ts': '2099-01-01T00:00:00+00:00',
                                      'dropped_bytes': 10}) + '\n')
        after = funnel()
        assert after['window_complete'] is False
        assert after['ledger_compacted']['oldest_kept_ts'] == '2099-01-01T00:00:00+00:00'
        assert after['events_scanned'] == 0


def test_waitlist_does_not_store_a_lone_surrogate(server):
    base, data = server
    status, body, _ = _srv.request(base, '/api/waitlist', method='POST',
        body=b'{"email":"a\\ud800@example.test"}', headers={'Content-Type': 'application/json'})
    assert status == 200 and b'On the list' not in body
    stored = (data / 'waitlist.jsonl')
    assert not stored.exists() or b'a\\ud800' not in stored.read_bytes()
    # Control: an ordinary address is stored, in the file checked above.
    status, body, _ = _srv.request(base, '/api/waitlist', method='POST',
        body=b'{"email":"ok@example.test"}', headers={'Content-Type': 'application/json'})
    assert status == 200 and b'On the list' in body
    assert b'ok@example.test' in stored.read_bytes()
    assert stored.read_bytes().count(b'\n') == 1
