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
