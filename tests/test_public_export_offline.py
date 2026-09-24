"""Exercise downloaded verifier kits against the actual HTTP export bytes."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest
import _srv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
import merkle
import renewal
import receipt_export


def _manifest(text, parent=None):
    digest = hashlib.sha256(text).digest()
    leaves = [dict(path='draft.txt', file_sha256_hex=digest.hex(),
                   leaf_hex=merkle._leaf_hash('draft.txt', digest).hex(), size_bytes=len(text))]
    if parent:
        root, rid = parent
        leaves.append(dict(path='.orphograph/parent', file_sha256_hex=root,
                           leaf_hex=merkle._leaf_hash('.orphograph/parent', bytes.fromhex(root)).hex(),
                           size_bytes=0))
    leaves.sort(key=lambda x: x['path'])
    m = dict(algorithm=merkle.ALGORITHM, version=merkle.VERSION, leaves=leaves,
             root_hex=merkle._build_levels([bytes.fromhex(x['leaf_hex']) for x in leaves])[-1][0].hex())
    if parent:
        m['parent'] = dict(root_hex=parent[0], receipt_id=parent[1])
    return m


def _cli(script, *args):
    return subprocess.run([sys.executable, str(script), *map(str, args)],
                          cwd=script.parent, capture_output=True, text=True, timeout=30)


def _export(base, rid, dest):
    status, body, _ = _srv.request(base, f'/api/receipt/{rid}.zip')
    assert status == 200
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        z.extractall(dest)


@pytest.fixture()
def bundles(tmp_path):
    data = tmp_path / 'data'; data.mkdir()
    kit = tmp_path / 'kit'; kit.mkdir()
    with zipfile.ZipFile(ROOT / 'web/dist/orphograph-verify.zip') as z:
        z.extractall(kit)
    simple = tmp_path / 'simple'; simple.mkdir()
    with tarfile.open(ROOT / 'web/verify/orphograph-verify-0.1.tar.gz') as t:
        t.extractall(simple, filter='data')
    simple_cli = next(simple.rglob('verify.py'))
    chain = tmp_path / 'chain'; chain.mkdir()
    with_server = _srv.server_processes(data, stub_calendars=True)
    for base in with_server:
        ids = []
        for content in [b'first draft', b'second draft']:
            m = _manifest(content, (root, ids[-1]) if ids else None)
            status, body, _ = _srv.request(base, '/api/anchor_folder', method='POST',
                body=json.dumps(m).encode(), headers={'Content-Type': 'application/json', 'User-Agent': 'uptime-check/1.0'})
            assert status == 200, body
            result = json.loads(body); rid = result['receipt_id']; root = result['root_hex']
            ids.append(rid)
            _export(base, rid, chain / rid)
        yield base, data, kit, simple_cli, chain, ids, tmp_path


def test_downloaded_lineage_checks_redacted_exports_and_rejects_tampering(bundles):
    _, _, kit, _, chain, ids, _ = bundles
    cli = kit / 'verify_lineage.py'
    assert cli.is_file(), 'download must include the lineage verifier'
    out = _cli(cli, '--chain', chain, '--tip', ids[-1])
    assert out.returncode == 0, out.stdout + out.stderr
    assert '[NOTE]' in out.stdout and 'completeness is unproven' in out.stdout
    child = chain / ids[-1] / 'manifest.json'
    m = json.loads(child.read_text()); m['leaves'][-1]['leaf_hex'] = '00' * 32
    child.write_text(json.dumps(m))
    out = _cli(cli, '--chain', chain, '--tip', ids[-1])
    assert out.returncode == 3, out.stdout + out.stderr


def test_hiding_parent_path_does_not_turn_child_into_genesis(bundles):
    _, _, kit, _, chain, ids, _ = bundles
    child = chain / ids[-1]
    m = json.loads((child / 'manifest.json').read_text())
    for leaf in m['leaves']:
        leaf.pop('path', None)
    m.pop('parent', None)
    (child / 'manifest.json').write_text(json.dumps(m))
    r = json.loads((child / 'receipt.json').read_text()); r.pop('lineage', None)
    (child / 'receipt.json').write_text(json.dumps(r))
    shutil.rmtree(chain / ids[0])
    out = _cli(kit / 'verify_lineage.py', '--chain', chain, '--tip', ids[-1])
    assert out.returncode == 5, out.stdout + out.stderr


def test_downloaded_folder_verifier_accepts_redacted_manifest(bundles):
    _, _, kit, _, chain, ids, tmp = bundles
    folder = tmp / 'original'; folder.mkdir(); (folder / 'draft.txt').write_bytes(b'first draft')
    out = _cli(kit / 'verify.py', 'folder', '--dir', folder,
               '--manifest', chain / ids[0] / 'manifest.json')
    assert out.returncode == 0, out.stdout + out.stderr


def test_downloaded_single_and_renewal_verifiers_accept_export(bundles):
    base, data, kit, simple, _, _, tmp = bundles
    content = b'single-file export'
    status, body, _ = _srv.request(base, '/api/anchor', method='POST',
        body=json.dumps({'hash_hex': hashlib.sha256(content).hexdigest()}).encode(),
        headers={'Content-Type': 'application/json', 'User-Agent': 'uptime-check/1.0'})
    assert status == 200, body
    rid = json.loads(body)['receipt_id']; d = data / 'receipts' / rid
    record = json.loads((d / 'receipt.json').read_text())
    record['notify_email'] = 'private@example.test'; record['source'] = 'sub:opaque-test-owner'
    (d / 'receipt.json').write_text(json.dumps(record))
    rr = renewal.build_record(record, 1, '2026-09-23T00:00:00Z')
    _, proofs = renewal.build_batch([rr]); rr['batch'] = proofs[rid]
    (d / 'renewal').mkdir(); (d / 'renewal/001.json').write_text(json.dumps(rr))
    exported = tmp / 'single-export'; _export(base, rid, exported)
    assert 'notify_email' not in json.loads((exported / 'receipt.json').read_text())
    original = tmp / 'file.txt'; original.write_bytes(content)
    out = _cli(simple, exported / 'receipt.json', '--file', original)
    assert out.returncode == 0, out.stdout + out.stderr
    out = _cli(kit / 'verify_renewal.py', exported / 'receipt.json')
    assert out.returncode == 0, out.stdout + out.stderr


def test_export_allowlist_covers_renewal_core():
    assert set(renewal.CORE_ALWAYS) <= receipt_export.EXPORT_FIELDS | {'source'}
    assert set(renewal.CORE_IF_PRESENT) <= receipt_export.EXPORT_FIELDS


def test_unredacted_manifest_preserves_renewal_committed_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(receipt_export, 'RECEIPTS_DIR', tmp_path)
    rid = 'RenewedFolder01'; d = tmp_path / rid; d.mkdir()
    m = _manifest(b'draft')
    raw = json.dumps(m, indent=4).encode()
    (d / 'manifest.json').write_bytes(raw)
    (d / 'receipt.json').write_text(json.dumps({'receipt_id': rid, 'paths_public': True}))
    blob, err = receipt_export.export_zip(rid)
    assert err is None
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert z.read('manifest.json') == raw


@pytest.mark.parametrize("rid", ["../outside", "/tmp/outside", "..", "x/../../outside", "x" * 65])
def test_export_boundary_rejects_unsafe_ids(tmp_path, monkeypatch, rid):
    # The neighboring record exists so traversing it cannot pass as not-found.
    receipts = tmp_path / 'receipts'; receipts.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'receipt.json').write_text('{"receipt_id":"outside"}')
    monkeypatch.setattr(receipt_export, 'RECEIPTS_DIR', receipts)
    assert receipt_export.export_zip(rid) == (None, receipt_export.NOT_FOUND)
    assert receipt_export.export_readable_json(rid) == (None, receipt_export.NOT_FOUND)


def test_export_boundary_rejects_receipt_symlink_escape(tmp_path, monkeypatch):
    receipts = tmp_path / 'receipts'; receipts.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'receipt.json').write_text('{"receipt_id":"outside"}')
    (receipts / 'LinkedReceipt01').symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(receipt_export, 'RECEIPTS_DIR', receipts)
    assert receipt_export.export_zip('LinkedReceipt01') == (None, receipt_export.NOT_FOUND)
    assert receipt_export.export_readable_json('LinkedReceipt01') == (None, receipt_export.NOT_FOUND)
