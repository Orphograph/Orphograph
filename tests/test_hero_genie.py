"""Mathematical regression tests for the real continuous genie renderer.

Run its exported pure geometry through Node, not a reimplementation. The entire
sheet shares one map: no inverted rows, cracks, lost endpoints or route-specific
opening/closing deformations.
"""
import json
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def geometry():
    js = r'''
const {point} = require('./web/hero-envelope.js');
const target = {left:32,top:18,width:336,height:720};
const mouth = {x:210,y:590,width:48};
const frames = [];
for (let i=0;i<=100;i++) frames.push(Array.from({length:201},(_,j)=>point(i/100,j/200,target,mouth)));
console.log(JSON.stringify({target,mouth,frames,negative:point(-1,.5,target,mouth),past:point(2,.5,target,mouth)}));
'''
    return json.loads(subprocess.check_output(['node', '-e', js], cwd=ROOT, text=True))

def test_closed_surface_collapses_to_one_mouth(geometry):
    for row in geometry['frames'][0]:
        assert row == {'x':186, 'y':590, 'width':48}

def test_open_surface_exactly_matches_real_receipt_rectangle(geometry):
    for i,row in enumerate(geometry['frames'][-1]):
        assert row['x'] == pytest.approx(32)
        assert row['width'] == pytest.approx(336)
        assert row['y'] == pytest.approx(18+720*i/200)

def test_all_intermediate_rows_remain_ordered_without_folds(geometry):
    for frame in geometry['frames'][1:]:
        assert all(b['y'] > a['y'] for a,b in zip(frame,frame[1:]))
        assert all(48 <= row['width'] <=336 for row in frame)
        # Shared vertical mapping has equal adjacent spans, rather than
        # independent strip timing that causes overlap and blank rows.
        spans=[b['y']-a['y'] for a,b in zip(frame,frame[1:])]
        assert max(spans)-min(spans) < 1e-9

def test_midflight_has_a_wide_head_and_narrow_neck(geometry):
    frame=geometry['frames'][50]
    assert frame[0]['width'] > frame[-1]['width']*3
    assert frame[-1]['y'] == pytest.approx(590,abs=2)
    assert frame[0]['y'] < 320

def test_progress_clamps_to_exact_endpoints(geometry):
    assert geometry['negative']==geometry['frames'][0][100]
    assert geometry['past']==geometry['frames'][-1][100]

def test_rapid_reversal_continues_from_current_position():
    js = r"""
const assert=require('node:assert/strict');
const {advance}=require('./web/hero-envelope.js');
let p=0;
for(let i=0;i<21;i++)p=advance(p,1,20);
assert.ok(Math.abs(p-.4)<1e-12);
const reversal=advance(p,0,20);
assert.ok(reversal<p && reversal>.38);
assert.ok(Math.abs(advance(reversal,1,20)-p)<1e-12);
for(let i=0;i<100;i++)p=advance(p,0,20);
assert.equal(p,0);
for(let i=0;i<100;i++)p=advance(p,1,20);
assert.equal(p,1);
// A suspended tab cannot jump through the whole transition on resume.
assert.ok(advance(0,1,10000)<.04);
"""
    subprocess.run(['node','-e',js],cwd=ROOT,check=True)
