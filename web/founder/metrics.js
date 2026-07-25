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

  const timestamp = new Date(data.timestamp);
  document.getElementById('timestamp').textContent = timestamp.toLocaleString();

  document.getElementById('metrics-content').style.display = 'block';
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
