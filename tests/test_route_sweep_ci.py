"""The deployed route sweep must fail CI on findings, not just unknown answers."""
import importlib.util
from pathlib import Path
from unittest import mock
import pytest
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('route_sweep', ROOT / 'scripts/route_sweep.py')
rs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rs)

def test_enumeration_controls():
    assert rs.es.selftest() == 0

def test_classifier_controls():
    assert rs.selftest() == 0

@pytest.mark.parametrize('verdict,expected', [('SAFE',0), ('FINDING',1), ('UNKNOWN',1)])
def test_cli_exit_matches_verdict(verdict, expected):
    elements = [dict(method='GET',kind='eq',literal='/api/health',suffix=None)]
    result = {'/api/health':dict(verdict=verdict,why='fixture')}
    with mock.patch.object(rs.es,'enumerate_routes',return_value=dict(ok=True,elements=elements)), \
         mock.patch.object(rs,'sweep',return_value=result), \
         mock.patch.object(rs,'plant_control',return_value=(True,'control caught')):
        assert rs.main(['sweep',str(ROOT),'HEAD','--max-unknown','0']) == expected


def test_interpolated_receipt_probe_remains_in_route_oracle():
    assert rs.es._probe_entries('Probe("receipt", "GET", f"/api/receipt/{SAMPLE_ID}")') == [
        ("GET", "/api/receipt/sweep-probe")]
