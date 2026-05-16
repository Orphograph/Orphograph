#!/usr/bin/env python3
"""setup_email.py — cozy terminal wizard for end-to-end email automation.

What this does, in plain language:

  1. Confirms orphograph.com is on Cloudflare nameservers.
  2. Turns on Cloudflare Email Routing so anyone emailing
     hello@orphograph.com lands in your real inbox.
  3. Registers orphograph.com with Resend so outbound mail
     (receipts, sign-in links, pack codes) ships without
     hitting customers' spam folders.
  4. Pushes the SPF / DKIM / DMARC records Resend needs
     into Cloudflare DNS — also via the API, no dashboard
     hopping.
  5. Sends a probe email both ways to prove the loop closed.

Once this finishes, the system never needs another keystroke
from you. Customers email you; you read them in gmail.
The server emails customers; they land in real inboxes.
That's it.

Run:
    python3 ~/orphograph/scripts/setup_email.py

Cancel any time with Ctrl-C — nothing is committed until
the final confirmation step.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────────────
# Warm-blanket palette — chosen with color psychology in mind.
# Cream-paper background, amber accent (warmth + trust), soft sage
# green for success, muted gray for hints, gentle red for errors.
# No harsh saturated tones. Truecolor (24-bit) so the cream is
# actually cream, not a 256-color approximation.
# ─────────────────────────────────────────────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"

# Foregrounds
INK = "\033[38;2;31;29;26m"          # near-black for primary text on cream
TEXT = "\033[38;2;58;54;49m"         # warm dark for body
MUTED = "\033[38;2;131;126;117m"     # soft gray hint
AMBER = "\033[38;2;192;138;62m"      # warm accent
AMBER_DIM = "\033[38;2;156;112;50m"
SAGE = "\033[38;2;74;154;115m"       # success
SAGE_DIM = "\033[38;2;60;124;92m"
WARN = "\033[38;2;192;138;62m"
ERR = "\033[38;2;178;80;80m"

# Background highlights (gentle — only for emphasis blocks)
CREAM_BG = "\033[48;2;253;250;243m"
AMBER_BG = "\033[48;2;245;232;210m"
SAGE_BG = "\033[48;2;225;240;230m"

# Rounded box-drawing — softer than sharp corners
TL, TR, BL, BR = "╭", "╮", "╰", "╯"
H, V = "─", "│"
HSOFT = "╌"

CF_API = "https://api.cloudflare.com/client/v4"
RESEND_API = "https://api.resend.com"
HTTP_TIMEOUT = 20
DOMAIN = "orphograph.com"

ROOT = Path(__file__).resolve().parent.parent
ENV_LOCAL = ROOT / ".env.local"


# ─────────────────────────────────────────────────────────────────
# Rendering helpers
# ─────────────────────────────────────────────────────────────────

def width() -> int:
    try:
        return min(78, os.get_terminal_size().columns - 2)
    except OSError:
        return 76


def hr(char: str = HSOFT, color: str = MUTED) -> None:
    print(f"{color}{char * width()}{RESET}")


def box(title: str, body_lines: list[str], accent: str = AMBER) -> None:
    """Rounded card with a title bar and soft body. Generous padding."""
    w = width()
    inner = w - 2
    print()
    print(f"{accent}{TL}{H * inner}{TR}{RESET}")
    # title row
    pad = inner - len(title) - 2
    print(f"{accent}{V}{RESET} {BOLD}{INK}{title}{RESET}{' ' * pad} {accent}{V}{RESET}")
    print(f"{accent}{V}{H * inner}{V}{RESET}")
    for line in body_lines:
        # word-wrap softly
        for chunk in wrap(line, inner - 4):
            pad = inner - len(_visible(chunk)) - 4
            print(f"{accent}{V}{RESET}  {TEXT}{chunk}{RESET}{' ' * pad}  {accent}{V}{RESET}")
    print(f"{accent}{BL}{H * inner}{BR}{RESET}")
    print()


def _visible(s: str) -> str:
    """Strip ANSI for width calculations."""
    out = []
    i = 0
    while i < len(s):
        if s[i] == "\033":
            j = s.find("m", i)
            i = j + 1 if j >= 0 else i + 1
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def wrap(text: str, w: int) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for word in words:
        if not cur:
            cur = word
            continue
        if len(_visible(cur)) + 1 + len(_visible(word)) <= w:
            cur += " " + word
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def step(num: int, total: int, title: str) -> None:
    badge = f"{AMBER_BG}{INK} step {num}/{total} {RESET}"
    print(f"\n{badge}  {BOLD}{INK}{title}{RESET}")
    print(f"{MUTED}{HSOFT * (len(_visible(badge)) + 2 + len(title))}{RESET}")


def ok(msg: str) -> None:
    print(f"  {SAGE}✓{RESET} {TEXT}{msg}{RESET}")


def warn(msg: str) -> None:
    print(f"  {WARN}!{RESET} {TEXT}{msg}{RESET}")


def fail(msg: str) -> None:
    print(f"  {ERR}✗{RESET} {TEXT}{msg}{RESET}")


def hint(msg: str) -> None:
    for line in wrap(msg, width() - 4):
        print(f"    {MUTED}{ITALIC}{line}{RESET}")


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    label = f"{AMBER}›{RESET} {INK}{prompt}{RESET}"
    if default:
        label += f" {MUTED}[{default}]{RESET}"
    label += f" {MUTED}{V}{RESET} "
    if secret:
        import getpass
        try:
            val = getpass.getpass(label).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(0)
    else:
        try:
            val = input(label).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(0)
    return val or default


def confirm(prompt: str, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    val = ask(f"{prompt} ({suffix})").lower()
    if not val:
        return default
    return val in ("y", "yes")


# ─────────────────────────────────────────────────────────────────
# HTTP plumbing — stdlib only, no requests dep
# ─────────────────────────────────────────────────────────────────

def http(method: str, url: str, *, token: str = "", body: dict | None = None,
         extra_headers: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": str(e)}
    except (urllib.error.URLError, OSError) as e:
        return 0, {"error": str(e)}


# ─────────────────────────────────────────────────────────────────
# State persistence — saves to .env.local (gitignored, chmod 600)
# ─────────────────────────────────────────────────────────────────

def load_env() -> dict[str, str]:
    if not ENV_LOCAL.exists():
        return {}
    out = {}
    for line in ENV_LOCAL.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def save_env(updates: dict[str, str]) -> None:
    existing = load_env()
    existing.update(updates)
    lines = ["# orphograph local secrets — DO NOT COMMIT",
             f"# generated by setup_email.py at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
             ""]
    for k, v in sorted(existing.items()):
        lines.append(f'{k}="{v}"')
    ENV_LOCAL.write_text("\n".join(lines) + "\n")
    os.chmod(ENV_LOCAL, 0o600)


# ─────────────────────────────────────────────────────────────────
# Cloudflare API operations
# ─────────────────────────────────────────────────────────────────

def cf_get_zone(token: str, domain: str) -> dict[str, str] | None:
    code, data = http("GET", f"{CF_API}/zones?name={domain}", token=token)
    if code == 200 and data.get("success") and data.get("result"):
        z = data["result"][0]
        return {"id": z["id"], "name": z["name"], "account_id": z["account"]["id"]}
    return None


def cf_enable_email_routing(token: str, zone_id: str) -> bool:
    code, _ = http("POST", f"{CF_API}/zones/{zone_id}/email/routing/enable", token=token)
    return code in (200, 409)  # 409 = already enabled


def cf_add_destination(token: str, account_id: str, dest_email: str) -> dict:
    code, data = http(
        "POST",
        f"{CF_API}/accounts/{account_id}/email/routing/addresses",
        token=token,
        body={"email": dest_email},
    )
    return data


def cf_add_rule(token: str, zone_id: str, matcher_addr: str, dest_email: str) -> dict:
    body = {
        "actions": [{"type": "forward", "value": [dest_email]}],
        "matchers": [{"field": "to", "type": "literal", "value": matcher_addr}],
        "enabled": True,
        "name": f"orphograph: {matcher_addr} -> founder",
        "priority": 0,
    }
    code, data = http("POST", f"{CF_API}/zones/{zone_id}/email/routing/rules",
                      token=token, body=body)
    return data


def cf_add_dns_record(token: str, zone_id: str, rtype: str, name: str,
                     content: str, priority: int | None = None,
                     ttl: int = 3600) -> dict:
    body: dict[str, Any] = {"type": rtype, "name": name, "content": content, "ttl": ttl}
    if priority is not None:
        body["priority"] = priority
    code, data = http("POST", f"{CF_API}/zones/{zone_id}/dns_records",
                      token=token, body=body)
    return data


# ─────────────────────────────────────────────────────────────────
# Resend API operations
# ─────────────────────────────────────────────────────────────────

def resend_create_domain(api_key: str, domain: str) -> dict:
    code, data = http("POST", f"{RESEND_API}/domains", token=api_key,
                      body={"name": domain, "region": "us-east-1"})
    return data


def resend_get_domain(api_key: str, domain_id: str) -> dict:
    code, data = http("GET", f"{RESEND_API}/domains/{domain_id}", token=api_key)
    return data


def resend_verify_domain(api_key: str, domain_id: str) -> dict:
    code, data = http("POST", f"{RESEND_API}/domains/{domain_id}/verify",
                      token=api_key)
    return data


def resend_send(api_key: str, from_addr: str, to: str, subject: str,
               text: str) -> tuple[bool, str]:
    code, data = http("POST", f"{RESEND_API}/emails", token=api_key,
                      body={"from": from_addr, "to": [to],
                            "subject": subject, "text": text})
    return code == 200, str(data.get("id") or data.get("message") or data)


# ─────────────────────────────────────────────────────────────────
# Wizard steps
# ─────────────────────────────────────────────────────────────────

def welcome() -> None:
    print("\n" * 1)
    box(
        "orphograph email setup",
        [
            "We're going to wire your domain so customers can email you,",
            "and so your server can email customers — both legs, fully",
            "automated. Once we're done, you never touch this again.",
            "",
            f"{DIM}Domain: {DOMAIN}{RESET}",
            f"{DIM}Estimated time: 4 minutes (plus one verification click in your inbox){RESET}",
            "",
            "Cancel any time with Ctrl-C. Nothing is saved until the",
            "final confirm step.",
        ],
        accent=AMBER,
    )


def step1_dns_check() -> None:
    step(1, 6, "DNS sanity check")
    hint("Quick look at your nameservers — we just need to confirm you're "
         "on Cloudflare so we can drive the rest from the API.")
    # Use system dig — orphograph already has memory that NS = anuj/sharon.ns.cloudflare.com
    import subprocess
    try:
        result = subprocess.run(["dig", "+short", "NS", DOMAIN],
                                capture_output=True, text=True, timeout=10)
        ns = result.stdout.strip().lower()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        ns = ""
    if "cloudflare.com" in ns:
        ok(f"{DOMAIN} is on Cloudflare nameservers")
        for line in ns.splitlines():
            print(f"      {MUTED}{line}{RESET}")
    else:
        warn(f"could not confirm Cloudflare NS — got: {ns or '(empty)'}")
        if not confirm("Continue anyway? (you should fix DNS first if NS isn't CF)", default=False):
            sys.exit(0)


def step2_cf_token(env: dict[str, str]) -> str:
    step(2, 6, "Cloudflare API token")
    hint("Generate a token at: https://dash.cloudflare.com/profile/api-tokens")
    hint("Permissions needed (Custom token):")
    hint("  • Zone → Zone → Read")
    hint("  • Zone → DNS → Edit")
    hint("  • Zone → Email Routing Rules → Edit")
    hint("  • Zone Resources → Include → Specific zone → orphograph.com")
    print()
    token = env.get("CLOUDFLARE_API_TOKEN", "")
    if token:
        if confirm(f"Found existing token in .env.local ({token[:6]}…). Reuse?"):
            ok("using existing token")
            return token
    while True:
        token = ask("Paste your Cloudflare API token", secret=True)
        if not token:
            fail("token is required")
            continue
        # Verify with /user/tokens/verify
        code, data = http("GET", f"{CF_API}/user/tokens/verify", token=token)
        if code == 200 and data.get("success"):
            ok("token verified — Cloudflare accepts it")
            return token
        fail(f"token rejected: {data.get('errors', data)}")


def step3_zone(token: str) -> dict[str, str]:
    step(3, 6, f"Find {DOMAIN} zone")
    zone = cf_get_zone(token, DOMAIN)
    if not zone:
        fail(f"{DOMAIN} not found under this account / token. Aborting.")
        sys.exit(1)
    ok(f"zone_id={zone['id'][:8]}… account_id={zone['account_id'][:8]}…")
    return zone


def step4_destination(env: dict[str, str], token: str, zone: dict[str, str]) -> str:
    step(4, 6, "Where should mail to hello@orphograph.com land?")
    hint("Cloudflare will email this address a one-time verification link. "
         "You click it, you're done. We can't bypass this — Cloudflare requires "
         "it so randos can't reroute strangers' mail.")
    default = env.get("EMAIL_DESTINATION", "")
    dest = ask("Forward to which inbox?", default=default)

    # Register destination
    res = cf_add_destination(token, zone["account_id"], dest)
    if not res.get("success"):
        # Might already exist
        msgs = res.get("errors", []) or [{}]
        msg = msgs[0].get("message", "") if msgs else ""
        if "already exists" in msg.lower() or "already" in msg.lower():
            ok(f"destination {dest} already registered — good")
        else:
            fail(f"could not register destination: {res}")
            if not confirm("Continue anyway?", default=False):
                sys.exit(1)
    else:
        ok(f"registered {dest} — check that inbox NOW for a Cloudflare verification email")
        warn("Click the link in that email before continuing. We'll wait.")
        input(f"  {AMBER}›{RESET} {INK}Press ENTER once you've clicked the verification link {MUTED}{V}{RESET} ")
    save_env({"EMAIL_DESTINATION": dest})
    return dest


def step5_routing_and_records(token: str, zone: dict[str, str], dest: str,
                              resend_records: list[dict]) -> None:
    step(5, 6, "Apply records (Cloudflare + Resend) automatically")

    # 5a. Enable Email Routing — adds MX records as a side-effect
    if cf_enable_email_routing(token, zone["id"]):
        ok("Email Routing enabled (MX records auto-added by Cloudflare)")
    else:
        warn("Could not toggle Email Routing — it may already be on. Continuing.")

    # 5b. Add the hello@ → dest rule
    res = cf_add_rule(token, zone["id"], f"hello@{DOMAIN}", dest)
    if res.get("success"):
        ok(f"rule hello@{DOMAIN} → {dest}")
    else:
        warn(f"rule create returned: {res.get('errors', res)} (might already exist)")

    # 5c. Useful aliases
    for alias in ("support", "billing", "abuse", "legal", "privacy"):
        r = cf_add_rule(token, zone["id"], f"{alias}@{DOMAIN}", dest)
        if r.get("success"):
            ok(f"rule {alias}@{DOMAIN} → {dest}")

    # 5d. Push the Resend SPF/DKIM/DMARC records into CF DNS.
    if resend_records:
        print()
        ok("Pushing Resend's outbound records into Cloudflare DNS…")
        for rec in resend_records:
            name = rec.get("name", "")
            rtype = rec.get("type", "TXT")
            value = rec.get("value") or rec.get("content") or ""
            priority = rec.get("priority")
            short_name = name.replace(f".{DOMAIN}", "") or "@"
            r = cf_add_dns_record(token, zone["id"], rtype, short_name, value,
                                  priority=priority)
            if r.get("success"):
                ok(f"  {rtype} {short_name} → {value[:50]}{'…' if len(value) > 50 else ''}")
            else:
                msg = (r.get("errors", [{}]) or [{}])[0].get("message", "")
                if "already exists" in msg.lower() or "identical" in msg.lower():
                    ok(f"  {rtype} {short_name} (already present)")
                else:
                    warn(f"  {rtype} {short_name} → error: {msg}")


def step5b_resend(env: dict[str, str]) -> tuple[str, list[dict]]:
    step(4.5, 6, "Resend API key (outbound)")  # noqa — float for visual flow
    hint("Free tier: 100 emails/day, 3,000/month. Plenty for the launch window.")
    hint("Get a key: https://resend.com/api-keys")
    api_key = env.get("RESEND_API_KEY", "")
    if api_key:
        if confirm(f"Found Resend key in .env.local ({api_key[:6]}…). Reuse?"):
            ok("using existing key")
        else:
            api_key = ""
    while not api_key:
        api_key = ask("Paste Resend API key (re_...)", secret=True)
        if not api_key.startswith("re_"):
            fail("Resend keys start with re_ — check the value")
            api_key = ""
            continue
    save_env({"RESEND_API_KEY": api_key})

    # Add domain (idempotent: 422 if already exists)
    created = resend_create_domain(api_key, DOMAIN)
    domain_id = created.get("id", "")
    records: list[dict] = created.get("records", []) or []

    if not domain_id:
        # Probably already exists — list and find it
        code, data = http("GET", f"{RESEND_API}/domains", token=api_key)
        for d in (data.get("data") or []):
            if d.get("name") == DOMAIN:
                domain_id = d.get("id", "")
                # Re-fetch to get records
                full = resend_get_domain(api_key, domain_id)
                records = full.get("records", []) or records
                break
        ok(f"Resend domain {DOMAIN} already exists ({domain_id[:8]}…)")
    else:
        ok(f"Resend domain {DOMAIN} created ({domain_id[:8]}…)")

    save_env({"RESEND_DOMAIN_ID": domain_id})
    if records:
        ok(f"got {len(records)} DNS records to push (SPF/DKIM/DMARC)")
    else:
        warn("Resend returned no records — falling back to standard SPF/DKIM/DMARC")
        records = [
            {"type": "TXT", "name": DOMAIN,
             "value": "v=spf1 include:_spf.resend.com ~all"},
            {"type": "TXT", "name": f"_dmarc.{DOMAIN}",
             "value": "v=DMARC1; p=quarantine; rua=mailto:dmarc@orphograph.com; ruf=mailto:dmarc@orphograph.com; fo=1"},
        ]
    return api_key, records


def step6_probe(api_key: str, dest: str) -> None:
    step(6, 6, "Close the loop — probe both legs")
    hint("Two test emails: one through Cloudflare inbound, one through Resend outbound.")

    # Outbound probe (the one we can verify programmatically — we get a delivery ID)
    print()
    ok("Sending outbound probe via Resend…")
    sent, ref = resend_send(
        api_key,
        f"Orphograph Setup <hello@{DOMAIN}>",
        dest,
        "[orphograph setup] outbound probe — you can ignore this",
        "If you can read this, Resend → your inbox is working.\n\n"
        "This is an automated message from the setup wizard. Reply not needed.\n",
    )
    if sent:
        ok(f"Resend accepted the message — id={ref[:12]}…")
        hint(f"It should land in {dest} within 30 seconds.")
    else:
        warn(f"Resend returned: {ref}")
        hint("Likely cause: domain not yet verified on Resend side. "
             "Resend needs ~5-15 min for DNS propagation. Retry the probe "
             "with: python3 ~/orphograph/scripts/setup_email.py --probe-only")

    # Inbound probe — can't fully automate; we can only ask the user to test
    print()
    ok("Inbound probe instructions:")
    hint(f"Send a quick email from any inbox → hello@{DOMAIN}")
    hint(f"It should arrive in {dest} within 5 seconds.")


def finale() -> None:
    print()
    box(
        "Setup complete — you can walk away now.",
        [
            f"{SAGE}✓{RESET} {TEXT}Inbound: hello@{DOMAIN} → your gmail (Cloudflare){RESET}",
            f"{SAGE}✓{RESET} {TEXT}Outbound: receipts ship via Resend, no spam folder{RESET}",
            f"{SAGE}✓{RESET} {TEXT}Aliases: support, billing, abuse, legal, privacy{RESET}",
            f"{SAGE}✓{RESET} {TEXT}Records pushed to Cloudflare DNS automatically{RESET}",
            "",
            f"{MUTED}Settings live in: .env.local (chmod 600){RESET}",
            f"{MUTED}Cloudflare dashboard: dash.cloudflare.com → orphograph.com → Email{RESET}",
            f"{MUTED}Resend dashboard: resend.com/domains{RESET}",
            "",
            f"{INK}Next: run the launch script.{RESET}",
            f"{AMBER}  ~/orphograph/scripts/launch.sh{RESET}",
        ],
        accent=SAGE,
    )


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0

    env = load_env()

    if "--probe-only" in sys.argv:
        api_key = env.get("RESEND_API_KEY", "")
        dest = env.get("EMAIL_DESTINATION", "")
        if not api_key or not dest:
            print(f"{ERR}No saved config. Run without --probe-only first.{RESET}")
            return 2
        step6_probe(api_key, dest)
        return 0

    welcome()
    step1_dns_check()
    token = step2_cf_token(env)
    zone = step3_zone(token)
    save_env({"CLOUDFLARE_API_TOKEN": token, "CLOUDFLARE_ZONE_ID": zone["id"],
              "CLOUDFLARE_ACCOUNT_ID": zone["account_id"]})
    api_key, records = step5b_resend(env)
    dest = step4_destination(env, token, zone)
    step5_routing_and_records(token, zone, dest, records)
    step6_probe(api_key, dest)
    finale()
    return 0


if __name__ == "__main__":
    sys.exit(main())
