const state = {
  q: "",
  source: "",
  task: "",
  target: "",
  status: "",
  sort: "relevance",
  limit: 200,
  stats: null,
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function fetchJson(url, options = {}) {
  const body = options.body;
  const headers = body instanceof FormData ? {} : { "Content-Type": "application/json" };
  const response = await fetch(url, {
    headers,
    ...options,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function countEntries(map) {
  return Object.entries(map || {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

function renderMetrics(stats) {
  $("#metrics").innerHTML = `
    <div class="metric"><strong>${stats.total || 0}</strong><span>Total kept</span></div>
    <div class="metric"><strong>${stats.unread || 0}</strong><span>Unread</span></div>
    <div class="metric"><strong>${stats.saved || 0}</strong><span>Saved</span></div>
    <div class="metric"><strong>${stats.with_pdf || 0}</strong><span>With PDF</span></div>
    <div class="metric"><strong>${stats.deleted || 0}</strong><span>Deleted</span></div>
  `;
}

function filterButton(label, count, group, activeValue) {
  const active = activeValue === label ? " active" : "";
  return `<button class="filter-button${active}" data-group="${group}" data-value="${escapeHtml(label)}">
    <span>${escapeHtml(label)}</span><span class="count">${count}</span>
  </button>`;
}

function renderFilters(stats) {
  const sourceHtml = [`<button class="filter-button${state.source === "" ? " active" : ""}" data-group="source" data-value=""><span>All sources</span><span class="count">${stats.total || 0}</span></button>`]
    .concat(countEntries(stats.sources).map(([label, count]) => filterButton(label, count, "source", state.source)))
    .join("");
  const taskHtml = [`<button class="filter-button${state.task === "" ? " active" : ""}" data-group="task" data-value=""><span>All tasks</span><span class="count">${stats.total || 0}</span></button>`]
    .concat(countEntries(stats.task_labels).map(([label, count]) => filterButton(label, count, "task", state.task)))
    .join("");
  const targetHtml = [`<button class="filter-button${state.target === "" ? " active" : ""}" data-group="target" data-value=""><span>All targets</span><span class="count">${stats.total || 0}</span></button>`]
    .concat(countEntries(stats.target_labels).map(([label, count]) => filterButton(label, count, "target", state.target)))
    .join("");

  $("#sourceFilters").innerHTML = sourceHtml;
  $("#taskFilters").innerHTML = taskHtml;
  $("#targetFilters").innerHTML = targetHtml;
}

function paperUrl(paper) {
  return paper.pdf_url || "";
}

function formatAuthors(authors) {
  if (!authors || authors.length === 0) return "";
  if (authors.length <= 3) return authors.join(", ");
  return `${authors.slice(0, 3).join(", ")} +${authors.length - 3}`;
}

function excerpt(text, max = 620) {
  const clean = String(text || "").trim();
  if (clean.length <= max) return clean || "No abstract available.";
  return `${clean.slice(0, max).trim()}...`;
}

function formatDate(value) {
  if (!value) return "";
  return String(value).slice(0, 10);
}

function renderLlmSummary(paper) {
  const summary = paper.llm_summary || {};
  if (!summary.summary_zh) return "";
  const points = Array.isArray(summary.key_points) && summary.key_points.length
    ? `<ul>${summary.key_points.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
    : "";
  const limitations = Array.isArray(summary.limitations) && summary.limitations.length
    ? `<p class="llm-summary-small">局限: ${escapeHtml(summary.limitations.join("；"))}</p>`
    : "";
  const meta = [
    summary.read_priority ? `Priority ${summary.read_priority}` : "",
    summary.summary_quality || "",
    typeof summary.confidence === "number" ? `conf ${summary.confidence.toFixed(2)}` : "",
    paper.llm_summary_model || "",
  ].filter(Boolean).join(" · ");
  return `
    <div class="llm-summary">
      <div class="llm-summary-head">
        <strong>DeepSeek Summary</strong>
        ${meta ? `<span>${escapeHtml(meta)}</span>` : ""}
      </div>
      <p>${escapeHtml(summary.summary_zh)}</p>
      ${summary.why_relevant ? `<p class="llm-summary-small">${escapeHtml(summary.why_relevant)}</p>` : ""}
      ${points}
      ${limitations}
    </div>
  `;
}

function papisLabel(paper) {
  const status = paper.papis_status || "not_exported";
  if (status === "synced") return "Papis: synced";
  if (status === "pending_pdf") return "Papis: needs PDF";
  if (status === "error") return "Papis: error";
  return "Papis: not exported";
}

function renderPaper(paper) {
  const sourceTags = (paper.sources || []).map((label) => `<span class="tag source">${escapeHtml(label)}</span>`).join("");
  const taskTags = (paper.task_labels || []).map((label) => `<span class="tag">${escapeHtml(label)}</span>`).join("");
  const targetTags = (paper.target_labels || []).map((label) => `<span class="tag target">${escapeHtml(label)}</span>`).join("");
  const venue = [paper.venue, paper.year].filter(Boolean).join(" ");
  const href = paperUrl(paper);
  const title = escapeHtml(paper.title);
  const titleHtml = href
    ? `<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer">${title}</a>`
    : `<span class="paper-title-text">${title}</span>`;
  const isDeleted = Boolean(paper.is_deleted);
  const actionHtml = isDeleted
    ? `<span class="deleted-note">Deleted ${paper.deleted_at ? escapeHtml(paper.deleted_at.slice(0, 10)) : ""}</span>`
    : `
        <button class="paper-action${paper.is_saved ? " active" : ""}" data-action="save">${paper.is_saved ? "Saved" : "Save"}</button>
        <button class="paper-action${paper.is_read ? " active" : ""}" data-action="read">${paper.is_read ? "Read" : "Mark read"}</button>
        ${paper.is_saved ? `<button class="paper-action" data-action="sync-papis">Sync Papis</button>` : ""}
        ${paper.is_saved ? `<button class="paper-action" data-action="attach-pdf">Attach PDF</button><input class="pdf-input" type="file" accept="application/pdf" hidden />` : ""}
        <button class="paper-action danger" data-action="delete">Delete</button>
        ${!paper.pdf_url && paper.publisher_pdf_url ? `<a class="paper-action access-link" href="${escapeHtml(paper.publisher_pdf_url)}" target="_blank" rel="noreferrer">Access PDF</a>` : ""}
        ${!paper.pdf_url && paper.publisher_url ? `<a class="paper-action access-link" href="${escapeHtml(paper.publisher_url)}" target="_blank" rel="noreferrer">Access page</a>` : ""}
      `;

  return `
    <article class="paper-card${paper.is_read ? " read" : ""}" data-paper-id="${paper.id}">
      <div class="paper-header">
        <div>
          <h3 class="paper-title">${titleHtml}</h3>
          <div class="meta">
            ${venue ? `<span>${escapeHtml(venue)}</span>` : ""}
            ${paper.published_at ? `<span>${escapeHtml(paper.published_at.slice(0, 10))}</span>` : ""}
            ${paper.first_seen_at ? `<span>Added ${escapeHtml(formatDate(paper.first_seen_at))}</span>` : ""}
            ${paper.pdf_url ? `<span>PDF: ${escapeHtml(paper.pdf_source || "found")}</span>` : ""}
            ${paper.is_saved ? `<span>${escapeHtml(papisLabel(paper))}</span>` : ""}
            ${paper.papis_error ? `<span class="error">Papis error</span>` : ""}
            ${!paper.pdf_url && paper.publisher_pdf_url ? `<span>Access PDF: ${escapeHtml(paper.publisher_pdf_source || "publisher")}</span>` : ""}
            ${!paper.pdf_url && paper.publisher_url ? `<span>Access: ${escapeHtml(paper.publisher_source || "publisher")}</span>` : ""}
            ${paper.deleted_at ? `<span>Deleted ${escapeHtml(paper.deleted_at.slice(0, 10))}</span>` : ""}
            ${formatAuthors(paper.authors) ? `<span>${escapeHtml(formatAuthors(paper.authors))}</span>` : ""}
          </div>
        </div>
        <div class="score">${isDeleted ? "" : Number(paper.relevance_score || 0).toFixed(1)}</div>
      </div>
      <p class="abstract">${escapeHtml(excerpt(paper.abstract))}</p>
      ${renderLlmSummary(paper)}
      <div class="tag-row">${sourceTags}${taskTags}${targetTags}</div>
      <p class="reason">${escapeHtml(paper.recommendation_reason || "")}</p>
      ${paper.papis_error ? `<p class="reason error">${escapeHtml(paper.papis_error)}</p>` : ""}
      <div class="paper-actions">
        ${actionHtml}
      </div>
    </article>
  `;
}

async function loadStats() {
  const stats = await fetchJson("/api/stats");
  state.stats = stats;
  renderMetrics(stats);
  renderFilters(stats);
}

async function loadPapers() {
  const params = new URLSearchParams({
    q: state.q,
    source: state.source,
    task: state.task,
    target: state.target,
    status: state.status,
    sort: state.sort,
    limit: String(state.limit),
  });
  const data = await fetchJson(`/api/papers?${params}`);
  $("#resultCount").textContent = `${data.papers.length} papers`;
  $("#paperList").innerHTML = data.papers.length
    ? data.papers.map(renderPaper).join("")
    : `<div class="summary-box">No papers match the current filters.</div>`;
}

function renderSummary(summary) {
  if (!summary) {
    $("#lastSummary").textContent = "No update has run in this server session.";
    return;
  }
  const sourceLines = Object.entries(summary.sources || {})
    .map(([source, item]) => `${source}: fetched ${item.fetched || 0}, kept ${item.kept || 0}, new ${item.new || 0}${item.error ? `, error ${item.error}` : ""}`)
    .join("\n");
  const llm = summary.llm_summaries
    ? `\nDeepSeek summaries: checked ${summary.llm_summaries.checked || 0}, summarized ${summary.llm_summaries.summarized || 0}, errors ${summary.llm_summaries.errors || 0}`
    : "";
  $("#lastSummary").innerHTML = `<strong>Last update</strong><br><pre>${escapeHtml((sourceLines || "No source activity.") + llm)}</pre>`;
}

function renderSummaryStatus(status) {
  const last = status.last_summary;
  const detail = last
    ? ` · checked ${last.checked || 0}, summarized ${last.summarized || 0}, errors ${last.errors || 0}`
    : "";
  $("#summaryState").textContent = `${status.running ? "Updating summaries..." : "Summaries idle"}${detail}`;
}

async function loadStatus() {
  const status = await fetchJson("/api/update-status");
  const summaryStatus = await fetchJson("/api/summary-status");
  $("#updateState").textContent = status.running ? "Updating..." : "Idle";
  $("#updateButton").disabled = Boolean(status.running);
  $("#summaryButton").disabled = Boolean(summaryStatus.running || status.running);
  renderSummaryStatus(summaryStatus);
  renderSummary(status.last_summary);
}

async function loadLogs() {
  const data = await fetchJson("/api/run-logs");
  $("#runLogs").innerHTML = (data.logs || []).map((log) => `
    <div class="log-item">
      <div class="log-head">
        <span>${escapeHtml(log.source)}</span>
        <span class="${log.status === "error" ? "error" : ""}">${escapeHtml(log.status)}</span>
      </div>
      <p>${escapeHtml(log.started_at)} · fetched ${log.fetched_count}, kept ${log.kept_count}, new ${log.new_count}</p>
      ${log.error ? `<p class="error">${escapeHtml(log.error)}</p>` : ""}
    </div>
  `).join("") || `<div class="summary-box">No run logs yet.</div>`;
}

async function refreshAll() {
  await loadStats();
  await loadPapers();
  await loadStatus();
  await loadLogs();
}

let searchTimer = null;
function bindEvents() {
  $("#searchInput").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.q = event.target.value.trim();
      loadPapers().catch(console.error);
    }, 180);
  });

  $("#limitSelect").addEventListener("change", (event) => {
    state.limit = Number(event.target.value);
    loadPapers().catch(console.error);
  });

  $("#sortSelect").addEventListener("change", (event) => {
    state.sort = event.target.value;
    loadPapers().catch(console.error);
  });

  $("#refreshButton").addEventListener("click", () => refreshAll().catch(console.error));

  $("#updateButton").addEventListener("click", async () => {
    await fetchJson("/api/update", { method: "POST", body: JSON.stringify({ bootstrap: false }) });
    await loadStatus();
  });

  $("#summaryButton").addEventListener("click", async () => {
    await fetchJson("/api/summaries/run", { method: "POST", body: JSON.stringify({}) });
    await loadStatus();
  });

  document.body.addEventListener("click", async (event) => {
    const nav = event.target.closest(".nav-item");
    if (nav) {
      document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
      nav.classList.add("active");
      state.status = nav.dataset.status || "";
      await loadPapers();
      return;
    }

    const filter = event.target.closest(".filter-button");
    if (filter) {
      const group = filter.dataset.group;
      state[group] = filter.dataset.value || "";
      await loadStats();
      await loadPapers();
      return;
    }

    const action = event.target.closest(".paper-action[data-action]");
    if (action) {
      const card = action.closest(".paper-card");
      const paperId = Number(card.dataset.paperId);
      const actionName = action.dataset.action;
      if (actionName === "delete") {
        const confirmed = window.confirm("Delete this paper permanently? Future updates will skip this title.");
        if (!confirmed) return;
        await fetchJson(`/api/papers/${paperId}/delete`, { method: "POST", body: JSON.stringify({}) });
        await refreshAll();
        return;
      }
      if (actionName === "sync-papis") {
        action.disabled = true;
        await fetchJson(`/api/papers/${paperId}/papis/sync`, { method: "POST", body: JSON.stringify({}) });
        await refreshAll();
        return;
      }
      if (actionName === "attach-pdf") {
        const input = card.querySelector(".pdf-input");
        if (input) input.click();
        return;
      }

      const payload = {};
      if (actionName === "save") payload.is_saved = !action.classList.contains("active");
      if (actionName === "read") payload.is_read = !action.classList.contains("active");
      await fetchJson(`/api/papers/${paperId}/flags`, { method: "POST", body: JSON.stringify(payload) });
      await refreshAll();
    }
  });

  document.body.addEventListener("change", async (event) => {
    const input = event.target.closest(".pdf-input");
    if (!input || !input.files || !input.files[0]) return;
    const card = input.closest(".paper-card");
    const paperId = Number(card.dataset.paperId);
    const form = new FormData();
    form.append("pdf", input.files[0]);
    await fetchJson(`/api/papers/${paperId}/pdf`, { method: "POST", body: form });
    input.value = "";
    await refreshAll();
  });
}

bindEvents();
refreshAll().catch((error) => {
  $("#paperList").innerHTML = `<div class="summary-box error">${escapeHtml(error.message)}</div>`;
});
setInterval(() => {
  loadStatus().catch(console.error);
  loadLogs().catch(console.error);
}, 5000);
