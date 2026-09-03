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

const state = {
  items: [],
  selectedId: null,
  selectedContext: null,
  statusFilters: new Set(),
  kindFilters: new Set(),
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
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
    ? `<ul class="runs-list">${runs.map((run) => `
        <li class="run-row"><code>${escapeHtml(run.id)}</code><span>${escapeHtml(run.status)} · ${escapeHtml(formatDate(run.started_at))}</span></li>`).join("")}</ul>`
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
    <div class="detail-body">
      ${item.is_draft ? `<div class="draft-notice"><strong>Draft WorkItem</strong><br />아직 실행 계획과 완료 조건을 다듬기 전입니다. <code>w/</code>로 선택하면 Codex가 먼저 구체화합니다.</div>` : ""}
      <section class="detail-section"><h3>Goal</h3><p class="detail-block">${escapeHtml(item.goal)}</p></section>
      ${item.description ? `<section class="detail-section"><h3>Description</h3><p class="detail-block">${escapeHtml(item.description)}</p></section>` : ""}
      <section class="detail-section"><h3>Next Action</h3>${item.next_action ? `<p class="detail-block next-action">${escapeHtml(item.next_action)}</p>` : emptySection("다음 행동은 구체화 과정에서 정해집니다.")}</section>
      <section class="detail-section"><h3>Acceptance Criteria</h3>${criteriaHtml}</section>
      <section class="detail-section"><h3>Recent Runs</h3>${runsHtml}</section>
    </div>
  `;
  elements.emptyDetail.classList.add("hidden");
  elements.detail.classList.remove("hidden");
  bindDetailActions();
}

function bindDetailActions() {
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
  if (!item || !window.confirm(`${labelFor(STATUS_OPTIONS, target)} 상태로 변경할까요?`)) return;
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
elements.deleteConfirmation.addEventListener("input", () => {
  elements.confirmDelete.disabled = elements.deleteConfirmation.value !== state.selectedContext?.work_item?.id;
});
elements.confirmDelete.addEventListener("click", deleteSelectedItem);
elements.modal.addEventListener("click", (event) => { if (event.target === elements.modal) closeModal(); });
elements.editModal.addEventListener("click", (event) => { if (event.target === elements.editModal) closeEditModal(); });
elements.deleteModal.addEventListener("click", (event) => { if (event.target === elements.deleteModal) closeDeleteModal(); });
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!elements.deleteModal.classList.contains("hidden")) closeDeleteModal();
  else if (!elements.editModal.classList.contains("hidden")) closeEditModal();
  else if (!elements.modal.classList.contains("hidden")) closeModal();
});

loadItems();
