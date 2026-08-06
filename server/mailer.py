#!/usr/bin/env python3
"""email.py — outbound email via Resend HTTP API (stdlib urllib).

Inert if RESEND_API_KEY is not set: logs intent to stderr, returns False.
This keeps the rest of the system functional during dev/test without
wiring a real email account.

Public API:
    send_pack_claim_email(to, claim_code, credit_count) -> bool
    send_receipt_email(to, receipt_record) -> bool
    send_integration_email(to, receipt_record) -> bool

Note on send_integration_email: this module only builds and sends the
message via the existing transport. Dispatch (the decision to call it,
one day after issuance) is wired at the call site behind the env flag
ORPHO_INTEGRATION_EMAIL — the function itself performs no scheduling
and no gating beyond the transport's own suppression checks.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Local import — auth.mask_email avoids dumping plaintext addresses
# into stderr when the mailer is in inert (dev) mode.
sys.path.insert(0, os.path.dirname(__file__))
import auth as _auth
try:
    import receipt_pdf as _receipt_pdf
except Exception:
    # Import is best-effort: a PDF generation failure must never block
    # the underlying email — the inbox-side experience degrades to text
    # only, which is still useful.
    _receipt_pdf = None

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "Orphograph <hello@orphograph.com>")
SITE_URL = os.environ.get("SITE_URL", "https://orphograph.com")
HTTP_TIMEOUT = 10

# Compliance constants — read by env so the founder can set them once.
#   ORPHO_BUSINESS_ADDRESS: physical postal address required by CAN-SPAM
#                            (US 15 USC § 7704) and EU GDPR transparency.
#                            Use a PO box if you don't want your home
#                            on every email (iPostal/USPS ~$10/mo).
#   ORPHO_BUSINESS_ENTITY:   legal entity name. Defaults to "Orphograph"
#                            but should be the actual LLC name once registered.
BUSINESS_ENTITY = os.environ.get("ORPHO_BUSINESS_ENTITY", "Orphograph")
BUSINESS_ADDRESS = os.environ.get(
    "ORPHO_BUSINESS_ADDRESS",
    "Address available on request — legal@orphograph.com",
)


def _header_html() -> str:
    """Branded header rendered above every transactional email body.

    Includes the seal image as the FIRST element so Gmail/Outlook inbox
    preview tiles render the logo (those clients pick the first inline
    image as the preview). Inline styles only — Gmail/Outlook strip
    <style> blocks. The seal is served from the production origin so the
    image loads identically in all clients; absolute URL is required —
    relative paths in email do not resolve.
    """
    seal_url = f"{SITE_URL}/seal.png"
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%;max-width:560px;margin:0 auto 18px;border-collapse:collapse;">'
        '<tr><td style="padding:18px 0 14px;text-align:left;'
        'font-family:Georgia,\'Times New Roman\',serif;vertical-align:middle;">'
        f'<a href="{SITE_URL}" style="text-decoration:none;color:#1a1a1a;'
        'display:inline-block;">'
        f'<img src="{seal_url}" alt="Orphograph" width="56" height="56" '
        'style="display:inline-block;vertical-align:middle;margin-right:14px;'
        'border:0;outline:none;text-decoration:none;height:56px;width:56px;">'
        '<span style="display:inline-block;vertical-align:middle;">'
        '<span style="display:block;font-size:22px;letter-spacing:0.06em;'
        'font-weight:500;color:#1a1a1a;">Orphograph</span>'
        '<span style="display:block;font-size:11px;color:#837e75;'
        'letter-spacing:0.18em;text-transform:uppercase;margin-top:2px;">'
        'Empirical Notary</span>'
        '</span>'
        '</a>'
        '</td></tr>'
        '<tr><td style="border-top:1px solid #e5dfd0;height:1px;line-height:1px;"></td></tr>'
        '</table>'
    )


def _wrap_html_body(inner: str) -> str:
    return (
        '<div style="font-family:Georgia,\'Times New Roman\',serif;'
        'max-width:560px;margin:0 auto;padding:0 20px;'
        'line-height:1.6;color:#2a2a2a;font-size:15px;">'
        + _header_html()
        + inner
        + '</div>'
    )


def _footer_text(to_email: str, transactional: bool) -> str:
    """Compliant plain-text footer.

    - Transactional emails (receipts, sign-in links, pack codes): CAN-SPAM
      §7704(a)(5)(A) exempts these from unsubscribe + physical-address
      requirements because they are tied to a transaction the recipient
      initiated. We INCLUDE the entity name + privacy/terms links for
      GDPR Art. 13 transparency, but NOT the postal address — keeping the
      founder's PMB out of every single email reduces unnecessary exposure.

    - Marketing/promotional emails: CAN-SPAM requires both physical address
      AND functional unsubscribe. We include both, no exceptions. The
      address is required by law; it cannot be omitted for marketing.
    """
    lines = [
        "",
        "—",
        f"{BUSINESS_ENTITY} · {SITE_URL}",
        f"Privacy: {SITE_URL}/privacy.html · Terms: {SITE_URL}/terms.html",
    ]
    if not transactional:
        # Legal requirement for commercial email — physical address.
        lines.insert(2, f"{BUSINESS_ADDRESS}")
        unsub = f"{SITE_URL}/api/unsubscribe?e={urllib.parse.quote(to_email)}"
        lines.append(f"Unsubscribe instantly: {unsub}")
    return "\n".join(lines) + "\n"


def _footer_html(to_email: str, transactional: bool) -> str:
    parts = [
        '<hr style="border:0;border-top:1px solid #e5dfd0;margin:24px 0 12px;">',
        '<p style="font-size:12px;color:#837e75;line-height:1.5;">',
        f'{BUSINESS_ENTITY} · <a href="{SITE_URL}" style="color:#837e75;">{SITE_URL}</a>',
    ]
    if not transactional:
        # CAN-SPAM-mandatory physical address (commercial email only).
        parts.append(f'<br>{BUSINESS_ADDRESS}')
    parts.append(
        f'<br><a href="{SITE_URL}/privacy.html" style="color:#4a9a73;">Privacy</a> · '
        f'<a href="{SITE_URL}/terms.html" style="color:#4a9a73;">Terms</a>'
    )
    if not transactional:
        unsub = f"{SITE_URL}/api/unsubscribe?e={urllib.parse.quote(to_email)}"
        parts.append(
            f'<br><a href="{unsub}" style="color:#837e75;text-decoration:underline;">'
            f'Unsubscribe from all marketing email</a>'
        )
    parts.append("</p>")
    return "".join(parts)


def _send(to: str, subject: str, text: str, html: str,
          transactional: bool = True, category: str = "transactional",
          attachments: list | None = None) -> bool:
    """Send via Resend with compliant footer + RFC 8058 List-Unsubscribe.

    Gmail + Yahoo Feb-2024 bulk-sender rules require:
      • SPF + DKIM + DMARC aligned (handled at DNS, via setup_email.py)
      • List-Unsubscribe and List-Unsubscribe-Post headers on all bulk mail
      • One-click unsubscribe for marketing
    Transactional mail is exempt from unsubscribe but still benefits from
    DKIM-aligned signing and a clean reply-to.
    """
    # Suppression gate: never email an address that hard-bounced or filed a
    # spam complaint (recorded by the Resend webhook). Repeatedly mailing a
    # suppressed address tanks sender reputation and can get the domain blocked.
    # Best-effort + lazy import so a webhook-module issue can never break sending.
    try:
        import resend_webhook as _rw  # type: ignore
        if _rw.is_suppressed(to):
            sys.stderr.write(
                f"[email:suppressed] skipping to={_auth.mask_email(to)} "
                f"subject={subject!r} (address on bounce/complaint suppression list)\n"
            )
            return False
    except Exception:  # noqa: BLE001 — suppression check must never block sending
        pass

    text_out = text + _footer_text(to, transactional)
    html_out = _wrap_html_body(html + _footer_html(to, transactional))

    if not RESEND_API_KEY:
        sys.stderr.write(
            f"[email:inert] would send to={_auth.mask_email(to)} subject={subject!r} category={category} (RESEND_API_KEY unset)\n"
        )
        return False

    headers_extra: dict[str, str] = {}
    if not transactional:
        unsub_url = f"{SITE_URL}/api/unsubscribe?e={urllib.parse.quote(to)}"
        # RFC 2369 + RFC 8058 — one-click unsubscribe Gmail/Yahoo require.
        headers_extra["List-Unsubscribe"] = f"<{unsub_url}>"
        headers_extra["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    payload = {
        "from": RESEND_FROM,
        "to": [to],
        "subject": subject,
        "text": text_out,
        "html": html_out,
        "tags": [{"name": "category", "value": category}],
    }
    if headers_extra:
        payload["headers"] = headers_extra
    if attachments:
        # Resend accepts base64-encoded `content` per attachment plus a
        # filename. Skip silently on malformed entries — better to ship
        # the email without the attachment than to fail the whole send.
        out_atts: list[dict] = []
        for a in attachments:
            try:
                raw = a.get("content")
                fname = a.get("filename") or "attachment"
                if isinstance(raw, (bytes, bytearray)) and raw:
                    out_atts.append({
                        "filename": fname,
                        "content": base64.b64encode(raw).decode("ascii"),
                    })
            except Exception:
                continue
        if out_atts:
            payload["attachments"] = out_atts
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # api.resend.com sits behind Cloudflare; the default Python
            # urllib UA is blocked as a bot (CF error 1010), which is why
            # every transactional send returned 403 even though external
            # curl probes with the same payload succeeded. A browser-shaped
            # UA + Accept-Encoding=identity (urllib does not auto-decompress
            # gzip — same gotcha as nowpayments_api) makes the request
            # indistinguishable from a normal API client.
            "User-Agent": "Mozilla/5.0 (compatible; OrphographMailer/0.1; +https://orphograph.com)",
            "Accept-Encoding": "identity",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    # Retry on transient Resend failures (e.g. Cloudflare flicker, DNS
    # blip, 5xx). Resend's API is normally fast — retries are bounded by
    # short timeouts. If all retries fail, append to the manual-
    # fulfillment queue so the founder sees it and can re-send manually;
    # the customer's claim code / receipt is preserved regardless.
    import time as _time
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                resp.read()
            return True
        except urllib.error.HTTPError as e:
            body_snip = ""
            try:
                body_snip = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                pass
            last_err = f"HTTPError {e.code} body={body_snip}"
            # 4xx (other than 429) is not retryable; bail immediately.
            if 400 <= e.code < 500 and e.code != 429:
                break
        except (urllib.error.URLError, OSError) as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < 2:
            _time.sleep(2 ** attempt)  # 1s, 2s
    # All retries exhausted — log + queue for manual founder attention.
    sys.stderr.write(
        f"[email:error] FINAL to={_auth.mask_email(to)} category={category} "
        f"last_err={last_err}\n"
    )
    try:
        from pathlib import Path as _Path
        q = _Path(os.environ.get(
            "ORPHO_MANUAL_FULFILL_QUEUE",
            str(_Path(__file__).resolve().parent.parent / "data" / "manual_fulfillment_queue.jsonl"),
        ))
        q.parent.mkdir(parents=True, exist_ok=True)
        with q.open("a") as f:
            f.write(json.dumps({
                "ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds"),
                "to_masked": _auth.mask_email(to),
                "to": to,
                "subject": subject,
                "category": category,
                "last_err": last_err,
            }, separators=(",", ":")) + "\n")
    except Exception:
        pass
    return False


def send_pack_claim_email(to: str, claim_code: str, credit_count: int) -> bool:
    # URL fragment, not query string: fragments never reach the server,
    # so the bearer claim code does not appear in access logs / referer headers.
    activate_url = f"{SITE_URL}/#pack={claim_code}"
    ref_code = "ref_" + claim_code[3:15] if claim_code.startswith("pk_") else ""
    referral_url = f"{SITE_URL}/?ref={ref_code}" if ref_code else ""
    subject = f"Orphograph — Pack of {credit_count} registered to your name"
    text = (
        f"Receipt of payment is acknowledged. A Pack of {credit_count} "
        f"anchors has been registered against the address on file.\n\n"
        f"Claim code:  {claim_code}\n"
        f"Redemption:  {activate_url}\n\n"
        f"Activation stores the code in the browser of your choosing; "
        f"any number of files may then be anchored against this Pack "
        f"until the credits are spent. Credits do not expire.\n\n"
        f"Please retain this notice. The claim code is the sole instrument "
        f"of recovery for the Pack.\n"
    )
    if ref_code:
        text += (
            f"\nReferral instrument\n"
            f"The bearer of the following address receives ten complimentary "
            f"anchors upon purchasing a Pack; an equivalent ten will be added "
            f"to the code above on the same occasion:\n"
            f"{referral_url}\n"
        )
    html = (
        f"<p>Receipt of payment is acknowledged. A Pack of "
        f"<strong>{credit_count} anchors</strong> has been registered against "
        f"the address on file.</p>"
        f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" "
        f"style=\"margin:18px 0;border-collapse:collapse;\">"
        f"<tr><td style=\"padding:4px 14px 4px 0;color:#666;font-size:13px;\">Claim code</td>"
        f"<td style=\"font-family:Menlo,Consolas,monospace;color:#1a1a1a;\"><code>{claim_code}</code></td></tr>"
        f"<tr><td style=\"padding:4px 14px 4px 0;color:#666;font-size:13px;\">Redemption</td>"
        f"<td><a href=\"{activate_url}\">Activate</a></td></tr>"
        f"</table>"
        f"<p style=\"color:#444;\">Activation stores the code in the browser "
        f"of your choosing; any number of files may then be anchored against "
        f"this Pack until the credits are spent. Credits do not expire.</p>"
        f"<p style=\"color:#666;font-size:13px;font-style:italic;\">"
        f"Please retain this notice. The claim code is the sole instrument "
        f"of recovery for the Pack.</p>"
    )
    if ref_code:
        html += (
            f"<hr style=\"border:0;border-top:1px solid #e5dfd0;margin:20px 0;\">"
            f"<p style=\"font-size:13px;color:#444;\"><strong>Referral instrument.</strong> "
            f"The bearer of the address below receives ten complimentary anchors "
            f"upon purchasing a Pack; an equivalent ten will be added to the "
            f"code above on the same occasion.<br>"
            f"<a href=\"{referral_url}\">{referral_url}</a></p>"
        )
    return _send(to, subject, text, html)


def send_pack_gift_email(
    to: str,
    from_email: str,
    claim_code: str,
    credit_count: int,
    message: str = "",
) -> bool:
    """Send a gift Pack email to the recipient. Includes the buyer's optional
    message and a "Hi from <buyer>" line so the recipient knows who sent it.

    The recipient receives the same claim code mechanism as a self-buy; they
    can activate it in one click. No account is required.
    """
    activate_url = f"{SITE_URL}/#pack={claim_code}"
    # Display name of the giver: just the local part of the email for
    # privacy (we don't have a registered display name yet).
    giver = (from_email or "").split("@", 1)[0] or "a friend"

    def _html_escape(s: str) -> str:
        return (
            s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")
        )

    giver_html = _html_escape(giver)
    from_email_html = _html_escape(from_email or "")

    subject = f"{giver} gifted you an Orphograph Pack ({credit_count} anchors)"
    text = (
        f"Good news — {giver} ({from_email}) gifted you an Orphograph Pack.\n\n"
        f"Claim code: {claim_code}\n"
        f"Activate it (one click): {activate_url}\n\n"
        f"You have {credit_count} anchors to use. They never expire.\n\n"
    )
    if message:
        text += f"Message from {giver}:\n{message}\n\n"
    text += (
        f"What this is: Orphograph anchors a file's SHA-256 hash to the Bitcoin\n"
        f"blockchain via OpenTimestamps. Your file never uploads — only the hash.\n"
        f"The receipt proves the file existed on the date it was anchored.\n\n"
        f"Site: {SITE_URL}\n"
    )
    html = (
        f"<p>Good news — <strong>{giver_html}</strong> ({from_email_html}) gifted you an "
        f"Orphograph Pack.</p>"
        f"<p><strong>Claim code:</strong> <code>{claim_code}</code></p>"
        f"<p><a href=\"{activate_url}\">Activate it (one click)</a></p>"
        f"<p>You have {credit_count} anchors to use. They never expire.</p>"
    )
    if message:
        safe_msg = _html_escape(message)
        html += f"<blockquote><em>Message from {giver_html}:</em><br>{safe_msg}</blockquote>"
    html += (
        f"<hr><p><small>What this is: Orphograph anchors a file's SHA-256 hash "
        f"to the Bitcoin blockchain. Your file never uploads — only the hash. "
        f"The receipt proves the file existed on the date anchored.</small></p>"
        f"<p><small><a href=\"{SITE_URL}\">{SITE_URL}</a></small></p>"
    )
    return _send(to, subject, text, html)


def send_subscription_welcome_email(to: str, plan_label: str = "Standing Order",
                                    signin_token: str = "") -> bool:
    """Fires once when a Stripe subscription checkout completes."""
    if signin_token:
        signin_url = f"{SITE_URL}/a/{signin_token}?next=/account.html"
    else:
        signin_url = f"{SITE_URL}/signin.html"
    subject = f"Orphograph — {plan_label} commenced"
    text = (
        f"Receipt of payment is acknowledged. The {plan_label} "
        f"subscription is now in good standing under the address on file.\n\n"
        f"Access is conferred by signed-in session; no claim code is issued, "
        f"as none is required. The single instrument below confers session "
        f"and lands directly on the account ledger:\n\n"
        f"  {signin_url}\n\n"
        f"The instrument is valid for twenty-four hours from issuance and "
        f"is consumed upon first use.\n\n"
        f"Anchoring is unrestricted for the duration of the period. Each "
        f"receipt is preserved in the account vault and may be exported in "
        f"its entirety at any time.\n\n"
        f"Correspondence regarding this account may be addressed to this "
        f"thread by reply.\n"
    )
    html = (
        f"<h2 style=\"font-family:Georgia,'Times New Roman',serif;margin:0 0 14px;"
        f"color:#1a1a1a;font-weight:500;font-size:22px;letter-spacing:0.01em;\">"
        f"Subscription commenced.</h2>"
        f"<p>Receipt of payment is acknowledged. The "
        f"<strong>{plan_label}</strong> subscription is now in good standing "
        f"under the address on file.</p>"
        f"<p>Access is conferred by signed-in session; no claim code is "
        f"issued, as none is required.</p>"
        f"<p style=\"margin:28px 0;\">"
        f"<a href=\"{signin_url}\" "
        f"style=\"display:inline-block;background:#1a1a1a;color:#f5efe0;"
        f"padding:14px 26px;text-decoration:none;"
        f"font-family:Georgia,'Times New Roman',serif;font-size:15px;"
        f"letter-spacing:0.03em;\">"
        f"Open the account ledger →</a></p>"
        f"<p style=\"color:#666;font-size:13px;font-style:italic;\">"
        f"Instrument valid for twenty-four hours from issuance; consumed upon "
        f"first use.</p>"
        f"<p style=\"margin-top:18px;color:#444;\">Anchoring is unrestricted "
        f"for the duration of the period. Each receipt is preserved in the "
        f"account vault and may be exported in its entirety at any time.</p>"
        f"<p style=\"color:#666;font-size:13px;\">Correspondence regarding "
        f"this account may be addressed to this thread by reply.</p>"
    )
    return _send(to, subject, text, html, transactional=True, category="subscription_welcome")


def send_login_link_email(to: str, token: str) -> bool:
    login_url = f"{SITE_URL}/a/{token}"
    subject = "Orphograph — Sign-in instrument"
    text = (
        f"An instrument of access has been issued at your request:\n\n"
        f"  {login_url}\n\n"
        f"Valid for twenty-four hours, consumed upon first use.\n\n"
        f"If this request was not made by you, the instrument may be "
        f"ignored; no action is taken on the account until the instrument "
        f"is followed.\n"
    )
    html = (
        f"<p>An instrument of access has been issued at your request.</p>"
        f"<p style=\"margin:28px 0;\">"
        f"<a href=\"{login_url}\" "
        f"style=\"display:inline-block;background:#1a1a1a;color:#f5efe0;"
        f"padding:14px 26px;text-decoration:none;"
        f"font-family:Georgia,'Times New Roman',serif;font-size:15px;"
        f"letter-spacing:0.03em;\">"
        f"Open the account ledger →</a></p>"
        f"<p style=\"color:#666;font-size:13px;font-style:italic;\">"
        f"Valid for twenty-four hours, consumed upon first use.</p>"
        f"<p style=\"color:#444;font-size:14px;margin-top:18px;\">"
        f"If this request was not made by you, the instrument may be "
        f"ignored; no action is taken on the account until the instrument "
        f"is followed.</p>"
    )
    return _send(to, subject, text, html)


def send_pin_email(to: str, receipt: dict) -> bool:
    """Notify a customer that their receipt has been Bitcoin-anchored.

    Fires once, from the upgrade worker, when btc_pinned_at first gets set
    on a record that has a notify_email. Honest-framing rule: if not all
    calendars confirmed, the body says "N of M calendars confirmed" rather
    than claiming a clean pin. Transactional only — no marketing CTA.
    """
    rid = receipt.get("receipt_id", "")
    btc_pinned_at = receipt.get("btc_pinned_at", "")
    pinned_count = int(receipt.get("pinned_count", receipt.get("calendars_ok", 0)))
    total = int(receipt.get("pinned_total", receipt.get("calendars_total", 0)))
    receipt_url = f"{SITE_URL}/r/{rid}"
    subject = f"Orphograph — Receipt {rid} committed to Bitcoin"
    text = (
        f"The instrument is now committed to the Bitcoin chain.\n\n"
        f"  Receipt           {rid}\n"
        f"  Pin observed (UTC)  {btc_pinned_at}\n"
        f"  Calendars         {pinned_count} of {total} confirmed\n\n"
        f"  Full receipt      {receipt_url}\n\n"
        f"From this point forward the receipt verifies against the chain "
        f"without further reference to this office. The original file "
        f"and this notice together constitute the complete evidentiary set.\n"
    )
    html = (
        f"<h2 style=\"font-family:Georgia,'Times New Roman',serif;margin:0 0 14px;"
        f"color:#1a1a1a;font-weight:500;font-size:20px;letter-spacing:0.01em;\">"
        f"Receipt committed to Bitcoin.</h2>"
        f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" "
        f"style=\"margin:18px 0;border-collapse:collapse;font-size:14px;\">"
        f"<tr><td style=\"padding:4px 14px 4px 0;color:#666;\">Receipt</td>"
        f"<td style=\"font-family:Menlo,Consolas,monospace;color:#1a1a1a;\"><code>{rid}</code></td></tr>"
        f"<tr><td style=\"padding:4px 14px 4px 0;color:#666;\">Pin observed (UTC)</td>"
        f"<td>{btc_pinned_at}</td></tr>"
        f"<tr><td style=\"padding:4px 14px 4px 0;color:#666;\">Calendars</td>"
        f"<td>{pinned_count} of {total} confirmed</td></tr>"
        f"</table>"
        f"<p style=\"margin:22px 0;\">"
        f"<a href=\"{receipt_url}\" "
        f"style=\"display:inline-block;background:#1a1a1a;color:#f5efe0;"
        f"padding:12px 22px;text-decoration:none;"
        f"font-family:Georgia,'Times New Roman',serif;font-size:14px;"
        f"letter-spacing:0.03em;\">"
        f"View the full receipt →</a></p>"
        f"<p style=\"color:#444;font-size:14px;\">From this point forward "
        f"the receipt verifies against the chain without further reference "
        f"to this office. The original file and this notice together "
        f"constitute the complete evidentiary set.</p>"
    )
    attachments = None
    if _receipt_pdf is not None:
        try:
            pdf_bytes = _receipt_pdf.render_receipt_pdf(receipt, SITE_URL)
            attachments = [{"filename": f"orphograph-receipt-{rid}.pdf", "content": pdf_bytes}]
        except Exception as e:
            sys.stderr.write(f"[email] pdf-attach skipped for {rid}: {type(e).__name__}\n")
    return _send(to, subject, text, html, transactional=True, category="pin_notification",
                 attachments=attachments)


def send_receipt_email(to: str, receipt: dict) -> bool:
    rid = receipt.get("receipt_id", "")
    created_at = receipt.get("created_at", "")
    hash_hex = receipt.get("hash_hex", "")
    cal_ok = receipt.get("calendars_ok", 0)
    cal_total = receipt.get("calendars_total", 0)
    receipt_url = f"{SITE_URL}/r/{rid}"
    subject = f"Orphograph — Receipt {rid} issued"
    text = (
        f"The instrument has been registered. Calendar attestations are "
        f"complete; commitment to Bitcoin typically confirms within a few "
        f"hours, once the calendars' aggregation batch is written on-chain.\n\n"
        f"  Receipt          {rid}\n"
        f"  SHA-256          {hash_hex}\n"
        f"  Registered (UTC) {created_at}\n"
        f"  Calendars        {cal_ok} of {cal_total} attesting\n\n"
        f"  Full receipt     {receipt_url}\n\n"
        f"A second notice will be issued upon Bitcoin commitment. Until "
        f"then, this receipt and the original file together comprise the "
        f"evidentiary set; please retain both.\n"
    )
    html = (
        f"<h2 style=\"font-family:Georgia,'Times New Roman',serif;margin:0 0 14px;"
        f"color:#1a1a1a;font-weight:500;font-size:20px;letter-spacing:0.01em;\">"
        f"Receipt issued.</h2>"
        f"<p style=\"color:#444;\">Calendar attestations are complete; "
        f"commitment to Bitcoin typically confirms within a few hours, "
        f"once the calendars&rsquo; aggregation batch is written on-chain.</p>"
        f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" "
        f"style=\"margin:18px 0;border-collapse:collapse;font-size:14px;\">"
        f"<tr><td style=\"padding:4px 14px 4px 0;color:#666;vertical-align:top;\">Receipt</td>"
        f"<td style=\"font-family:Menlo,Consolas,monospace;color:#1a1a1a;\"><code>{rid}</code></td></tr>"
        f"<tr><td style=\"padding:4px 14px 4px 0;color:#666;vertical-align:top;\">SHA-256</td>"
        f"<td style=\"font-family:Menlo,Consolas,monospace;color:#1a1a1a;word-break:break-all;font-size:12px;\"><code>{hash_hex}</code></td></tr>"
        f"<tr><td style=\"padding:4px 14px 4px 0;color:#666;\">Registered (UTC)</td>"
        f"<td>{created_at}</td></tr>"
        f"<tr><td style=\"padding:4px 14px 4px 0;color:#666;\">Calendars</td>"
        f"<td>{cal_ok} of {cal_total} attesting</td></tr>"
        f"</table>"
        f"<p style=\"margin:22px 0;\">"
        f"<a href=\"{receipt_url}\" "
        f"style=\"display:inline-block;background:#1a1a1a;color:#f5efe0;"
        f"padding:12px 22px;text-decoration:none;"
        f"font-family:Georgia,'Times New Roman',serif;font-size:14px;"
        f"letter-spacing:0.03em;\">"
        f"View the full receipt →</a></p>"
        f"<p style=\"color:#444;font-size:14px;\">A second notice will be "
        f"issued upon Bitcoin commitment. Until then, this receipt and "
        f"the original file together comprise the evidentiary set; please "
        f"retain both.</p>"
    )
    attachments = None
    if _receipt_pdf is not None:
        try:
            pdf_bytes = _receipt_pdf.render_receipt_pdf(receipt, SITE_URL)
            attachments = [{"filename": f"orphograph-receipt-{rid}.pdf", "content": pdf_bytes}]
        except Exception as e:
            sys.stderr.write(f"[email] pdf-attach skipped for {rid}: {type(e).__name__}\n")
    return _send(to, subject, text, html, transactional=True, category="receipt_issued",
                 attachments=attachments)


def send_integration_email(to: str, receipt: dict) -> bool:
    """Day-after notice: three ways to put an issued receipt to work.

    Builds and sends only — the call site decides when to dispatch, gated
    behind the ORPHO_INTEGRATION_EMAIL env flag (see module docstring).
    Transactional follow-up to a receipt the recipient requested; no
    marketing content, no unsubscribe requirement.
    """
    rid = receipt.get("receipt_id", "")
    receipt_url = f"{SITE_URL}/r/{rid}"
    badge_url = f"{SITE_URL}/api/badge/{rid}.svg"
    subject = "Your receipt is ready to work — Orphograph"
    text = (
        f"Receipt {rid} is on file and verifiable. Three standing uses "
        f"are available to you now.\n\n"
        f"1. Hand the link to whoever needs convincing.\n"
        f"   {receipt_url}\n"
        f"   The receipt verifies for them directly; no account is "
        f"required on their part.\n\n"
        f"2. Place the live status badge where the work lives.\n"
        f"   <img src=\"{badge_url}\">\n"
        f"   The badge reflects the receipt's current verification "
        f"status wherever it is embedded.\n\n"
        f"3. Download the .ots proof bundle from the receipt page and "
        f"keep it with the file.\n"
        f"   {receipt_url}\n"
        f"   The bundle verifies against the Bitcoin chain independently, "
        f"without reference to this office.\n\n"
        f"No action is required; the receipt remains on file either way.\n"
    )
    html = (
        f"<h2 style=\"font-family:Georgia,'Times New Roman',serif;margin:0 0 14px;"
        f"color:#1a1a1a;font-weight:500;font-size:20px;letter-spacing:0.01em;\">"
        f"Your receipt is ready to work.</h2>"
        f"<p style=\"color:#444;\">Receipt "
        f"<code style=\"font-family:Menlo,Consolas,monospace;\">{rid}</code> "
        f"is on file and verifiable. Three standing uses are available "
        f"to you now.</p>"
        f"<ol style=\"color:#2a2a2a;padding-left:20px;\">"
        f"<li style=\"margin:0 0 14px;\">Hand the link to whoever needs "
        f"convincing — <a href=\"{receipt_url}\" style=\"color:#1a1a1a;\">"
        f"{receipt_url}</a> verifies for them directly; no account is "
        f"required on their part.</li>"
        f"<li style=\"margin:0 0 14px;\">Place the live status badge where "
        f"the work lives:<br>"
        f"<code style=\"font-family:Menlo,Consolas,monospace;font-size:12px;"
        f"word-break:break-all;\">&lt;img src=\"{badge_url}\"&gt;</code><br>"
        f"The badge reflects the receipt's current verification status "
        f"wherever it is embedded.</li>"
        f"<li style=\"margin:0 0 14px;\">Download the .ots proof bundle "
        f"from the <a href=\"{receipt_url}\" style=\"color:#1a1a1a;\">"
        f"receipt page</a> and keep it with the file. The bundle verifies "
        f"against the Bitcoin chain independently, without reference to "
        f"this office.</li>"
        f"</ol>"
        f"<p style=\"color:#444;font-size:14px;\">No action is required; "
        f"the receipt remains on file either way.</p>"
    )
    return _send(to, subject, text, html, transactional=True,
                 category="integration_notice")
