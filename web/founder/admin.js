(function() {
  'use strict';
  var REFRESH_MS = 30000;
  var refreshTimer = null;
  function $(id) { return document.getElementById(id); }

  function showError(msg) {
    var box = $('error-box');
    box.textContent = '';
    var div = document.createElement('div');
    div.className = 'error-message';
    div.textContent = msg;
    box.appendChild(div);
  }
  function clearError() { $('error-box').textContent = ''; }
  function getToken() { return ($('founder-token').value || '').trim(); }

  function formatUptime(sec) {
    if (typeof sec !== 'number' || sec < 0) return '—';
    var d = Math.floor(sec / 86400);
    var h = Math.floor((sec % 86400) / 3600);
    var m = Math.floor((sec % 3600) / 60);
    if (d > 0) return d + 'd ' + h + 'h';
    if (h > 0) return h + 'h ' + m + 'm';
    return m + 'm';
  }

  function fetchJSON(url, token) {
    var opts = token ? { headers: { 'X-Orpho-Founder': token } } : {};
    return fetch(url, opts).then(function(r) {
      if (r.status === 401 || r.status === 403 || r.status === 404) {
        var err = new Error('not authorized (HTTP ' + r.status + ')');
        err.notAuthorized = true;
        throw err;
      }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function renderToggles(toggles) {
    var grid = $('toggle-grid');
    grid.textContent = '';
    var rows = [
      { key: 'maintenance_mode',  label: 'ORPHO_MAINTENANCE_MODE',
        desc: 'All non-founder routes return 503. The most severe toggle.' },
      { key: 'checkout_disabled', label: 'ORPHO_DISABLE_CHECKOUT',
        desc: 'Stripe checkout blocked. Anchoring continues for existing customers.' },
      { key: 'anchoring_disabled', label: 'ORPHO_DISABLE_ANCHORING',
        desc: 'New anchor submissions blocked. Verify endpoints still serve.' }
    ];
    rows.forEach(function(row) {
      var on = !!toggles[row.key];
      var card = document.createElement('div');
      card.className = 'toggle-card ' + (on ? 'on' : 'off');
      var top = document.createElement('div');
      top.className = 'toggle-row';
      var name = document.createElement('span');
      name.className = 'toggle-name';
      name.textContent = row.label;
      var pill = document.createElement('span');
      pill.className = 'pill ' + (on ? 'on' : 'off');
      pill.textContent = on ? 'ON' : 'OFF';
      top.appendChild(name); top.appendChild(pill);
      var desc = document.createElement('p');
      desc.className = 'toggle-desc';
      desc.textContent = row.desc;
      card.appendChild(top); card.appendChild(desc);
      grid.appendChild(card);
    });
  }

  function renderOverall(toggles) {
    var banner = $('overall-banner');
    banner.classList.remove('green', 'amber', 'red');
    if (toggles.maintenance_mode) {
      banner.classList.add('red');
      $('overall-headline').textContent = 'MAINTENANCE MODE — site is serving 503 to customers';
      $('overall-sub').textContent = 'All non-founder routes are blocked. Disable the flag and re-deploy to recover.';
    } else {
      var onCount = (toggles.checkout_disabled ? 1 : 0) + (toggles.anchoring_disabled ? 1 : 0);
      if (onCount === 0) {
        banner.classList.add('green');
        $('overall-headline').textContent = 'All systems normal';
        $('overall-sub').textContent = 'Maintenance off, checkout open, anchoring live.';
      } else {
        banner.classList.add('amber');
        $('overall-headline').textContent = 'Degraded — ' + onCount + ' toggle' + (onCount === 1 ? '' : 's') + ' ON';
        $('overall-sub').textContent = 'Customer-facing path partially disabled. Review toggles below.';
      }
    }
  }

  function renderHealth(health) {
    $('uptime-val').textContent = formatUptime(health.uptime_sec);
    $('boot-at').textContent = 'boot ' + (health.boot_at || '—');
    $('version-val').textContent = health.version || '—';
    var cal = health.calendars || [];
    var reachable = 0;
    for (var i = 0; i < cal.length; i++) { if (cal[i] && cal[i].reachable) reachable++; }
    $('calendars-val').textContent = reachable + ' / ' + cal.length;
    var names = [];
    for (var j = 0; j < cal.length; j++) {
      var c = cal[j] || {};
      var short = (c.url || '').replace(/^https?:\/\//, '').split('.')[0];
      names.push(short + (c.reachable ? ' ok' : ' DOWN'));
    }
    $('calendars-sub').textContent = names.join(' · ') || 'OpenTimestamps pool';
    var oracle = health.btc_oracle || {};
    if (oracle.available && typeof oracle.usd_per_btc === 'number') {
      $('btc-val').textContent = '$' + Math.round(oracle.usd_per_btc).toLocaleString();
      $('btc-source').textContent = 'source: ' + (oracle.source || 'unknown');
    } else {
      $('btc-val').textContent = 'unavailable';
      $('btc-source').textContent = 'no oracle response';
    }
  }

  function renderStats(stats) {
    var a = stats.anchors || {};
    $('anchors-total').textContent = (a.total != null ? a.total : '—');
    $('anchors-24h').textContent = (a.last_24h != null ? a.last_24h : '—');
    $('anchors-7d').textContent = (a.last_7d != null ? a.last_7d : '—');
    var byBox = $('anchors-by-source');
    byBox.textContent = '';
    var rows = [['Free', a.free_anchors], ['Pack', a.pack_anchors], ['Sub/API', a.sub_anchors]];
    rows.forEach(function(r) {
      var row = document.createElement('div');
      row.className = 'source-row';
      var lbl = document.createElement('span'); lbl.textContent = r[0];
      var val = document.createElement('strong'); val.textContent = (r[1] != null ? r[1] : '—');
      row.appendChild(lbl); row.appendChild(val);
      byBox.appendChild(row);
    });
  }

  function loadAll() {
    var token = getToken();
    if (!token) {
      showError('Please paste your founder token above.');
      $('loading').style.display = 'none';
      $('admin-content').style.display = 'none';
      return;
    }
    localStorage.setItem('orpho_founder_token', token);
    if ($('admin-content').style.display !== 'block') $('loading').style.display = 'block';
    clearError();

    Promise.all([
      fetchJSON('/api/founder/admin/toggles', token),
      fetchJSON('/api/health', null),
      fetchJSON('/api/stats', null)
    ]).then(function(results) {
      renderOverall(results[0]);
      renderToggles(results[0]);
      renderHealth(results[1]);
      renderStats(results[2]);
      $('last-refresh').textContent = new Date().toLocaleTimeString();
      $('loading').style.display = 'none';
      $('admin-content').style.display = 'block';
    }).catch(function(err) {
      $('loading').style.display = 'none';
      if (err && err.notAuthorized) {
        showError('Not authorized — token rejected or admin endpoint unreachable. Verify ORPHO_FOUNDER_TOKEN matches the server secret.');
      } else {
        showError('Failed to load admin status: ' + (err && err.message ? err.message : 'unknown error'));
      }
      $('admin-content').style.display = 'none';
    });
  }

  function startAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(loadAll, REFRESH_MS);
  }

  window.addEventListener('DOMContentLoaded', function() {
    var saved = localStorage.getItem('orpho_founder_token');
    if (saved) $('founder-token').value = saved;
    $('load-btn').addEventListener('click', function() { loadAll(); startAutoRefresh(); });
    $('refresh-btn').addEventListener('click', loadAll);
    $('founder-token').addEventListener('change', function(e) {
      localStorage.setItem('orpho_founder_token', e.target.value);
    });
    if (saved) { loadAll(); startAutoRefresh(); }
  });
})();
