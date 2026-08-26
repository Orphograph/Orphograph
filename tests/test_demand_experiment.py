from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_smoke_test_is_hidden_non_transactional_and_has_no_checkout_hook():
    html = (ROOT / "web" / "index.html").read_text()
    js = (ROOT / "web" / "v2.js").read_text()
    assert 'id="demand-pack-v1" hidden' in html
    assert "This is an interest test, not checkout. Nothing will be charged." in html
    assert 'interest: "demand_pack_v1"' in js
    assert 'transactional === false' in js
    block = html.split('id="demand-pack-v1"', 1)[1].split("</form>", 1)[0]
    assert "data-checkout" not in block
    assert "stripe" not in block.lower()


def test_waitlist_has_distinct_experiment_attribution():
    source = (ROOT / "server" / "waitlist.py").read_text()
    assert '"demand_pack_v1"' in source
