async function loadMetrics() {
  const token = document.getElementById('founder-token').value.trim();
  if (!token) {
    showError('Please paste your founder token first.');
    return;
  }

  document.getElementById('loading').style.display = 'block';
  document.getElementById('metrics-content').style.display = 'none';
  clearError();

  try {
    const response = await fetch('/api/founder/metrics', {
      headers: { 'X-Orpho-Founder': token }
    });

    if (response.status === 404) {
      showError('Token invalid or metrics endpoint not available.');
      document.getElementById('loading').style.display = 'none';
      return;
    }

    if (!response.ok) {
      throw new Error('HTTP ' + response.status);
    }

    const data = await response.json();
    displayMetrics(data);
  } catch (err) {
    showError('Failed to load metrics: ' + err.message);
  } finally {
    document.getElementById('loading').style.display = 'none';
  }
}

function displayMetrics(data) {
  document.getElementById('mrr').textContent = data.mrr.toFixed(2);
  document.getElementById('arr').textContent = data.arr.toFixed(2);
  document.getElementById('churn-rate').textContent = (data.churn_rate * 100).toFixed(1);
  document.getElementById('ltv').textContent = data.ltv.toFixed(2);
  document.getElementById('active-customers').textContent = data.customers.active;
  document.getElementById('total-customers').textContent = data.customers.total;
  document.getElementById('active-count').textContent = data.customers.active;
  document.getElementById('churned-count').textContent = data.customers.churned_this_month;

  renderWaitlist(data.waitlist);

  const timestamp = new Date(data.timestamp);
  document.getElementById('timestamp').textContent = timestamp.toLocaleString();

  document.getElementById('metrics-content').style.display = 'block';
}

// Demand readout. Revenue answers "did anyone pay"; this answers "did anyone
// ASK", which is the prior question while the answer to the first is zero.
// An UNAVAILABLE read is rendered as such and never as 0 -- a failed read
// must not be mistaken for a measured absence of demand.
function renderWaitlist(w) {
  const el = document.getElementById('waitlist-readout');
  if (!el) return;
  el.textContent = '';
  if (!w) {
    el.textContent = 'not reported by this build';
    return;
  }
  if (w.error) {
    el.textContent = 'UNAVAILABLE — ' + w.error + ' (this is NOT zero)';
    return;
  }
  const byInterest = w.by_interest || {};
  const rows = [
    ['unique people', w.unique_signups],
    ['confirmed', w.confirmed],
    ['pending', w.pending],
    ['agent-receipts LP', byInterest.agent_receipts || 0],
    ['ledger rows (not people)', w.ledger_rows]
  ];
  rows.forEach(function (r) {
    const dt = document.createElement('dt');
    dt.textContent = r[0];
    const dd = document.createElement('dd');
    dd.textContent = String(r[1] === undefined ? '—' : r[1]);
    el.appendChild(dt);
    el.appendChild(dd);
  });
}

function showError(msg) {
  const box = document.getElementById('error-box');
  const div = document.createElement('div');
  div.className = 'error-message';
  div.textContent = msg;
  box.textContent = '';
  box.appendChild(div);
}

function clearError() {
  document.getElementById('error-box').textContent = '';
}

// Restore token from localStorage if available
window.addEventListener('DOMContentLoaded', function() {
  const saved = localStorage.getItem('orpho_founder_token');
  if (saved) {
    document.getElementById('founder-token').value = saved;
  }
});

// Auto-save token
document.getElementById('founder-token').addEventListener('change', function(e) {
  localStorage.setItem('orpho_founder_token', e.target.value);
});

/* ── CSP wiring ────────────────────────────────────────────────────────
   Everything above this line is the former inline <script>, moved verbatim.
   The two buttons that call loadMetrics() carried onclick= attributes,
   which script-src 'self' blocks exactly like an inline <script> block —
   so without the two listeners below the functions above have no reachable
   call site and the page stays dead. Wiring only: no behaviour change. */
document.getElementById('load-btn').addEventListener('click', function () { loadMetrics(); });
document.getElementById('refresh-btn').addEventListener('click', function () { loadMetrics(); });
