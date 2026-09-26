"""Rejected HTTP input must not consume an anchor; outages refund paid work."""
from __future__ import annotations

import hashlib
import json

import pytest
import _srv

PATHS = ['/api/anchor', '/api/anchor/batch', '/api/anchor_folder']
TOKEN = 'pk_accounting_test'


def payload(path):
    if path.endswith('/batch'):
        return {'hashes': [{'hash_hex': 'ab' * 32}]}
    if path.endswith('_folder'):
        leaf = hashlib.sha256(b'\x00file.txt\x00' + bytes.fromhex('ab' * 32)).hexdigest()
        return {'manifest': {'algorithm': 'orphograph-merkle-v1-rfc6962', 'version': 1,
                'root_hex': leaf, 'leaves': [{'path': 'file.txt', 'file_sha256_hex': 'ab' * 32,
                                           'leaf_hex': leaf, 'size_bytes': 1}]}}
    return {'hash_hex': 'ab' * 32}


def post(base, path, body, paid=False):
    headers = {'Content-Type': 'application/json'}
    if paid:
        headers['X-Pack-Token'] = TOKEN
    return _srv.request(base, path, method='POST', body=body, headers=headers, timeout=30)


def seed_credit(tmp_path):
    ledger = tmp_path / 'credit_ledger.jsonl'
    ledger.write_text(json.dumps({'claim_code': TOKEN, 'email': '', 'credits_delta': 4,
                                  'source': 'test'}) + '\n')
    return ledger


@pytest.mark.parametrize('path', PATHS)
@pytest.mark.parametrize('body', [b'', b'{', b'\xff', b'null', b'[]', b'1', b'9' * 5000, b'[' * 1500 + b']' * 1500])
@pytest.mark.parametrize('paid', [False, True])
def test_rejected_json_does_not_spend(tmp_path, path, body, paid):
    ledger = seed_credit(tmp_path)
    original = ledger.read_bytes()
    for base in _srv.server_processes(tmp_path, stub_calendars=True, RATE_LIMIT_PER_DAY='1'):
        status, response, _ = post(base, path, body, paid)
        assert status == 400, (status, response)
        assert ledger.read_bytes() == original
        status, response, _ = post(base, path, json.dumps(payload(path)).encode(), paid)
        assert status == 200, (status, response)


@pytest.mark.parametrize('path', PATHS)
def test_calendar_outage_refunds_pack_credit(tmp_path, path):
    ledger = seed_credit(tmp_path)
    for base in _srv.server_processes(tmp_path, stub_calendars=True, fail_calendars='a,b,alice,finney,btc'):
        status, response, _ = post(base, path, json.dumps(payload(path)).encode(), True)
        assert status == 200, response
        data = json.loads(response)
        result = data['results'][0] if path.endswith('/batch') else data
        assert result['calendars_ok'] == 0
        assert result['credit_refunded'] is True
        assert sum(json.loads(line)['credits_delta'] for line in ledger.read_text().splitlines()) == 4


def test_batch_honors_anchoring_disabled(tmp_path):
    ledger = seed_credit(tmp_path)
    original = ledger.read_bytes()
    for base in _srv.server_processes(tmp_path, stub_calendars=True, ORPHO_DISABLE_ANCHORING='1'):
        status, response, _ = post(base, PATHS[1], json.dumps(payload(PATHS[1])).encode(), True)
        assert status == 503, response
        assert ledger.read_bytes() == original


def test_invalid_pack_token_does_not_bypass_batch_free_limit(tmp_path):
    for base in _srv.server_processes(tmp_path, stub_calendars=True, RATE_LIMIT_PER_DAY='1'):
        status, response, _ = post(base, PATHS[1], json.dumps(payload(PATHS[1])).encode())
        assert status == 200, response
        status, response, _ = post(base, PATHS[1], json.dumps(payload(PATHS[1])).encode(), True)
        assert status == 402, response
        assert 'exhausted or invalid' in json.loads(response)['error']


@pytest.mark.parametrize('path', PATHS[:2])
@pytest.mark.parametrize('invalid', [None, 17, [], {}, 'not-a-hash'])
@pytest.mark.parametrize('paid', [False, True])
def test_bad_hash_does_not_spend(tmp_path, path, invalid, paid):
    ledger = seed_credit(tmp_path)
    original = ledger.read_bytes()
    item = {'hash_hex': invalid}
    bad = {'hashes': [item]} if path.endswith('/batch') else item
    for base in _srv.server_processes(tmp_path, stub_calendars=True, RATE_LIMIT_PER_DAY='1'):
        status, response, _ = post(base, path, json.dumps(bad).encode(), paid)
        assert status in (200, 400), response
        if status == 200:
            assert json.loads(response)['succeeded'] == 0
        assert ledger.read_bytes() == original
        status, response, _ = post(base, path, json.dumps(payload(path)).encode(), paid)
        assert status == 200, response


@pytest.mark.parametrize('field,value', [('hardware_attestation', []), ('zk_proof', 1),
                                        ('c2pa_manifest_hash', {}), ('sha512_hex', []),
                                        ('client_label', []), ('notify_email', []),
                                        ('private', 'false'), ('attestation', []), ('metadata', []),
                                        ('c2pa_manifest_hash', 'invalid'), ('sha512_hex', 'invalid')])
@pytest.mark.parametrize('paid', [False, True])
def test_optional_field_types_rejected_before_spending(tmp_path, field, value, paid):
    ledger = seed_credit(tmp_path)
    original = ledger.read_bytes()
    bad = {**payload(PATHS[0]), field: value}
    for base in _srv.server_processes(tmp_path, stub_calendars=True, RATE_LIMIT_PER_DAY='1'):
        status, response, _ = post(base, PATHS[0], json.dumps(bad).encode(), paid)
        assert status == 400, response
        assert ledger.read_bytes() == original
        status, response, _ = post(base, PATHS[0], json.dumps(payload(PATHS[0])).encode(), paid)
        assert status == 200, response


@pytest.mark.parametrize('paid', [False, True])
@pytest.mark.parametrize('items', [[None], [1], [[]], ['hash'], []])
def test_invalid_batch_items_do_not_spend(tmp_path, paid, items):
    ledger = seed_credit(tmp_path)
    original = ledger.read_bytes()
    for base in _srv.server_processes(tmp_path, stub_calendars=True, RATE_LIMIT_PER_DAY='1'):
        status, response, _ = post(base, PATHS[1], json.dumps({'hashes': items}).encode(), paid)
        assert status in (200, 400), response
        if status == 200:
            assert json.loads(response)['succeeded'] == 0
        assert ledger.read_bytes() == original
        status, response, _ = post(base, PATHS[1], json.dumps(payload(PATHS[1])).encode(), paid)
        assert status == 200, response


@pytest.mark.parametrize('paid', [False, True])
@pytest.mark.parametrize('bad', [{}, {'manifest': []}, {'manifest': {'leaves': [None]}},
                                 {'manifest': {'leaves': [{}]}},
                                 {**payload(PATHS[2]), 'client_label': []},
                                 {**payload(PATHS[2]), 'paths_public': 'false'},
                                 {'manifest': {**payload(PATHS[2])['manifest'], 'signature': []}}])
def test_invalid_folder_does_not_spend(tmp_path, paid, bad):
    ledger = seed_credit(tmp_path)
    original = ledger.read_bytes()
    for base in _srv.server_processes(tmp_path, stub_calendars=True, RATE_LIMIT_PER_DAY='1'):
        status, response, _ = post(base, PATHS[2], json.dumps(bad).encode(), paid)
        assert status == 400, response
        assert ledger.read_bytes() == original
        status, response, _ = post(base, PATHS[2], json.dumps(payload(PATHS[2])).encode(), paid)
        assert status == 200, response


@pytest.mark.parametrize('path', PATHS)
def test_successful_anchor_spends_exactly_one_pack_credit(tmp_path, path):
    ledger = seed_credit(tmp_path)
    for base in _srv.server_processes(tmp_path, stub_calendars=True):
        status, response, _ = post(base, path, json.dumps(payload(path)).encode(), True)
        assert status == 200, response
        data = json.loads(response)
        result = data['results'][0] if path.endswith('/batch') else data
        assert result['calendars_ok'] > 0
        assert result['credit_refunded'] is False
        assert sum(json.loads(line)['credits_delta'] for line in ledger.read_text().splitlines()) == 3


@pytest.mark.parametrize('paid', [False, True])
def test_batch_valid_and_invalid_items_charge_only_valid_work(tmp_path, paid):
    ledger = seed_credit(tmp_path)
    body = {'hashes': [None, {'hash_hex': []}, {'hash_hex': 'ab' * 32},
                       {'hash_hex': 'ab' * 32, 'sha512_hex': []}]}
    for base in _srv.server_processes(tmp_path, stub_calendars=True, RATE_LIMIT_PER_DAY='1'):
        status, response, _ = post(base, PATHS[1], json.dumps(body).encode(), paid)
        assert status == 200, response
        data = json.loads(response)
        assert data['succeeded'] == 1 and data['failed'] == 3
        assert data['results'][2]['ok'] is True
        balance = sum(json.loads(line)['credits_delta'] for line in ledger.read_text().splitlines())
        assert balance == (3 if paid else 4)
        if not paid:
            status, response, _ = post(base, PATHS[1], json.dumps(payload(PATHS[1])).encode())
            assert status == 429, response


@pytest.mark.parametrize('path', PATHS[:2])
def test_absent_optional_values_and_normalized_hash_still_anchor(tmp_path, path):
    item = {'hash_hex': '  ' + 'AB' * 32 + '  ', 'client_label': None,
            'sha512_hex': None, 'c2pa_manifest_hash': None, 'hardware_attestation': None,
            'zk_proof': None, 'attestation': None, 'metadata': None}
    body = {'hashes': [item]} if path.endswith('/batch') else item
    for base in _srv.server_processes(tmp_path, stub_calendars=True):
        status, response, _ = post(base, path, json.dumps(body).encode())
        assert status == 200, response
        data = json.loads(response)
        result = data['results'][0] if path.endswith('/batch') else data
        assert result['calendars_ok'] > 0


@pytest.mark.parametrize('path', [PATHS[0], PATHS[2]])
def test_ineligible_private_request_does_not_spend_free_allowance(tmp_path, path):
    bad = {**payload(path), 'private': True}
    for base in _srv.server_processes(tmp_path, stub_calendars=True, RATE_LIMIT_PER_DAY='1'):
        status, response, _ = post(base, path, json.dumps(bad).encode())
        assert status == 402, response
        status, response, _ = post(base, path, json.dumps(payload(path)).encode())
        assert status == 200, response
        assert json.loads(response)['calendars_ok'] > 0


@pytest.mark.parametrize('exhausted', [False, True])
def test_unusable_pack_token_does_not_spend_free_batch_allowance(tmp_path, exhausted):
    ledger = tmp_path / 'credit_ledger.jsonl'
    if exhausted:
        seed_credit(tmp_path)
        with ledger.open('a') as stream:
            stream.write(json.dumps({'claim_code': TOKEN, 'credits_delta': -4,
                                     'source': 'test-consumed'}) + '\n')
    before = ledger.read_bytes() if ledger.exists() else b''
    for base in _srv.server_processes(tmp_path, stub_calendars=True, RATE_LIMIT_PER_DAY='1'):
        receipts_before = set((tmp_path / 'receipts').glob('*/receipt.json'))
        status, response, _ = post(base, PATHS[1], json.dumps(payload(PATHS[1])).encode(), True)
        assert status == 402, response
        assert set((tmp_path / 'receipts').glob('*/receipt.json')) == receipts_before
        assert (ledger.read_bytes() if ledger.exists() else b'') == before
        status, response, _ = post(base, PATHS[1], json.dumps(payload(PATHS[1])).encode())
        assert status == 200, response
        assert json.loads(response)['succeeded'] == 1
        status, response, _ = post(base, PATHS[1], json.dumps(payload(PATHS[1])).encode())
        assert status == 429, response


def test_paid_batch_partial_fill_does_not_spend_free_allowance(tmp_path):
    ledger = seed_credit(tmp_path)
    body = {'hashes': [{'hash_hex': f'{i:064x}'} for i in range(5)]}
    for base in _srv.server_processes(tmp_path, stub_calendars=True, RATE_LIMIT_PER_DAY='1'):
        status, response, _ = post(base, PATHS[1], json.dumps(body).encode(), True)
        assert status == 200, response
        data = json.loads(response)
        assert data['succeeded'] == 4 and data['failed'] == 1
        assert data['results'][4]['error'] == 'pack credits exhausted'
        assert sum(json.loads(line)['credits_delta'] for line in ledger.read_text().splitlines()) == 0
        status, response, _ = post(base, PATHS[1], json.dumps(payload(PATHS[1])).encode())
        assert status == 200, response
        assert json.loads(response)['succeeded'] == 1
