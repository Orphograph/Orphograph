# Orphograph Operator Runbook

**For:** Founder/solo operator running Orphograph in production  
**Updated:** 2026-05-15

---

## Daily Operations

### Morning Check (5 min)

```bash
# Check uptime
fly status

# Check logs for errors
fly logs -f --level error --lines 50

# Check stats
curl -s https://orphograph.com/api/stats | jq .

# Check founder metrics
# Visit https://orphograph.com/web/founder/metrics.html
# Paste ORPHO_FOUNDER_TOKEN
# Expected: MRR = 0 on day 1
```

### Monitor Key Metrics

```bash
# Total anchors (should grow over first week)
curl -s https://orphograph.com/api/stats | jq .total_anchors

# 24h anchors (daily volume)
curl -s https://orphograph.com/api/stats | jq .anchors_24h

# Failing calendars (if any, investigate)
fly logs | grep -i "calendar.*error" | tail -5
```

---

## Incident Response

### Server Won't Start

**Symptom:** `fly deploy` fails or server crashes on startup

1. **Check logs**
   ```bash
   fly logs -f --level error
   ```

2. **Common causes:**
   - `ORPHO_DATA_DIR` missing or not writable
   - `.hmac_secret` file missing (encryption keys)
   - Ledger file corrupted
   - Port already in use (shouldn't happen on Fly)

3. **Recovery:**
   ```bash
   # Ensure data directory exists
   mkdir -p $ORPHO_DATA_DIR && chmod 700 $ORPHO_DATA_DIR
   
   # Ensure .hmac_secret exists (generate if needed)
   if [ ! -f $ORPHO_DATA_DIR/.hmac_secret ]; then
     python3 -c "import secrets; print(secrets.token_urlsafe(32))" > $ORPHO_DATA_DIR/.hmac_secret
     chmod 600 $ORPHO_DATA_DIR/.hmac_secret
   fi
   
   # Restart
   fly deploy --remote-only
   ```

### High Error Rate (>1% in last hour)

**Symptom:** Logs show 500 errors, users report failures

1. **Identify error pattern**
   ```bash
   fly logs --level error | tail -20 | cut -d' ' -f4- | sort | uniq -c | sort -rn
   ```

2. **Common causes:**
   - Stripe webhook broken → users can't buy Pack
   - OTS calendars down → anchoring fails
   - Email delivery broken → receipts don't arrive
   - Rate limit too strict → legitimate users blocked

3. **Immediate mitigation:**
   ```bash
   # Disable checkout (users can still anchor free)
   fly secrets set ORPHO_DISABLE_CHECKOUT=1
   fly deploy --remote-only
   
   # OR: disable anchoring (if calendars down)
   fly secrets set ORPHO_DISABLE_ANCHORING=1
   fly deploy --remote-only
   ```

4. **Investigate root cause**
   - Stripe webhook: check `stripe_processed_events.jsonl` for recent events
   - OTS calendars: manually test `curl https://a.pool.opentimestamps.org/`
   - Email: test magic-link from production
   - Rate limit: check if many IPs are hitting it

5. **Fix and roll back mitigation**
   ```bash
   # Fix the code
   git commit -am "Fix: calendar timeout handling"
   git push origin master
   
   # Re-enable features
   fly secrets unset ORPHO_DISABLE_CHECKOUT
   fly deploy --remote-only
   ```

### PII Leaked in Logs (Security Incident)

**Symptom:** Someone reports seeing email/credit card in logs

**CRITICAL:** Do not panic, follow these steps exactly:

1. **Immediate containment** (1 min)
   ```bash
   # Check if secret scanner caught it
   grep -r "@.*\.com\|4\d{3}.*\d{4}" /path/to/logs | head -5
   
   # If real data leaked:
   # a) Disable checkout immediately (no new payment data)
   # b) Check when leak started
   # c) Who could have seen it?
   ```

2. **Log audit** (5 min)
   ```bash
   # Export recent logs to file
   fly logs --since 1h --all > /tmp/logs_1h.txt
   
   # Search for patterns
   grep -i "buyer@\|@example\|password" /tmp/logs_1h.txt | wc -l
   
   # If >10 lines, data was leaking
   ```

3. **Check code for causes** (10 min)
   - Did a request body get logged?
   - Did an error message expose data?
   - Did someone paste a secret in a commit message?
   
   ```bash
   # Search recent commits
   git log --oneline -20 | while read commit msg; do
     git show $commit | grep -i "password\|secret\|email" | head -1
   done
   ```

4. **Fix the code** (30 min)
   - Remove logging of request bodies
   - Mask emails in error messages
   - Remove secrets from code

5. **Notify affected users** (if real data leaked)
   - Email to support@
   - Be transparent: "We found a logging issue that exposed emails. We've fixed it. Your data is safe."
   - Do NOT blame users

6. **Deploy fix**
   ```bash
   git push origin master
   fly deploy --remote-only
   ```

### Email Not Delivering

**Symptom:** Users request magic-link but email never arrives

1. **Test email path**
   ```bash
   # From production, request a magic-link
   curl -X POST https://orphograph.com/api/auth/email-link \
     -H "Content-Type: application/json" \
     -d '{"email":"YOUR_EMAIL@example.com"}'
   
   # Check inbox for email (wait 30 sec)
   # Check spam folder
   ```

2. **If not arrived, check logs**
   ```bash
   fly logs | grep -i "resend\|email" | tail -10
   ```

3. **Common causes:**
   - `RESEND_API_KEY` not set → API call fails silently
   - Resend quota exceeded
   - Email address on blocked list
   - Domain reputation issue

4. **Check Resend service**
   ```bash
   # Verify API key is set
   fly secrets list | grep RESEND
   ```

5. **Mitigation (temporary)**
   ```bash
   # Disable magic-link flow (users can only use Pack tokens)
   # OR: send instructions via browser console
   # "Your sign-in token is: [token]"
   ```

6. **Fix**
   - Check Resend dashboard for errors
   - Retry failed sends
   - Contact Resend support if quota issue

---

## Data Management

### Backup & Recovery

**Automated backup:** Daily to B2 at 2 AM UTC

**To restore from backup:**

```bash
# 1. Download latest backup from B2
rclone copy b2:orphograph-backup/data /tmp/orphograph-restore --max-depth 1

# 2. Verify backup contents
ls -la /tmp/orphograph-restore/
# Expected: ledger.jsonl, anchors.jsonl, stripe_processed_events.jsonl, etc.

# 3. Stop current server
fly scale count web=0

# 4. Replace data volume
# (This requires volume management in Fly dashboard)
# OR: SSH into VM and restore manually

# 5. Restart server
fly scale count web=1

# 6. Verify
curl https://orphograph.com/api/health
```

### Disaster Recovery Test

**Monthly:** Run a restore simulation

```bash
# Download backup to staging
rclone copy b2:orphograph-backup/data /tmp/staging-restore

# Verify: all important files present
for file in ledger.jsonl stripe_processed_events.jsonl rate_limit_state.json; do
  if [ -f "/tmp/staging-restore/$file" ]; then
    echo "✓ $file present"
  else
    echo "✗ $file MISSING"
  fi
done
```

### Ledger Inspection

**To audit transactions:**

```bash
# Read raw ledger entries (recent)
tail -100 data/stripe_processed_events.jsonl | jq '.type, .data.object.id'

# Count by type
grep '"type"' data/stripe_processed_events.jsonl | grep -o '"[^"]*"' | sort | uniq -c

# Search for specific charge
grep "charge_id_xyz" data/stripe_processed_events.jsonl | jq .

# Export for accounting
cat data/stripe_processed_events.jsonl | jq -r '[.data.object.created, .data.object.amount, .data.object.metadata.email] | @csv' > ledger_export.csv
```

### Ledger Corruption Recovery

**If ledger.jsonl is corrupted (malformed JSON):**

```bash
# 1. Backup the corrupted file
cp data/ledger.jsonl data/ledger.jsonl.corrupted.$(date +%s)

# 2. Remove bad line
# Identify which line (use jq to find first parse error)
python3 -c "
import json
with open('data/ledger.jsonl') as f:
    for i, line in enumerate(f):
        try:
            json.loads(line)
        except:
            print(f'Line {i+1}: {line[:100]}')
"

# 3. Edit ledger.jsonl manually (remove the bad line)
# OR: reconstruct from B2 backup

# 4. Verify
python3 -c "
with open('data/ledger.jsonl') as f:
    for line in f:
        json.loads(line)  # Will fail if any line is invalid
print('✓ Ledger is valid')
"
```

---

## Maintenance

### Scheduled Downtime

**If you need to take service down:**

1. **Set maintenance mode** (if implemented)
   ```bash
   fly secrets set ORPHO_MAINTENANCE_MODE=1
   fly deploy --remote-only
   ```
   Users will see: "Server undergoing maintenance. Back in 30 min."

2. **Do your maintenance** (backup, migrations, etc.)

3. **Restore service**
   ```bash
   fly secrets unset ORPHO_MAINTENANCE_MODE
   fly deploy --remote-only
   ```

### Stripe Webhook Verification

**Monthly: Verify webhooks are processing**

```bash
# Check if recent Stripe events are in the ledger
tail -100 data/stripe_processed_events.jsonl | jq '.type' | sort | uniq -c

# Expected: mix of customer.subscription.* and charge.* events

# If no recent events, check:
# 1. Is webhook secret set?
fly secrets list | grep STRIPE_WEBHOOK_SECRET

# 2. Is endpoint registered in Stripe dashboard?
# Dashboard → Developers → Webhooks → look for <your-domain>/api/stripe/webhook

# 3. Are there failed attempts?
fly logs | grep "webhook\|signature" | tail -5
```

### Rate Limit Tuning

**Monitor if legitimate users are being rate-limited:**

```bash
# Check rate limit rejections in logs
fly logs | grep "rate.*limit" | wc -l

# If >10/day, rate limit may be too strict
# Current: 10 anchors per hour per IP /24

# To adjust (if needed):
# Edit server/rate_limit.py, adjust RATE_LIMIT_PER_HOUR
# OR: change ORPHO_TRUST_PROXY_HEADERS to use /32 (per user IP)

# Testing: 
# For your local network (/24), you can create 10 anchors rapidly
# Should succeed on anchors 1-10, fail on anchor 11
```

---

## Monitoring & Alerts

### Key Metrics to Watch

| Metric | Target | Action if Exceeded |
|---|---|---|
| Error rate (500s) | <1% | Page in within 5 min |
| Stripe webhook failures | <1/day | Investigate immediately |
| Email delivery failures | <5% | Check Resend quota |
| Calendar failures (any) | 0 | Use working calendars only |
| Free anchors per day | Growing | OK, measure interest |
| Pack purchases per day | >0 (Week 2+) | Celebrate! |
| Refunds | <10% | Investigate if >15% |

### Log Patterns to Monitor

**WARNING patterns (investigate):**
```
- "Stripe webhook signature invalid" → webhook secret wrong
- "Calendar timeout" → OTS service slow, use fallback
- "Email delivery failed" → Resend issue or quota
- "Rate limit exceeded" → Legitimate user blocked?
- "PII leaked" → CRITICAL, see incident response above
```

**HEALTHY patterns:**
```
- "Successfully anchored" → normal operation
- "Receipt verified" → users verifying
- "Pack purchased" → income! 
- "Periodic cleanup" → expiry worker running
```

---

## Scaling Checklist

**When traffic increases:**

- [ ] Check database (Fly volume) isn't full
  ```bash
  du -sh data/
  # If >1GB, investigate what's large
  ```

- [ ] Check rate limiter isn't too strict for real growth
  ```bash
  fly logs | grep "rate limit" | tail -20
  ```

- [ ] Consider upgrading Fly VM size
  ```bash
  fly scale vm <new-size>
  # Current: shared-cpu-2x (small, fine for MVP)
  ```

- [ ] Archive old audit logs if >100MB
  ```bash
  gzip data/logs/* && rclone copy data/logs/ b2:orphograph-backup/archive/
  ```

---

## Communication

### When Things Break

**To users (via status page / banner):**
- Be honest about what's wrong
- Estimate time to fix
- Don't blame third-party services (even if true)

**Example:**
```
"We're experiencing a brief issue with receipt delivery.
Your file is safely anchored, but the receipt email is delayed.
We expect this to be resolved within 30 minutes."
```

### Status Page

- Publish at: https://orphograph.com/status.html
- Update manually or via `/api/health` endpoint
- Show: calendar reachability, ledger size, uptime, version

---

## Contact

**For critical issues:**
- Email: support@orphograph.com (auto-responder)
- GitHub Issues (if public): https://github.com/your-repo/issues
- Direct: [founder's phone number] (for security incidents)

---

**Last Updated:** 2026-05-15  
**Review Frequency:** Monthly  
**For Questions:** Email support@orphograph.com
