"""Every line stripe_webhook writes goes out with Checkout Session ids masked.

A full id answers /api/stripe/session with the buyer's email; the webhook
printed it on ~10 lines ("minted claim_code for session cs_live_...").
"""
from __future__ import annotations

import stripe_webhook


def test_the_webhook_log_sink_masks_session_ids(capsys):
    sid = "cs_live_WebhookSinkCanary0123456789"
    stripe_webhook._stderr(f"[stripe_webhook] minted claim_code for session {sid}\n")
    err = capsys.readouterr().err
    assert sid not in err
    assert "cs_live_…456789" in err, "control: the line was written, masked"


def test_no_line_bypasses_the_sink():
    import inspect
    src = inspect.getsource(stripe_webhook)
    assert src.count("sys.stderr.write(") == 1, "a line writes around the masking sink"
