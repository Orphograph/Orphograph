(function () {
  var $ = function (id) { return document.getElementById(id); };

  // Safe DOM helpers — never use innerHTML with server-supplied data.
  function el(tag, props, children) {
    var n = document.createElement(tag);
    if (props) for (var k in props) {
      if (k === "class") n.className = props[k];
      else if (k === "text") n.textContent = props[k];
      else n.setAttribute(k, props[k]);
    }
    if (children) children.forEach(function (c) { n.appendChild(c); });
    return n;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  var FUNNEL = [
    ["drop_zone_visible",          "Drop zone visible"],
    ["file_anchored",              "File anchored"],
    ["checkout_clicked",           "Checkout clicked"],
    ["checkout_returned_success",  "Checkout paid"]
  ];
  var RATES = [
    ["visible_to_anchored",  "visible → anchored"],
    ["anchored_to_checkout", "anchored → checkout"],
    ["checkout_to_paid",     "checkout → paid"],
    ["visible_to_paid",      "visible → paid (end-to-end)"]
  ];

  function rateClass(v) {
    return v === 0 ? "zero" : v < 5 ? "low" : v < 25 ? "mid" : "high";
  }

  function paintTotals(totals) {
    var root = $("totals"); clear(root);
    FUNNEL.forEach(function (kv) {
      var v = (totals && totals[kv[0]]) || 0;
      root.appendChild(el("div", {class: "stat"}, [
        el("div", {class: "label", text: kv[1]}),
        el("div", {class: "val", text: v.toLocaleString()}),
        el("div", {class: "sub", text: "30d total"})
      ]));
    });
  }

  function paintRates(rates) {
    var root = $("rates"); clear(root);
    RATES.forEach(function (kv) {
      var v = (rates && rates[kv[0]]) || 0;
      root.appendChild(el("div", {class: "rate-row"}, [
        el("div", {class: "name", text: kv[1]}),
        el("div", {class: "pct " + rateClass(v), text: v.toFixed(1) + "%"})
      ]));
    });
  }

  function paintSeries(series) {
    var root = $("series-wrap"); clear(root);
    if (!series || !series.length) {
      root.appendChild(el("div", {class: "empty", text:
        "no funnel events recorded in the last 30 days yet — visit the site, drop a file, then refresh"}));
      return;
    }
    var tbl = el("table", null, null);
    var thead = el("thead", null, null);
    var headRow = el("tr", null, null);
    headRow.appendChild(el("th", {text: "Date"}));
    FUNNEL.forEach(function (kv) { headRow.appendChild(el("th", {text: kv[1]})); });
    thead.appendChild(headRow); tbl.appendChild(thead);
    var tbody = el("tbody", null, null);
    series.forEach(function (row) {
      var tr = el("tr", null, null);
      tr.appendChild(el("td", {text: String(row.date || "")}));
      FUNNEL.forEach(function (kv) {
        var v = row[kv[0]] || 0;
        tr.appendChild(el("td", {text: v ? String(v) : "—"}));
      });
      tbody.appendChild(tr);
    });
    tbl.appendChild(tbody);
    root.appendChild(tbl);
  }

  function paint(data) {
    $("auth-card").hidden = true;
    $("content").hidden = false;
    var ts = String(data.timestamp || "");
    $("ts").textContent = ts ? ("updated " + ts.replace("T", " ").replace("Z", " UTC").substring(0, 19)) : "";
    paintTotals(data.totals_30d);
    paintRates(data.rates_30d_pct);
    paintSeries(data.series_by_day);
    $("meta").textContent =
      "events_scanned=" + (data.events_scanned || 0) +
      " · window=30d · timezone=UTC";
  }

  function load() {
    var t = $("token").value.trim();
    if (!t) { $("err").textContent = "token required"; return; }
    $("err").textContent = "loading…";
    fetch("/api/founder/funnel", { headers: { "X-Orpho-Founder": t } })
      .then(function (r) {
        if (r.status === 404) { throw new Error("404 — token wrong, or ORPHO_FOUNDER_TOKEN not set on server"); }
        if (!r.ok) { throw new Error("HTTP " + r.status); }
        return r.json();
      })
      .then(function (data) {
        $("err").textContent = "";
        paint(data);
      })
      .catch(function (e) {
        $("err").textContent = String(e.message || e);
      });
  }

  $("load-btn").addEventListener("click", load);
  $("token").addEventListener("keydown", function (e) {
    if (e.key === "Enter") load();
  });
})();
