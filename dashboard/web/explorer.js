(function(){
  "use strict";

  var H = window.ariaHelpers;
  if (!H) return;

  var state = {
    event: "all",
    harness: "all",
    model: "all",
    outcome: "all",
    q: "",
    limit: 100,
    offset: 0
  };

  var filtersEl = document.getElementById("ledger-filters");
  var summaryEl = document.getElementById("ledger-summary");
  var tableEl = document.getElementById("ledger-table");

  function buildQs() {
    var parts = [];
    if (state.event !== "all") parts.push("event=" + encodeURIComponent(state.event));
    if (state.harness !== "all") parts.push("harness=" + encodeURIComponent(state.harness));
    if (state.model !== "all") parts.push("model=" + encodeURIComponent(state.model));
    if (state.outcome !== "all") parts.push("outcome=" + encodeURIComponent(state.outcome));
    if (state.q) parts.push("q=" + encodeURIComponent(state.q));
    parts.push("limit=" + state.limit);
    parts.push("offset=" + state.offset);
    return parts.join("&");
  }

  function load() {
    var qs = buildQs();
    fetch("/api/ledger?" + qs)
      .then(function(res) { return res.json(); })
      .then(function(data) {
        renderFilters(data);
        renderSummary(data);
        renderTable(data);
      })
      .catch(function(err) {
        if (tableEl) {
          tableEl.innerHTML = '<div class="error">Failed to load ledger data.</div>';
        }
      });
  }

  function renderFilters(data) {
    if (!filtersEl) return;

    var isFirstLoad = filtersEl.children.length === 0;

    if (isFirstLoad) {
      filtersEl.innerHTML = '<div class="filter-bar"></div>';
    }

    var bar = filtersEl.querySelector(".filter-bar");
    if (!bar) {
      filtersEl.innerHTML = '<div class="filter-bar"></div>';
      bar = filtersEl.querySelector(".filter-bar");
    }

    var dims = ["event", "harness", "model", "outcome"];
    var labels = { event: "Event", harness: "Harness", model: "Model", outcome: "Outcome" };

    dims.forEach(function(dim) {
      var group = bar.querySelector('[data-dim="' + dim + '"]');
      if (!group) {
        group = document.createElement("label");
        group.className = "filter-group";
        group.setAttribute("data-dim", dim);
        var caption = document.createElement("span");
        caption.className = "filter-label";
        caption.textContent = labels[dim];
        var sel = document.createElement("select");
        sel.className = "filter-select";
        sel.setAttribute("data-dim", dim);
        group.appendChild(caption);
        group.appendChild(sel);
        bar.appendChild(group);
      }

      var sel = group.querySelector("select");
      var facets = data.facets && data.facets[dim];
      var currentVal = state[dim];

      sel.innerHTML = "";
      var allOpt = document.createElement("option");
      allOpt.value = "all";
      allOpt.textContent = "All";
      if (currentVal === "all") allOpt.selected = true;
      sel.appendChild(allOpt);

      if (facets) {
        Object.keys(facets).forEach(function(key) {
          var opt = document.createElement("option");
          opt.value = key;
          opt.textContent = key + " (" + facets[key] + ")";
          if (currentVal === key) opt.selected = true;
          sel.appendChild(opt);
        });
      }

      sel.onchange = function() {
        state[dim] = this.value;
        state.offset = 0;
        load();
      };
    });

    var searchInput = bar.querySelector(".ledger-search");
    if (!searchInput) {
      searchInput = document.createElement("input");
      searchInput.type = "text";
      searchInput.className = "ledger-search";
      searchInput.placeholder = "search task text…";
      bar.appendChild(searchInput);
    }

    searchInput.value = state.q;
    searchInput.onchange = function() {
      state.q = this.value;
      state.offset = 0;
      load();
    };

    var resetBtn = bar.querySelector(".btn-ghost");
    if (!resetBtn) {
      resetBtn = document.createElement("button");
      resetBtn.className = "btn btn-ghost";
      resetBtn.textContent = "Reset";
      bar.appendChild(resetBtn);
    }

    resetBtn.onclick = function() {
      state.event = "all";
      state.harness = "all";
      state.model = "all";
      state.outcome = "all";
      state.q = "";
      state.offset = 0;
      load();
    };
  }

  function renderSummary(data) {
    if (!summaryEl) return;
    var s = data.summary || {};
    var n = s.n || 0;
    var imputed = s.imputed_usd || 0;
    var inTok = s.in_tok || 0;
    var outTok = s.out_tok || 0;
    var total = data.total || 0;
    var rowsLen = data.rows ? data.rows.length : 0;

    var html = '<div class="ledger-summary">';
    html += '<b>' + H.fmtNum(n) + '</b> events match · ';
    html += '<b>' + H.fmtUsd(imputed) + '</b> imputed · ';
    html += '<b>' + H.fmtNum(inTok) + '</b> in / <b>' + H.fmtNum(outTok) + '</b> out tokens';

    if (total > rowsLen) {
      html += ' · <span class="muted">showing latest ' + rowsLen + ' of ' + total + '</span>';
    }

    html += '</div>';
    summaryEl.innerHTML = html;
  }

  function renderTable(data) {
    if (!tableEl) return;

    if (!data.rows || data.rows.length === 0) {
      tableEl.innerHTML = '<div class="table-scroll"><div class="empty">No events match this filter.</div></div>';
      return;
    }

    var html = '<div class="table-scroll"><table>';
    html += '<thead><tr>';
    html += '<th>Time</th><th>Event</th><th>Harness</th><th>Model</th><th>Tier</th><th>Task</th><th>Outcome</th><th>Tokens</th><th>Cost</th>';
    html += '</tr></thead><tbody>';

    data.rows.forEach(function(row) {
      html += '<tr>';

      var ts = row.ts;
      var timeStr = "";
      if (ts) {
        try {
          var d = new Date(ts);
          var hh = String(d.getHours()).padStart(2, "0");
          var mm = String(d.getMinutes()).padStart(2, "0");
          var ss = String(d.getSeconds()).padStart(2, "0");
          timeStr = hh + ":" + mm + ":" + ss;
        } catch (e) {
          timeStr = ts;
        }
      }
      html += '<td>' + timeStr + '</td>';

      var event = row.event || "";
      var eventClass = event === "usage" ? "pill pill-accent" : "pill";
      html += '<td><span class="' + eventClass + '">' + H.escapeHtml(event) + '</span></td>';

      html += '<td>' + H.escapeHtml(row.harness || "") + '</td>';
      html += '<td>' + H.escapeHtml(row.model || "") + '</td>';

      var tier = row.tier;
      if (tier) {
        var tierClass = tier.toLowerCase().replace(/\s+/g, "-");
        html += '<td><span class="tier-badge ' + tierClass + '">' + H.escapeHtml(tier) + '</span></td>';
      } else {
        html += '<td>—</td>';
      }

      var task = row.task_text || "";
      var displayTask = task.length > 70 ? task.substring(0, 70) + "…" : task;
      html += '<td title="' + H.escapeHtml(task) + '">' + H.escapeHtml(displayTask) + '</td>';

      var outcome = row.outcome;
      if (event === "route_decision" || outcome === null || outcome === undefined) {
        html += '<td>—</td>';
      } else {
        var outcomeClass = outcome === "success" ? "pill pill-ok" : (outcome === "failure" ? "pill pill-bad" : "pill");
        html += '<td><span class="' + outcomeClass + '">' + H.escapeHtml(outcome) + '</span></td>';
      }

      if (event === "usage" && row.in_tok !== null && row.out_tok !== null) {
        html += '<td>' + H.fmtNum(row.in_tok) + ' / ' + H.fmtNum(row.out_tok) + '</td>';
      } else {
        html += '<td>—</td>';
      }

      var cost = row.usd || 0;
      html += '<td>' + H.fmtUsd(cost) + '</td>';

      html += '</tr>';
    });

    html += '</tbody></table></div>';
    tableEl.innerHTML = html;
  }

  document.addEventListener("DOMContentLoaded", function() {
    load();

    var ledgerTab = document.querySelector('[data-pane="ledger"]');
    if (ledgerTab) {
      ledgerTab.addEventListener("click", function() {
        load();
      });
    }
  });
})();
