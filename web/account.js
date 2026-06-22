// account.js — populate the dashboard from /api/me + /api/me/anchors.

const $ = (sel) => document.querySelector(sel);

async function main() {
  let me;
  try {
    const r = await fetch("/api/me");
    if (r.status === 401) {
      $("#account-card").hidden = true;
      $("#auth-required").hidden = false;
      return;
    }
    if (!r.ok) throw new Error(`${r.status}`);
    me = await r.json();
  } catch (e) {
    $("#email").textContent = `error: ${e}`;
    return;
  }

  $("#email").textContent = me.email;
  const planEl = $("#plan-label");
  if (planEl) planEl.textContent = me.plan || (me.subscription_active ? "Standing Order" : "Free tier");
  $("#sub-status").textContent = me.subscription_active ? "Active" : "Not active";
  const renewal = me.subscription_status && me.subscription_status.current_period_end;
  if (renewal) {
    const d = new Date(renewal * 1000);
    const iso = d.toISOString().slice(0, 10);
    const days = typeof me.days_remaining === "number" ? ` (in ${me.days_remaining} day${me.days_remaining === 1 ? "" : "s"})` : "";
    $("#renewal").textContent = iso + days;
  } else {
    $("#renewal").textContent = "—";
  }
  const acEl = $("#anchor-count");
  if (acEl) {
    const n = typeof me.anchor_count === "number" ? me.anchor_count : 0;
    acEl.textContent = `${n} (unlimited under this plan)`;
  }
  const explainerEl = $("#access-explainer");
  if (explainerEl && !me.subscription_active) explainerEl.hidden = true;

  $("#signout-link").addEventListener("click", async (e) => {
    e.preventDefault();
    await fetch("/api/auth/signout", { method: "POST" });
    location.href = "/";
  });

  const logoutAllLink = $("#logout-all-link");
  if (logoutAllLink) {
    logoutAllLink.addEventListener("click", async (e) => {
      e.preventDefault();
      if (!confirm("Log out of all devices? You'll be signed out everywhere, including here, and will need a new sign-in link.")) return;
      try {
        await fetch("/api/me/logout-all", { method: "POST", credentials: "same-origin" });
      } catch (_) {
        // Network error: still redirect — sessions are revoked server-side once the request lands.
      }
      location.href = "/";
    });
  }

  renderTeam(me);

  // Subscription cancel / reactivate buttons.
  const cancelBtn = $("#cancel-sub");
  const reactivateBtn = $("#reactivate-sub");
  const msgEl = $("#sub-action-msg");
  const subStatus = me.subscription_status || {};
  const isActive = !!me.subscription_active;
  const isCancelling = subStatus.status === "active" && subStatus.cancel_at_period_end === true;

  if (isActive && !isCancelling) cancelBtn.hidden = false;
  if (isCancelling || subStatus.status === "canceled") reactivateBtn.hidden = false;

  // Refund-request self-serve button — visible while any paid sub
  // record exists so a customer can put the request on the office's
  // desk without having to find a contact address.
  const refundBtn = $("#refund-request-btn");
  const refundForm = $("#refund-form");
  const refundReason = $("#refund-reason");
  const refundSubmit = $("#refund-submit");
  const refundCancel = $("#refund-cancel");
  const refundMsg = $("#refund-msg");
  if (refundBtn && (isActive || subStatus.status)) {
    refundBtn.hidden = false;
    refundBtn.addEventListener("click", () => {
      refundForm.hidden = false;
      refundBtn.hidden = true;
    });
    refundCancel.addEventListener("click", () => {
      refundForm.hidden = true;
      refundBtn.hidden = false;
      refundMsg.hidden = true;
    });
    refundSubmit.addEventListener("click", async () => {
      refundSubmit.disabled = true;
      refundMsg.hidden = false;
      refundMsg.textContent = "Submitting…";
      try {
        const r = await fetch("/api/me/refund-request", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: (refundReason.value || "").slice(0, 500) }),
        });
        const j = await r.json();
        if (r.ok) {
          refundMsg.textContent = j.message || "Request received.";
          refundReason.value = "";
        } else {
          refundMsg.textContent = j.error || "Could not submit the request just now.";
        }
      } catch (e) {
        refundMsg.textContent = "Network interruption — try again in a moment.";
      } finally {
        refundSubmit.disabled = false;
      }
    });
  }

  const setMsg = (text, kind) => {
    msgEl.hidden = false;
    msgEl.textContent = text;
    msgEl.style.color = kind === "error" ? "var(--bad)" : "var(--accent)";
  };

  cancelBtn.addEventListener("click", async () => {
    if (!confirm("Cancel your subscription at the end of the current period? You'll keep access until then.")) return;
    cancelBtn.disabled = true;
    try {
      const r = await fetch("/api/me/cancel-subscription", { method: "POST" });
      const j = await r.json();
      if (r.ok) {
        setMsg(j.message || "Cancellation queued.", "ok");
        cancelBtn.hidden = true;
        reactivateBtn.hidden = false;
      } else {
        setMsg(j.error || "Could not cancel right now.", "error");
      }
    } catch (e) {
      setMsg("Network error. Try again.", "error");
    } finally {
      cancelBtn.disabled = false;
    }
  });

  reactivateBtn.addEventListener("click", async () => {
    reactivateBtn.disabled = true;
    try {
      const r = await fetch("/api/me/reactivate-subscription", { method: "POST" });
      const j = await r.json();
      if (r.ok) {
        setMsg(j.message || "Reactivated.", "ok");
        reactivateBtn.hidden = true;
        cancelBtn.hidden = false;
      } else {
        setMsg(j.error || "Could not reactivate.", "error");
      }
    } catch (e) {
      setMsg("Network error. Try again.", "error");
    } finally {
      reactivateBtn.disabled = false;
    }
  });

  // API key — show this section only to active subscribers (Creator-tier
  // gating is enforced server-side; the section appears for any active sub
  // so the user can self-serve once they understand the value).
  if (isActive) {
    $("#api-section").hidden = false;
    const prefix = me.api_key_prefix || "";
    const display = $("#api-key-display");
    const issueBtn = $("#issue-api-key");
    const revokeBtn = $("#revoke-api-key");
    const apiMsg = $("#api-key-msg");
    const reveal = $("#api-key-reveal");
    const revealKey = $("#api-key-new");
    const copyBtn = $("#api-key-copy");

    function setApiMsg(text, kind) {
      apiMsg.hidden = false;
      apiMsg.textContent = text;
      apiMsg.style.color = kind === "error" ? "var(--bad)" : "var(--accent)";
    }

    function applyPrefix(p) {
      if (p) {
        display.textContent = p + "…";
        revokeBtn.hidden = false;
        issueBtn.textContent = "Rotate key";
      } else {
        display.textContent = "none issued yet";
        revokeBtn.hidden = true;
        issueBtn.textContent = "Generate new key";
      }
    }
    applyPrefix(prefix);

    issueBtn.addEventListener("click", async () => {
      if (revokeBtn.hidden === false &&
          !confirm("Rotating will revoke your existing API key. Anything using the old key will start failing. Continue?")) {
        return;
      }
      issueBtn.disabled = true;
      try {
        const r = await fetch("/api/me/api-key", { method: "POST" });
        const j = await r.json();
        if (r.ok) {
          revealKey.textContent = j.api_key;
          reveal.hidden = false;
          applyPrefix(j.api_key.slice(0, 14));
          setApiMsg("New key generated.", "ok");
        } else {
          setApiMsg(j.error || "Could not generate key.", "error");
        }
      } catch (e) {
        setApiMsg("Network error.", "error");
      } finally {
        issueBtn.disabled = false;
      }
    });

    revokeBtn.addEventListener("click", async () => {
      if (!confirm("Revoke your API key? Anything using it will stop working immediately.")) return;
      revokeBtn.disabled = true;
      try {
        const r = await fetch("/api/me/api-key/revoke", { method: "POST" });
        const j = await r.json();
        if (r.ok) {
          applyPrefix("");
          reveal.hidden = true;
          setApiMsg(j.revoked ? "Key revoked." : "No active key to revoke.", "ok");
        } else {
          setApiMsg(j.error || "Could not revoke.", "error");
        }
      } catch (e) {
        setApiMsg("Network error.", "error");
      } finally {
        revokeBtn.disabled = false;
      }
    });

    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(revealKey.textContent);
        copyBtn.textContent = "Copied ✓";
        setTimeout(() => { copyBtn.textContent = "Copy"; }, 2500);
      } catch {}
    });

    // Webhooks — same active-subscription gating as the API section.
    setupWebhooks();
  }

  let anchors = [];
  let nextBefore = null;
  let hasMore = false;

  async function loadPage(before) {
    const url = before
      ? `/api/me/anchors?limit=50&before=${encodeURIComponent(before)}`
      : "/api/me/anchors?limit=50";
    try {
      const r = await fetch(url);
      if (!r.ok) return false;
      const data = await r.json();
      const batch = data.anchors || [];
      anchors = anchors.concat(batch);
      hasMore = !!data.has_more;
      nextBefore = data.next_before || null;
      return true;
    } catch { return false; }
  }

  await loadPage(null);

  if (!anchors.length) {
    $("#anchors-empty").hidden = false;
    return;
  }

  $("#filter-row").hidden = false;
  $("#anchors-table").hidden = false;

  let loadMoreBtn = $("#anchors-load-more");
  if (!loadMoreBtn) {
    loadMoreBtn = document.createElement("button");
    loadMoreBtn.id = "anchors-load-more";
    loadMoreBtn.type = "button";
    loadMoreBtn.className = "btn btn-secondary";
    loadMoreBtn.style.marginTop = "12px";
    loadMoreBtn.textContent = "Load 50 more";
    const table = $("#anchors-table");
    table.parentNode.insertBefore(loadMoreBtn, table.nextSibling);
  }
  loadMoreBtn.addEventListener("click", async () => {
    if (!hasMore || !nextBefore) return;
    loadMoreBtn.disabled = true;
    loadMoreBtn.textContent = "Loading…";
    const ok = await loadPage(nextBefore);
    loadMoreBtn.disabled = false;
    loadMoreBtn.textContent = "Load 50 more";
    if (ok) render();
  });

  function render() {
    const q = ($("#filter-text").value || "").toLowerCase().trim();
    const from = $("#filter-from").value;
    const to = $("#filter-to").value;
    const privFilter = ($("#filter-private")?.value || "");
    const tbody = $("#anchors-table tbody");
    tbody.replaceChildren();
    let shown = 0;
    const filtered = [];
    for (const a of anchors) {
      const created = (a.created_at || "");
      const label = (a.client_label || "");
      if (q && !label.toLowerCase().includes(q)) continue;
      if (from && created < from) continue;
      if (to && created > to + "T23:59:59") continue;
      if (privFilter === "true" && !a.private) continue;
      if (privFilter === "false" && a.private) continue;
      filtered.push(a);
      const tr = document.createElement("tr");
      const td = (text) => { const c = document.createElement("td"); c.textContent = text; return c; };
      tr.appendChild(td(created.replace("T", " ").slice(0, 19)));
      tr.appendChild(td(label || "(none)"));
      tr.appendChild(td(a.private ? "private" : "public"));
      tr.appendChild(td(`${a.calendars_ok}/${a.calendars_total}`));
      tr.appendChild(td(a.status || "pending"));
      const link = document.createElement("td");
      const a_el = document.createElement("a");
      a_el.href = `/r/${a.receipt_id}`;
      a_el.textContent = "view";
      link.appendChild(a_el);
      tr.appendChild(link);
      tbody.appendChild(tr);
      shown++;
    }
    const suffix = hasMore ? "+" : "";
    $("#filter-count").textContent = shown === anchors.length
      ? `${anchors.length}${suffix} anchors`
      : `${shown} of ${anchors.length}${suffix} match`;
    const lm = $("#anchors-load-more");
    if (lm) lm.hidden = !hasMore;
    drawTimeline(filtered);
  }

  $("#filter-text").addEventListener("input", render);
  $("#filter-from").addEventListener("change", render);
  $("#filter-to").addEventListener("change", render);
  $("#filter-private")?.addEventListener("change", render);
  render();

  // Founder-only Payouts panel — visible only when a valid founder token is
  // present in localStorage. Set it once via the browser console:
  //   localStorage.setItem("orpho_founder_token", "<the token you set as ORPHO_FOUNDER_TOKEN on the server>")
  // Customers never see this panel — the server returns 404 without the token.
  setupFounderPanel();
}

async function setupWebhooks() {
  const section = $("#webhooks-section");
  const table = $("#webhooks-table");
  const tbody = table.querySelector("tbody");
  const emptyEl = $("#webhooks-empty");
  const form = $("#webhook-add-form");
  const urlInput = $("#webhook-url-input");
  const msgEl = $("#webhook-msg");
  const reveal = $("#webhook-reveal");
  const revealSecret = $("#webhook-secret-new");
  const copyBtn = $("#webhook-secret-copy");

  section.hidden = false;

  function setMsg(text, kind) {
    msgEl.hidden = false;
    msgEl.textContent = text;
    msgEl.style.color = kind === "error" ? "var(--bad)" : "var(--accent)";
  }

  async function refresh() {
    let data;
    try {
      const r = await fetch("/api/me/webhooks");
      if (!r.ok) {
        emptyEl.hidden = false;
        emptyEl.textContent = "Could not load webhooks.";
        table.hidden = true;
        return;
      }
      data = await r.json();
    } catch {
      emptyEl.hidden = false;
      emptyEl.textContent = "Network error loading webhooks.";
      table.hidden = true;
      return;
    }

    const hooks = (data && data.webhooks) || [];
    tbody.replaceChildren();
    if (!hooks.length) {
      table.hidden = true;
      emptyEl.hidden = false;
      emptyEl.textContent = "No webhooks registered on this account.";
      return;
    }
    emptyEl.hidden = true;
    table.hidden = false;

    for (const w of hooks) {
      const tr = document.createElement("tr");
      const td = (text, mono) => {
        const c = document.createElement("td");
        if (mono) c.className = "mono";
        c.textContent = text;
        return c;
      };
      tr.appendChild(td(w.url || "", true));
      tr.appendChild(td(w.secret_prefix || "", true));
      const created = (w.created_at || "").replace("T", " ").slice(0, 19);
      tr.appendChild(td(created || "—"));
      const actionTd = document.createElement("td");
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "btn-link";
      rm.textContent = "Remove";
      rm.dataset.url = w.url || "";
      rm.addEventListener("click", async () => {
        if (!confirm("Remove this webhook? Deliveries will stop immediately.")) return;
        rm.disabled = true;
        try {
          const r = await fetch("/api/me/webhooks/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: rm.dataset.url }),
          });
          const j = await r.json().catch(() => ({}));
          if (r.ok && j.ok) {
            setMsg("Webhook removed.", "ok");
            await refresh();
          } else {
            setMsg(j.error || "Could not remove webhook.", "error");
          }
        } catch {
          setMsg("Network error.", "error");
        } finally {
          rm.disabled = false;
        }
      });
      actionTd.appendChild(rm);
      tr.appendChild(actionTd);
      tbody.appendChild(tr);
    }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = (urlInput.value || "").trim();
    if (!url) return;
    const submitBtn = form.querySelector("button[type=submit]");
    submitBtn.disabled = true;
    try {
      const r = await fetch("/api/me/webhooks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const j = await r.json().catch(() => ({}));
      if (r.ok && j.ok) {
        revealSecret.textContent = j.secret || "";
        reveal.hidden = false;
        urlInput.value = "";
        setMsg("Webhook registered.", "ok");
        await refresh();
      } else {
        setMsg(j.error || "Could not register webhook.", "error");
      }
    } catch {
      setMsg("Network error.", "error");
    } finally {
      submitBtn.disabled = false;
    }
  });

  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(revealSecret.textContent);
      copyBtn.textContent = "Copied ✓";
      setTimeout(() => { copyBtn.textContent = "Copy"; }, 2500);
    } catch {}
  });

  await refresh();
}

function renderTeam(me) {
  const team = me && me.team;
  const role = me && me.team_role;
  const isOwner = role === "owner";

  if (!team) {
    // No team — show join form + create form (only if owner has sub)
    $("#team-none").hidden = false;
    $("#team-info").hidden = true;
    $("#team-join-section").hidden = false;
    if (!me.subscription_active) {
      // Subscriber-only feature; hide create form if no sub
      const createForm = $("#team-create-form");
      if (createForm) createForm.hidden = true;
      const noneSection = $("#team-none");
      if (noneSection) {
        noneSection.querySelector(".hint").textContent =
          "Teams require an active subscription. Subscribe to create a team, " +
          "or paste an invite code below if someone shared one with you.";
      }
    }
    wireTeamCreate();
    wireTeamRedeem();
    return;
  }

  $("#team-none").hidden = true;
  $("#team-info").hidden = false;
  $("#team-join-section").hidden = true;
  $("#team-role-pill").textContent = isOwner ? "(owner)" : "(member)";
  $("#team-name").textContent = team.name || "(no name)";
  $("#team-owner").textContent = team.owner || "";
  const members = team.members || [];
  $("#team-member-count").textContent = members.length;
  const memberList = $("#team-members");
  memberList.replaceChildren();
  if (!members.length) {
    memberList.textContent = "(none yet — share an invite code)";
  } else {
    const ul = document.createElement("ul");
    for (const m of members) {
      const li = document.createElement("li");
      li.textContent = m;
      if (isOwner) {
        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "btn-link";
        rm.style.marginLeft = "8px";
        rm.textContent = "remove";
        rm.addEventListener("click", async () => {
          if (!confirm(`Remove ${m} from the team?`)) return;
          const r = await fetch("/api/me/team/remove", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ member_email: m }),
          });
          if (r.ok) location.reload();
        });
        li.appendChild(rm);
      }
      ul.appendChild(li);
    }
    memberList.appendChild(ul);
  }

  if (isOwner) {
    $("#team-owner-actions").hidden = false;
    $("#team-member-actions").hidden = true;
    $("#team-invite-btn").addEventListener("click", async () => {
      const r = await fetch("/api/me/team/invite", { method: "POST" });
      const result = $("#team-invite-result");
      result.hidden = false;
      if (!r.ok) {
        result.textContent = "Could not issue invite — check that your subscription is active.";
        return;
      }
      const data = await r.json();
      result.replaceChildren();
      result.textContent = "Share this single-use invite link: ";
      const code = document.createElement("code");
      code.textContent = data.share_url || data.invite_code;
      result.appendChild(code);
    });
  } else {
    $("#team-owner-actions").hidden = true;
    $("#team-member-actions").hidden = false;
    $("#team-leave-btn").addEventListener("click", async () => {
      if (!confirm("Leave this team? You'll lose access to the owner's subscription benefits.")) return;
      const r = await fetch("/api/me/team/leave", { method: "POST" });
      if (r.ok) location.reload();
    });
  }
}

function wireTeamCreate() {
  const form = document.getElementById("team-create-form");
  if (!form || form.dataset.wired) return;
  form.dataset.wired = "1";
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = (document.getElementById("team-create-name").value || "").trim();
    const r = await fetch("/api/me/team/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ team_name: name || "My Team" }),
    });
    if (r.ok) location.reload();
    else {
      const data = await r.json().catch(() => ({}));
      alert(data.error || "Could not create team.");
    }
  });
}

function wireTeamRedeem() {
  const form = document.getElementById("team-redeem-form");
  if (!form || form.dataset.wired) return;
  form.dataset.wired = "1";
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = (document.getElementById("team-redeem-code").value || "").trim();
    const result = document.getElementById("team-redeem-result");
    if (!code) return;
    const r = await fetch("/api/me/team/redeem", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invite_code: code }),
    });
    const data = await r.json().catch(() => ({}));
    result.hidden = false;
    if (data.ok) {
      result.textContent = "Joined! Reloading…";
      setTimeout(() => location.reload(), 600);
    } else {
      result.textContent = data.error || "Could not redeem invite.";
    }
  });
}

function drawTimeline(anchors) {
  // Renders an SVG dot-plot of anchor timestamps on a continuous date axis.
  // A dense, evenly-spaced timeline is the visual proof-of-existence pattern
  // — significantly harder to fake retroactively than a single receipt.
  const svg = document.getElementById("timeline-svg");
  const empty = document.getElementById("timeline-empty");
  if (!svg) return;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  if (!anchors || !anchors.length) {
    if (empty) empty.hidden = false;
    svg.removeAttribute("viewBox");
    return;
  }
  if (empty) empty.hidden = true;
  const NS = "http://www.w3.org/2000/svg";
  const W = 720, H = 88;
  const PAD_X = 24, PAD_Y = 18;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", "100%");
  svg.setAttribute("height", H);

  const times = anchors
    .map((a) => Date.parse(a.created_at || ""))
    .filter((t) => !isNaN(t));
  if (!times.length) return;
  let tMin = Math.min(...times);
  let tMax = Math.max(...times);
  if (tMax === tMin) tMax = tMin + 86400000; // 1 day padding
  const span = tMax - tMin;

  // Baseline
  const baseline = document.createElementNS(NS, "line");
  baseline.setAttribute("x1", PAD_X);
  baseline.setAttribute("y1", H / 2);
  baseline.setAttribute("x2", W - PAD_X);
  baseline.setAttribute("y2", H / 2);
  baseline.setAttribute("stroke", "#d8d2c5");
  baseline.setAttribute("stroke-width", "1");
  svg.appendChild(baseline);

  // Dots
  for (const a of anchors) {
    const t = Date.parse(a.created_at || "");
    if (isNaN(t)) continue;
    const x = PAD_X + ((t - tMin) / span) * (W - 2 * PAD_X);
    const dot = document.createElementNS(NS, "circle");
    dot.setAttribute("cx", x.toFixed(2));
    dot.setAttribute("cy", H / 2);
    dot.setAttribute("r", a.private ? 4 : 3.2);
    dot.setAttribute("fill", a.private ? "#b07a3a" : "#1f1d1a");
    dot.setAttribute("opacity", "0.78");
    const title = document.createElementNS(NS, "title");
    title.textContent = `${a.created_at} · ${a.client_label || "(no label)"} · ${a.private ? "private" : "public"}`;
    dot.appendChild(title);
    svg.appendChild(dot);
  }

  // Range labels
  const fmt = (ts) => new Date(ts).toISOString().slice(0, 10);
  const leftLabel = document.createElementNS(NS, "text");
  leftLabel.setAttribute("x", PAD_X);
  leftLabel.setAttribute("y", H - 4);
  leftLabel.setAttribute("fill", "#6b6660");
  leftLabel.setAttribute("font-size", "11");
  leftLabel.setAttribute("font-family", "ui-monospace, monospace");
  leftLabel.textContent = fmt(tMin);
  svg.appendChild(leftLabel);

  const rightLabel = document.createElementNS(NS, "text");
  rightLabel.setAttribute("x", W - PAD_X);
  rightLabel.setAttribute("y", H - 4);
  rightLabel.setAttribute("fill", "#6b6660");
  rightLabel.setAttribute("font-size", "11");
  rightLabel.setAttribute("font-family", "ui-monospace, monospace");
  rightLabel.setAttribute("text-anchor", "end");
  rightLabel.textContent = fmt(tMax);
  svg.appendChild(rightLabel);

  const count = document.createElementNS(NS, "text");
  count.setAttribute("x", W / 2);
  count.setAttribute("y", PAD_Y);
  count.setAttribute("fill", "#6b6660");
  count.setAttribute("font-size", "11");
  count.setAttribute("text-anchor", "middle");
  count.setAttribute("font-family", "ui-monospace, monospace");
  count.textContent = `${anchors.length} anchor${anchors.length === 1 ? "" : "s"} · ${Math.round(span / 86400000)} days span`;
  svg.appendChild(count);
}

function formatSats(sats) {
  if (typeof sats !== "number" || isNaN(sats)) return "—";
  const btc = sats / 100_000_000;
  return `${sats.toLocaleString()} sats (${btc.toFixed(8)} BTC)`;
}

function formatTs(iso) {
  if (!iso) return "never";
  return iso.replace("T", " ").slice(0, 19) + " UTC";
}

async function setupFounderPanel() {
  let token = "";
  try { token = localStorage.getItem("orpho_founder_token") || ""; } catch { /* localStorage disabled */ }
  if (!token) return;  // not the founder; panel stays hidden

  let resp;
  try {
    resp = await fetch("/api/founder/payout-status", {
      headers: { "X-Orpho-Founder": token },
    });
  } catch {
    return;  // network error; panel stays hidden
  }
  if (resp.status === 404) return;  // wrong token or feature disabled — pretend it doesn't exist
  if (!resp.ok) return;

  const data = await resp.json();

  $("#fp-balance").textContent = formatSats(data.hot_balance_sats);
  $("#fp-threshold").textContent = formatSats(data.threshold_sats);
  $("#fp-ready").textContent = data.ready_to_sweep ? "✓ yes — tap Phantom" : "no";
  $("#fp-ready").className = data.ready_to_sweep ? "ready-yes" : "ready-no";
  $("#fp-cold").textContent = data.cold_destination || "(not configured)";
  $("#fp-pool").textContent = String(data.pool_size ?? 0);
  $("#fp-last-ping").textContent = formatTs(data.last_ping_at);
  $("#fp-last-snap").textContent = formatTs(data.last_snapshot_at);

  if (data.cold_destination) {
    const link = $("#fp-cold-explorer");
    link.href = `https://mempool.space/address/${encodeURIComponent(data.cold_destination)}`;
    link.hidden = false;
  }

  $("#fp-refresh").addEventListener("click", (e) => {
    e.preventDefault();
    setupFounderPanel();
  });

  $("#founder-section").hidden = false;
}

main();
