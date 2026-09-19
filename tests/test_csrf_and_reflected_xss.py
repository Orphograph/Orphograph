"""test_csrf_and_reflected_xss.py

Every state-changing POST stays un-forgeable cross-origin, and no query
parameter is reflected into HTML (audit 2026-08-25, backlog item A:
"XSS / CSRF injection points — CSP blocks inline script (verified), but audit
every reflected param and every state-changing POST for a CSRF token").

Result: CLEAN. Both properties hold, and both are pinned here.

CSRF. The site has no CSRF token, and does not need one, because
_reject_non_json_post() is the FIRST statement in do_POST — a universal gate
rather than a per-route opt-in someone can forget on the next endpoint. A
cross-origin form can only send the three CORS-"simple" content types, and all
three are refused with 415 before any handler runs. Measured across 15
state-changing endpoints x 3 simple types: 45/45 refused.

XSS. 234 route x parameter combinations were probed with an executable payload
and NOTHING was reflected into any response body. Combined with the strict CSP
(script-src 'self', no inline), that is defence in depth rather than a single
control.

Both halves carry negative controls, because "no reflections found" and "the
scanner never ran" produce identical output.
"""
from __future__ import annotations

import urllib.parse
from pathlib import Path

import pytest

import _srv

REPO_ROOT = Path(__file__).resolve().parent.parent

# The only content types a cross-origin <form> can send without a preflight.
SIMPLE_CONTENT_TYPES = ["text/plain", "application/x-www-form-urlencoded",
                        "multipart/form-data"]

STATE_CHANGING = [
    "/api/anchor", "/api/anchor/batch", "/api/anchor_folder", "/api/event",
    "/api/auth/email-link", "/api/auth/signout", "/api/me/delete",
    "/api/me/api-key", "/api/me/api-key/revoke", "/api/me/logout-all",
    "/api/me/cancel-subscription", "/api/me/refund-request",
    "/api/inclusion_proof", "/api/founder/admin/toggles",
]
# /api/btc/claim left this list on 2026-09-19: the direct-BTC rail was retired
# and the handler deleted, so the endpoint changes no state to forge. It is not
# simply dropped — tests/test_direct_btc_rail_is_gone.py asserts it answers 410
# to all three CORS-simple content types, which is strictly stronger than the
# 415 this guard requires.

XSS_MARK = '<svg/onload=alert(1)>xSsPrObE"\'`'
XSS_ROUTES = ["/", "/account", "/signin", "/pricing", "/docs", "/verify",
              "/faq", "/learn", "/recover", "/access", "/pack", "/gift",
              "/badge", "/stats", "/certificate", "/anchor-output", "/blog",
              "/method/architecture"]
XSS_PARAMS = ["q", "next", "email", "code", "id", "receipt", "ref", "token",
              "label", "utm_source", "plan", "msg", "error"]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """One server, via the shared helper. See tests/_srv.py for why
    the hand-copied version of this was replaced."""
    data_dir = tmp_path_factory.mktemp("xss_csrf_data")
    yield from _srv.server_processes(data_dir, stub_calendars=True)



def _post(base: str, path: str, ctype: str) -> int:
    body = b'{"hash_hex":"' + b"a" * 64 + b'"}'
    return _srv.request(base, path, "POST", body, {"Content-Type": ctype}, timeout=20)[0]


def _get(base: str, url: str) -> tuple[int, str]:
    """One answered request. A server that is gone raises (_srv.ServerGone);
    nothing here turns "no answer" into an empty, reflection-free body."""
    status, raw, headers = _srv.request(base, url, timeout=20)
    return status, raw.decode("utf-8", "replace") + "\n" + (headers.get("Location") or "")


@pytest.mark.parametrize("path", STATE_CHANGING)
def test_state_changing_post_refuses_cors_simple_content_types(server, path):
    """THE CSRF GUARD. A cross-origin form can only send these three types; all
    three must be refused BEFORE any handler runs, or the endpoint is forgeable
    from any site the victim visits while logged in."""
    for ctype in SIMPLE_CONTENT_TYPES:
        code = _post(server, path, ctype)
        assert code == 415, f"{path} accepted {ctype} with {code} — forgeable cross-origin"


def test_json_is_not_blanket_rejected(server):
    """NEGATIVE CONTROL for the CSRF guard. If every POST returned 415 for any
    reason, the parametrised test above would pass while proving nothing."""
    code = _post(server, "/api/event", "application/json")
    assert code != 415, "application/json was refused — the gate is over-broad, not selective"


def test_no_query_parameter_is_reflected_into_a_response(server):
    """THE XSS GUARD. 234 route x param combinations with an executable payload."""
    reflected, unanswered = [], []
    for route in XSS_ROUTES:
        for p in XSS_PARAMS:
            st, body = _get(server, f"{route}?{p}={urllib.parse.quote(XSS_MARK)}")
            if st != 200:
                # A page that did not render was not swept. Counting it clean
                # is how a renamed route leaves this guard without anyone seeing.
                unanswered.append((route, p, st))
            if "xSsPrObE" in body:
                raw = "<svg/onload" in body
                reflected.append((route, p, "RAW-UNESCAPED" if raw else "escaped"))
    executable = [r for r in reflected if r[2] == "RAW-UNESCAPED"]
    assert not executable, f"reflected XSS: {executable}"
    assert not unanswered, f"swept pages that did not answer 200: {unanswered[:10]}"
    assert not reflected, f"unexpected reflection (escaped, but still new): {reflected}"


def test_the_xss_probe_can_actually_detect_a_reflection(server):
    """NEGATIVE CONTROL for the XSS guard. The marker must be findable when it
    IS echoed — /api/receipt/<id> echoes the requested id. Without this, a
    broken fetch or a renamed marker would report a clean sweep forever."""
    _st, body = _get(server, "/api/receipt/xSsPrObE")
    assert "xSsPrObE" in body, (
        "the probe marker was not detected even where the server echoes it — "
        "the sweep above cannot distinguish 'clean' from 'never ran'"
    )
