(function () {
  "use strict";

  /* ── formatting ───────────────────────────────────────────── */

  function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }
  function fmtUsd(v) {
    if (v == null || isNaN(v)) return "—";
    return "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function fmtNum(v) {
    if (v == null || isNaN(v)) return "—";
    return Number(v).toLocaleString("en-US");
  }
  function fmtPct(v, digits) {
    if (v == null || isNaN(v)) return "—";
    return (v * 100).toFixed(digits == null ? 1 : digits) + "%";
  }
  function signedUsd(v) {
    if (v == null || isNaN(v)) return "—";
    return (v < 0 ? "−" : "") + "$" + Math.abs(Number(v)).toLocaleString("en-US",
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  /* ── horizontal bar chart ─────────────────────────────────── */
  // data: [{label, value, color?}]; opts: {format: fn -> string}
  function renderBars(targetEl, data, opts) {
    if (!targetEl) return;
    opts = opts || {};
    var fmt = opts.format || function (v) { return fmtNum(v); };
    var rows = (data || []).filter(function (d) { return d && d.value != null; });
    if (!rows.length) { targetEl.innerHTML = '<p class="empty">No data</p>'; return; }
    var max = 0;
    rows.forEach(function (d) { if (Math.abs(d.value) > max) max = Math.abs(d.value); });
    if (!max) max = 1;
    var html = "";
    rows.forEach(function (d) {
      var pct = Math.max(2, Math.round((Math.abs(d.value) / max) * 100));
      var style = "width:" + pct + "%" + (d.color ? ";background:" + d.color : "");
      html += '<div class="bar-row">' +
        '<div class="bar-label" title="' + escapeHtml(d.label) + '">' + escapeHtml(d.label) + '</div>' +
        '<div class="bar-track"><div class="bar-fill" style="' + style + '"></div></div>' +
        '<div class="bar-val">' + escapeHtml(fmt(d.value)) + '</div>' +
        '</div>';
    });
    targetEl.innerHTML = html;
  }

  /* ── api ──────────────────────────────────────────────────── */

  async function apiGet(url) {
    var r = await fetch(url);
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }
  async function apiPost(url, body) {
    var r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  /* ── status dot + tabs ────────────────────────────────────── */

  function checkStatus() {
    apiGet("/api/overview")
      .then(function () { setDot("green"); })
      .catch(function () { setDot("red"); });
  }
  function setDot(cls) {
    var d = document.getElementById("status-dot");
    if (d) d.className = "status-dot " + cls;
  }

  var loaded = {};
  function switchPane(name) {
    document.querySelectorAll(".tab").forEach(function (t) {
      t.classList.toggle("active", t.getAttribute("data-pane") === name);
    });
    document.querySelectorAll(".pane").forEach(function (p) {
      p.classList.toggle("active", p.id === "pane-" + name);
    });
    if (!loaded[name]) { loaded[name] = true; loadPane(name); }
  }
  function loadPane(name) {
    if (name === "overview") loadOverview();
    else if (name === "calculator") loadCalculator();
  }

  /* ── tier colors ──────────────────────────────────────────── */

  var TIER_COLOR = {
    CRITICAL: "#f2555a", COMPLEX: "#f59e42", MODERATE: "#5b9dff", SIMPLE: "#42c98a"
  };
  function tierColor(t) { return TIER_COLOR[(t || "").toUpperCase()] || "#8b93a1"; }

  /* ── pane 1 — overview ────────────────────────────────────── */

  function loadOverview() {
    // classifier-status is optional (a results file) — never let it block the pane.
    var statusP = apiGet("/api/classifier-status").catch(function () { return {}; });
    Promise.all([apiGet("/api/overview"), statusP]).then(function (res) {
      var d = res[0], s = res[1] || {};
      var cards = document.getElementById("overview-cards");
      if (cards) {
        function card(title, value, sub, accent) {
          return '<div class="stat-card">' +
            '<div class="stat-label">' + escapeHtml(title) + '</div>' +
            '<div class="stat-value' + (accent ? ' accent' : '') + '">' + escapeHtml(value) + '</div>' +
            (sub ? '<div class="stat-sub">' + escapeHtml(sub) + '</div>' : '') + '</div>';
        }
        // Honest classifier headline: production hybrid on the balanced set,
        // keyword floor as the sub. "n/a" (not a fake number) when unmeasured.
        var bal = s.datasets && s.datasets.balanced;
        var clsVal = "n/a", clsSub = "run evals/classifier_status.py";
        if (bal && bal.model_hybrid && bal.model_hybrid.accuracy != null) {
          clsVal = fmtPct(bal.model_hybrid.accuracy);
          clsSub = "balanced set (n=" + bal.n + ") · keyword floor " +
            fmtPct(bal.keyword ? bal.keyword.accuracy : null);
        } else if (bal && bal.keyword) {
          clsVal = fmtPct(bal.keyword.accuracy);
          clsSub = "keyword floor (n=" + bal.n + ") — model unmeasured";
        }
        cards.innerHTML =
          card("Imputed cost avoided", fmtUsd(d.imputed_usd), "list-rate what-if — not real spend", true) +
          card("Actual spend", fmtUsd(d.actual_usd), "real dollars charged") +
          card("Saved", fmtUsd(d.saved_usd), "imputed − actual") +
          card("Usage events", fmtNum(d.events), "in the ledger") +
          card("Route decisions", fmtNum(d.route_decisions), "logged") +
          card("Classifier accuracy", clsVal, clsSub) +
          card("Local-first tiers", (d.local_first_pct != null ? d.local_first_pct + "%" : "—"),
            "config fact, not a quality claim");
      }
      renderBars(document.getElementById("chart-by-model"),
        (d.by_model || []).map(function (m) { return { label: m.model, value: m.usd }; }),
        { format: fmtUsd });
      renderBars(document.getElementById("chart-tier-dist"),
        (d.tier_dist || []).map(function (t) {
          return { label: t.tier, value: t.count, color: tierColor(t.tier) };
        }), { format: fmtNum });
    }).catch(function (e) { console.error("overview", e); });
  }

  /* ── pane — calculator (Runner/Ledger/Batch live in their own modules) ──────────────────────────────────── */

  var CALC_FIELDS = [
    ["calc-tasks_per_month", "Tasks / month", "1"],
    ["calc-loaded_hourly_usd", "Loaded hourly $ (human)", "1"],
    ["calc-minutes_per_task_human", "Minutes / task (human)", "0.5"],
    ["calc-automatable_fraction", "Automatable fraction", "0.05"],
    ["calc-calls_per_task", "LLM calls / task", "1"],
    ["calc-tokens_in_per_call", "Tokens in / call", "50"],
    ["calc-tokens_out_per_call", "Tokens out / call", "50"],
    ["calc-human_review_fraction", "Human review fraction", "0.05"],
    ["calc-local_infra_usd_month", "Local infra $/month", "10"],
    ["calc-setup_fee_usd", "Setup fee $", "100"],
    ["calc-service_fee_usd_month", "Service fee $/month", "50"]
  ];
  var BOUND_FIELDS = CALC_FIELDS.map(function (f) { return f[0]; });
  var calcInputsBuilt = false;

  function buildCalcInputs() {
    if (calcInputsBuilt) return;
    var host = document.getElementById("calc-inputs");
    if (!host) return;
    var html = '<div class="form-grid">';
    CALC_FIELDS.forEach(function (f) {
      html += '<label class="field"><span class="field-label">' + escapeHtml(f[1]) + '</span>' +
        '<input type="number" id="' + f[0] + '" step="' + f[2] + '" inputmode="decimal"></label>';
    });
    html += '</div><div class="form-actions">' +
      '<button id="calc-recompute" type="button" class="btn btn-primary">Recompute</button>' +
      '<span class="form-hint">edits recompute automatically</span></div>';
    host.innerHTML = html;
    calcInputsBuilt = true;
  }

  function loadCalculator() {
    buildCalcInputs();
    apiGet("/api/calculator").then(function (d) {
      var inp = d.inputs || {};
      CALC_FIELDS.forEach(function (f) {
        var key = f[0].replace("calc-", "");
        var el = document.getElementById(f[0]);
        if (el && inp[key] != null) el.value = inp[key];
      });
      bindCalcInputs();
      renderCalcResults(d);
    }).catch(function (e) { console.error("calculator", e); });
  }

  function bindCalcInputs() {
    var run = debounce(computeCalculator, 300);
    BOUND_FIELDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener("input", run);
    });
    var btn = document.getElementById("calc-recompute");
    if (btn) btn.addEventListener("click", computeCalculator);
  }

  function computeCalculator() {
    var qs = [];
    BOUND_FIELDS.forEach(function (id) {
      var el = document.getElementById(id);
      var v = el ? parseFloat(el.value) : NaN;
      if (!isNaN(v)) qs.push(id.replace("calc-", "") + "=" + encodeURIComponent(v));
    });
    apiGet("/api/calculator" + (qs.length ? "?" + qs.join("&") : ""))
      .then(renderCalcResults).catch(console.error);
  }

  function renderCalcResults(d) {
    var el = document.getElementById("calc-results");
    if (!el || !d) return;
    var md = d.monthly_usd || {};
    var savings = d.client_net_savings_usd_month || {};
    var rec = d.recommended_configuration || "—";
    var payback = d.payback_months_on_setup_fee;

    // Build DOM first, THEN render the chart into the now-existing container.
    var recCard =
      '<div class="rec-card">' +
      '<div class="rec-row"><span class="rec-k">Recommended configuration</span>' +
      '<span class="rec-v"><span class="pill pill-accent">' + escapeHtml(String(rec).replace(/[_-]/g, " ")) + '</span></span></div>' +
      '<div class="rec-row"><span class="rec-k">Routed recommended</span><span class="rec-v strong">' +
      fmtUsd(md.routed_recommended) + '<span class="unit"> / mo</span></span></div>' +
      '<div class="rec-row"><span class="rec-k">Net savings vs human</span><span class="rec-v ' +
      (savings.vs_human_baseline >= 0 ? "pos" : "neg") + '">' + signedUsd(savings.vs_human_baseline) + '<span class="unit"> / mo</span></span></div>' +
      '<div class="rec-row"><span class="rec-k">Net savings vs naive AI</span><span class="rec-v ' +
      (savings.vs_naive_ai >= 0 ? "pos" : "neg") + '">' + signedUsd(savings.vs_naive_ai) + '<span class="unit"> / mo</span></span></div>' +
      '<div class="rec-row"><span class="rec-k">Payback on setup fee</span><span class="rec-v">' +
      (payback != null ? payback.toFixed(1) + " months" : "—") + '</span></div>' +
      '</div>';

    var honesty = (d.honesty || []);
    var honHtml = honesty.length
      ? '<details class="honesty" open><summary>Honesty notes</summary><ul>' +
        honesty.map(function (h) { return '<li>' + escapeHtml(h) + '</li>'; }).join("") + '</ul></details>'
      : "";

    el.innerHTML =
      '<div class="calc-cols">' +
      '<div class="calc-col"><h3>Four worlds <span class="h3-sub">monthly cost</span></h3>' +
      '<div id="calc-chart-worlds" class="chart"></div></div>' +
      '<div class="calc-col"><h3>Recommendation</h3>' + recCard + '</div>' +
      '</div>' + honHtml;

    renderBars(document.getElementById("calc-chart-worlds"), [
      { label: "Human baseline", value: md.human_baseline, color: "#f2555a" },
      { label: "Naive AI (all cloud)", value: md.naive_ai, color: "#f59e42" },
      { label: "Routed — local box", value: md.routed_local_box, color: "#5b9dff" },
      { label: "Routed — cloud only", value: md.routed_cloud_only, color: "#42c98a" }
    ], { format: fmtUsd });
  }

  /* ── util ─────────────────────────────────────────────────── */

  function debounce(fn, ms) {
    var t;
    return function () {
      var ctx = this, args = arguments;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(ctx, args); }, ms);
    };
  }

  /* ── init ─────────────────────────────────────────────────── */

  document.addEventListener("DOMContentLoaded", function () {
    checkStatus();
    setInterval(checkStatus, 15000);
    document.querySelectorAll(".tab").forEach(function (t) {
      t.addEventListener("click", function () { switchPane(t.getAttribute("data-pane")); });
    });
    var first = document.querySelector(".tab.active");
    switchPane(first ? first.getAttribute("data-pane") : "overview");
  });

  // Shared helpers for the runner.js module (Ornith-built) — one design system.
  window.ariaHelpers = {
    escapeHtml: escapeHtml, fmtUsd: fmtUsd, fmtNum: fmtNum, fmtPct: fmtPct, tierColor: tierColor
  };
})();
