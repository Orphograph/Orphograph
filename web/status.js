// status.js — poll /api/health and render the dashboard. Same-origin only.

const $ = (sel) => document.querySelector(sel);

function fmtUptime(sec) {
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h`;
  return `${Math.floor(sec / 86400)}d ${Math.floor((sec % 86400) / 3600)}h`;
}

async function refresh() {
  let h;
  try {
    const r = await fetch("/api/health");
    if (!r.ok) throw new Error(r.status);
    h = await r.json();
  } catch {
    const okEl = $("#ok");
    okEl.textContent = "Unreachable";
    okEl.className = "card-value bad";
    return;
  }
  const okEl = $("#ok");
  okEl.textContent = h.ok ? "Operational" : "Degraded";
  okEl.className = "card-value " + (h.ok ? "ok" : "bad");

  $("#version").textContent = h.version || "—";
  $("#uptime").textContent = fmtUptime(h.uptime_sec || 0);
  $("#receipts").textContent = (h.counts && h.counts.receipts_on_disk) ?? "0";

  $("#last-anchor").textContent = (h.last && h.last.anchor_at) || "—";
  $("#last-upgrade").textContent = (h.last && h.last.upgrade_run_at) || "—";
  $("#last-expiry").textContent = (h.last && h.last.expiry_run_at) || "—";
  $("#checked").textContent = h.checked_at || "—";

  const tbody = $("#calendars tbody");
  tbody.replaceChildren();
  for (const cal of (h.calendars || [])) {
    const tr = document.createElement("tr");
    const url = document.createElement("td");
    url.textContent = cal.url;
    const reach = document.createElement("td");
    reach.textContent = cal.reachable ? "✓ reachable" : "✗ unreachable";
    reach.className = cal.reachable ? "cal-ok" : "cal-bad";
    tr.appendChild(url);
    tr.appendChild(reach);
    tbody.appendChild(tr);
  }
}

refresh();
setInterval(refresh, 30_000);
