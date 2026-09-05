const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
const number = new Intl.NumberFormat("en-US");
const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });

function duration(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m ${Math.floor(seconds % 60)}s`;
}

function recency(seconds) {
  if (seconds == null) return "No activity yet";
  if (seconds < 10) return "Just active";
  if (seconds < 60) return `Active ${seconds}s ago`;
  return `Active ${Math.floor(seconds / 60)}m ago`;
}

function shortName(instance) {
  return String(instance).replace(/^instance_/, "").replace(/-v[0-9a-f]+$/, "");
}

function renderSlots(active, concurrency) {
  const slots = $("slots");
  slots.replaceChildren();
  for (let index = 0; index < concurrency; index += 1) {
    const item = active[index];
    const node = document.createElement("div");
    node.className = `slot${item ? "" : " empty"}`;
    if (!item) {
      node.innerHTML = `<div class="slot-top"><span class="slot-index">SLOT ${index + 1}</span><span class="slot-state">Idle</span></div><div class="slot-name">Waiting for next task</div><div class="slot-time">—</div>`;
    } else {
      const state = item.phase === "solving" ? "Solving" : item.phase === "grading" ? "Grading" : "Preparing";
      const step = item.step == null ? "STEP —" : `STEP ${item.step} / ${item.max_step}`;
      const elapsedLabel = item.phase === "preparing" ? "Preparing for" : "Running for";
      node.innerHTML = `<div class="slot-top"><span class="slot-index">SLOT ${index + 1}</span><span class="slot-state ${escapeHtml(item.phase)}"><i></i>${state}</span></div><div class="slot-name" title="${escapeHtml(item.task_id)}">${escapeHtml(shortName(item.task_id))}</div><div class="slot-metrics"><span>${step}</span><span>${number.format(item.requests || 0)} REQUESTS</span></div><div class="slot-time"><span>${elapsedLabel} ${duration(item.elapsed_seconds)}</span><span>${recency(item.last_activity_seconds)}</span></div>`;
    }
    slots.appendChild(node);
  }
}

function renderResults(rows) {
  const body = $("recent-results");
  body.replaceChildren();
  for (const row of rows) {
    const tr = document.createElement("tr");
    const mark = row.outcome === "passed" ? "✓" : row.outcome === "failed" ? "×" : row.outcome === "completed" ? "·" : "!";
    const markClass = row.outcome === "failed" ? "failed" : row.outcome === "error" ? "error" : "";
    const detail = row.failure ? `${row.failure.kind}: ${row.failure.code || "unknown"} — ${row.failure.details || "Review required"}` : row.outcome;
    const provenance = row.retry_phase ? `Previous result · Retrying: ${row.retry_phase}` : row.attempt_source === "history" ? "Previous result" : "Current attempt";
    tr.innerHTML = `<td><span class="result-mark ${markClass}" title="${escapeHtml(detail)}">${mark}</span></td><td class="instance" title="${escapeHtml(row.task_id)}">${escapeHtml(shortName(row.task_id))}<div class="slot-time">${escapeHtml(provenance)}</div></td><td>${duration(row.time_seconds)}</td><td>${number.format(row.calls || 0)}</td><td>${money.format(row.cost_usd || 0)}</td>`;
    body.appendChild(tr);
  }
}

function render(data) {
  const progress = data.progress;
  const telemetry = data.telemetry;
  const launcher = data.launcher;
  const percent = progress.total ? (progress.completed / progress.total) * 100 : 0;
  const score = data.pass_rate?.percent ?? (progress.completed ? (progress.passed / progress.completed) * 100 : null);

  document.title = `${data.title} · Live`;
  $("title").textContent = data.title;
  $("run-name").textContent = data.run_id + (data.score_mode === "cumulative_retry" ? ` · Cumulative retries (not pass@1) · ${data.current_attempt?.completed || 0} completed in this attempt` : "");
  $("completed").textContent = number.format(progress.completed);
  $("total").textContent = `/ ${number.format(progress.total)}`;
  $("progress-fill").style.width = `${Math.max(0, Math.min(100, percent))}%`;
  $("progress-percent").textContent = `${percent.toFixed(2)}%`;
  $("eta").textContent = data.eta_seconds == null ? "Waiting for samples to estimate ETA" : `Time remaining: ${duration(data.eta_seconds)}`;
  $("score").textContent = score == null ? "—" : `${score.toFixed(1)}%`;
  $("score").title = `${progress.passed} passed / ${progress.completed} completed, including evaluation issues. ${progress.scored} have valid scores.`;
  $("resolved").textContent = number.format(progress.passed);
  $("unresolved").textContent = number.format(progress.failed);
  $("harness-errors").textContent = number.format(progress.errors);
  $("elapsed").textContent = duration(launcher.elapsed_seconds);
  $("active-count").textContent = launcher.active.length;
  $("concurrency").textContent = launcher.concurrency;
  $("launcher-pid").textContent = launcher.pid ? `PID ${launcher.pid}` : "PID —";
  const phases = launcher.active.reduce((counts, item) => {
    counts[item.phase] = (counts[item.phase] || 0) + 1;
    return counts;
  }, {});
  $("slot-summary").textContent = `${phases.solving || 0} solving · ${phases.grading || 0} grading · ${phases.preparing || 0} preparing`;
  $("cost").textContent = money.format(telemetry.cost_usd);
  $("calls").textContent = number.format(telemetry.calls);
  $("input-tokens").textContent = number.format(telemetry.input_tokens);
  $("output-tokens").textContent = number.format(telemetry.output_tokens);
  $("cache-read").textContent = number.format(telemetry.cache_read_tokens);
  $("cache-rate").textContent = `${telemetry.cache_hit_percent.toFixed(1)}%`;
  $("data-path").textContent = data.results_path;
  const labels = {test_compatibility: "test compatibility", grading_setup: "grading setup", evaluation: "other evaluation"};
  const issueSummary = Object.entries(data.issue_counts || {}).map(([kind, count]) => `${count} ${labels[kind] || kind}`).join(" · ");
  $("error-summary").textContent = issueSummary || (progress.errors ? `${progress.errors} evaluation issues` : "No evaluation issues");
  $("error-summary").title = issueSummary;
  $("harness-errors").parentElement.title = $("error-summary").title;

  const complete = data.status.startsWith("completed");
  const alive = launcher.alive || complete;
  $("health-dot").className = `health-dot ${alive ? "live" : ""}`;
  $("health-label").textContent = complete ? (data.status === "completed" ? "Completed" : "Completed with errors") : alive ? "Running" : "Interrupted";
  $("updated-at").textContent = `Updated ${new Date(data.updated_at).toLocaleTimeString("en-US")}`;
  renderSlots(launcher.active, launcher.concurrency);
  renderResults(data.recent);
}

async function refresh() {
  try {
    const response = await fetch(new URL("api/status", location.href), { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    $("health-dot").className = "health-dot";
    $("health-label").textContent = "Connection failed";
    $("updated-at").textContent = error.message;
  }
}

refresh();
setInterval(refresh, 5000);
