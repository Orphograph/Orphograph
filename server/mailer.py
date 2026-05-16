#!/usr/bin/env python3
"""email.py — outbound email via Resend HTTP API (stdlib urllib).

Inert if RESEND_API_KEY is not set: logs intent to stderr, returns False.
This keeps the rest of the system functional during dev/test without
wiring a real email account.

Public API:
    send_pack_claim_email(to, claim_code, credit_count) -> bool
    send_receipt_email(to, receipt_record) -> bool
"""
from __future__ import annotations

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
          transactional: bool = True, category: str = "transactional") -> bool:
    """Send via Resend with compliant footer + RFC 8058 List-Unsubscribe.

    Gmail + Yahoo Feb-2024 bulk-sender rules require:
      • SPF + DKIM + DMARC aligned (handled at DNS, via setup_email.py)
      • List-Unsubscribe and List-Unsubscribe-Post headers on all bulk mail
      • One-click unsubscribe for marketing
    Transactional mail is exempt from unsubscribe but still benefits from
    DKIM-aligned signing and a clean reply-to.
    """
    text_out = text + _footer_text(to, transactional)
    html_out = html + _footer_html(to, transactional)

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
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            resp.read()
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        sys.stderr.write(f"[email:error] {type(e).__name__}: {e}\n")
        return False


def send_pack_claim_email(to: str, claim_code: str, credit_count: int) -> bool:
    # URL fragment, not query string: fragments never reach the server,
    # so the bearer claim code does not appear in access logs / referer headers.
    activate_url = f"{SITE_URL}/#pack={claim_code}"
    # Referral code (give-10-get-10). The code is derived from claim_code
    # so we don't need a separate lookup table; see server/referrals.py.
    ref_code = "ref_" + claim_code[3:15] if claim_code.startswith("pk_") else ""
    referral_url = f"{SITE_URL}/?ref={ref_code}" if ref_code else ""
    subject = f"Your Orphograph Pack — {credit_count} anchors ready"
    text = (
        f"Thanks for buying an Orphograph Pack.\n\n"
        f"Your claim code: {claim_code}\n"
        f"Activate it (auto-stores in your browser): {activate_url}\n\n"
        f"You have {credit_count} anchors to use, never expires.\n"
        f"Keep this email — without the claim code there's no way to recover the pack.\n"
    )
    if ref_code:
        text += (
            f"\n— Give 10, get 10 —\n"
            f"Share this link with a friend: {referral_url}\n"
            f"When they buy a Pack, they get 10 bonus anchors AND we add 10 back "
            f"to your code above.\n"
        )
    html = (
        f"<p>Thanks for buying an Orphograph Pack.</p>"
        f"<p><strong>Claim code:</strong> <code>{claim_code}</code></p>"
        f"<p><a href=\"{activate_url}\">Activate it (one click — stores in your browser)</a></p>"
        f"<p>You have {credit_count} anchors to use, never expires.</p>"
        f"<p>Keep this email — without the claim code there is no way to recover the pack.</p>"
    )
    if ref_code:
        html += (
            f"<hr><p><strong>Give 10, get 10.</strong></p>"
            f"<p>Share this link with a friend: <a href=\"{referral_url}\">{referral_url}</a></p>"
            f"<p>When they buy a Pack, they get 10 bonus anchors AND we add 10 back to "
            f"your code above. No cap.</p>"
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


def send_login_link_email(to: str, token: str) -> bool:
    login_url = f"{SITE_URL}/a/{token}"
    subject = "Your Orphograph sign-in link"
    text = (
        f"Click to sign in to Orphograph:\n\n{login_url}\n\n"
        f"This link is one-time use and expires in 24 hours.\n"
        f"If you did not request it, ignore this email — nothing was changed.\n"
    )
    html = (
        f"<p>Click to sign in to Orphograph:</p>"
        f"<p><a href=\"{login_url}\">{login_url}</a></p>"
        f"<p>This link is one-time use and expires in 24 hours.</p>"
        f"<p>If you did not request it, ignore this email — nothing was changed.</p>"
    )
    return _send(to, subject, text, html)


def send_receipt_email(to: str, receipt: dict) -> bool:
    rid = receipt.get("receipt_id", "")
    created_at = receipt.get("created_at", "")
    hash_hex = receipt.get("hash_hex", "")
    verify_url = f"{SITE_URL}/#verify"
    subject = f"Orphograph receipt {rid}"
    text = (
        f"Your file is anchored.\n\n"
        f"Receipt: {rid}\n"
        f"Anchored at (UTC): {created_at}\n"
        f"SHA-256: {hash_hex}\n\n"
        f"Verify any time at {verify_url}\n"
        f"Keep this email + your original file. That's all anyone needs to verify.\n"
    )
    html = (
        f"<p>Your file is anchored.</p>"
        f"<ul>"
        f"<li><strong>Receipt:</strong> <code>{rid}</code></li>"
        f"<li><strong>Anchored at (UTC):</strong> {created_at}</li>"
        f"<li><strong>SHA-256:</strong> <code>{hash_hex}</code></li>"
        f"</ul>"
        f"<p><a href=\"{verify_url}\">Verify any time</a>.</p>"
        f"<p>Keep this email + your original file. That is all anyone needs to verify.</p>"
    )
    return _send(to, subject, text, html)
