(function() {
  "use strict";

  /* ── helpers ──────────────────────────────────────────────── */

  function escapeHtml(s) {
    if (s == null) return "";
    var str = String(s);
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function renderBars(targetEl, data, valueKey, labelKey) {
    if (!targetEl || !data || !data.length) {
      if (targetEl) targetEl.innerHTML = "<em>No data</em>";
      return;
    }
    var max = 0;
    for (var i = 0; i < data.length; i++) {
      if (data[i][valueKey] > max) max = data[i][valueKey];
    }
    if (!max) max = 1;
    var html = "";
    for (var j = 0; j < data.length; j++) {
      var d = data[j];
      var pct = Math.round((d[valueKey] / max) * 100);
      html += '<div class="bar-row">';
      html += '<span class="bar-label">' + escapeHtml(d[labelKey] || d.tier || d.model || "?") + '</span>';
      html += '<div class="bar-track"><div class="bar-fill" style="width:' + pct + '%"></div></div>';
      html += '<span class="bar-val">' + escapeHtml(d[valueKey]) + '</span>';
      html += '</div>';
    }
    targetEl.innerHTML = html;
  }

  async function apiGet(url) {
    var resp = await fetch(url);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  }

  async function apiPost(url, body) {
    var resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  }

  /* ── status dot ──────────────────────────────────────────── */

  function checkStatus() {
    apiGet("/api/overview")
      .then(function() { document.getElementById("status-dot").className = "status-dot green"; })
      .catch(function() { document.getElementById("status-dot").className = "status-dot red"; });
  }

  /* ── tab switching ───────────────────────────────────────── */

  var loaded = {};

  function switchPane(name) {
    var tabs = document.querySelectorAll(".tab");
    var panes = document.querySelectorAll("[id^='pane-']");
    for (var i = 0; i < tabs.length; i++) {
      var t = tabs[i];
      if (t.getAttribute("data-pane") === name) {
        t.classList.add("active");
      } else {
        t.classList.remove("active");
      }
    }
    for (var j = 0; j < panes.length; j++) {
      var p = panes[j];
      if (p.id === "pane-" + name) {
        p.classList.add("active");
      } else {
        p.classList.remove("active");
      }
    }
    if (!loaded[name]) {
      loaded[name] = true;
      loadPane(name);
    }
  }

  function loadPane(name) {
    switch (name) {
      case "overview": loadOverview(); break;
      case "router": break;
      case "classifier": loadClassifier(); break;
      case "calculator": loadCalculator(); break;
      case "liverun": loadLiverun(); break;
    }
  }

  /* ── pane 1 — overview ───────────────────────────────────── */

  function loadOverview() {
    apiGet("/api/overview").then(function(d) {
      var cards = document.getElementById("overview-cards");
      if (!cards || !d) return;

      function card(title, value, sub) {
        var h = '<div class="card">';
        h += '<div class="card-title">' + escapeHtml(title) + '</div>';
        h += '<div class="card-value">' + escapeHtml(value) + '</div>';
        if (sub) h += '<div class="card-sub">' + escapeHtml(sub) + '</div>';
        h += '</div>';
        return h;
      }

      var html = "";
      html += card("Imputed cost avoided", "$" + (d.imputed_usd || 0).toFixed(4),
        "list-rate what-if cost (cloud at list prices) — not real spend");
      html += card("Actual spend", "$" + (d.actual_usd || 0).toFixed(4),
        "real dollars charged");
      html += card("Saved", "$" + (d.saved_usd || 0).toFixed(4),
        "imputed minus actual");
      html += card("Usage events", d.events || 0, "");
      html += card("Route decisions", d.route_decisions || 0, "");
      html += card("Local-first tiers", (d.local_first_pct || 0) + "%",
        "config fact (which tiers route local) — not a quality claim");
      cards.innerHTML = html;

      renderBars(document.getElementById("chart-by-model"), d.by_model || [], "usd", "model");
      renderBars(document.getElementById("chart-tier-dist"), d.tier_dist || [], "count", "tier");
    }).catch(function(e) {
      console.error("overview load failed", e);
    });
  }

  /* ── pane 2 — router ─────────────────────────────────────── */

  function tierColor(tier) {
    switch ((tier || "").toUpperCase()) {
      case "CRITICAL": return "#dc2626";
      case "COMPLEX":  return "#ea580c";
      case "MODERATE": return "#2563eb";
      case "SIMPLE":   return "#16a34a";
      default:         return "#6b7280";
    }
  }

  function loadRouter() {
    var form = document.getElementById("router-form");
    if (!form) return;
    form.addEventListener("submit", function(e) {
      e.preventDefault();
      var input = document.getElementById("router-input");
      var result = document.getElementById("router-result");
      var btn = form.querySelector('button[type="submit"]');
      if (!input || !result || !btn) return;

      var task = input.value.trim();
      if (!task) return;

      btn.disabled = true;
      result.innerHTML = '<em>Loading…</em>';

      apiPost("/api/classify", { task: task }).then(function(d) {
        var tier = d.tier || "?";
        var color = tierColor(tier);
        var keywordNote = d.keyword_matched
          ? '<span class="badge">keyword rule fired</span>'
          : '<span class="badge muted">defaulted / low-confidence</span>';

        var altRows = "";
        if (d.alternatives && d.alternatives.length) {
          altRows = '<ol class="alt-list">';
          for (var i = 0; i < d.alternatives.length; i++) {
            var a = d.alternatives[i];
            altRows += '<li>' + escapeHtml(a.model) + ' → $' +
              (a.estimated_cost != null ? a.estimated_cost.toFixed(4) : "?") + '</li>';
          }
          altRows += '</ol>';
        }

        result.innerHTML =
          '<div class="router-tier" style="background:' + color + ';">' + escapeHtml(tier) + '</div>' +
          '<div class="router-kw">' + keywordNote + '</div>' +
          '<div><strong>Chosen model:</strong> ' + escapeHtml(d.chosen_model || "?") + '</div>' +
          '<div><strong>Estimated cost:</strong> $' +
          (d.estimated_usd != null ? d.estimated_usd.toFixed(4) : "?") + '</div>' +
          '<div><strong>Reason:</strong> ' + escapeHtml(d.reason || "") + '</div>' +
          (altRows ? '<div><strong>Alternatives:</strong></div>' + altRows : '');
      }).catch(function(err) {
        result.innerHTML = '<span class="error">Error: ' + escapeHtml(err.message) + '</span>';
      }).finally(function() {
        btn.disabled = false;
      });
    });
  }

  /* ── pane 3 — classifier ─────────────────────────────────── */

  function loadClassifier() {
    apiGet("/api/classification").then(function(d) {
      var headline = document.getElementById("classifier-headline");
      var detail = document.getElementById("classifier-detail");
      if (!headline || !detail || !d) return;

      var pb = d.prose_blind || {};
      var kt = d.keyword_tuned || {};

      headline.innerHTML =
        '<div class="classifier-h1">' +
        (pb.accuracy != null ? (pb.accuracy * 100).toFixed(1) : "?") + '%</div>' +
        '<div class="classifier-sub">' +
        'on keyword-blind prose (n=' + (pb.n || "?") + ') — the honest number</div>';

      var smallHtml = '';
      if (kt && kt.accuracy != null) {
        smallHtml =
          '<div class="classifier-small">' +
          '<span class="muted">(</span>' +
          (kt.accuracy * 100).toFixed(1) + '% — ' +
          '<em>tuning target, self-fulfilling by construction — drift detection only</em>' +
          '<span class="muted">)</span></div>';
      }
      headline.innerHTML += smallHtml;

      // per-tier table
      var pt = pb.per_tier || {};
      var tierKeys = Object.keys(pt);
      if (tierKeys.length) {
        var tHtml = '<table class="tier-table"><thead><tr>' +
          '<th>Tier</th><th>Precision</th><th>Recall</th><th>Support</th></tr></thead><tbody>';
        for (var i = 0; i < tierKeys.length; i++) {
          var tk = tierKeys[i];
          var td = pt[tk] || {};
          tHtml += '<tr>' +
            '<td>' + escapeHtml(tk) + '</td>' +
            '<td>' + (td.precision != null ? (td.precision * 100).toFixed(1) + '%' : '—') + '</td>' +
            '<td>' + (td.recall != null ? (td.recall * 100).toFixed(1) + '%' : '—') + '</td>' +
            '<td>' + (td.support != null ? td.support : '—') + '</td>' +
            '</tr>';
        }
        tHtml += '</tbody></table>';
        detail.innerHTML = tHtml;
      } else {
        detail.innerHTML = '<em>No per-tier breakdown available</em>';
      }
    }).catch(function(e) {
      console.error("classifier load failed", e);
    });
  }

  /* ── pane 4 — calculator ─────────────────────────────────── */

  var calcCache = null;

  // Opus hand-holding fix: the model wrote all the fill/bind/read logic but never
  // generated the input elements — build them here so fillInput/bindCalcInputs work.
  var CALC_FIELDS = [
    ["calc-tasks_per_month", "Tasks / month", "1"],
    ["calc-loaded_hourly_usd", "Loaded hourly $ (human)", "any"],
    ["calc-minutes_per_task_human", "Minutes / task (human)", "any"],
    ["calc-automatable_fraction", "Automatable fraction (0-1)", "0.01"],
    ["calc-calls_per_task", "LLM calls / task", "1"],
    ["calc-tokens_in_per_call", "Tokens in / call", "1"],
    ["calc-tokens_out_per_call", "Tokens out / call", "1"],
    ["calc-human_review_fraction", "Human review fraction (0-1)", "0.01"],
    ["calc-local_infra_usd_month", "Local infra $/month", "any"],
    ["calc-setup_fee_usd", "Setup fee $", "any"],
    ["calc-service_fee_usd_month", "Service fee $/month", "any"]
  ];
  var calcInputsBuilt = false;
  function buildCalcInputs() {
    if (calcInputsBuilt) return;
    var host = document.getElementById("calc-inputs");
    if (!host) return;
    var html = '<div class="calc-grid">';
    for (var i = 0; i < CALC_FIELDS.length; i++) {
      var f = CALC_FIELDS[i];
      html += '<label class="calc-field"><span>' + f[1] + '</span>' +
              '<input type="number" id="' + f[0] + '" step="' + f[2] + '"></label>';
    }
    html += '</div><button id="calc-recompute" type="button">Recompute</button>';
    host.innerHTML = html;
    calcInputsBuilt = true;
  }

  function loadCalculator() {
    buildCalcInputs();
    apiGet("/api/calculator").then(function(d) {
      calcCache = d;
      var inputs = document.getElementById("calc-inputs");
      if (!inputs || !d || !d.inputs) return;
      var inp = d.inputs;
      fillInput("calc-tasks_per_month", inp.tasks_per_month);
      fillInput("calc-loaded_hourly_usd", inp.loaded_hourly_usd);
      fillInput("calc-minutes_per_task_human", inp.minutes_per_task_human);
      fillInput("calc-automatable_fraction", inp.automatable_fraction);
      fillInput("calc-calls_per_task", inp.calls_per_task);
      fillInput("calc-tokens_in_per_call", inp.tokens_in_per_call);
      fillInput("calc-tokens_out_per_call", inp.tokens_out_per_call);
      fillInput("calc-human_review_fraction", inp.human_review_fraction);
      fillInput("calc-local_infra_usd_month", inp.local_infra_usd_month);
      fillInput("calc-setup_fee_usd", inp.setup_fee_usd);
      fillInput("calc-service_fee_usd_month", inp.service_fee_usd_month);

      bindCalcInputs();
      renderCalcResults(d);
    }).catch(function(e) {
      console.error("calculator load failed", e);
    });
  }

  function fillInput(id, val) {
    var el = document.getElementById(id);
    if (el && val != null) el.value = val;
  }

  function bindCalcInputs() {
    var ids = [
      "calc-tasks_per_month", "calc-loaded_hourly_usd", "calc-minutes_per_task_human",
      "calc-automatable_fraction", "calc-human_review_fraction", "calc-setup_fee_usd",
      "calc-service_fee_usd_month"
    ];
    for (var i = 0; i < ids.length; i++) {
      (function(id) {
        var el = document.getElementById(id);
        if (!el) return;
        el.addEventListener("input", debounce(function() {
          computeCalculator();
        }, 300));
      })(ids[i]);
    }
    var btn = document.getElementById("calc-recompute");
    if (btn) btn.addEventListener("click", computeCalculator);
  }

  function getCalcParam(id) {
    var el = document.getElementById(id);
    if (!el) return undefined;
    var v = parseFloat(el.value);
    return isNaN(v) ? undefined : v;
  }

  function computeCalculator() {
    var params = [];
    var pairs = [
      ["tasks_per_month", "calc-tasks_per_month"],
      ["loaded_hourly_usd", "calc-loaded_hourly_usd"],
      ["minutes_per_task_human", "calc-minutes_per_task_human"],
      ["automatable_fraction", "calc-automatable_fraction"],
      ["human_review_fraction", "calc-human_review_fraction"],
      ["setup_fee_usd", "calc-setup_fee_usd"],
      ["service_fee_usd_month", "calc-service_fee_usd_month"]
    ];
    for (var i = 0; i < pairs.length; i++) {
      var v = getCalcParam(pairs[i][1]);
      if (v != null) params.push(pairs[i][0] + "=" + encodeURIComponent(v));
    }
    if (params.length) {
      apiGet("/api/calculator?" + params.join("&")).then(renderCalcResults).catch(console.error);
    }
  }

  function renderCalcResults(d) {
    var results = document.getElementById("calc-results");
    if (!results || !d) return;

    var md = d.monthly_usd || {};
    var rc = d.recommended_configuration || {};
    var savings = d.client_net_savings_usd_month || {};
    var honesty = d.honesty || [];

    var chartData = [
      { label: "Human baseline", value: md.human_baseline },
      { label: "Naive AI", value: md.naive_ai },
      { label: "Routed (local box)", value: md.routed_local_box },
      { label: "Routed (cloud only)", value: md.routed_cloud_only }
    ];
    renderBars(document.getElementById("calc-chart-worlds"), chartData, "value", "label");

    var recHtml = "";
    recHtml += '<div><strong>Recommended configuration:</strong> ' +
      escapeHtml(rc.description || JSON.stringify(rc) || "—") + '</div>';
    recHtml += '<div><strong>Routed recommended monthly cost:</strong> $' +
      (md.routed_recommended != null ? md.routed_recommended.toFixed(4) : "?") + '</div>';
    recHtml += '<div><strong>Net savings vs human baseline:</strong> $' +
      (savings.vs_human_baseline != null ? savings.vs_human_baseline.toFixed(4) : "?") + '</div>';
    recHtml += '<div><strong>Net savings vs naive AI:</strong> $' +
      (savings.vs_naive_ai != null ? savings.vs_naive_ai.toFixed(4) : "?") + '</div>';
    recHtml += '<div><strong>Payback on setup fee:</strong> ' +
      (rc.payback_months_on_setup_fee != null ? rc.payback_months_on_setup_fee.toFixed(1) + ' months' : '—') + '</div>';

    var honHtml = "";
    if (honesty.length) {
      honHtml = '<div class="honesty"><strong>Honesty notes:</strong><ul>';
      for (var i = 0; i < honesty.length; i++) {
        honHtml += '<li>' + escapeHtml(honesty[i]) + '</li>';
      }
      honHtml += '</ul></div>';
    }

    results.innerHTML =
      '<h3>Four worlds</h3>' +
      '<div id="calc-chart-worlds"></div>' +
      '<h3>Recommendation</h3>' +
      recHtml +
      honHtml;
  }

  /* ── pane 5 — live run ───────────────────────────────────── */

  function loadLiverun() {
    apiGet("/api/liverun").then(function(d) {
      var summary = document.getElementById("liverun-summary");
      var table = document.getElementById("liverun-table");
      if (!summary || !table || !d) return;

      var meta = d.run_meta || {};
      var metaKeys = Object.keys(meta);
      var metaHtml = "<dl>";
      for (var i = 0; i < metaKeys.length; i++) {
        var k = metaKeys[i];
        metaHtml += '<dt>' + escapeHtml(k) + '</dt><dd>' +
          escapeHtml(meta[k]) + '</dd>';
      }
      metaHtml += "</dl>";
      summary.innerHTML = metaHtml;

      // arm_hybrid is a dict; the per-task rows live under .records (Opus fix)
      var records = (d.arm_hybrid && d.arm_hybrid.records) || [];
      if (!records.length) {
        table.innerHTML = "<em>No arm_hybrid records yet</em>";
        return;
      }

      // Inspect shape from first record
      var sample = records[0];
      var keys = Object.keys(sample);
      var tHtml = "<table><thead><tr>";
      for (var j = 0; j < keys.length; j++) {
        tHtml += "<th>" + escapeHtml(keys[j]) + "</th>";
      }
      tHtml += "</tr></thead><tbody>";
      for (var r = 0; r < records.length; r++) {
        tHtml += "<tr>";
        for (var k = 0; k < keys.length; k++) {
          var v = records[r][keys[k]];
          if (v && typeof v === "object") v = JSON.stringify(v);
          tHtml += "<td>" + escapeHtml(v) + "</td>";
        }
        tHtml += "</tr>";
      }
      tHtml += "</tbody></table>";
      table.innerHTML = tHtml;
    }).catch(function(e) {
      console.error("liverun load failed", e);
    });
  }

  /* ── debounce ────────────────────────────────────────────── */

  function debounce(fn, ms) {
    var t;
    return function() {
      var ctx = this, args = arguments;
      clearTimeout(t);
      t = setTimeout(function() { fn.apply(ctx, args); }, ms);
    };
  }

  /* ── init ────────────────────────────────────────────────── */

  document.addEventListener("DOMContentLoaded", function() {
    checkStatus();

    // Tab clicks
    var tabs = document.querySelectorAll(".tab");
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].addEventListener("click", function() {
        switchPane(this.getAttribute("data-pane"));
      });
    }

    // Router form
    loadRouter();

    // Auto-load first active pane if any
    var firstActive = document.querySelector(".tab.active");
    if (firstActive) {
      switchPane(firstActive.getAttribute("data-pane"));
    }
  });

})();
