const STATUS_OPTIONS = [
  ["draft", "Draft"],
  ["backlog", "Backlog"],
  ["ready", "Ready"],
  ["in_progress", "In Progress"],
  ["blocked", "Blocked"],
  ["done", "Done"],
  ["cancelled", "Cancelled"],
];

const KIND_OPTIONS = [
  ["implementation", "Implementation"],
  ["bug", "Bug"],
  ["research", "Research"],
  ["decision", "Decision"],
  ["refactor", "Refactor"],
  ["migration", "Migration"],
  ["verification", "Verification"],
  ["maintenance", "Maintenance"],
];

const MEMO_KIND_OPTIONS = [
  ["general", "일반"], ["decision", "결정"], ["problem", "문제"],
  ["idea", "아이디어"], ["question", "질문"], ["reference", "참고"],
];
const MEMO_STATUS_OPTIONS = [["", "전체 상태"], ["open", "확인 필요"], ["closed", "정리됨"]];

const state = {
  items: [],
  selectedId: null,
  selectedContext: null,
  statusFilters: new Set(),
  kindFilters: new Set(),
  memoStatusFilter: "",
  memoKindFilter: "",
  editingMemoId: null,
  detailTab: "overview",
};

const elements = {
  itemsList: document.querySelector("#items-list"),
  loading: document.querySelector("#loading-state"),
  detail: document.querySelector("#detail-content"),
  emptyDetail: document.querySelector("#empty-detail"),
  visibleCount: document.querySelector("#visible-count"),
  dockSummary: document.querySelector("#dock-summary"),
  healthDot: document.querySelector("#health-dot"),
  healthLabel: document.querySelector("#health-label"),
  modal: document.querySelector("#draft-modal"),
  form: document.querySelector("#draft-form"),
  formError: document.querySelector("#form-error"),
  submitButton: document.querySelector("#submit-draft"),
  editModal: document.querySelector("#edit-modal"),
  editForm: document.querySelector("#edit-form"),
  editFormError: document.querySelector("#edit-form-error"),
  deleteModal: document.querySelector("#delete-modal"),
  deleteConfirmation: document.querySelector("#delete-confirmation"),
  confirmDelete: document.querySelector("#confirm-delete"),
  memoModal: document.querySelector("#memo-modal"),
  memoForm: document.querySelector("#memo-form"),
  memoFormError: document.querySelector("#memo-form-error"),
  submitMemo: document.querySelector("#submit-memo"),
};

function statusOf(item) {
  return item.is_draft ? "draft" : item.status;
}

function labelFor(options, value) {
  return options.find(([key]) => key === value)?.[1] || value;
}

function priorityLabel(value) {
  return { urgent: "Urgent", high: "High", normal: "Normal", low: "Low" }[value] || value;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

function formatRunDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function formatElapsed(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  if (hours) return `${hours}시간 ${minutes}분 ${remainder}초`;
  if (minutes) return `${minutes}분 ${remainder}초`;
  return `${remainder}초`;
}

function elapsedSeconds(startedAt, endedAt = null, now = Date.now()) {
  const started = new Date(startedAt).getTime();
  const ended = endedAt ? new Date(endedAt).getTime() : now;
  if (Number.isNaN(started) || Number.isNaN(ended)) return null;
  return Math.max(0, Math.floor((ended - started) / 1000));
}

function durationLabel(run) {
  if (!run.started_at) return "시간 미상";
  const totalSeconds = elapsedSeconds(run.started_at, run.ended_at);
  if (totalSeconds === null) return "시간 미상";
  const elapsed = formatElapsed(totalSeconds);
  return run.status === "running" ? `${elapsed} 경과 중` : `${elapsed} 소요`;
}

function updateRunningDurations(now = Date.now()) {
  document.querySelectorAll("[data-run-elapsed]").forEach((element) => {
    const totalSeconds = elapsedSeconds(element.dataset.startedAt, null, now);
    if (totalSeconds !== null) {
      element.textContent = `${formatElapsed(totalSeconds)} 경과 중`;
    }
  });
}

function runStatusLabel(value) {
  return {
    running: "Running", succeeded: "Succeeded", failed: "Failed",
    interrupted: "Interrupted", cancelled: "Cancelled",
  }[value] || value;
}

function runStatusClass(value) {
  return ["running", "succeeded", "failed", "interrupted", "cancelled"].includes(value)
    ? value : "unknown";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderMarkdown(value) {
  // Detect block syntax from the raw text, then escape every user-authored fragment.
  const source = String(value ?? "");
  const lines = source.split("\n");
  const output = [];
  let inCode = false;
  let openList = null;
  const inline = (line) => escapeHtml(line)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/_([^_]+)_/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>');
  const closeList = () => {
    if (openList) { output.push(`</${openList}>`); openList = null; }
  };
  const addListItem = (type, content) => {
    if (openList !== type) {
      closeList();
      output.push(`<${type}>`);
      openList = type;
    }
    output.push(`<li>${inline(content)}</li>`);
  };
  for (const line of lines) {
    if (line.startsWith("```")) {
      if (inCode) { output.push("</code></pre>"); inCode = false; }
      else { closeList(); output.push("<pre><code>"); inCode = true; }
      continue;
    }
    if (inCode) { output.push(`${escapeHtml(line)}\n`); continue; }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    const quote = line.match(/^\s*>\s?(.*)$/);
    if (heading) { closeList(); output.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`); }
    else if (bullet) { addListItem("ul", bullet[1]); }
    else if (ordered) { addListItem("ol", ordered[1]); }
    else if (quote) { closeList(); output.push(`<blockquote>${inline(quote[1])}</blockquote>`); }
    else if (!line.trim()) { closeList(); }
    else { closeList(); output.push(`<p>${inline(line)}</p>`); }
  }
  closeList();
  if (inCode) output.push("</code></pre>");
  return output.join("");
}

function updateMemoPreview() {
  const content = elements.memoForm.elements.namedItem("content")?.value || "";
  const preview = document.querySelector("#memo-preview");
  if (!preview) return;
  preview.classList.toggle("is-empty", !content.trim());
  preview.innerHTML = content.trim()
    ? renderMarkdown(content)
    : '<p class="memo-preview-empty">Markdown을 입력하면 여기에 미리보기가 표시됩니다.</p>';
}

function toggleMemoCodeFormatting(textarea) {
  const value = textarea.value;
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const selected = value.slice(start, end);
  const multiline = selected.includes("\n");
  const opening = multiline ? "```\n" : "`";
  const closing = multiline ? "\n```" : "`";
  const wrapped = value.slice(start - opening.length, start) === opening
    && value.slice(end, end + closing.length) === closing;

  if (wrapped) {
    textarea.setRangeText(selected, start - opening.length, end + closing.length, "select");
  } else {
    textarea.setRangeText(`${opening}${selected}${closing}`, start, end, "end");
    const contentStart = start + opening.length;
    textarea.setSelectionRange(contentStart, contentStart + selected.length);
  }
  textarea.focus();
  updateMemoPreview();
}

function toggleMemoInlineFormatting(textarea, marker) {
  const value = textarea.value;
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const selected = value.slice(start, end);
  const wrapped = value.slice(start - marker.length, start) === marker
    && value.slice(end, end + marker.length) === marker;

  if (wrapped) {
    textarea.setRangeText(selected, start - marker.length, end + marker.length, "select");
  } else {
    textarea.setRangeText(`${marker}${selected}${marker}`, start, end, "end");
    const contentStart = start + marker.length;
    textarea.setSelectionRange(contentStart, contentStart + selected.length);
  }
  textarea.focus();
  updateMemoPreview();
}

function indentMemoSelection(textarea, outdent = false) {
  const value = textarea.value;
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const lineStart = value.lastIndexOf("\n", start - 1) + 1;
  const trailingNewline = end > start && value[end - 1] === "\n";
  const lineEndSearch = trailingNewline ? end - 1 : end;
  const nextNewline = value.indexOf("\n", lineEndSearch);
  const lineEnd = nextNewline === -1 ? value.length : nextNewline;
  const block = value.slice(lineStart, lineEnd);
  const lines = block.split("\n");
  const transformed = lines.map((line) => outdent ? line.replace(/^ {1,2}/, "") : `  ${line}`).join("\n");
  const firstDelta = transformed.split("\n", 1)[0].length - lines[0].length;
  const totalDelta = transformed.length - block.length;

  textarea.setRangeText(transformed, lineStart, lineEnd, "start");
  if (start === end) {
    const caret = Math.max(lineStart, start + firstDelta);
    textarea.setSelectionRange(caret, caret);
  } else {
    textarea.setSelectionRange(Math.max(lineStart, start + firstDelta), end + totalDelta);
  }
  textarea.focus();
  updateMemoPreview();
}

function memoKindLabel(value) {
  return labelFor(MEMO_KIND_OPTIONS, value);
}

function memoSectionHtml() {
  const statusOptions = [["", "전체 상태"], ...MEMO_STATUS_OPTIONS].map(([value, label]) =>
    `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join("");
  const kindOptions = [["", "전체 종류"], ...MEMO_KIND_OPTIONS].map(([value, label]) =>
    `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join("");
  return `
    <section class="detail-section memo-section">
      <div class="memo-heading"><div class="section-heading-inline"><h3>작업 메모 <small>(WORK MEMOS)</small></h3><span class="memo-count" id="memo-count">총 0건</span></div><button class="primary-button memo-add-button" id="add-memo" type="button">＋ 새 메모 추가</button></div>
      <div class="memo-toolbar" role="group" aria-label="메모 필터">
        <label class="memo-filter-control"><span>상태:</span><select id="memo-status-filter">${statusOptions}</select></label>
        <label class="memo-filter-control"><span>종류:</span><select id="memo-kind-filter">${kindOptions}</select></label>
      </div>
      <div class="memo-filter-summary"><span id="memo-filter-summary">전체 0건 표시 중</span><span class="memo-filter-divider" aria-hidden="true">•</span><span>작성자: ALL</span><button type="button" id="reset-memo-filters"><span aria-hidden="true">↻</span> 필터 초기화</button></div>
      <p class="memo-filter-hint hidden" id="memo-filter-hint">필터 적용 중 · 순서 변경은 전체 보기에서만 가능합니다.</p>
      <div class="memo-list" id="memo-list"></div>
    </section>`;
}

function detailTabsHtml(activeTab) {
  const memoCount = (state.selectedContext?.memos || []).length;
  return `
    <nav class="detail-tabs" id="detail-tabs" aria-label="WorkItem 상세 메뉴">
      <button type="button" class="detail-tab ${activeTab === "overview" ? "active" : ""}" data-detail-tab="overview" aria-selected="${activeTab === "overview" ? "true" : "false"}">Overview</button>
      <button type="button" class="detail-tab ${activeTab === "memos" ? "active" : ""}" data-detail-tab="memos" aria-selected="${activeTab === "memos" ? "true" : "false"}">Memo <span>${memoCount}</span></button>
      <span class="detail-tab-id">${escapeHtml(state.selectedContext?.work_item?.id || "")}</span>
    </nav>`;
}

function setDetailTab(tab) {
  state.detailTab = tab === "memos" ? "memos" : "overview";
  document.querySelectorAll("[data-detail-tab]").forEach((button) => {
    const active = button.dataset.detailTab === state.detailTab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelector("#overview-panel")?.classList.toggle("hidden", state.detailTab !== "overview");
  document.querySelector("#memos-panel")?.classList.toggle("hidden", state.detailTab !== "memos");
}

function visibleMemos() {
  const memos = state.selectedContext?.memos || [];
  return memos.filter((memo) =>
    (!state.memoStatusFilter || memo.status === state.memoStatusFilter) &&
    (!state.memoKindFilter || memo.kind === state.memoKindFilter));
}

function renderMemoCards() {
  const list = document.querySelector("#memo-list");
  const count = document.querySelector("#memo-count");
  if (!list || !count) return;
  const statusFilter = document.querySelector("#memo-status-filter");
  const kindFilter = document.querySelector("#memo-kind-filter");
  if (statusFilter) statusFilter.value = state.memoStatusFilter;
  if (kindFilter) kindFilter.value = state.memoKindFilter;
  const memos = visibleMemos();
  const total = (state.selectedContext?.memos || []).length;
  count.textContent = `총 ${total}건`;
  const filtered = Boolean(state.memoStatusFilter || state.memoKindFilter);
  const summary = document.querySelector("#memo-filter-summary");
  if (summary) summary.textContent = `${filtered ? "필터 결과 " : "전체 "}${memos.length}건 표시 중`;
  const hint = document.querySelector("#memo-filter-hint");
  hint?.classList.toggle("hidden", !filtered);
  if (!memos.length) {
    list.innerHTML = `<div class="subtle-empty">표시할 메모가 없습니다.</div>`;
    return;
  }
  list.innerHTML = memos.map((memo) => {
    const author = (memo.author || "anon").trim() || "anon";
    const authorInitial = [...author][0]?.toUpperCase() || "A";
    const statusLabel = memo.status === "open" ? "활성" : "종료";
    const statusActionLabel = memo.status === "open" ? "종료 상태로 변경" : "활성 상태로 변경";
    return `
    <article class="memo-card ${memo.is_pinned ? "is-pinned" : ""} ${memo.status === "closed" ? "is-closed" : ""}" data-memo-id="${escapeHtml(memo.id)}" data-pinned="${memo.is_pinned ? "true" : "false"}" draggable="${filtered ? "false" : "true"}">
      <header class="memo-card-header">
        <div class="memo-card-header-main">
          <span class="memo-drag-handle" aria-hidden="true" title="드래그하여 순서 변경">⋮⋮</span>
          <div class="memo-card-title">
            <h4>${escapeHtml(memo.title)}</h4>
            <span class="memo-kind memo-kind-${escapeHtml(memo.kind)}">${escapeHtml(memoKindLabel(memo.kind))}</span>
            <span class="memo-status">${escapeHtml(statusLabel)}</span>
          </div>
        </div>
        <div class="memo-card-actions" aria-label="메모 작업">
          <button type="button" data-memo-action="pin" aria-label="${memo.is_pinned ? "고정 해제" : "상단 고정"}" title="${memo.is_pinned ? "고정 해제" : "상단 고정"}">${memo.is_pinned ? "★" : "☆"}</button>
          <button type="button" data-memo-action="edit" aria-label="메모 수정" title="메모 수정">✎</button>
          <button type="button" data-memo-action="delete" aria-label="메모 삭제" title="메모 삭제">×</button>
        </div>
      </header>
      <section class="memo-content" aria-label="메모 본문">${renderMarkdown(memo.content)}</section>
      <footer class="memo-card-footer">
        <div class="memo-card-author">
          <span class="memo-author-avatar" aria-hidden="true">${escapeHtml(authorInitial)}</span>
          <span class="memo-author-name">${escapeHtml(author)}</span>
          <span class="memo-footer-divider" aria-hidden="true">•</span>
          <time>${escapeHtml(formatDate(memo.updated_at || memo.created_at))}</time>
        </div>
        <div class="memo-card-footer-actions">
          <button type="button" class="memo-copy-button" data-memo-action="copy" aria-label="마크다운 원본 복사" title="마크다운 원본 복사"><span aria-hidden="true">⧉</span><span class="memo-copy-label">Markdown 원본 복사</span></button>
          <button type="button" class="memo-status-button" data-memo-action="status"><span aria-hidden="true">✓</span><span>${escapeHtml(statusActionLabel)}</span></button>
        </div>
      </footer>
    </article>`;
  }).join("");
}

async function refreshSelectedDetail() {
  const item = state.selectedContext?.work_item;
  if (item) await selectItem(item.id);
}

async function handleMemoAction(event) {
  const button = event.target.closest("[data-memo-action]");
  const card = event.target.closest("[data-memo-id]");
  if (!button || !card) return;
  const item = state.selectedContext?.work_item;
  const memoId = card.dataset.memoId;
  const memo = (state.selectedContext?.memos || []).find((entry) => entry.id === memoId);
  if (!item || !memo) return;
  const action = button.dataset.memoAction;
  try {
    if (action === "edit") return openMemoModal(memoId);
    if (action === "copy") return copyMemoMarkdown(button, memo.content);
    if (action === "delete") {
      if (!window.confirm(`“${memo.title}” 메모를 삭제할까요? 삭제 후 복구할 수 없습니다.`)) return;
      await api(`/api/work-items/${encodeURIComponent(item.id)}/memos/${encodeURIComponent(memoId)}`, { method: "DELETE" });
    } else if (action === "status" || action === "pin") {
      const payload = action === "status" ? { status: memo.status === "open" ? "closed" : "open" } : { is_pinned: !memo.is_pinned };
      await api(`/api/work-items/${encodeURIComponent(item.id)}/memos/${encodeURIComponent(memoId)}`, { method: "PATCH", body: JSON.stringify(payload) });
    }
    await refreshSelectedDetail();
  } catch (error) { window.alert(error.message); }
}

async function copyMemoMarkdown(button, content) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(content);
    } else {
      const helper = document.createElement("textarea");
      helper.value = content;
      helper.setAttribute("readonly", "");
      helper.style.position = "fixed";
      helper.style.opacity = "0";
      document.body.append(helper);
      helper.select();
      const copied = document.execCommand("copy");
      helper.remove();
      if (!copied) throw new Error("clipboard unavailable");
    }
    const label = button.querySelector(".memo-copy-label");
    if (!label) return;
    const original = label.textContent;
    label.textContent = "복사됨";
    button.classList.add("is-copied");
    window.setTimeout(() => {
      label.textContent = original;
      button.classList.remove("is-copied");
    }, 1600);
  } catch (_) {
    window.alert("Markdown 원본을 복사하지 못했습니다.");
  }
}

async function saveMemoOrder() {
  const item = state.selectedContext?.work_item;
  const list = document.querySelector("#memo-list");
  if (!item || !list || state.memoStatusFilter || state.memoKindFilter) return;
  const memoIds = [...list.querySelectorAll("[data-memo-id]")].map((node) => node.dataset.memoId);
  try {
    const payload = await api(`/api/work-items/${encodeURIComponent(item.id)}/memos/reorder`, { method: "POST", body: JSON.stringify({ memo_ids: memoIds }) });
    state.selectedContext.memos = payload.memos;
    renderMemoCards();
  } catch (error) { window.alert(error.message); await refreshSelectedDetail(); }
}

function bindMemoInteractions() {
  const list = document.querySelector("#memo-list");
  document.querySelector("#memo-status-filter")?.addEventListener("change", (event) => { state.memoStatusFilter = event.target.value; renderMemoCards(); });
  document.querySelector("#memo-kind-filter")?.addEventListener("change", (event) => { state.memoKindFilter = event.target.value; renderMemoCards(); });
  document.querySelector("#reset-memo-filters")?.addEventListener("click", () => {
    state.memoStatusFilter = ""; state.memoKindFilter = "";
    document.querySelector("#memo-status-filter").value = ""; document.querySelector("#memo-kind-filter").value = ""; renderMemoCards();
  });
  document.querySelector("#add-memo")?.addEventListener("click", () => openMemoModal());
  list?.addEventListener("click", handleMemoAction);
  list?.addEventListener("dragstart", (event) => {
    const card = event.target.closest("[data-memo-id]");
    if (!card || state.memoStatusFilter || state.memoKindFilter) { event.preventDefault(); return; }
    card.classList.add("dragging"); event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", card.dataset.memoId);
  });
  list?.addEventListener("dragend", (event) => event.target.closest("[data-memo-id]")?.classList.remove("dragging"));
  list?.addEventListener("dragover", (event) => {
    event.preventDefault();
    const target = event.target.closest("[data-memo-id]");
    const source = list.querySelector(".dragging");
    if (!target || !source || target === source || target.dataset.pinned !== source.dataset.pinned) return;
    const rect = target.getBoundingClientRect();
    target.parentNode.insertBefore(source, event.clientY < rect.top + rect.height / 2 ? target : target.nextSibling);
  });
  list?.addEventListener("drop", (event) => { event.preventDefault(); saveMemoOrder(); });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let payload = {};
  try { payload = await response.json(); } catch (_) { /* empty or non-JSON response */ }
  if (!response.ok) {
    const error = new Error(payload.error?.message || "요청을 처리하지 못했습니다.");
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function countFor(type, value) {
  return state.items.filter((item) => type === "status" ? statusOf(item) === value : item.kind === value).length;
}

function buildFilters(containerId, type, options) {
  const container = document.querySelector(containerId);
  container.innerHTML = "";
  for (const [value, label] of options) {
    const row = document.createElement("label");
    row.className = "filter-row";
    row.innerHTML = `
      <input type="checkbox" value="${escapeHtml(value)}" />
      <span>${escapeHtml(label)}</span>
      <span class="filter-count">${countFor(type, value)}</span>
    `;
    const input = row.querySelector("input");
    input.checked = (type === "status" ? state.statusFilters : state.kindFilters).has(value);
    input.addEventListener("change", () => {
      const selected = type === "status" ? state.statusFilters : state.kindFilters;
      input.checked ? selected.add(value) : selected.delete(value);
      renderItems();
    });
    container.append(row);
  }
}

function filteredItems() {
  return state.items.filter((item) => {
    const matchesStatus = state.statusFilters.size === 0 || state.statusFilters.has(statusOf(item));
    const matchesKind = state.kindFilters.size === 0 || state.kindFilters.has(item.kind);
    return matchesStatus && matchesKind;
  });
}

function renderItems() {
  const items = filteredItems();
  elements.visibleCount.textContent = `${items.length} visible`;
  elements.dockSummary.textContent = `전체 ${state.items.length}개 · 화면 ${items.length}개`;
  elements.itemsList.innerHTML = "";

  if (items.length === 0) {
    elements.itemsList.innerHTML = `<div class="empty-list"><strong>표시할 WorkItem이 없습니다.</strong><p>필터를 초기화하거나 새 Draft를 만들어보세요.</p></div>`;
    return;
  }

  for (const item of items) {
    const virtualStatus = statusOf(item);
    const card = document.createElement("button");
    card.type = "button";
    card.className = `item-card ${item.id === state.selectedId ? "selected" : ""} ${item.is_draft ? "muted" : ""}`;
    card.dataset.workItemId = item.id;
    card.innerHTML = `
      <div class="card-top">
        <div class="card-meta">
          <span class="wi-id">${escapeHtml(item.id)}</span>
          <span class="badge kind-badge">${escapeHtml(labelFor(KIND_OPTIONS, item.kind))}</span>
        </div>
        <span class="badge status-${escapeHtml(virtualStatus)}">${escapeHtml(labelFor(STATUS_OPTIONS, virtualStatus))}</span>
      </div>
      <h3>${escapeHtml(item.title)}</h3>
      <p class="item-goal">${escapeHtml(item.goal)}</p>
      <div class="card-footer">
        <span class="priority-${escapeHtml(item.priority)}">◆ ${escapeHtml(priorityLabel(item.priority))}</span>
        <span class="feature-name">◇ ${escapeHtml(item.feature_title || "Feature 미지정")}</span>
        <span>${escapeHtml(formatDate(item.created_at))}</span>
      </div>
    `;
    card.addEventListener("click", () => selectItem(item.id));
    elements.itemsList.append(card);
  }
}

function emptySection(message) {
  return `<div class="subtle-empty">${escapeHtml(message)}</div>`;
}

function renderDetail(context) {
  state.selectedContext = context;
  const item = context.work_item;
  const capabilities = context.capabilities || {};
  const virtualStatus = statusOf(item);
  const criteria = context.acceptance_criteria || [];
  const runs = [context.running_run, ...(context.recent_runs || [])].filter(Boolean);
  const criteriaHtml = criteria.length
    ? `<ul class="criteria-list">${criteria.map((criterion) => `
        <li class="criterion ${escapeHtml(criterion.status)}">
          <span class="criterion-mark">✓</span>
          <span>${escapeHtml(criterion.description)}</span>
        </li>`).join("")}</ul>`
    : emptySection(item.is_draft ? "구체화 전이라 완료 조건이 아직 없습니다." : "등록된 완료 조건이 없습니다.");
  const runsHtml = runs.length
    ? `<ul class="runs-list">${runs.map((run) => {
        const status = runStatusClass(run.status);
        const result = run.summary || (run.status === "running" ? "현재 실행 중입니다." : "실행 결과가 기록되지 않았습니다.");
        return `
        <li class="run-card run-card-${status}">
          <div class="run-card-header">
            <div class="run-card-identity">
              <span class="run-status-pill run-status-${status}"><span class="run-status-dot"></span>${escapeHtml(runStatusLabel(run.status))}</span>
              <code>${escapeHtml(run.id)}</code>
            </div>
            <div class="run-card-time">
              <time>${escapeHtml(formatRunDate(run.started_at))}</time>
              <span class="run-elapsed ${run.status === "running" ? "is-running" : ""}" ${run.status === "running" ? `data-run-elapsed data-started-at="${escapeHtml(run.started_at || "")}"` : ""}>${escapeHtml(durationLabel(run))}</span>
            </div>
          </div>
          <div class="run-card-body">
            <div class="run-field"><span>실행 목적</span><p>${escapeHtml(run.intent || "실행 목적이 기록되지 않았습니다.")}</p></div>
            <div class="run-field"><span>실행 결과</span>${run.status === "running"
              ? '<div class="run-result is-running"><span class="run-live-status" role="status" aria-label="실행 중"><span class="run-progress-ring" aria-hidden="true"></span><span>현재 실행 중입니다.</span></span></div>'
              : `<p class="run-result">${escapeHtml(result)}</p>`}</div>
            ${run.termination_reason ? `<div class="run-termination"><span class="run-warning-icon">△</span><span>경고: ${escapeHtml(run.termination_reason)}</span></div>` : ""}
          </div>
        </li>`;
      }).join("")}</ul>`
    : emptySection(item.is_draft ? "Draft에는 아직 Run이 없습니다." : "실행 기록이 없습니다.");
  const statusOptions = (capabilities.allowed_statuses || []).map((target) => `
    <button type="button" role="menuitem" data-status-target="${escapeHtml(target)}">
      ${escapeHtml(labelFor(STATUS_OPTIONS, target))}로 변경
    </button>`).join("");
  const deleteDisabled = capabilities.can_delete ? "" : "disabled";
  const editDisabled = capabilities.can_edit ? "" : "disabled";

  elements.detail.innerHTML = `
    <header class="detail-header">
      <div class="detail-header-top">
        <div class="card-meta">
          <span class="wi-id">${escapeHtml(item.id)}</span>
          <span class="badge kind-badge">${escapeHtml(labelFor(KIND_OPTIONS, item.kind))}</span>
        </div>
        <div class="detail-actions">
          <div class="status-control">
            <button class="status-button badge status-${escapeHtml(virtualStatus)}" id="status-menu-button" type="button" aria-haspopup="menu" aria-expanded="false" ${statusOptions ? "" : "disabled"}>
              ${escapeHtml(labelFor(STATUS_OPTIONS, virtualStatus))}${statusOptions ? " ▾" : ""}
            </button>
            ${statusOptions ? `<div class="status-menu" id="status-menu" role="menu">${statusOptions}</div>` : ""}
          </div>
          <button class="detail-action-button" id="edit-work-item" type="button" ${editDisabled}>수정</button>
          <button class="detail-action-button danger" id="delete-work-item" type="button" ${deleteDisabled} title="${escapeHtml(capabilities.delete_reason || "WorkItem 삭제")}">삭제</button>
        </div>
      </div>
      <h2>${escapeHtml(item.title)}</h2>
      <div class="detail-metadata">
        <div class="metadata-pair"><span>Status</span><strong>${escapeHtml(labelFor(STATUS_OPTIONS, virtualStatus))}</strong></div>
        <div class="metadata-pair"><span>Priority</span><strong>${escapeHtml(priorityLabel(item.priority))}</strong></div>
        <div class="metadata-pair"><span>Feature</span><strong>${escapeHtml(context.feature?.title || "미지정")}</strong></div>
        <div class="metadata-pair"><span>Created</span><strong>${escapeHtml(formatDate(item.created_at))}</strong></div>
      </div>
    </header>
    ${detailTabsHtml(state.detailTab)}
    <div class="detail-body">
      <div id="overview-panel" class="detail-panel ${state.detailTab === "overview" ? "" : "hidden"}">
        ${item.is_draft ? `<div class="draft-notice"><strong>Draft WorkItem</strong><br />아직 실행 계획과 완료 조건을 다듬기 전입니다. <code>w/</code>로 선택하면 Codex가 먼저 구체화합니다.</div>` : ""}
        <section class="detail-section"><h3>Goal</h3><p class="detail-block">${escapeHtml(item.goal)}</p></section>
        ${item.description ? `<section class="detail-section"><h3>Description</h3><p class="detail-block">${escapeHtml(item.description)}</p></section>` : ""}
        <section class="detail-section"><h3>Next Action</h3>${item.next_action ? `<p class="detail-block next-action">${escapeHtml(item.next_action)}</p>` : emptySection("다음 행동은 구체화 과정에서 정해집니다.")}</section>
        <section class="detail-section"><h3>Acceptance Criteria</h3>${criteriaHtml}</section>
        <section class="detail-section runs-section"><div class="runs-section-heading"><h3>Recent Runs</h3>${runs.length ? `<span class="runs-count">총 ${runs.length}건</span>` : ""}</div>${runsHtml}</section>
      </div>
      <div id="memos-panel" class="detail-panel ${state.detailTab === "memos" ? "" : "hidden"}">
        ${memoSectionHtml()}
      </div>
    </div>
  `;
  elements.emptyDetail.classList.add("hidden");
  elements.detail.classList.remove("hidden");
  bindDetailActions();
  renderMemoCards();
  bindMemoInteractions();
}

function bindDetailActions() {
  document.querySelectorAll("[data-detail-tab]").forEach((button) => {
    button.addEventListener("click", () => setDetailTab(button.dataset.detailTab));
  });
  const statusButton = document.querySelector("#status-menu-button");
  const statusControl = statusButton?.closest(".status-control");
  statusButton?.addEventListener("click", () => {
    const opened = statusControl.classList.toggle("open");
    statusButton.setAttribute("aria-expanded", String(opened));
  });
  document.querySelectorAll("[data-status-target]").forEach((button) => {
    button.addEventListener("click", () => changeSelectedStatus(button.dataset.statusTarget));
  });
  document.querySelector("#edit-work-item")?.addEventListener("click", openEditModal);
  document.querySelector("#delete-work-item")?.addEventListener("click", openDeleteModal);
}

async function changeSelectedStatus(target) {
  const item = state.selectedContext?.work_item;
  const confirmation = target === "done"
    ? "이 WorkItem의 결과를 확인했으며 완료 처리할까요?"
    : `${labelFor(STATUS_OPTIONS, target)} 상태로 변경할까요?`;
  if (!item || !window.confirm(confirmation)) return;
  try {
    const context = await api(`/api/work-items/${encodeURIComponent(item.id)}/status`, {
      method: "PATCH", body: JSON.stringify({ status: target }),
    });
    await loadItems({ selectId: context.work_item.id });
  } catch (error) {
    window.alert(error.status === 409 ? `${error.message}\n최신 상태를 다시 불러옵니다.` : error.message);
    await loadItems({ selectId: item.id });
  }
}

async function selectItem(workItemId) {
  state.selectedId = workItemId;
  renderItems();
  elements.detail.innerHTML = `<div class="loading-state"><span class="spinner"></span><p>상세 정보를 불러오는 중입니다.</p></div>`;
  elements.emptyDetail.classList.add("hidden");
  elements.detail.classList.remove("hidden");
  try {
    renderDetail(await api(`/api/work-items/${encodeURIComponent(workItemId)}`));
  } catch (error) {
    elements.detail.innerHTML = `<div class="empty-detail"><h2>상세 정보를 불러오지 못했습니다.</h2><p>${escapeHtml(error.message)}</p></div>`;
  }
}

async function loadItems({ selectId = null } = {}) {
  elements.loading.classList.remove("hidden");
  try {
    const payload = await api("/api/work-items");
    state.items = payload.work_items;
    elements.healthDot.className = "health-dot healthy";
    elements.healthLabel.textContent = "State DB 연결됨";
    buildFilters("#status-filters", "status", STATUS_OPTIONS);
    buildFilters("#kind-filters", "kind", KIND_OPTIONS);
    renderItems();
    const target = selectId || (state.selectedId && state.items.some((item) => item.id === state.selectedId) ? state.selectedId : state.items[0]?.id);
    if (target) await selectItem(target);
  } catch (error) {
    elements.healthDot.className = "health-dot failed";
    elements.healthLabel.textContent = "State DB 연결 실패";
    elements.itemsList.innerHTML = `<div class="empty-list"><strong>WorkItem을 불러오지 못했습니다.</strong><p>${escapeHtml(error.message)}</p></div>`;
    elements.dockSummary.textContent = "상태 저장소를 확인해 주세요";
  } finally {
    elements.loading.classList.add("hidden");
  }
}

function clearFormErrors() {
  elements.formError.classList.add("hidden");
  elements.formError.textContent = "";
  for (const element of elements.form.elements) element.removeAttribute?.("aria-invalid");
  document.querySelectorAll(".field-error").forEach((node) => { node.textContent = ""; });
}

function openModal() {
  clearFormErrors();
  elements.modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  document.querySelector("#draft-title").focus();
}

function closeModal() {
  elements.modal.classList.add("hidden");
  document.body.style.overflow = "";
  document.querySelector("#open-draft-modal").focus();
}

function openEditModal() {
  const context = state.selectedContext;
  if (!context?.capabilities?.can_edit) return;
  const item = context.work_item;
  const editable = new Set(context.capabilities.editable_fields || []);
  for (const field of elements.editForm.querySelectorAll("[data-edit-field]")) {
    field.classList.toggle("hidden", !editable.has(field.dataset.editField));
  }
  for (const name of ["title", "kind", "goal", "description", "priority", "next_action"]) {
    const input = elements.editForm.elements.namedItem(name);
    if (input) input.value = item[name] ?? "";
  }
  elements.editFormError.classList.add("hidden");
  elements.editModal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  elements.editForm.elements.namedItem("title")?.focus();
}

function closeEditModal() {
  elements.editModal.classList.add("hidden");
  document.body.style.overflow = "";
}

async function submitEdit(event) {
  event.preventDefault();
  const context = state.selectedContext;
  if (!context) return;
  const editable = new Set(context.capabilities.editable_fields || []);
  const data = new FormData(elements.editForm);
  const payload = {};
  for (const name of editable) payload[name] = data.get(name) || null;
  elements.editFormError.classList.add("hidden");
  try {
    const updated = await api(`/api/work-items/${encodeURIComponent(context.work_item.id)}`, {
      method: "PATCH", body: JSON.stringify(payload),
    });
    closeEditModal();
    await loadItems({ selectId: updated.work_item.id });
  } catch (error) {
    elements.editFormError.textContent = error.message;
    elements.editFormError.classList.remove("hidden");
    if (error.status === 409) await loadItems({ selectId: context.work_item.id });
  }
}

function openDeleteModal() {
  const context = state.selectedContext;
  if (!context?.capabilities?.can_delete) return;
  document.querySelector("#delete-work-item-id").textContent = context.work_item.id;
  elements.deleteConfirmation.value = "";
  elements.confirmDelete.disabled = true;
  document.querySelector("#delete-error").classList.add("hidden");
  elements.deleteModal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  elements.deleteConfirmation.focus();
}

function closeDeleteModal() {
  elements.deleteModal.classList.add("hidden");
  document.body.style.overflow = "";
}

async function deleteSelectedItem() {
  const item = state.selectedContext?.work_item;
  if (!item || elements.deleteConfirmation.value !== item.id) return;
  try {
    await api(`/api/work-items/${encodeURIComponent(item.id)}`, { method: "DELETE" });
    state.selectedId = null;
    state.selectedContext = null;
    closeDeleteModal();
    await loadItems();
  } catch (error) {
    const target = document.querySelector("#delete-error");
    target.textContent = error.message;
    target.classList.remove("hidden");
  }
}

function openMemoModal(memoId = null) {
  const memo = (state.selectedContext?.memos || []).find((entry) => entry.id === memoId);
  if (!state.selectedContext || (memoId && !memo)) return;
  state.editingMemoId = memoId;
  elements.memoForm.reset();
  elements.memoForm.elements.namedItem("kind").value = "general";
  if (memo) {
    document.querySelector("#memo-modal-title").textContent = "메모 수정";
    elements.memoForm.elements.namedItem("title").value = memo.title;
    elements.memoForm.elements.namedItem("content").value = memo.content;
    elements.memoForm.elements.namedItem("author").value = memo.author || "";
    const kind = elements.memoForm.querySelector(`input[name="kind"][value="${CSS.escape(memo.kind)}"]`);
    if (kind) kind.checked = true;
  } else {
    document.querySelector("#memo-modal-title").textContent = "새 메모";
  }
  elements.memoFormError.classList.add("hidden");
  elements.memoFormError.textContent = "";
  updateMemoPreview();
  elements.memoModal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  elements.memoForm.elements.namedItem("title")?.focus();
}

function closeMemoModal() {
  elements.memoModal.classList.add("hidden");
  document.body.style.overflow = "";
  state.editingMemoId = null;
}

async function submitMemo(event) {
  event.preventDefault();
  const item = state.selectedContext?.work_item;
  if (!item) return;
  const data = new FormData(elements.memoForm);
  const payload = {
    title: data.get("title"), content: data.get("content"), kind: data.get("kind"),
    author: data.get("author") || null,
  };
  elements.submitMemo.disabled = true;
  elements.submitMemo.querySelector(".memo-submit-label").textContent = "저장 중";
  elements.submitMemo.querySelector(".button-spinner").classList.remove("hidden");
  try {
    const base = `/api/work-items/${encodeURIComponent(item.id)}/memos`;
    await api(state.editingMemoId ? `${base}/${encodeURIComponent(state.editingMemoId)}` : base, {
      method: state.editingMemoId ? "PATCH" : "POST", body: JSON.stringify(payload),
    });
    closeMemoModal();
    await refreshSelectedDetail();
  } catch (error) {
    elements.memoFormError.textContent = error.message;
    elements.memoFormError.classList.remove("hidden");
  } finally {
    elements.submitMemo.disabled = false;
    elements.submitMemo.querySelector(".memo-submit-label").textContent = "메모 저장";
    elements.submitMemo.querySelector(".button-spinner").classList.add("hidden");
  }
}

function showFormError(error) {
  clearFormErrors();
  const details = error.payload?.error;
  const fields = details?.fields || {};
  for (const [name, message] of Object.entries(fields)) {
    const input = elements.form.elements.namedItem(name);
    input?.setAttribute("aria-invalid", "true");
    const target = document.querySelector(`[data-error-for="${CSS.escape(name)}"]`);
    if (target) target.textContent = message;
  }
  if (!Object.keys(fields).length || error.status !== 422) {
    elements.formError.textContent = error.status === 503 ? "상태 저장소를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요." : error.message;
    elements.formError.classList.remove("hidden");
  }
}

async function submitDraft(event) {
  event.preventDefault();
  clearFormErrors();
  const data = new FormData(elements.form);
  const payload = {
    title: data.get("title"),
    kind: data.get("kind"),
    goal: data.get("goal"),
    description: data.get("description") || null,
  };
  elements.submitButton.disabled = true;
  elements.submitButton.querySelector(".submit-label").textContent = "저장 중";
  elements.submitButton.querySelector(".button-spinner").classList.remove("hidden");
  try {
    const response = await api("/api/draft-work-items", { method: "POST", body: JSON.stringify(payload) });
    elements.form.reset();
    closeModal();
    await loadItems({ selectId: response.work_item.id });
  } catch (error) {
    showFormError(error);
  } finally {
    elements.submitButton.disabled = false;
    elements.submitButton.querySelector(".submit-label").textContent = "Draft 생성";
    elements.submitButton.querySelector(".button-spinner").classList.add("hidden");
  }
}

document.querySelector("#open-draft-modal").addEventListener("click", openModal);
document.querySelector("#close-draft-modal").addEventListener("click", closeModal);
document.querySelector("#cancel-draft").addEventListener("click", closeModal);
document.querySelector("#refresh-items").addEventListener("click", () => loadItems());
document.querySelector("#clear-filters").addEventListener("click", () => {
  state.statusFilters.clear();
  state.kindFilters.clear();
  buildFilters("#status-filters", "status", STATUS_OPTIONS);
  buildFilters("#kind-filters", "kind", KIND_OPTIONS);
  renderItems();
});
elements.form.addEventListener("submit", submitDraft);
elements.editForm.addEventListener("submit", submitEdit);
document.querySelector("#close-edit-modal").addEventListener("click", closeEditModal);
document.querySelector("#cancel-edit").addEventListener("click", closeEditModal);
document.querySelector("#cancel-delete").addEventListener("click", closeDeleteModal);
document.querySelector("#close-memo-modal").addEventListener("click", closeMemoModal);
document.querySelector("#cancel-memo").addEventListener("click", closeMemoModal);
elements.memoForm.addEventListener("submit", submitMemo);
elements.memoForm.elements.namedItem("content").addEventListener("input", updateMemoPreview);
elements.memoForm.elements.namedItem("content").addEventListener("keydown", (event) => {
  const textarea = event.currentTarget;
  const command = event.metaKey || event.ctrlKey;
  const key = event.key.toLowerCase();
  if (command && !event.altKey && key === "e") {
    event.preventDefault();
    toggleMemoCodeFormatting(textarea);
  } else if (command && !event.altKey && key === "b") {
    event.preventDefault();
    toggleMemoInlineFormatting(textarea, "**");
  } else if (command && !event.altKey && key === "i") {
    event.preventDefault();
    toggleMemoInlineFormatting(textarea, "*");
  } else if (command && key === "enter") {
    event.preventDefault();
    elements.memoForm.requestSubmit();
  } else if (!command && !event.altKey && event.key === "Tab") {
    event.preventDefault();
    indentMemoSelection(textarea, event.shiftKey);
  }
});
elements.deleteConfirmation.addEventListener("input", () => {
  elements.confirmDelete.disabled = elements.deleteConfirmation.value !== state.selectedContext?.work_item?.id;
});
elements.confirmDelete.addEventListener("click", deleteSelectedItem);
elements.modal.addEventListener("click", (event) => { if (event.target === elements.modal) closeModal(); });
elements.editModal.addEventListener("click", (event) => { if (event.target === elements.editModal) closeEditModal(); });
elements.deleteModal.addEventListener("click", (event) => { if (event.target === elements.deleteModal) closeDeleteModal(); });
elements.memoModal.addEventListener("click", (event) => { if (event.target === elements.memoModal) closeMemoModal(); });
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!elements.deleteModal.classList.contains("hidden")) closeDeleteModal();
  else if (!elements.editModal.classList.contains("hidden")) closeEditModal();
  else if (!elements.modal.classList.contains("hidden")) closeModal();
  else if (!elements.memoModal.classList.contains("hidden")) closeMemoModal();
});

window.setInterval(updateRunningDurations, 1000);
loadItems();
