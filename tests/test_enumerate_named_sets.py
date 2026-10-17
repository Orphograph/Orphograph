"""scripts/enumerate_surface.py could not see a route matched through a
module-level named set (`path in AB_CHECKOUT_PATHS`): the route surface
shrank 91 -> 90 after #269 and the shape oracle stayed green
(@enum-set-literals). The walk now resolves a NAME bound once to a
frozenset/set of string literals; any other NAME fails the shape oracle."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import enumerate_surface as es  # noqa: E402
from _srv import APP  # noqa: E402  (the path only; no server is started here)


def _report():
    app = APP.read_text()
    probes = (ROOT / "scripts" / "all_endpoints_probe.py").read_text()
    return es.routes_report(app, probes, set())


def test_the_pay_crypto_control_is_enumerated_from_the_real_app():
    rep = _report()
    lits = {(e["method"], e["literal"]) for e in rep["elements"]}
    assert ("GET", "/pay/crypto") in lits and ("GET", "/pay/crypto/") in lits
    assert not rep["shape_unaccounted"], rep["shape_unaccounted"]


def test_an_unresolvable_named_set_fails_the_shape_oracle():
    app = APP.read_text()
    broken = app.replace('AB_CHECKOUT_PATHS = frozenset({"/pay/crypto", "/pay/crypto/"})',
                         'AB_CHECKOUT_PATHS = frozenset(_load())', 1)
    assert broken != app
    rep = es.routes_report(broken, (ROOT / "scripts" / "all_endpoints_probe.py").read_text(), set())
    assert any("AB_CHECKOUT_PATHS" in text for _, text in rep["shape_unaccounted"])
    assert not rep["ok"]


def test_the_selftest_passes():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "enumerate_surface.py"),
                          "--selftest"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
