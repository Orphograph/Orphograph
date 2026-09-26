"""HTTP regressions for API source-prefix collisions; no external calendars."""
import hashlib
import hmac
import io
import json
import time
import zipfile

import pytest
import _srv

SECRET = 'receipt-ownership-test'
EMAILS = ['alice@example.test', 'bob@example.test']
KEYS = ['orpho_sameAliceKey1234567890', 'orpho_sameBobKey123456789012']


def account(email):
    return hmac.new(SECRET.encode(), email.encode(), hashlib.sha256).hexdigest()[:16]


def headers(who):
    return {'Cookie': 'orpho_sid=session-' + str(who), 'Content-Type': 'application/json'}


@pytest.fixture
def server(tmp_path):
    def ledger(name, rows):
        (tmp_path / name).write_text(''.join(json.dumps(r) + '\n' for r in rows))
    ledger('auth_sessions.jsonl', [dict(event='created', session_hash=hashlib.sha256(('session-'+str(i)).encode()).hexdigest(), email=e, expires_unix=time.time()+3600) for i,e in enumerate(EMAILS)])
    ledger('subscriptions.jsonl', [dict(email=e, status='active', stripe_sub='sub_'+str(i)) for i,e in enumerate(EMAILS)])
    ledger('api_keys.jsonl', [dict(event='issued', email=e, key_hash=hashlib.sha256(k.encode()).hexdigest(), key_prefix=k[:14]) for e,k in zip(EMAILS,KEYS)] + [dict(event='issued', email=EMAILS[0], key_hash='old', key_prefix='orpho_oldA12345'),dict(event='revoked',email=EMAILS[0],key_hash='old')])
    for rid, extra in [
        ('LegacyPrivate01', dict(private=True, owner_id=account(EMAILS[0]))),
        ('LegacyPublic001', {}),
        ('LegacyRotated01', dict(source='api:orpho_oldA')),
        ('DurablePublic01', dict(account_id=account(EMAILS[0]))),
    ]:
        d=tmp_path/'receipts'/rid;d.mkdir(parents=True)
        rec=dict(receipt_id=rid,created_at='2026-09-23T00:00:00Z',hash_hex='ab'*32,source='api:orpho_same',private=False,calendars_ok=0,calendars_total=5)
        rec.update(extra);(d/'receipt.json').write_text(json.dumps(rec))
    for base in _srv.server_processes(tmp_path,stub_calendars=True,ORPHO_HMAC_SECRET=SECRET):
        yield base,tmp_path


def request(base,path,who=0,body=None):
    return _srv.request(base,path,headers=headers(who),method='POST' if body is not None else 'GET',body=json.dumps(body).encode() if body is not None else None)


def test_colliding_account_cannot_list_count_export_or_toggle(server):
    base,data=server
    for path in ['/api/me/anchors','/api/me/anchors.csv','/api/me/anchors.jsonld','/api/me/anchors.zip']:
        status,body,_=request(base,path,1)
        assert status==200,(path,status,body)
        if path.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(body)) as z: body=b' '.join(z.read(n) for n in z.namelist())
        for rid in ['LegacyPrivate01','LegacyPublic001','LegacyRotated01','DurablePublic01']:
            assert rid.encode() not in body,(path,rid)
    status,body,_=request(base,'/api/me',1)
    assert status==200 and json.loads(body)['anchor_count']==0
    for rid in ['LegacyPrivate01','LegacyPublic001','DurablePublic01']:
        status,body,_=request(base,'/api/me/receipt/'+rid+'/privacy',1,{'private':False})
        assert status==404,(rid,status,body)


def test_owner_keeps_historical_private_rotated_and_durable_receipts(server):
    base,data=server
    status,body,_=request(base,'/api/me/anchors')
    assert status==200
    for rid in ['LegacyPrivate01','LegacyRotated01','DurablePublic01']: assert rid.encode() in body
    assert b'LegacyPublic001' not in body
    assert json.loads(request(base,'/api/me')[1])['anchor_count']==3
    for private in [False,True]:
        status,body,_=request(base,'/api/me/receipt/LegacyPrivate01/privacy',body={'private':private})
        assert status==200,body
        rec=json.loads((data/'receipts/LegacyPrivate01/receipt.json').read_text())
        assert rec['account_id']==account(EMAILS[0])
    assert request(base,'/api/receipt/LegacyPrivate01')[0]==200
    assert request(base,'/api/receipt/LegacyPrivate01',1)[0]==404


@pytest.mark.parametrize('route',['/api/anchor','/api/anchor/batch','/api/anchor_folder'])
def test_new_api_receipts_store_internal_owner(server,route):
    base,data=server
    payload={'hash_hex':'cd'*32,'account_id':account(EMAILS[1])}
    if route.endswith('/batch'): payload={'hashes':[payload]}
    if route.endswith('_folder'):
        import sys
        from pathlib import Path
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
        import merkle
        leaf=merkle._leaf_hash('a.txt',bytes.fromhex('cd'*32)).hex()
        payload={'algorithm':merkle.ALGORITHM,'version':1,'root_hex':leaf,'leaves':[{'path':'a.txt','file_sha256_hex':'cd'*32,'leaf_hex':leaf,'size_bytes':1}]}
    status,body,_=_srv.request(base,route,method='POST',body=json.dumps(payload).encode(),headers={'Content-Type':'application/json','X-Orpho-Api-Key':KEYS[0]},timeout=30)
    assert status==200,body
    result=json.loads(body)
    rid=result['results'][0]['receipt_id'] if route.endswith('/batch') else result['receipt_id']
    rec=json.loads((data/'receipts'/rid/'receipt.json').read_text())
    assert rec['account_id']==account(EMAILS[0])
    assert b'account_id' not in body
    assert b'account_id' not in request(base,'/api/receipt/'+rid)[1]
    for prefix in ['/api/receipt/', '/api/verify/']:
        status, exported, _ = _srv.request(base, prefix + rid + '/summary')
        assert status == 200
        assert b'account_id' not in exported
        status, exported, _ = _srv.request(base, prefix + rid + '.zip')
        assert status == 200
        with zipfile.ZipFile(io.BytesIO(exported)) as bundle:
            assert 'account_id' not in json.loads(bundle.read('receipt.json'))
    if route.endswith('_folder'):
        status, exported, _ = _srv.request(base, '/api/verify_folder/' + rid)
        assert status == 200 and b'account_id' not in exported
    import renewal
    assert 'account_id' not in renewal.receipt_core(rec)
    assert rid.encode() in request(base,'/api/me/anchors')[1]
    assert rid.encode() not in request(base,'/api/me/anchors',1)[1]


def test_historical_revoked_collision_remains_ambiguous(server):
    base, data = server
    with (data / 'api_keys.jsonl').open('a') as f:
        f.write(json.dumps(dict(event='revoked', email=EMAILS[1],
            key_hash=hashlib.sha256(KEYS[1].encode()).hexdigest())) + '\n')
    assert b'LegacyPublic001' not in request(base, '/api/me/anchors')[1]
    assert request(base, '/api/me/receipt/LegacyPublic001/privacy',
                   body={'private': True})[0] == 404


@pytest.mark.parametrize('private', [False, True])
def test_session_anchor_account_survives_privacy_changes(server, private):
    base, data = server
    status, body, _ = request(base, '/api/anchor', body={
        'hash_hex': 'de' * 32, 'private': private,
        'account_id': account(EMAILS[1]),
    })
    assert status == 200, body
    rid = json.loads(body)['receipt_id']
    recfile = data / 'receipts' / rid / 'receipt.json'
    assert json.loads(recfile.read_text())['account_id'] == account(EMAILS[0])
    assert b'account_id' not in body
    for desired in [True, False, True]:
        assert request(base, '/api/me/receipt/' + rid + '/privacy',
                       body={'private': desired})[0] == 200
        assert json.loads(recfile.read_text())['account_id'] == account(EMAILS[0])
    assert request(base, '/api/receipt/' + rid)[0] == 200
    assert request(base, '/api/receipt/' + rid, 1)[0] == 404


def test_api_key_vault_auth_obeys_same_account_boundary(server):
    base, _ = server
    for who in [0, 1]:
        status, body, _ = _srv.request(base, '/api/me/anchors',
            headers={'X-Orpho-Api-Key': KEYS[who]})
        assert status == 200, body
        assert (b'LegacyPrivate01' in body) is (who == 0)
        assert (b'DurablePublic01' in body) is (who == 0)
        assert b'LegacyPublic001' not in body


def test_invalid_account_identity_never_falls_back_to_source(server):
    base, data = server
    path = data / 'receipts/LegacyRotated01/receipt.json'
    rec = json.loads(path.read_text())
    rec['account_id'] = None
    path.write_text(json.dumps(rec))
    assert b'LegacyRotated01' not in request(base, '/api/me/anchors')[1]
    assert request(base, '/api/me/receipt/LegacyRotated01/privacy',
                   body={'private': True})[0] == 404


def test_unauthenticated_caller_cannot_assign_account(server):
    base, data = server
    status, body, _ = _srv.request(base, '/api/anchor', method='POST',
        headers={'Content-Type': 'application/json'},
        body=json.dumps({'hash_hex': 'ed' * 32,
                         'account_id': account(EMAILS[0])}).encode())
    assert status == 200, body
    rid = json.loads(body)['receipt_id']
    rec = json.loads((data / 'receipts' / rid / 'receipt.json').read_text())
    assert 'account_id' not in rec
    assert rid.encode() not in request(base, '/api/me/anchors')[1]


def _issue(data, email, prefix, ts):
    with (data / 'api_keys.jsonl').open('a') as f:
        f.write(json.dumps(dict(event='issued', email=email, key_hash='h-' + prefix + ts,
                                key_prefix=prefix, ts=ts)) + '\n')


def _legacy(data, rid, created_at, **extra):
    d = data / 'receipts' / rid
    d.mkdir(parents=True)
    rec = dict(receipt_id=rid, created_at=created_at, hash_hex='ab' * 32,
               source='api:orpho_tbnd', private=False, calendars_ok=0, calendars_total=5)
    rec.update(extra)
    (d / 'receipt.json').write_text(json.dumps(rec))


def test_a_key_issued_after_a_legacy_receipt_cannot_take_it(server):
    """Ownership of an api: tag was recomputed from every key ever issued, so
    a later key sharing the 10-char prefix (4 random chars) made it ambiguous
    and the owner lost the receipt, with no way back: the privacy toggle, the
    only thing that writes account_id, answered 404. A key issued after the
    receipt cannot have made it."""
    base, data = server
    _issue(data, EMAILS[0], 'orpho_tbndAAAA', '2026-09-01T00:00:00+00:00')
    _legacy(data, 'TimeBound00001', '2026-09-10T00:00:00+00:00')
    assert b'TimeBound00001' in request(base, '/api/me/anchors')[1]  # control
    _issue(data, EMAILS[1], 'orpho_tbndBBBB', '2026-09-20T00:00:00+00:00')
    assert b'TimeBound00001' in request(base, '/api/me/anchors')[1]
    assert b'TimeBound00001' not in request(base, '/api/me/anchors', 1)[1]
    assert request(base, '/api/me/receipt/TimeBound00001/privacy', 1,
                   {'private': True})[0] == 404
    assert request(base, '/api/me/receipt/TimeBound00001/privacy',
                   body={'private': False})[0] == 200


def test_a_key_issued_before_a_legacy_receipt_still_makes_it_ambiguous(server):
    base, data = server
    _issue(data, EMAILS[0], 'orpho_tbndAAAA', '2026-09-01T00:00:00+00:00')
    _issue(data, EMAILS[1], 'orpho_tbndBBBB', '2026-09-05T00:00:00+00:00')
    _legacy(data, 'TimeBound00002', '2026-09-10T00:00:00+00:00')
    for who in (0, 1):
        assert b'TimeBound00002' not in request(base, '/api/me/anchors', who)[1]


def test_an_unreadable_time_can_only_deny(server):
    base, data = server
    _issue(data, EMAILS[0], 'orpho_tbndAAAA', '2026-09-01T00:00:00+00:00')
    _issue(data, EMAILS[1], 'orpho_tbndBBBB', 'not-a-time')
    _legacy(data, 'TimeBound00003', '2026-09-10T00:00:00+00:00')
    _legacy(data, 'TimeBound00004', 'not-a-time', source='api:orpho_oldA')
    assert b'TimeBound00003' not in request(base, '/api/me/anchors')[1]
    # orpho_oldA has one issuer ever, so an unreadable receipt time still
    # resolves to that one account.
    assert b'TimeBound00004' in request(base, '/api/me/anchors')[1]


def test_recorded_identity_outranks_an_unambiguous_source(server):
    """account_id and a private owner_id decide ownership even when the source
    tag unambiguously names someone else."""
    base, data = server
    _legacy(data, 'PrecedenceAcct1', '2026-09-23T00:00:00Z',
            source='api:orpho_oldA', account_id=account(EMAILS[1]))
    _legacy(data, 'PrecedencePriv1', '2026-09-23T00:00:00Z',
            source='api:orpho_oldA', private=True, owner_id=account(EMAILS[1]))
    for rid in ('PrecedenceAcct1', 'PrecedencePriv1'):
        assert rid.encode() not in request(base, '/api/me/anchors')[1]
        assert rid.encode() in request(base, '/api/me/anchors', 1)[1]


def test_a_pack_anchor_by_a_subscriber_stays_unowned(server):
    base, data = server
    code = 'pk_accountOwnership01'
    (data / 'credit_ledger.jsonl').write_text(json.dumps(dict(
        ts='2026-09-01T00:00:00+00:00', claim_code=code, email=EMAILS[0],
        credits_delta=2, source='test')) + '\n')
    status, body, _ = _srv.request(base, '/api/anchor', method='POST',
        body=json.dumps({'hash_hex': 'fa' * 32}).encode(),
        headers={'Content-Type': 'application/json', 'X-Orpho-Api-Key': KEYS[0],
                 'X-Pack-Token': code}, timeout=30)
    assert status == 200, body
    rid = json.loads(body)['receipt_id']
    rec = json.loads((data / 'receipts' / rid / 'receipt.json').read_text())
    assert rec['source'].startswith('pack:')  # control: the pack paid
    assert 'account_id' not in rec
    assert rid.encode() not in request(base, '/api/me/anchors')[1]


def test_batch_files_the_receipt_under_the_paying_account(server):
    """A lapsed key plus an active session: the session's subscription pays,
    so the session's account owns the receipt, not the lapsed key's."""
    base, data = server
    lapsed_key = 'orpho_lapsedCarolKey123456'
    with (data / 'api_keys.jsonl').open('a') as f:
        f.write(json.dumps(dict(event='issued', email='carol@example.test',
            key_hash=hashlib.sha256(lapsed_key.encode()).hexdigest(),
            key_prefix=lapsed_key[:14])) + '\n')
    status, body, _ = _srv.request(base, '/api/anchor/batch', method='POST',
        body=json.dumps({'hashes': [{'hash_hex': 'fb' * 32}]}).encode(),
        headers={**headers(0), 'X-Orpho-Api-Key': lapsed_key}, timeout=30)
    assert status == 200, body
    rid = json.loads(body)['results'][0]['receipt_id']
    rec = json.loads((data / 'receipts' / rid / 'receipt.json').read_text())
    assert rec['account_id'] == account(EMAILS[0])
    assert rec['source'] == 'sub:' + account(EMAILS[0])
    assert rid.encode() in request(base, '/api/me/anchors')[1]
