(function(){
  "use strict";
  var H = window.ariaHelpers;

  var controlsEl = document.getElementById("batch-controls");
  var summaryEl  = document.getElementById("batch-summary");
  var confusionEl= document.getElementById("batch-confusion");
  var tableEl    = document.getElementById("batch-table");

  function esc(s){ return H.escapeHtml(s); }

  function tierLower(t){ return (t||"").toLowerCase(); }

  function loadDatasets(){
    fetch("/api/datasets")
      .then(function(r){ return r.json(); })
      .then(function(data){
        var datasets = data.datasets || [];
        controlsEl.innerHTML = "";
        var bar = document.createElement("div");
        bar.className = "batch-controls";

        var label = document.createElement("label");
        label.className = "filter-group";
        var span = document.createElement("span");
        span.className = "filter-label";
        span.textContent = "Dataset";
        label.appendChild(span);

        var sel = document.createElement("select");
        sel.id = "batch-dataset";
        var defaultName = "labeled_tasks_balanced.jsonl";
        var hasDefault = false;
        datasets.forEach(function(d){
          var opt = document.createElement("option");
          opt.value = d.name;
          opt.textContent = d.name + " — " + d.labeled + " labeled";
          if(d.name === defaultName) hasDefault = true;
          sel.appendChild(opt);
        });
        if(hasDefault) sel.value = defaultName;
        label.appendChild(sel);

        var cbLabel = document.createElement("label");
        cbLabel.className = "checkbox";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.id = "batch-log";
        cb.checked = true;
        cbLabel.appendChild(cb);
        cbLabel.appendChild(document.createTextNode(" log to ledger"));
        var muted = document.createElement("span");
        muted.className = "muted";
        muted.textContent = " (as dashboard-batch)";
        cbLabel.appendChild(muted);

        var btn = document.createElement("button");
        btn.id = "batch-run";
        btn.className = "btn btn-primary";
        btn.textContent = "Run batch";

        bar.appendChild(label);
        bar.appendChild(cbLabel);
        bar.appendChild(btn);
        controlsEl.appendChild(bar);

        btn.addEventListener("click", onRunClick);
      })
      .catch(function(err){
        controlsEl.innerHTML = '<div class="error">Failed to load datasets: ' + esc(String(err)) + '</div>';
      });
  }

  function onRunClick(){
    var sel = document.getElementById("batch-dataset");
    var cb  = document.getElementById("batch-log");
    var btn = document.getElementById("batch-run");
    if(!sel || !cb || !btn) return;

    var dataset = sel.value;
    var log = cb.checked;

    btn.disabled = true;
    summaryEl.innerHTML = '<div class="loading">Routing all tasks through the classifier… this can take a minute or two.</div>';
    confusionEl.innerHTML = "";
    tableEl.innerHTML = "";

    fetch("/api/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset: dataset, log: log })
    })
    .then(function(r){ return r.json(); })
    .then(function(data){
      if(data.error){
        summaryEl.innerHTML = '<div class="error">Batch error: ' + esc(data.error) + '</div>';
        btn.disabled = false;
        return;
      }
      var summary = data.summary || {};
      var rows = data.rows || [];
      renderSummary(summary);
      renderConfusion(summary);
      renderTable(rows);
      btn.disabled = false;
    })
    .catch(function(err){
      summaryEl.innerHTML = '<div class="error">Request failed: ' + esc(String(err)) + '</div>';
      btn.disabled = false;
    });
  }

  function renderSummary(summary){
    var n = summary.n || 0;
    var n_labeled = summary.n_labeled || 0;
    var n_agree = summary.n_agree || 0;
    var accuracy = summary.accuracy;
    var logged = !!summary.logged;

    var accText;
    if(accuracy === null || accuracy === undefined){
      accText = esc("— (no labels)");
    } else {
      accText = esc(H.fmtPct(accuracy));
    }

    var detailParts = [];
    detailParts.push(esc(n_agree) + "/" + esc(n_labeled) + " labeled tasks routed to the expected tier");
    detailParts.push(esc(n) + " total");
    if(logged) detailParts.push("logged to ledger");
    var detail = detailParts.join(" · ");

    summaryEl.innerHTML =
      '<div class="batch-summary-card">' +
        '<div class="batch-acc-wrap">' +
          '<span class="batch-acc">' + accText + '</span> tier agreement' +
        '</div>' +
        '<div class="batch-detail muted">' + detail + '</div>' +
      '</div>';
  }

  function renderConfusion(summary){
    if(!summary || !summary.tiers) return;
    var tiers = summary.tiers;
    var confusion = summary.confusion || {};
    var n_labeled = summary.n_labeled || 0;

    confusionEl.innerHTML = "";

    if(n_labeled === 0){
      confusionEl.innerHTML = '<h3>Confusion matrix</h3><div class="muted">No labeled tasks in this dataset — nothing to compare.</div>';
      return;
    }

    var wrap = document.createElement("div");
    wrap.className = "table-scroll";
    var table = document.createElement("table");
    table.className = "confusion";

    var thead = document.createElement("thead");
    var headerRow = document.createElement("tr");
    var corner = document.createElement("th");
    corner.className = "cm-corner";
    corner.textContent = "exp \\ routed";
    headerRow.appendChild(corner);
    tiers.forEach(function(t){
      var th = document.createElement("th");
      var badge = document.createElement("span");
      badge.className = "tier-badge " + tierLower(t);
      badge.textContent = t;
      th.appendChild(badge);
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    tiers.forEach(function(expected){
      var tr = document.createElement("tr");
      var th = document.createElement("th");
      var badge = document.createElement("span");
      badge.className = "tier-badge " + tierLower(expected);
      badge.textContent = expected;
      th.appendChild(badge);
      tr.appendChild(th);

      tiers.forEach(function(routed){
        var td = document.createElement("td");
        var count = ((confusion[expected]||{})[routed] || 0);
        td.textContent = count;
        if(expected === routed && count > 0){
          td.className = "cm-correct";
        } else if(expected !== routed && count > 0){
          td.className = "cm-miss";
        } else {
          td.className = "cm-zero";
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    confusionEl.appendChild(wrap);
  }

  function renderTable(rows){
    tableEl.innerHTML = "";
    var heading = document.createElement("h3");
    heading.textContent = "Per-task results";
    tableEl.appendChild(heading);

    if(!rows || rows.length === 0){
      var empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "No results yet.";
      tableEl.appendChild(empty);
      return;
    }

    var sorted = rows.slice().sort(function(a, b){
      var aBad = (a.agree === false) ? 1 : 0;
      var bBad = (b.agree === false) ? 1 : 0;
      if(aBad !== bBad) return bBad - aBad;  // disagreements first, for inspection
      return 0;
    });

    var wrap = document.createElement("div");
    wrap.className = "table-scroll";
    var table = document.createElement("table");

    var thead = document.createElement("thead");
    var hRow = document.createElement("tr");
    ["Expected","Routed","Match","Task"].forEach(function(h){
      var th = document.createElement("th");
      th.textContent = h;
      hRow.appendChild(th);
    });
    thead.appendChild(hRow);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    sorted.forEach(function(row){
      var tr = document.createElement("tr");
      if(row.agree === false){
        tr.className = "row-miss";
      }

      var tdExp = document.createElement("td");
      if(row.expected_tier){
        var b1 = document.createElement("span");
        b1.className = "tier-badge " + tierLower(row.expected_tier);
        b1.textContent = row.expected_tier;
        tdExp.appendChild(b1);
      } else {
        tdExp.textContent = "—";
      }
      tr.appendChild(tdExp);

      var tdRouted = document.createElement("td");
      if(row.routed_tier){
        var b2 = document.createElement("span");
        b2.className = "tier-badge " + tierLower(row.routed_tier);
        b2.textContent = row.routed_tier;
        tdRouted.appendChild(b2);
      } else {
        tdRouted.textContent = "—";
      }
      tr.appendChild(tdRouted);

      var tdMatch = document.createElement("td");
      if(row.agree === true){
        var pOk = document.createElement("span");
        pOk.className = "pill pill-ok";
        pOk.textContent = "✓";
        tdMatch.appendChild(pOk);
      } else if(row.agree === false){
        var pBad = document.createElement("span");
        pBad.className = "pill pill-bad";
        pBad.textContent = "✗";
        tdMatch.appendChild(pBad);
      } else {
        tdMatch.textContent = "—";
      }
      tr.appendChild(tdMatch);

      var tdTask = document.createElement("td");
      if(row.error){
        var errSpan = document.createElement("span");
        errSpan.className = "error";
        errSpan.textContent = row.error;
        tdTask.appendChild(errSpan);
      } else {
        var taskText = row.task || "";
        tdTask.textContent = taskText.length > 80 ? taskText.substring(0, 80) + "…" : taskText;
        tdTask.title = taskText;
      }
      tr.appendChild(tdTask);

      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    tableEl.appendChild(wrap);
  }

  document.addEventListener("DOMContentLoaded", function(){
    loadDatasets();
  });
})();
