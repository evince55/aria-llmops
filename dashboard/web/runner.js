(function(){
  "use strict";
  var H = window.ariaHelpers;

  var currentRunId = null;
  var currentTask = null;

  function postJson(path, body) {
    try {
      return fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).then(function(res) {
        return res.json().then(function(data) {
          if (!res.ok && data.error) return data;
          return data;
        });
      }).catch(function() { return { error: "Network error" }; });
    } catch (e) {
      return Promise.resolve({ error: "Network error" });
    }
  }

  function esc(str) {
    return H.escapeHtml(str || "");
  }

  function renderDecisionCard(data) {
    var result = document.getElementById("runner-result");
    var card = document.createElement("div");
    card.className = "card runner-decision";

    var taskDiv = document.createElement("div");
    taskDiv.className = "muted";
    taskDiv.style.marginBottom = "0.5em";
    taskDiv.textContent = data.task || "";
    card.appendChild(taskDiv);

    var metaRow = document.createElement("div");
    metaRow.className = "kv";
    metaRow.style.marginBottom = "0.5em";

    var tierSpan = document.createElement("span");
    tierSpan.className = "tier-badge " + (data.tier || "").toLowerCase();
    tierSpan.textContent = data.tier || "—";
    metaRow.appendChild(tierSpan);

    var modelSpan = document.createElement("span");
    modelSpan.className = "muted";
    modelSpan.textContent = " routed to " + (data.model || "—");
    metaRow.appendChild(modelSpan);

    var costSpan = document.createElement("span");
    costSpan.className = "muted";
    costSpan.textContent = " · " + H.fmtUsd(data.estimated_usd || 0);
    metaRow.appendChild(costSpan);

    card.appendChild(metaRow);

    if (data.reason) {
      var reasonP = document.createElement("p");
      reasonP.className = "muted";
      reasonP.textContent = data.reason;
      card.appendChild(reasonP);
    }

    if (data.alternatives && data.alternatives.length) {
      var altRow = document.createElement("div");
      altRow.style.marginTop = "0.75em";
      altRow.style.display = "flex";
      altRow.style.flexWrap = "wrap";
      altRow.style.gap = "0.35em";
      data.alternatives.forEach(function(alt) {
        var pill = document.createElement("span");
        pill.className = "pill";
        if (alt.model === data.model) pill.className += " pill-accent";
        pill.textContent = alt.model + " · " + H.fmtUsd(alt.estimated_cost || 0);
        altRow.appendChild(pill);
      });
      card.appendChild(altRow);
    }

    if (data.executed) {
      var usageRow = document.createElement("div");
      usageRow.className = "kv";
      usageRow.style.marginTop = "1em";
      var usageK = document.createElement("span");
      usageK.className = "k";
      usageK.textContent = "Tokens";
      usageRow.appendChild(usageK);
      var usageV = document.createElement("span");
      usageV.className = "v";
      var u = data.usage || {};
      usageV.textContent = "in " + H.fmtNum(u.input_tokens || 0) + " / out " + H.fmtNum(u.output_tokens || 0);
      usageRow.appendChild(usageV);
      card.appendChild(usageRow);

      if (data.output != null) {
        var pre = document.createElement("pre");
        pre.style.marginTop = "0.5em";
        pre.style.whiteSpace = "pre-wrap";
        pre.style.wordBreak = "break-word";
        pre.textContent = data.output;
        card.appendChild(pre);
      }

      if (data.exec_error) {
        var errP = document.createElement("p");
        errP.className = "runner-error";
        errP.textContent = "Error: " + data.exec_error;
        card.appendChild(errP);
      }
    }

    var outcomeRow = document.createElement("div");
    outcomeRow.id = "runner-outcome-row";
    outcomeRow.className = "runner-sep";

    var outcomeLabel = document.createElement("span");
    outcomeLabel.textContent = "How did it go? ";
    outcomeLabel.className = "muted";
    outcomeRow.appendChild(outcomeLabel);

    var successBtn = document.createElement("button");
    successBtn.className = "btn btn-primary";
    successBtn.textContent = "✓ Success";
    successBtn.style.marginRight = "0.5em";
    successBtn.addEventListener("click", function() { submitOutcome("success"); });
    outcomeRow.appendChild(successBtn);

    var failBtn = document.createElement("button");
    failBtn.className = "btn btn-ghost";
    failBtn.textContent = "✗ Failure";
    failBtn.addEventListener("click", function() { submitOutcome("failure"); });
    outcomeRow.appendChild(failBtn);

    card.appendChild(outcomeRow);

    var captureRow = document.createElement("div");
    captureRow.className = "runner-sep";

    var capLabel = document.createElement("span");
    capLabel.textContent = "Capture as labeled example: ";
    capLabel.className = "muted";
    captureRow.appendChild(capLabel);

    var tierSelect = document.createElement("select");
    tierSelect.style.marginRight = "0.5em";
    tierSelect.style.padding = "0.25em 0.5em";
    ["SIMPLE", "MODERATE", "COMPLEX", "CRITICAL"].forEach(function(t) {
      var opt = document.createElement("option");
      opt.value = t;
      opt.textContent = t;
      if (t === (data.tier || "").toUpperCase()) opt.selected = true;
      tierSelect.appendChild(opt);
    });
    captureRow.appendChild(tierSelect);

    var capBtn = document.createElement("button");
    capBtn.className = "btn btn-ghost";
    capBtn.textContent = "Capture";
    capBtn.addEventListener("click", function() { submitCapture(tierSelect.value); });
    captureRow.appendChild(capBtn);

    var capStatus = document.createElement("span");
    capStatus.id = "runner-capture-status";
    capStatus.className = "muted";
    capStatus.style.marginLeft = "0.5em";
    captureRow.appendChild(capStatus);

    card.appendChild(captureRow);

    result.innerHTML = "";
    result.appendChild(card);
  }

  function submitOutcome(outcome) {
    if (!currentRunId) return;
    var row = document.getElementById("runner-outcome-row");
    postJson("/api/run/outcome", { run_id: currentRunId, outcome: outcome })
      .then(function(res) {
        if (res.error) {
          if (row) row.innerHTML = '<span class="runner-error">Grade failed: ' + esc(res.error) + '</span>';
          return;
        }
        if (row) row.innerHTML = '<span class="muted">Graded: </span><span class="pill ' +
          (outcome === "success" ? "pill-ok" : "pill-bad") + '">' + esc(outcome) + '</span>';
        currentRunId = null;  // one grade per run — a second would double-log usage
        loadRecent();
      })
      .catch(function() {});
  }

  function submitCapture(tier) {
    if (!currentTask) return;
    var status = document.getElementById("runner-capture-status");
    postJson("/api/dataset/capture", { task: currentTask, tier: tier })
      .then(function(res) {
        if (!status) return;
        status.className = res.error ? "runner-error" : "muted";
        status.textContent = res.error
          ? ("Capture failed: " + res.error)
          : ("Captured (" + (res.captured_total || 0) + " total)");
      })
      .catch(function() {});
  }

  function loadRecent() {
    var container = document.getElementById("runner-recent");
    try {
      fetch("/api/runs?limit=25")
        .then(function(res) { return res.json(); })
        .then(function(data) {
          if (!data || !data.runs) {
            container.innerHTML = '<p class="muted">No runs yet — submit a task above.</p>';
            return;
          }
          var runs = data.runs;
          var total = data.total || 0;

          if (total === 0 || !runs.length) {
            container.innerHTML = '<p class="muted">No runs yet — submit a task above.</p>';
            return;
          }

          var list = document.createElement("div");
          list.className = "run-list";

          runs.forEach(function(e) {
            var item = document.createElement("div");
            item.className = "run-item";

            var tierBadge = document.createElement("span");
            var tier = e.complexity || e.tier || "";
            tierBadge.className = "tier-badge " + (tier || "").toLowerCase();
            tierBadge.textContent = tier || "—";
            item.appendChild(tierBadge);

            var taskText = e.task_text || "";
            var taskSpan = document.createElement("span");
            taskSpan.className = "muted";
            if (taskText.length > 90) {
              taskSpan.textContent = taskText.substring(0, 90) + "…";
            } else {
              taskSpan.textContent = taskText;
            }
            item.appendChild(taskSpan);

            var modelSpan = document.createElement("span");
            modelSpan.className = "muted";
            modelSpan.textContent = " · " + (e.chosen_model || e.model || "—");
            item.appendChild(modelSpan);

            if (e.event === "usage") {
              var chip = document.createElement("span");
              chip.className = "pill " + (e.outcome === "success" ? "pill-ok" : "pill-bad");
              chip.textContent = e.outcome || "—";
              item.appendChild(chip);
            }

            list.appendChild(item);
          });

          container.innerHTML = "";
          container.appendChild(list);
        })
        .catch(function() {
          container.innerHTML = '<p class="muted">Failed to load recent runs.</p>';
        });
    } catch (e) {
      container.innerHTML = '<p class="muted">Failed to load recent runs.</p>';
    }
  }

  document.addEventListener("DOMContentLoaded", function() {
    var form = document.getElementById("runner-form");
    var textarea = document.getElementById("runner-input");
    var executeCheckbox = document.getElementById("runner-execute");
    var submitBtn = form.querySelector("button[type=submit]");

    form.addEventListener("submit", function(e) {
      e.preventDefault();
      var task = textarea.value.trim();
      if (!task) return;

      currentTask = task;
      currentRunId = null;

      submitBtn.disabled = true;
      var result = document.getElementById("runner-result");
      result.innerHTML = '<p class="muted">' +
        (executeCheckbox.checked ? "Executing on the local model…" : "Routing…") +
        '</p>';

      postJson("/api/run", { task: task, execute: executeCheckbox.checked })
        .then(function(res) {
          if (res.error) {
            result.innerHTML = '<div class="card"><p class="muted">' + esc(res.error) + '</p></div>';
            submitBtn.disabled = false;
            return;
          }
          currentRunId = res.run_id;
          renderDecisionCard(res);
          submitBtn.disabled = false;
        })
        .catch(function() {
          result.innerHTML = '<div class="card"><p class="muted">Request failed.</p></div>';
          submitBtn.disabled = false;
        });
    });

    var tabBtn = document.querySelector('[data-pane="runner"]');
    if (tabBtn) {
      tabBtn.addEventListener("click", function() {
        loadRecent();
      });
    }

    loadRecent();
  });
})();
