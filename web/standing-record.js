// standing-record.js — renders the office's weekly self-anchors from
// /api/standing-record. CSP-safe: external file, textContent-only DOM.

(function () {
  const loading = document.getElementById("record-loading");
  const list = document.getElementById("record-list");
  const empty = document.getElementById("record-empty");

  function fmt(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso || "";
    return d.toISOString().slice(0, 10);
  }

  fetch("/api/standing-record")
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status))))
    .then((j) => {
      const rows = (j && j.anchors) || [];
      loading.hidden = true;
      if (!rows.length) {
        empty.hidden = false;
        return;
      }
      for (const row of rows) {
        const li = document.createElement("li");
        const label = document.createElement("span");
        label.className = "record-label";
        label.textContent = row.client_label || "(unlabeled)";
        const date = document.createElement("span");
        date.className = "record-date";
        date.textContent = fmt(row.created_at);
        const status = document.createElement("span");
        status.className = "record-status" + (row.btc_pinned_at ? "" : " pending");
        status.textContent = row.btc_pinned_at ? "anchored in Bitcoin" : "awaiting Bitcoin confirmation";
        const link = document.createElement("a");
        link.className = "record-link";
        link.href = "/r/" + encodeURIComponent(row.receipt_id || "");
        link.textContent = "View receipt →";
        li.append(label, date, status, link);
        list.appendChild(li);
      }
      list.hidden = false;
    })
    .catch(() => {
      loading.hidden = true;
      empty.textContent = "The record could not be consulted just now — refresh to try again.";
      empty.hidden = false;
    });
})();
