// stats.js — poll /api/stats and render the public marketing dashboard.
// Same-origin only. No PII fields are read because the endpoint never sends any.

const $ = (sel) => document.querySelector(sel);

const NUMBER_FMT = new Intl.NumberFormat(undefined);
const USD_FMT = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});
// Pick a sensible English locale for the RelativeTimeFormat fallback;
// fall back to whatever the browser exposes if undefined.
const RTF = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

function fmtUptime(sec) {
  sec = Math.max(0, sec | 0);
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h`;
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  return `${d}d ${h}h`;
}

function fmtRelativeFromIso(iso) {
  if (!iso) return null;
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return null;
  const deltaSec = Math.round((ts - Date.now()) / 1000); // negative for past
  const abs = Math.abs(deltaSec);
  let unit, value;
  if (abs < 60) { unit = "second"; value = deltaSec; }
  else if (abs < 3600) { unit = "minute"; value = Math.round(deltaSec / 60); }
  else if (abs < 86400) { unit = "hour"; value = Math.round(deltaSec / 3600); }
  else { unit = "day"; value = Math.round(deltaSec / 86400); }
  return RTF.format(value, unit);
}

function setNumber(el, n) {
  if (!el) return;
  if (typeof n !== "number" || Number.isNaN(n)) {
    el.textContent = "—";
    el.dataset.pending = "1";
    return;
  }
  el.textContent = NUMBER_FMT.format(n);
  el.dataset.pending = "0";
}

async function refresh() {
  let s;
  try {
    const r = await fetch("/api/stats", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    s = await r.json();
  } catch {
    // Soft-fail: leave the previous values in place and mark checked-at.
    const ca = $("#checked-at");
    if (ca) ca.textContent = "last refresh failed";
    return;
  }

  const a = s.anchors || {};
  setNumber($("#anchors-total"), a.total);
  setNumber($("#anchors-24h"), a.last_24h);
  setNumber($("#anchors-7d"), a.last_7d);

  const cals = s.calendars || { reachable: 0, total: 0, items: [] };
  const calSum = $("#cal-summary");
  if (calSum) {
    calSum.textContent = `${cals.reachable}/${cals.total}`;
    calSum.className = "stat-value " + (
      cals.reachable === cals.total && cals.total > 0 ? "ok" : "bad"
    );
  }
  const calList = $("#cal-list");
  if (calList) {
    calList.replaceChildren();
    for (const item of (cals.items || [])) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.className = "cal-name";
      name.textContent = item.name;
      const status = document.createElement("span");
      status.className = item.reachable ? "cal-ok" : "cal-bad";
      status.textContent = item.reachable ? "reachable" : "unreachable";
      li.appendChild(name);
      li.appendChild(status);
      calList.appendChild(li);
    }
  }

  const oracle = s.btc_oracle || {};
  const src = $("#btc-source");
  if (src) src.textContent = oracle.available ? (oracle.source || "—") : "offline";
  const price = $("#btc-price");
  if (price) {
    const p = oracle.usd_per_btc;
    if (oracle.available && typeof p === "number" && p > 0) {
      price.textContent = `${USD_FMT.format(p)} / BTC`;
    } else {
      price.textContent = "no live price";
    }
  }

  const upt = $("#uptime");
  if (upt) upt.textContent = fmtUptime(s.uptime_sec || 0);
  const bootEl = $("#boot-at");
  if (bootEl) bootEl.textContent = s.boot_at ? `booted ${s.boot_at}` : "booted —";

  const lastIso = a.last_anchor_at;
  const rel = $("#last-anchor-rel");
  const abs = $("#last-anchor-abs");
  if (rel) {
    const phrase = fmtRelativeFromIso(lastIso);
    rel.textContent = phrase || (lastIso ? "just now" : "no anchors yet");
  }
  if (abs) abs.textContent = lastIso || "—";

  const ca = $("#checked-at");
  if (ca) ca.textContent = `last refreshed ${s.checked_at || "—"}`;
}

refresh();
setInterval(refresh, 30_000);
