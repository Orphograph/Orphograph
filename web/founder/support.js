let authorizedToken = '';

function authorizeTools() {
  const token = document.getElementById('founder-token').value.trim();
  if (!token) {
    showError('Please paste your founder token first.');
    return;
  }
  authorizedToken = token;
  document.getElementById('auth-required').style.display = 'none';
  document.getElementById('tools-panel').style.display = 'block';
  localStorage.setItem('orpho_founder_token', token);
}

async function lookupCustomer() {
  const email = document.getElementById('search-email').value.trim();
  if (!email) {
    showError('Please enter an email address.');
    return;
  }

  document.getElementById('loading').style.display = 'block';
  document.getElementById('customer-data').style.display = 'none';
  clearMessages();

  try {
    const response = await fetch('/api/founder/customer?email=' + encodeURIComponent(email), {
      headers: { 'X-Orpho-Founder': authorizedToken }
    });

    if (response.status === 404) {
      showError('Customer not found: ' + email);
      document.getElementById('loading').style.display = 'none';
      return;
    }

    if (!response.ok) {
      throw new Error('HTTP ' + response.status);
    }

    const customer = await response.json();
    displayCustomer(customer);
  } catch (err) {
    showError('Failed to look up customer: ' + err.message);
  } finally {
    document.getElementById('loading').style.display = 'none';
  }
}

function displayCustomer(customer) {
  document.getElementById('customer-email').textContent = customer.email;
  document.getElementById('stat-anchors').textContent = customer.anchor_count;
  document.getElementById('stat-purchases').textContent = customer.purchases.length;
  document.getElementById('stat-spent').textContent = customer.total_spent.toFixed(2);

  // Anchors
  const anchorsList = document.getElementById('anchors-list');
  anchorsList.textContent = '';
  if (customer.anchors.length === 0) {
    const li = document.createElement('li');
    li.textContent = 'No anchors yet';
    li.style.color = '#6b6354';
    anchorsList.appendChild(li);
  } else {
    customer.anchors.slice(0, 5).forEach(anchor => {
      const li = document.createElement('li');
      const label = anchor.label ? ' — ' + anchor.label : '';
      li.textContent = anchor.hash + label;
      anchorsList.appendChild(li);
    });
  }

  // Purchases
  const purchasesList = document.getElementById('purchases-list');
  purchasesList.textContent = '';
  if (customer.purchases.length === 0) {
    const li = document.createElement('li');
    li.textContent = 'No purchases';
    li.style.color = '#6b6354';
    purchasesList.appendChild(li);
  } else {
    customer.purchases.forEach(purchase => {
      const li = document.createElement('li');
      const date = new Date(purchase.created).toLocaleDateString();
      li.textContent = '$' + purchase.amount + ' — ' + date + ' (ID: ' + purchase.charge_id + ')';
      purchasesList.appendChild(li);
    });
  }

  // Subscription
  const subSection = document.getElementById('subscription-section');
  if (customer.subscription) {
    subSection.style.display = 'block';
    const info = document.getElementById('subscription-info');
    info.textContent = 'Status: ' + customer.subscription.status;
  } else {
    subSection.style.display = 'none';
  }

  document.getElementById('customer-data').style.display = 'block';
}

function clearCustomer() {
  document.getElementById('customer-data').style.display = 'none';
  document.getElementById('search-email').value = '';
  clearRefundForm();
}

function clearRefundForm() {
  document.getElementById('charge-id').value = '';
  document.getElementById('refund-reason').value = '';
  document.getElementById('refund-notes').value = '';
}

function processRefund() {
  const chargeId = document.getElementById('charge-id').value.trim();
  const reason = document.getElementById('refund-reason').value.trim();
  if (!chargeId) {
    showError('Please select a charge ID to refund.');
    return;
  }
  if (!reason) {
    showError('Please select a refund reason.');
    return;
  }
  showSuccess('Refund functionality coming soon. For now, use: scripts/refund_pack.py --charge-id ' + chargeId);
}

function showError(msg) {
  const box = document.getElementById('error-box');
  const div = document.createElement('div');
  div.className = 'error-message';
  div.textContent = msg;
  box.textContent = '';
  box.appendChild(div);
}

function showSuccess(msg) {
  const box = document.getElementById('success-box');
  const div = document.createElement('div');
  div.className = 'success-message';
  div.textContent = msg;
  box.textContent = '';
  box.appendChild(div);
}

function clearMessages() {
  document.getElementById('error-box').textContent = '';
  document.getElementById('success-box').textContent = '';
}

// Restore token from localStorage
window.addEventListener('DOMContentLoaded', function() {
  const saved = localStorage.getItem('orpho_founder_token');
  if (saved) {
    document.getElementById('founder-token').value = saved;
  }
});

/* ── CSP wiring ────────────────────────────────────────────────────────
   Everything above this line is the former inline <script>, moved verbatim.
   The five buttons on this page carried onclick= attributes, which
   script-src 'self' blocks exactly like an inline <script> block — so
   without the listeners below the functions above have no reachable call
   site and the page stays dead. Wiring only: no behaviour change, and no
   change to token handling or to the /api/founder/* request path. */
document.getElementById('authorize-btn').addEventListener('click', function () { authorizeTools(); });
document.getElementById('search-btn').addEventListener('click', function () { lookupCustomer(); });
document.getElementById('clear-customer-btn').addEventListener('click', function () { clearCustomer(); });
document.getElementById('process-refund-btn').addEventListener('click', function () { processRefund(); });
document.getElementById('clear-refund-btn').addEventListener('click', function () { clearRefundForm(); });
