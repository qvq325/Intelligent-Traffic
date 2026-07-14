const API_BASE = "/api/config";
const FINAL_OPERATION_STATES = new Set([
  "succeeded",
  "failed",
  "rolled_back",
  "interrupted",
  "cancelled",
]);
const TAB_ORDER = [
  "runtime",
  "streams",
  "stream-profiles",
  "topologies",
  "scenes",
  "model-pipelines",
  "devices",
  "configuration",
  "audit",
];
const MODEL_PIPELINE_SCENES = [
  { key: "realtime", label: "实时监控", icon: "scan-line" },
  { key: "traffic_map", label: "道路态势", icon: "map" },
  { key: "no_parking", label: "禁停监控", icon: "octagon-alert" },
  { key: "road_abnormal", label: "道路异常", icon: "triangle-alert" },
];
const DEVICE_STREAM_KEYS = [
  { key: "realtime", label: "实时监控" },
  { key: "traffic_map", label: "道路态势" },
  { key: "no_parking", label: "禁停监控" },
  { key: "road_abnormal", label: "道路异常" },
];
const DEVICE_POLL_INTERVAL_MS = 5000;
const SCENE_TYPE_LABELS = {
  no_parking: "禁停",
  road_abnormal: "道路异常",
};
const STATUS_LABELS = {
  pending: "等待中",
  preflighting: "预检中",
  applying: "应用中",
  succeeded: "成功",
  failed: "失败",
  rolled_back: "已回滚",
  interrupted: "已中断",
  ready: "可用",
  needs_review: "待复核",
  online: "在线",
  offline: "离线",
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const elements = {
  view: $("#view-system"),
  navButton: $('[data-view="system"]'),
  summaryText: $("#system-summary-text"),
  refreshButton: $("#system-refresh-button"),
  operationBanner: $("#system-operation-banner"),
  operationTitle: $("#system-operation-title"),
  operationDetail: $("#system-operation-detail"),
  operationStatus: $("#system-operation-status"),
  runtimeUpdated: $("#system-runtime-updated"),
  runtimeDomains: $("#system-runtime-domains"),
  integrityList: $("#system-integrity-list"),
  recentOperationsBody: $("#system-recent-operations-body"),
  recentOperationsEmpty: $("#system-recent-operations-empty"),
  streamsBody: $("#system-streams-body"),
  streamSelectAll: $("#system-stream-select-all"),
  streamSelectionCount: $("#system-stream-selection-count"),
  streamBatchProbe: $("#system-stream-batch-probe"),
  streamBatchEdit: $("#system-stream-batch-edit"),
  streamBatchDelete: $("#system-stream-batch-delete"),
  streamProfilesBody: $("#system-stream-profiles-body"),
  topologiesBody: $("#system-topologies-body"),
  scenesBody: $("#system-scenes-body"),
  sceneTypeFilter: $("#system-scene-type-filter"),
  modelPipelineForm: $("#system-model-pipeline-form"),
  modelPipelineScenes: $("#system-model-pipeline-scenes"),
  modelPipelineSave: $("#system-model-pipeline-save"),
  modelPipelineStatus: $("#system-model-pipeline-status"),
  deviceCollected: $("#system-device-collected"),
  deviceHealth: $("#system-device-health"),
  deviceMetrics: $("#system-device-metrics"),
  deviceGpuState: $("#system-device-gpu-state"),
  deviceGpusBody: $("#system-device-gpus-body"),
  deviceGpusEmpty: $("#system-device-gpus-empty"),
  deviceStreamsBody: $("#system-device-streams-body"),
  exportButton: $("#system-export-button"),
  importButton: $("#system-import-button"),
  importInput: $("#system-import-input"),
  importSummary: $("#system-import-summary"),
  storageDetails: $("#system-storage-details"),
  auditBody: $("#system-audit-body"),
  auditResultFilter: $("#system-audit-result-filter"),
  auditPrevious: $("#system-audit-previous"),
  auditNext: $("#system-audit-next"),
  auditPage: $("#system-audit-page"),
  editorDialog: $("#system-editor-dialog"),
  editorForm: $("#system-editor-form"),
  editorKicker: $("#system-editor-kicker"),
  editorTitle: $("#system-editor-title"),
  editorBody: $("#system-editor-body"),
  editorSubmit: $("#system-editor-submit"),
  editorCancel: $("#system-editor-cancel"),
  editorClose: $("#system-editor-close"),
  confirmDialog: $("#system-confirm-dialog"),
  confirmTitle: $("#system-confirm-title"),
  confirmMessage: $("#system-confirm-message"),
  confirmDetails: $("#system-confirm-details"),
  confirmCheckWrap: $("#system-confirm-check-wrap"),
  confirmCheck: $("#system-confirm-check"),
  confirmCheckLabel: $("#system-confirm-check-label"),
  confirmSubmit: $("#system-confirm-submit"),
  toastRegion: $("#toast-region"),
};

const state = {
  activeTab: "runtime",
  loaded: new Set(),
  loading: new Set(),
  summary: null,
  streams: [],
  streamProfiles: [],
  topologies: [],
  scenes: [],
  modelPipelinePayload: null,
  deviceSnapshot: null,
  devicePollTimer: null,
  devicePollPending: false,
  revealedStreams: new Set(),
  selectedStreams: new Set(),
  probingStreams: new Set(),
  pendingStreamProfileActions: new Set(),
  operations: new Map(),
  auditPage: 1,
  auditPageSize: 25,
  auditPages: 1,
  editorSubmit: null,
};

class ConfigApiError extends Error {
  constructor(message, options = {}) {
    super(redactSecrets(message || "配置请求失败"));
    this.name = "ConfigApiError";
    this.status = options.status || 0;
    this.code = options.code || "CONFIG_REQUEST_FAILED";
    this.details = Array.isArray(options.details) ? options.details : [];
    this.operationId = options.operationId || "";
    this.rollback = options.rollback || "";
  }
}

function redactSecrets(value) {
  return String(value ?? "")
    .replace(/([a-z][a-z0-9+.-]*:\/\/)([^/@\s]+)@/gi, "$1***:***@")
    .replace(/(rtsp[_ -]?(?:url|address)["']?\s*[:=]\s*["']?)([^\s"']+)/gi, (_, prefix, url) => (
      `${prefix}${maskRtspUrl(url)}`
    ));
}

function maskRtspUrl(value) {
  const url = String(value || "");
  return url.replace(/^([a-z][a-z0-9+.-]*:\/\/)([^/@\s]+)@/i, "$1***:***@");
}

function refreshIcons() {
  window.lucide?.createIcons();
}

function jsonRequest(method, body) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  let payload = null;

  if (contentType.includes("json")) {
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const contract = payload?.error || {};
    const validation = Array.isArray(payload?.detail)
      ? payload.detail.map((item) => item?.msg || item?.message || String(item))
      : [];
    const contractDetails = Array.isArray(contract.details)
      ? contract.details
      : contract.details && typeof contract.details === "object"
        ? Object.entries(contract.details).map(([key, value]) => `${key}: ${value}`)
        : [];
    const message = contract.message
      || (typeof payload?.detail === "string" ? payload.detail : "")
      || validation[0]
      || `配置请求失败 (${response.status})`;
    throw new ConfigApiError(message, {
      status: response.status,
      code: contract.code,
      details: contractDetails.length ? contractDetails : validation,
      operationId: contract.operation_id,
      rollback: contract.rollback,
    });
  }

  if (contentType.includes("json")) return payload;
  if (response.status === 204) return null;
  return response;
}

function collection(payload, ...keys) {
  if (Array.isArray(payload)) return payload;
  for (const key of [...keys, "items", "results", "data"]) {
    if (Array.isArray(payload?.[key])) return payload[key];
  }
  return [];
}

function identifier(item, ...keys) {
  for (const key of [...keys, "id"]) {
    if (item?.[key] !== undefined && item?.[key] !== null) return String(item[key]);
  }
  return "";
}

function formatDate(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return redactSecrets(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function displayStatus(value) {
  const key = String(value || "").toLowerCase();
  return STATUS_LABELS[key] || redactSecrets(value || "未知");
}

function statusTone(value) {
  const key = String(value || "").toLowerCase();
  if (["succeeded", "ready", "online", "ok", "complete", "active"].includes(key)) return "allowed";
  if (["failed", "offline", "error", "interrupted"].includes(key)) return "denied";
  if (["pending", "preflighting", "applying", "needs_review", "rolled_back"].includes(key)) return "warning";
  return "";
}

function textCell(value, className = "") {
  const cell = document.createElement("td");
  cell.textContent = redactSecrets(value ?? "--");
  if (className) cell.className = className;
  return cell;
}

function identityCell(name, id = "") {
  const cell = document.createElement("td");
  const strong = document.createElement("strong");
  strong.className = "system-primary-text";
  strong.textContent = redactSecrets(name || "未命名");
  cell.append(strong);
  if (id) {
    const code = document.createElement("code");
    code.className = "system-id";
    code.textContent = redactSecrets(id);
    cell.append(code);
  }
  return cell;
}

function statusCell(value, label = "") {
  const cell = document.createElement("td");
  const chip = document.createElement("span");
  chip.className = `status-chip ${statusTone(value)}`.trim();
  chip.textContent = label || displayStatus(value);
  cell.append(chip);
  return cell;
}

function actionButton({ action, id = "", type = "", icon, title, danger = false, disabled = false }) {
  const button = document.createElement("button");
  button.className = `icon-button small${danger ? " danger-ghost" : ""}`;
  button.type = "button";
  button.title = title;
  button.dataset.systemAction = action;
  if (id) button.dataset.id = id;
  if (type) button.dataset.type = type;
  button.disabled = disabled;
  const glyph = document.createElement("i");
  glyph.dataset.lucide = icon;
  button.append(glyph);
  return button;
}

function actionCell(...buttons) {
  const cell = document.createElement("td");
  cell.className = "system-row-actions";
  const group = document.createElement("div");
  group.className = "system-action-group";
  group.append(...buttons.filter(Boolean));
  cell.append(group);
  return cell;
}

function notify(message, type = "info") {
  if (!elements.toastRegion) return;
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  const icon = document.createElement("i");
  icon.dataset.lucide = type === "error" ? "circle-alert" : type === "success" ? "circle-check" : "info";
  const text = document.createElement("span");
  text.textContent = redactSecrets(message);
  toast.append(icon, text);
  elements.toastRegion.append(toast);
  refreshIcons();
  window.setTimeout(() => toast.remove(), 4600);
}

function errorDetail(error) {
  if (!(error instanceof ConfigApiError)) return redactSecrets(error instanceof Error ? error.message : error);
  const detail = error.details
    .slice(0, 2)
    .map((item) => redactSecrets(typeof item === "string" ? item : item?.message || JSON.stringify(item)))
    .join("；");
  const suffix = [
    error.code && `错误码 ${error.code}`,
    error.rollback && `回滚 ${error.rollback}`,
    error.operationId && `操作 ${error.operationId}`,
    detail,
  ].filter(Boolean).join(" · ");
  return suffix ? `${error.message} · ${suffix}` : error.message;
}

function setPanelState(key, mode, message = "", retry = null) {
  const status = $(`[data-system-state="${key}"]`);
  const content = $(`[data-system-content="${key}"]`);
  if (!status || !content) return;
  status.replaceChildren();
  status.hidden = mode === "ready";
  content.hidden = mode !== "ready";
  status.className = `system-panel-state ${mode}`;
  if (mode === "ready") return;

  const icon = document.createElement("i");
  icon.dataset.lucide = mode === "loading" ? "loader-circle" : mode === "error" ? "circle-alert" : "inbox";
  const strong = document.createElement("strong");
  strong.textContent = mode === "loading" ? "正在加载" : mode === "error" ? "加载失败" : "暂无数据";
  const detail = document.createElement("span");
  detail.textContent = redactSecrets(message || (mode === "loading" ? "正在读取配置中心" : "当前分类没有可显示的记录"));
  status.append(icon, strong, detail);
  if (mode === "error" && retry) {
    const button = document.createElement("button");
    button.className = "command-button";
    button.type = "button";
    button.textContent = "重试";
    button.addEventListener("click", retry, { once: true });
    status.append(button);
  }
  refreshIcons();
}

function activateTab(key, { load = true, focus = false } = {}) {
  if (!TAB_ORDER.includes(key)) return;
  state.activeTab = key;
  $$('[data-system-tab]').forEach((button) => {
    const active = button.dataset.systemTab === key;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    if (active && focus) button.focus();
  });
  $$('[data-system-panel]').forEach((panel) => {
    const active = panel.dataset.systemPanel === key;
    panel.classList.toggle("active", active);
    panel.hidden = !active;
  });
  if (key === "devices") startDevicePolling();
  else stopDevicePolling();
  if (load) ensureTabLoaded(key).catch((error) => showLoadError(key, error));
}

function showLoadError(key, error) {
  state.loaded.delete(key);
  setPanelState(key, "error", errorDetail(error), () => {
    ensureTabLoaded(key, true).catch((nextError) => showLoadError(key, nextError));
  });
}

async function ensureTabLoaded(key, force = false) {
  if (state.loading.has(key) || (!force && state.loaded.has(key))) return;
  state.loading.add(key);
  setPanelState(key, "loading");
  try {
    if (key === "runtime") await loadSummary();
    else if (key === "streams") await loadStreams();
    else if (key === "stream-profiles") await loadStreamProfiles();
    else if (key === "topologies") await loadTopologies();
    else if (key === "scenes") await loadScenes();
    else if (key === "model-pipelines") await loadModelPipelines();
    else if (key === "devices") await loadDevices();
    else if (key === "configuration") await loadConfiguration(force);
    else if (key === "audit") await loadAudit();
    state.loaded.add(key);
  } finally {
    state.loading.delete(key);
  }
}

function invalidate(...keys) {
  for (const key of keys.flat()) state.loaded.delete(key);
  if (keys.flat().includes(state.activeTab)) {
    ensureTabLoaded(state.activeTab, true).catch((error) => showLoadError(state.activeTab, error));
  }
}

function currentActivation(summary = state.summary) {
  return summary?.activation_state || summary?.activation || summary?.active || {};
}

function activeReference(domain, summary = state.summary) {
  const activation = currentActivation(summary);
  const aliases = {
    stream_profile: ["stream_profile", "active_stream_profile", "stream_binding_profile"],
    topology: ["topology", "active_topology"],
    no_parking_scene: ["no_parking_scene", "active_no_parking_scene"],
    road_abnormal_scene: ["road_abnormal_scene", "active_road_abnormal_scene"],
  };
  const idKeys = {
    stream_profile: ["stream_profile_id", "stream_binding_profile_id"],
    topology: ["topology_id"],
    no_parking_scene: ["no_parking_scene_id"],
    road_abnormal_scene: ["road_abnormal_scene_id"],
  };
  for (const key of aliases[domain] || []) {
    const value = summary?.[key] || activation?.[key];
    if (value && typeof value === "object") return value;
  }
  for (const key of idKeys[domain] || []) {
    const id = activation?.[key] || summary?.[key];
    if (id) return { id, name: id };
  }
  return null;
}

async function loadSummary() {
  const summary = await request("/summary");
  state.summary = summary || {};
  renderRuntimeSummary(state.summary);
  renderStorageDetails(state.summary);
  setPanelState("runtime", "ready");
  setPanelState("configuration", "ready");
  state.loaded.add("configuration");
}

function renderRuntimeSummary(summary) {
  const cameraCatalog = collection(summary, "cameras", "fixed_cameras", "camera_catalog");
  const schemaVersion = summary.schema_version || summary.repository?.schema_version || "--";
  elements.summaryText.textContent = `配置中心在线 · Schema ${schemaVersion} · ${cameraCatalog.length || 12} 个固定摄像头`;
  elements.runtimeUpdated.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  elements.runtimeDomains.replaceChildren();

  const domains = [
    { key: "stream_profile", label: "流关联方案", icon: "radio-tower", tab: "stream-profiles" },
    { key: "topology", label: "道路拓扑", icon: "network", tab: "topologies" },
    { key: "no_parking_scene", label: "禁停场景", icon: "octagon-alert", tab: "scenes" },
    { key: "road_abnormal_scene", label: "道路异常场景", icon: "triangle-alert", tab: "scenes" },
  ];

  for (const domain of domains) {
    const current = activeReference(domain.key, summary);
    const row = document.createElement("article");
    row.className = "system-domain-row";
    const glyph = document.createElement("span");
    glyph.className = "system-domain-icon";
    const icon = document.createElement("i");
    icon.dataset.lucide = domain.icon;
    glyph.append(icon);
    const copy = document.createElement("div");
    const eyebrow = document.createElement("span");
    eyebrow.textContent = domain.label;
    const name = document.createElement("strong");
    name.textContent = redactSecrets(current?.name || current?.display_name || "未激活");
    const detail = document.createElement("small");
    const currentId = identifier(current || {}, "profile_id", "topology_id", "scene_id");
    const currentStatus = current?.status || current?.online_status || (current ? "active" : "offline");
    detail.textContent = currentId ? `${displayStatus(currentStatus)} · ${redactSecrets(currentId)}` : "当前没有激活项";
    copy.append(eyebrow, name, detail);
    const manage = document.createElement("button");
    manage.className = "command-button subtle";
    manage.type = "button";
    manage.dataset.systemAction = "switch-tab";
    manage.dataset.tab = domain.tab;
    manage.textContent = "管理";
    row.append(glyph, copy, manage);
    elements.runtimeDomains.append(row);
  }

  renderIntegrity(summary);
  renderRecentOperations(collection(summary, "recent_operations", "operations"));
  refreshIcons();
}

function renderIntegrity(summary) {
  elements.integrityList.replaceChildren();
  let checks = collection(summary, "integrity", "checks", "preconditions");
  if (!checks.length && typeof summary.integrity?.ok === "boolean") {
    checks = [
      {
        name: "SQLite 配置仓库",
        status: summary.integrity.ok,
        message: collection(summary.integrity, "messages").join("；") || (summary.integrity.ok ? "完整性正常" : "完整性异常"),
      },
      { name: "内容寻址资源", status: summary.integrity.ok, message: `${summary.counts?.assets ?? 0} 个资源` },
      { name: "固定摄像头目录", status: true, message: `${collection(summary, "camera_catalog").length} 个摄像头` },
    ];
  }
  if (!checks.length && summary.integrity && typeof summary.integrity === "object") {
    checks = Object.entries(summary.integrity).map(([name, value]) => ({ name, status: value }));
  }
  if (!checks.length) {
    checks = [
      { name: "SQLite 配置仓库", status: summary.repository?.status || summary.database_status || "ready" },
      { name: "内容寻址资源", status: summary.assets?.status || summary.asset_status || "ready" },
      { name: "固定摄像头目录", status: summary.camera_catalog_status || "ready" },
    ];
  }
  for (const check of checks) {
    const item = document.createElement("div");
    const icon = document.createElement("i");
    const status = check.status === true ? "ready" : check.status === false ? "failed" : check.status;
    icon.dataset.lucide = statusTone(status) === "allowed" ? "circle-check" : statusTone(status) === "denied" ? "circle-x" : "circle-alert";
    const text = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = redactSecrets(check.name || check.label || check.code || "完整性检查");
    const detail = document.createElement("span");
    detail.textContent = redactSecrets(check.message || check.detail || displayStatus(status));
    text.append(name, detail);
    item.className = statusTone(status);
    item.append(icon, text);
    elements.integrityList.append(item);
  }
}

function renderRecentOperations(operations) {
  elements.recentOperationsBody.replaceChildren();
  for (const operation of operations.slice(0, 8)) {
    const row = document.createElement("tr");
    row.append(
      textCell(formatDate(operation.started_at || operation.created_at)),
      textCell(operation.operation_type || operation.type || "配置操作"),
      textCell(operation.target_name || operation.target || "--"),
      statusCell(operation.status),
      textCell(operation.error_summary || operation.summary || operation.message || "--"),
    );
    elements.recentOperationsBody.append(row);
  }
  elements.recentOperationsEmpty.hidden = operations.length > 0;
}

async function loadStreams() {
  const payload = await request("/streams");
  state.streams = collection(payload, "streams");
  const existingIds = new Set(state.streams.map((stream) => identifier(stream, "stream_id")));
  state.selectedStreams = new Set(
    [...state.selectedStreams].filter((streamId) => existingIds.has(streamId)),
  );
  state.revealedStreams.clear();
  renderStreams();
  setPanelState("streams", state.streams.length ? "ready" : "empty", "尚未添加 RTSP 流");
}

function selectedStreamItems() {
  return state.streams.filter((stream) => (
    state.selectedStreams.has(identifier(stream, "stream_id"))
  ));
}

function updateStreamSelectionControls() {
  const selectedCount = selectedStreamItems().length;
  const total = state.streams.length;
  if (elements.streamSelectionCount) {
    elements.streamSelectionCount.textContent = `已选 ${selectedCount} 项`;
  }
  if (elements.streamSelectAll) {
    elements.streamSelectAll.disabled = total === 0;
    elements.streamSelectAll.checked = total > 0 && selectedCount === total;
    elements.streamSelectAll.indeterminate = selectedCount > 0 && selectedCount < total;
  }
  if (elements.streamBatchProbe) elements.streamBatchProbe.disabled = total === 0;
  if (elements.streamBatchEdit) elements.streamBatchEdit.disabled = selectedCount === 0;
  if (elements.streamBatchDelete) elements.streamBatchDelete.disabled = selectedCount === 0;
}

function streamSelectionCell(streamId) {
  const cell = document.createElement("td");
  cell.className = "system-select-column";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = state.selectedStreams.has(streamId);
  input.dataset.streamSelection = streamId;
  input.setAttribute("aria-label", `选择流 ${streamId}`);
  cell.append(input);
  return cell;
}

function renderStreams() {
  elements.streamsBody.replaceChildren();
  for (const stream of state.streams) {
    const streamId = identifier(stream, "stream_id");
    const revealed = state.revealedStreams.has(streamId);
    const urlCell = document.createElement("td");
    const wrap = document.createElement("div");
    wrap.className = "system-secret-value";
    const code = document.createElement("code");
    code.textContent = revealed ? String(stream.rtsp_url || "") : maskRtspUrl(stream.rtsp_url);
    const reveal = actionButton({
      action: "toggle-stream-secret",
      id: streamId,
      icon: revealed ? "eye-off" : "eye",
      title: revealed ? "隐藏凭据" : "显示凭据",
    });
    wrap.append(code, reveal);
    urlCell.append(wrap);

    const probing = state.probingStreams.has(streamId);
    const probeStatus = probing ? "探测中" : stream.last_probe?.code
      || stream.last_probe?.status
      || stream.last_probe_status?.status
      || stream.last_probe_status
      || stream.probe_status
      || "未探测";
    const row = document.createElement("tr");
    row.classList.toggle("batch-selected", state.selectedStreams.has(streamId));
    row.append(
      streamSelectionCell(streamId),
      identityCell(stream.name, streamId),
      urlCell,
      statusCell(stream.enabled === false ? "offline" : "ready", stream.enabled === false ? "已禁用" : "可用"),
      statusCell(probing ? "applying" : probeStatus, probeStatus),
      textCell(formatDate(stream.updated_at || stream.last_probe_at)),
      actionCell(
        actionButton({ action: "probe-stream", id: streamId, icon: "activity", title: "探测流" }),
        actionButton({ action: "edit-stream", id: streamId, icon: "pencil", title: "编辑流" }),
        actionButton({
          action: "delete-stream",
          id: streamId,
          icon: "trash-2",
          title: "删除流",
          danger: true,
          disabled: stream.can_delete === false || Boolean(stream.reference_count),
        }),
      ),
    );
    elements.streamsBody.append(row);
  }
  updateStreamSelectionControls();
  refreshIcons();
}

async function loadStreamProfiles() {
  const payload = await request("/stream-profiles");
  state.streamProfiles = collection(payload, "profiles", "stream_profiles");
  renderStreamProfiles();
  setPanelState("stream-profiles", state.streamProfiles.length ? "ready" : "empty", "尚未创建流关联方案");
}

function renderStreamProfiles() {
  const activeId = identifier(activeReference("stream_profile") || {}, "profile_id");
  elements.streamProfilesBody.replaceChildren();
  for (const profile of state.streamProfiles) {
    const profileId = identifier(profile, "profile_id");
    const active = Boolean(profile.is_active || profile.active || profileId === activeId);
    const builtin = Boolean(profile.is_builtin);
    const bindingCount = Number(profile.binding_count ?? profile.bindings?.length ?? 0);
    const requiredCount = Number(profile.required_count ?? 12);
    const complete = profile.complete ?? bindingCount === requiredCount;
    const pending = state.pendingStreamProfileActions.has(profileId);
    const row = document.createElement("tr");
    if (active) row.classList.add("selected");
    if (pending) row.setAttribute("aria-busy", "true");
    row.append(
      identityCell(profile.name, profileId),
      statusCell(complete ? "ready" : "needs_review", complete ? "完整" : "不完整"),
      textCell(`${bindingCount} / ${requiredCount}`),
      statusCell(builtin ? "pending" : active ? "active" : "ready", builtin ? "内置只读" : active ? "当前激活" : "用户方案"),
      textCell(formatDate(profile.updated_at)),
      actionCell(
        actionButton({ action: "edit-stream-profile", id: profileId, icon: builtin ? "eye" : "pencil", title: builtin ? "查看方案" : "编辑方案" }),
        actionButton({ action: "clone-stream-profile", id: profileId, icon: "copy", title: "复制方案" }),
        actionButton({ action: "preflight-stream-profile", id: profileId, icon: "list-checks", title: pending ? "方案正在处理" : "执行预检", disabled: !complete || pending }),
        actionButton({ action: "activate-stream-profile", id: profileId, icon: "power", title: pending ? "方案正在处理" : "激活方案", disabled: active || !complete || pending }),
        actionButton({ action: "delete-stream-profile", id: profileId, icon: "trash-2", title: "删除方案", danger: true, disabled: active || builtin || profile.can_delete === false }),
      ),
    );
    elements.streamProfilesBody.append(row);
  }
  refreshIcons();
}

async function loadTopologies() {
  const payload = await request("/topologies");
  state.topologies = collection(payload, "topologies", "profiles");
  renderTopologies();
  setPanelState("topologies", state.topologies.length ? "ready" : "empty", "尚未创建道路拓扑方案");
}

function renderTopologies() {
  const activeId = identifier(activeReference("topology") || {}, "topology_id");
  elements.topologiesBody.replaceChildren();
  for (const topology of state.topologies) {
    const topologyId = identifier(topology, "topology_id");
    const active = Boolean(topology.is_active || topology.active || topologyId === activeId);
    const builtin = Boolean(topology.is_builtin);
    const row = document.createElement("tr");
    if (active) row.classList.add("selected");
    row.append(
      identityCell(topology.name, topologyId),
      textCell(`r${topology.revision || 1}`),
      textCell(String(topology.segment_count ?? topology.road_count ?? topology.segments?.length ?? 0)),
      textCell(String(topology.camera_count ?? topology.cameras?.length ?? 0)),
      textCell(String(topology.scene_reference_count ?? topology.scene_count ?? 0)),
      textCell(formatDate(topology.updated_at)),
      actionCell(
        actionButton({ action: "open-topology", id: topologyId, icon: "map", title: active ? "进入道路编辑器" : "查看方案信息" }),
        actionButton({ action: "clone-topology", id: topologyId, icon: "copy", title: "复制拓扑" }),
        actionButton({ action: "activate-topology", id: topologyId, icon: "power", title: "激活拓扑", disabled: active }),
        actionButton({ action: "delete-topology", id: topologyId, icon: "trash-2", title: "删除拓扑", danger: true, disabled: active || builtin || topology.can_delete === false }),
      ),
    );
    elements.topologiesBody.append(row);
  }
  refreshIcons();
}

async function loadScenes() {
  const params = new URLSearchParams();
  if (elements.sceneTypeFilter.value) params.set("scene_type", elements.sceneTypeFilter.value);
  const payload = await request(`/scenes${params.size ? `?${params}` : ""}`);
  state.scenes = collection(payload, "scenes");
  renderScenes();
  setPanelState("scenes", state.scenes.length ? "ready" : "empty", "当前筛选条件下没有场景档案");
}

function renderScenes() {
  const activation = currentActivation();
  elements.scenesBody.replaceChildren();
  for (const scene of state.scenes) {
    const sceneId = identifier(scene, "scene_id");
    const sceneType = scene.scene_type || scene.type || "";
    const activeId = sceneType === "no_parking"
      ? activation.no_parking_scene_id
      : activation.road_abnormal_scene_id;
    const active = Boolean(scene.is_active || scene.active || sceneId === activeId);
    const reviewStatus = scene.review_status || "ready";
    const row = document.createElement("tr");
    if (active) row.classList.add("selected");
    row.append(
      identityCell(scene.name, sceneId),
      textCell(SCENE_TYPE_LABELS[sceneType] || sceneType || "--"),
      textCell(scene.camera_name || scene.camera_id || "--"),
      textCell(`${scene.topology_name || scene.topology_id || "--"} · r${scene.topology_revision || "--"}`),
      statusCell(reviewStatus),
      statusCell(active ? "active" : "offline", active ? "已激活" : "未激活"),
      actionCell(
        actionButton({ action: "edit-scene", id: sceneId, type: sceneType, icon: "square-pen", title: "进入场景编辑器" }),
        actionButton({ action: "activate-scene", id: sceneId, type: sceneType, icon: "power", title: "激活场景", disabled: active || reviewStatus === "needs_review" }),
        actionButton({ action: "deactivate-scene", id: sceneId, type: sceneType, icon: "circle-stop", title: "停用此类场景", danger: true, disabled: !active }),
      ),
    );
    elements.scenesBody.append(row);
  }
  refreshIcons();
}

async function loadModelPipelines() {
  const payload = await request("/model-pipelines");
  state.modelPipelinePayload = payload || {};
  renderModelPipelines();
  setPanelState("model-pipelines", "ready");
}

function renderModelPipelines() {
  const payload = state.modelPipelinePayload || {};
  const settings = collection(payload, "settings");
  const presets = collection(payload, "presets");
  const devices = collection(payload, "devices");
  elements.modelPipelineScenes.replaceChildren();

  for (const scene of MODEL_PIPELINE_SCENES) {
    const setting = settings.find((item) => item.scene_key === scene.key);
    if (!setting) {
      throw new ConfigApiError(`缺少 ${scene.label} 的模型配置`, {
        code: "MODEL_PIPELINE_RESPONSE_INVALID",
      });
    }

    const article = document.createElement("article");
    article.className = "system-model-scene";
    article.dataset.modelScene = scene.key;

    const header = document.createElement("header");
    const identity = document.createElement("div");
    identity.className = "system-model-identity";
    const glyph = document.createElement("span");
    glyph.className = "system-model-icon";
    const icon = document.createElement("i");
    icon.dataset.lucide = scene.icon;
    glyph.append(icon);
    const title = document.createElement("div");
    const strong = document.createElement("strong");
    strong.textContent = scene.label;
    const revision = document.createElement("span");
    revision.textContent = `修订 ${setting.revision ?? "--"}`;
    title.append(strong, revision);
    identity.append(glyph, title);

    const enabled = document.createElement("label");
    enabled.className = "switch-field compact";
    const enabledInput = document.createElement("input");
    enabledInput.type = "checkbox";
    enabledInput.checked = Boolean(setting.enabled);
    enabledInput.dataset.modelField = "enabled";
    const switchTrack = document.createElement("span");
    switchTrack.className = "switch";
    switchTrack.setAttribute("aria-hidden", "true");
    const enabledLabel = document.createElement("span");
    enabledLabel.textContent = "启用";
    enabled.append(enabledInput, switchTrack, enabledLabel);
    header.append(identity, enabled);

    const fields = document.createElement("div");
    fields.className = "system-model-fields";
    fields.append(
      modelPipelineField(
        "流水线",
        modelPipelineSelect(scene.key, "preset", presets, setting.preset, (item) => {
          const label = item.id === "legacy" ? "现有模型" : item.id === "trained" ? "训练后模型" : item.label || item.id;
          return item.available === false ? `${label}（不可用）` : label;
        }),
      ),
      modelPipelineField(
        "推理设备",
        modelPipelineSelect(scene.key, "device_preference", devices, setting.device_preference, (item) => item.label || item.id),
      ),
      modelPipelineField(
        "车辆阈值",
        modelPipelineNumberInput("yolo_threshold", setting.yolo_threshold, { min: 0.05, max: 1, step: 0.01 }),
      ),
      modelPipelineField(
        "车牌阈值",
        modelPipelineNumberInput("lpr_threshold", setting.lpr_threshold, { min: 0.05, max: 1, step: 0.01 }),
      ),
      modelPipelineField(
        "推理间隔",
        modelPipelineNumberInput("frame_interval", setting.frame_interval, { min: 1, max: 60, step: 1 }),
        "帧",
      ),
      modelPipelineField(
        "输入尺寸",
        modelPipelineNumberInput("inference_size", setting.inference_size, { min: 160, max: 2048, step: 32 }),
        "px",
      ),
    );
    article.append(header, fields);

    const advanced = modelPipelineAdvancedFields(scene.key, setting);
    if (advanced) article.append(advanced);
    elements.modelPipelineScenes.append(article);
  }

  elements.modelPipelineStatus.textContent = `已加载 ${settings.length} 个场景 · ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
  refreshIcons();
}

function modelPipelineSelect(sceneKey, field, options, value, labeler) {
  const select = document.createElement("select");
  select.name = `${sceneKey}-${field}`;
  select.dataset.modelField = field;
  for (const item of options) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = labeler(item);
    option.disabled = item.available === false && item.id !== value;
    option.selected = item.id === value;
    select.append(option);
  }
  return select;
}

function modelPipelineNumberInput(field, value, { min, max, step }) {
  const input = document.createElement("input");
  input.type = "number";
  input.name = field;
  input.dataset.modelField = field;
  input.value = String(value);
  input.min = String(min);
  input.max = String(max);
  input.step = String(step);
  input.required = true;
  return input;
}

function modelPipelineField(labelText, control, suffix = "") {
  const label = document.createElement("label");
  label.className = "system-model-field";
  const caption = document.createElement("span");
  caption.textContent = labelText;
  if (!suffix) {
    label.append(caption, control);
    return label;
  }
  const group = document.createElement("span");
  group.className = "system-model-input-unit";
  const unit = document.createElement("span");
  unit.textContent = suffix;
  group.append(control, unit);
  label.append(caption, group);
  return label;
}

function modelPipelineAdvancedFields(sceneKey, setting) {
  if (!["no_parking", "road_abnormal"].includes(sceneKey)) return null;
  const details = document.createElement("details");
  details.className = "system-model-advanced";
  const summary = document.createElement("summary");
  summary.textContent = "高级参数";
  const fields = document.createElement("div");
  fields.className = "system-model-advanced-grid";

  if (sceneKey === "no_parking") {
    fields.append(
      modelPipelineField(
        "静止位移阈值",
        modelPipelineNumberInput("parking_move_threshold", setting.parking_move_threshold, { min: 0.001, max: 1, step: 0.005 }),
      ),
    );
  } else {
    fields.append(
      modelPipelineField("背景历史", modelPipelineNumberInput("mog_history", setting.mog_history, { min: 10, max: 5000, step: 1 }), "帧"),
      modelPipelineField("方差阈值", modelPipelineNumberInput("mog_variance_threshold", setting.mog_variance_threshold, { min: 1, max: 255, step: 0.5 })),
      modelPipelineField("最小面积", modelPipelineNumberInput("mog_min_area", setting.mog_min_area, { min: 1, max: 1000000, step: 1 }), "px²"),
      modelPipelineField("最短持续", modelPipelineNumberInput("mog_min_duration", setting.mog_min_duration, { min: 0.1, max: 300, step: 0.1 }), "秒"),
      modelPipelineField("最长持续", modelPipelineNumberInput("mog_max_duration", setting.mog_max_duration, { min: 0.1, max: 3600, step: 0.1 }), "秒"),
      modelPipelineField("预热帧数", modelPipelineNumberInput("mog_warmup_frames", setting.mog_warmup_frames, { min: 0, max: 5000, step: 1 }), "帧"),
    );
  }
  details.append(summary, fields);
  return details;
}

function readModelPipelineSetting(sceneKey) {
  const root = $(`[data-model-scene="${sceneKey}"]`, elements.modelPipelineScenes);
  const source = collection(state.modelPipelinePayload || {}, "settings")
    .find((item) => item.scene_key === sceneKey);
  if (!root || !source) {
    throw new ConfigApiError("模型配置尚未完成加载", { code: "MODEL_PIPELINE_FORM_INVALID" });
  }
  const control = (field) => $(`[data-model-field="${field}"]`, root);
  const number = (field, integer = false) => {
    const target = control(field);
    const value = Number(target ? target.value : source[field]);
    if (!Number.isFinite(value) || (integer && !Number.isInteger(value))) {
      throw new ConfigApiError(`${sceneKey}.${field} 不是有效数值`, { code: "MODEL_PIPELINE_FORM_INVALID" });
    }
    return value;
  };
  const minimumDuration = number("mog_min_duration");
  const maximumDuration = number("mog_max_duration");
  if (maximumDuration < minimumDuration) {
    throw new ConfigApiError("最长持续时间不能小于最短持续时间", { code: "MODEL_PIPELINE_FORM_INVALID" });
  }
  return {
    scene_key: sceneKey,
    preset: control("preset").value,
    enabled: control("enabled").checked,
    device_preference: control("device_preference").value,
    yolo_threshold: number("yolo_threshold"),
    lpr_threshold: number("lpr_threshold"),
    frame_interval: number("frame_interval", true),
    inference_size: number("inference_size", true),
    parking_move_threshold: number("parking_move_threshold"),
    mog_history: number("mog_history", true),
    mog_variance_threshold: number("mog_variance_threshold"),
    mog_min_area: number("mog_min_area", true),
    mog_min_duration: minimumDuration,
    mog_max_duration: maximumDuration,
    mog_warmup_frames: number("mog_warmup_frames", true),
  };
}

async function saveModelPipelines() {
  const payload = {
    settings: MODEL_PIPELINE_SCENES.map((scene) => readModelPipelineSetting(scene.key)),
  };
  elements.modelPipelineSave.disabled = true;
  elements.modelPipelineStatus.textContent = "正在保存";
  try {
    state.modelPipelinePayload = await request(
      "/model-pipelines",
      jsonRequest("PUT", payload),
    );
    renderModelPipelines();
    notify("模型配置已保存", "success");
  } catch (error) {
    elements.modelPipelineStatus.textContent = errorDetail(error);
    throw error;
  } finally {
    elements.modelPipelineSave.disabled = false;
  }
}

async function loadDevices() {
  if (state.devicePollPending) return;
  state.devicePollPending = true;
  try {
    state.deviceSnapshot = await request("/devices");
    renderDevices(state.deviceSnapshot || {});
    setPanelState("devices", "ready");
  } finally {
    state.devicePollPending = false;
  }
}

function renderDevices(snapshot) {
  const sections = [snapshot.cpu, snapshot.memory, snapshot.process];
  const unavailable = sections.filter((item) => item?.available === false).length;
  elements.deviceCollected.textContent = formatDate(snapshot.collected_at);
  elements.deviceHealth.className = `status-chip ${unavailable ? "warning" : "allowed"}`;
  elements.deviceHealth.textContent = unavailable ? `${unavailable} 项不可用` : "采样正常";
  renderDeviceMetrics(snapshot);
  renderDeviceGpus(snapshot.gpu || {});
  renderDeviceStreams(collection(snapshot, "streams"));
}

function renderDeviceMetrics(snapshot) {
  const cpu = snapshot.cpu || {};
  const memory = snapshot.memory || {};
  const process = snapshot.process || {};
  const metrics = [
    {
      key: "cpu",
      label: "CPU",
      icon: "cpu",
      percent: cpu.utilization_percent,
      value: cpu.available === false ? "不可用" : `${formatPercent(cpu.utilization_percent)}`,
      detail: cpu.available === false ? cpu.error : `${cpu.physical_cores ?? "--"} 物理核 · ${cpu.logical_cores ?? "--"} 逻辑核`,
    },
    {
      key: "memory",
      label: "内存",
      icon: "memory-stick",
      percent: memory.utilization_percent,
      value: memory.available === false ? "不可用" : `${formatPercent(memory.utilization_percent)}`,
      detail: memory.available === false ? memory.error : `${formatBytes(memory.used_bytes)} / ${formatBytes(memory.total_bytes)}`,
    },
    {
      key: "process",
      label: "服务进程",
      icon: "activity",
      percent: process.cpu_percent,
      value: process.available === false ? "不可用" : `${formatPercent(process.cpu_percent)} CPU`,
      detail: process.available === false ? process.error : `${formatBytes(process.rss_bytes)} RSS · ${formatPercent(process.memory_percent)} 内存`,
    },
  ];

  elements.deviceMetrics.replaceChildren();
  for (const metric of metrics) {
    const article = document.createElement("article");
    article.className = `system-device-metric ${metric.key}`;
    const header = document.createElement("div");
    const icon = document.createElement("i");
    icon.dataset.lucide = metric.icon;
    const label = document.createElement("span");
    label.textContent = metric.label;
    header.append(icon, label);
    const value = document.createElement("strong");
    value.textContent = metric.value;
    const meter = document.createElement("meter");
    meter.min = 0;
    meter.max = 100;
    meter.value = Math.max(0, Math.min(100, Number(metric.percent) || 0));
    meter.setAttribute("aria-label", `${metric.label} 利用率`);
    const detail = document.createElement("small");
    detail.textContent = metric.detail || "--";
    article.append(header, value, meter, detail);
    elements.deviceMetrics.append(article);
  }
  refreshIcons();
}

function renderDeviceGpus(gpu) {
  const devices = Array.isArray(gpu.devices) ? gpu.devices : [];
  elements.deviceGpusBody.replaceChildren();
  elements.deviceGpusEmpty.hidden = devices.length > 0;
  elements.deviceGpuState.textContent = gpu.available === false
    ? "NVML 不可用"
    : `${devices.length} 个设备`;
  for (const device of devices) {
    const row = document.createElement("tr");
    row.append(
      identityCell(device.name || `GPU ${device.index}`, `GPU ${device.index}`),
      textCell(formatPercent(device.utilization_percent)),
      textCell(`${formatBytes(device.memory_used_bytes)} / ${formatBytes(device.memory_total_bytes)} · ${formatPercent(device.memory_utilization_percent)}`),
      textCell(device.temperature_c == null ? "--" : `${device.temperature_c.toFixed(1)} °C`),
    );
    elements.deviceGpusBody.append(row);
  }
}

function renderDeviceStreams(streams) {
  elements.deviceStreamsBody.replaceChildren();
  for (const descriptor of DEVICE_STREAM_KEYS) {
    const stream = streams.find((item) => item.scene_key === descriptor.key);
    const row = document.createElement("tr");
    row.dataset.sceneKey = descriptor.key;
    if (!stream || stream.available === false) {
      row.append(
        identityCell(descriptor.label, descriptor.key),
        statusCell("offline", "不可用"),
        textCell("--"),
        textCell("--"),
        textCell("--"),
      );
      elements.deviceStreamsBody.append(row);
      continue;
    }
    const source = stream.active_source || {};
    const resolution = stream.resolution;
    const detection = stream.detection || {};
    const picture = resolution
      ? `${resolution.width} × ${resolution.height} · ${Number(stream.fps || 0).toFixed(1)} FPS`
      : "--";
    const model = detection.enabled
      ? `${detection.preset === "trained" ? "训练后模型" : "现有模型"} · ${detection.status || "--"}`
      : "未启用";
    row.append(
      identityCell(descriptor.label, descriptor.key),
      statusCell(stream.connected ? "online" : "offline", stream.connected ? "已连接" : "未连接"),
      identityCell(source.display_name || source.name || "--", source.id || ""),
      textCell(picture),
      textCell(model),
    );
    elements.deviceStreamsBody.append(row);
  }
}

function startDevicePolling() {
  if (
    state.devicePollTimer !== null
    || state.activeTab !== "devices"
    || document.hidden
    || !elements.view.classList.contains("active")
  ) return;
  state.devicePollTimer = window.setInterval(() => {
    loadDevices().catch(() => {
      elements.deviceHealth.className = "status-chip warning";
      elements.deviceHealth.textContent = "刷新失败";
    });
  }, DEVICE_POLL_INTERVAL_MS);
}

function stopDevicePolling() {
  if (state.devicePollTimer === null) return;
  window.clearInterval(state.devicePollTimer);
  state.devicePollTimer = null;
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "--";
  if (bytes < 1024) return `${bytes.toFixed(0)} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let amount = bytes;
  let index = -1;
  do {
    amount /= 1024;
    index += 1;
  } while (amount >= 1024 && index < units.length - 1);
  return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${units[index]}`;
}

function formatPercent(value) {
  const percent = Number(value);
  return Number.isFinite(percent) ? `${percent.toFixed(1)}%` : "--";
}

async function loadConfiguration(force = false) {
  if (!state.summary || force) await loadSummary();
  else {
    renderStorageDetails(state.summary);
    setPanelState("configuration", "ready");
  }
}

function renderStorageDetails(summary) {
  if (!elements.storageDetails) return;
  const repository = summary.repository || summary.storage || {};
  const cameras = collection(summary, "cameras", "fixed_cameras", "camera_catalog");
  const rows = [
    ["Schema 版本", summary.schema_version || repository.schema_version || "--"],
    ["数据库状态", repository.status || summary.database_status || "正常"],
    ["资源文件", repository.asset_count ?? summary.asset_count ?? summary.counts?.assets ?? summary.assets?.count ?? "--"],
    ["自动备份", repository.backup_count ?? summary.backup_count ?? "--"],
    ["固定摄像头", cameras.length || summary.camera_count || 12],
    ["最近操作", summary.last_operation?.status || summary.recent_operations?.[0]?.status || "--"],
  ];
  elements.storageDetails.replaceChildren();
  for (const [term, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = term;
    const dd = document.createElement("dd");
    dd.textContent = redactSecrets(value);
    elements.storageDetails.append(dt, dd);
  }
}

async function loadAudit() {
  const params = new URLSearchParams({
    page: String(state.auditPage),
    page_size: String(state.auditPageSize),
  });
  if (elements.auditResultFilter.value) params.set("result", elements.auditResultFilter.value);
  const payload = await request(`/audit?${params}`);
  const records = collection(payload, "audit", "entries", "records");
  const total = Number(payload?.total ?? records.length);
  state.auditPages = Math.max(1, Number(payload?.pages) || Math.ceil(total / state.auditPageSize));
  renderAudit(records);
  setPanelState("audit", records.length ? "ready" : "empty", "当前筛选条件下没有审计记录");
}

function renderAudit(records) {
  elements.auditBody.replaceChildren();
  for (const entry of records) {
    const row = document.createElement("tr");
    row.append(
      textCell(formatDate(entry.occurred_at || entry.created_at || entry.finished_at)),
      textCell(entry.operation_type || entry.action || "配置操作"),
      textCell(entry.target_name || entry.target || "--"),
      statusCell(entry.result || entry.status),
      textCell(
        entry.summary && typeof entry.summary === "object"
          ? Object.entries(entry.summary).map(([key, value]) => `${key}: ${value}`).join("；")
          : entry.summary || entry.message || "--",
        "system-summary-cell",
      ),
      textCell(entry.operation_id || "--", "system-code-cell"),
    );
    elements.auditBody.append(row);
  }
  elements.auditPage.textContent = `${state.auditPage} / ${state.auditPages}`;
  elements.auditPrevious.disabled = state.auditPage <= 1;
  elements.auditNext.disabled = state.auditPage >= state.auditPages;
}

function editorField(labelText, input) {
  const label = document.createElement("label");
  label.className = "field system-editor-field";
  const caption = document.createElement("span");
  caption.textContent = labelText;
  label.append(caption, input);
  return label;
}

function textInput(name, value = "", options = {}) {
  const input = document.createElement(options.multiline ? "textarea" : "input");
  input.name = name;
  input.value = value ?? "";
  if (!options.multiline) input.type = options.type || "text";
  if (options.required) input.required = true;
  if (options.maxLength) input.maxLength = options.maxLength;
  if (options.placeholder) input.placeholder = options.placeholder;
  if (options.autocomplete) input.autocomplete = options.autocomplete;
  return input;
}

function openEditor({ kicker, title, submitLabel = "保存", readonly = false, content, onSubmit = null }) {
  state.editorSubmit = onSubmit;
  elements.editorKicker.textContent = kicker;
  elements.editorTitle.textContent = title;
  elements.editorBody.replaceChildren(...(Array.isArray(content) ? content : [content]));
  elements.editorSubmit.hidden = readonly;
  elements.editorSubmit.disabled = readonly;
  const submitText = $("span", elements.editorSubmit);
  if (submitText) submitText.textContent = submitLabel;
  elements.editorDialog.showModal();
  refreshIcons();
  const firstInput = $("input:not([type=hidden]), select, textarea", elements.editorBody);
  window.setTimeout(() => firstInput?.focus(), 0);
}

function closeEditor() {
  state.editorSubmit = null;
  elements.editorDialog.close();
}

function openStreamEditor(stream = null) {
  const streamId = identifier(stream || {}, "stream_id");
  const name = textInput("name", stream?.name || "", { required: true, maxLength: 100, autocomplete: "off" });
  const url = textInput("rtsp_url", stream?.rtsp_url || "", {
    required: true,
    type: "password",
    autocomplete: "off",
    placeholder: "rtsp://user:password@host/path",
  });
  const secretWrap = document.createElement("div");
  secretWrap.className = "system-sensitive-input";
  const reveal = actionButton({ action: "toggle-editor-secret", icon: "eye", title: "显示或隐藏 RTSP 地址" });
  secretWrap.append(url, reveal);
  const enabled = document.createElement("input");
  enabled.type = "checkbox";
  enabled.name = "enabled";
  enabled.checked = stream?.enabled !== false;
  const enabledLabel = document.createElement("label");
  enabledLabel.className = "switch-field compact system-editor-switch";
  const switchVisual = document.createElement("span");
  switchVisual.className = "switch";
  switchVisual.setAttribute("aria-hidden", "true");
  const enabledText = document.createElement("span");
  enabledText.textContent = "允许加入激活方案";
  enabledLabel.append(enabled, switchVisual, enabledText);

  openEditor({
    kicker: stream ? "RTSP 流" : "新增资源",
    title: stream ? `编辑 ${redactSecrets(stream.name)}` : "新增 RTSP 流",
    content: [
      editorField("显示名称", name),
      editorField("RTSP 地址", secretWrap),
      enabledLabel,
    ],
    onSubmit: async () => {
      const payload = {
        name: name.value.trim(),
        rtsp_url: url.value.trim(),
        enabled: enabled.checked,
      };
      if (!payload.name || !payload.rtsp_url) return;
      await runMutation({
        label: stream ? "更新 RTSP 流" : "新增 RTSP 流",
        execute: () => request(stream ? `/streams/${encodeURIComponent(streamId)}` : "/streams", jsonRequest(stream ? "PUT" : "POST", payload)),
        refresh: ["streams", "stream-profiles", "runtime", "configuration"],
      });
      closeEditor();
    },
  });
}

function createStreamBatchEditorRow(stream = null, { removable = false } = {}) {
  const row = document.createElement("section");
  row.className = "system-stream-batch-row";
  row.dataset.streamId = identifier(stream || {}, "stream_id");

  const heading = document.createElement("div");
  heading.className = "system-stream-batch-row-heading";
  const title = document.createElement("strong");
  title.className = "system-stream-batch-index";
  const streamId = document.createElement("code");
  streamId.textContent = row.dataset.streamId;
  streamId.hidden = !row.dataset.streamId;
  heading.append(title, streamId);

  const name = textInput("batch_name", stream?.name || "", {
    maxLength: 120,
    autocomplete: "off",
    placeholder: "流名称",
  });
  const url = textInput("batch_rtsp_url", stream?.rtsp_url || "", {
    type: "password",
    autocomplete: "off",
    placeholder: "rtsp://user:password@host/path",
  });
  const secretWrap = document.createElement("div");
  secretWrap.className = "system-sensitive-input";
  const reveal = actionButton({
    action: "toggle-batch-secret",
    icon: "eye",
    title: "显示 RTSP 地址",
  });
  reveal.addEventListener("click", () => {
    const revealing = url.type === "password";
    url.type = revealing ? "text" : "password";
    reveal.title = revealing ? "隐藏 RTSP 地址" : "显示 RTSP 地址";
    const icon = document.createElement("i");
    icon.dataset.lucide = revealing ? "eye-off" : "eye";
    reveal.replaceChildren(icon);
    refreshIcons();
  });
  secretWrap.append(url, reveal);

  const enabled = document.createElement("input");
  enabled.type = "checkbox";
  enabled.name = "batch_enabled";
  enabled.checked = stream?.enabled !== false;
  const enabledLabel = document.createElement("label");
  enabledLabel.className = "switch-field compact system-stream-batch-enabled";
  const switchVisual = document.createElement("span");
  switchVisual.className = "switch";
  switchVisual.setAttribute("aria-hidden", "true");
  const enabledText = document.createElement("span");
  enabledText.textContent = "启用";
  enabledLabel.append(enabled, switchVisual, enabledText);

  if (removable) {
    const remove = actionButton({
      action: "remove-batch-stream-row",
      icon: "trash-2",
      title: "删除此行",
      danger: true,
    });
    remove.classList.add("system-stream-batch-remove");
    remove.addEventListener("click", () => {
      const list = row.parentElement;
      row.remove();
      if (list) renumberStreamBatchRows(list);
    });
    heading.append(remove);
  }

  for (const input of [name, url]) {
    input.addEventListener("input", () => input.setCustomValidity(""));
  }
  row.append(
    heading,
    editorField("显示名称", name),
    editorField("RTSP 地址", secretWrap),
    enabledLabel,
  );
  return row;
}

function renumberStreamBatchRows(list) {
  $$(".system-stream-batch-row", list).forEach((row, index) => {
    const title = $(".system-stream-batch-index", row);
    if (title) title.textContent = `流 ${index + 1}`;
  });
  refreshIcons();
}

function validateStreamBatchRows(list, { ignoreBlank, existingNames = new Set() }) {
  const payload = [];
  const names = new Map();
  for (const row of $$(".system-stream-batch-row", list)) {
    const nameInput = $('input[name="batch_name"]', row);
    const urlInput = $('input[name="batch_rtsp_url"]', row);
    const enabledInput = $('input[name="batch_enabled"]', row);
    const name = nameInput.value.trim();
    const rtspUrl = urlInput.value.trim();
    nameInput.setCustomValidity("");
    urlInput.setCustomValidity("");
    if (ignoreBlank && !name && !rtspUrl) continue;
    if (!name || !rtspUrl) {
      const invalid = !name ? nameInput : urlInput;
      invalid.setCustomValidity(!name ? "请填写流名称" : "请填写 RTSP 地址");
      invalid.reportValidity();
      invalid.focus();
      return null;
    }
    try {
      const parsed = new URL(rtspUrl);
      if (parsed.protocol !== "rtsp:" || !parsed.hostname) throw new Error("invalid RTSP URL");
    } catch {
      urlInput.setCustomValidity("请输入包含主机名的 rtsp:// 地址");
      urlInput.reportValidity();
      urlInput.focus();
      return null;
    }
    if (names.has(name)) {
      nameInput.setCustomValidity("批次内流名称不能重复");
      nameInput.reportValidity();
      nameInput.focus();
      return null;
    }
    if (existingNames.has(name)) {
      nameInput.setCustomValidity("流名称已存在，请使用其他名称");
      nameInput.reportValidity();
      nameInput.focus();
      return null;
    }
    names.set(name, row);
    payload.push({
      ...(row.dataset.streamId ? { stream_id: row.dataset.streamId } : {}),
      name,
      rtsp_url: rtspUrl,
      enabled: enabledInput.checked,
    });
  }
  return payload;
}

function streamBatchEditorShell({ streams, removable, addable }) {
  const shell = document.createElement("div");
  shell.className = "system-stream-batch-editor";
  const toolbar = document.createElement("div");
  toolbar.className = "system-stream-batch-editor-toolbar";
  const count = document.createElement("span");
  const list = document.createElement("div");
  list.className = "system-stream-batch-list";
  const refreshCount = () => {
    const total = $$(".system-stream-batch-row", list).length;
    count.textContent = `${total} 行`;
  };
  for (const stream of streams) list.append(createStreamBatchEditorRow(stream, { removable }));
  if (addable) {
    const add = document.createElement("button");
    add.className = "command-button";
    add.type = "button";
    add.innerHTML = '<i data-lucide="plus"></i><span>添加一行</span>';
    add.addEventListener("click", () => {
      list.append(createStreamBatchEditorRow(null, { removable: true }));
      renumberStreamBatchRows(list);
      refreshCount();
      $('input[name="batch_name"]', list.lastElementChild)?.focus();
    });
    toolbar.append(count, add);
    list.addEventListener("click", () => window.setTimeout(refreshCount, 0));
  } else {
    toolbar.append(count);
  }
  shell.append(toolbar, list);
  renumberStreamBatchRows(list);
  refreshCount();
  return { shell, list };
}

function openStreamBatchCreator() {
  const { shell, list } = streamBatchEditorShell({
    streams: [null, null, null],
    removable: true,
    addable: true,
  });
  openEditor({
    kicker: "批量新增",
    title: "新增 RTSP 流",
    submitLabel: "新增",
    content: shell,
    onSubmit: async () => {
      const existingNames = new Set(
        state.streams.map((stream) => String(stream.name || "").trim()),
      );
      const streams = validateStreamBatchRows(list, {
        ignoreBlank: true,
        existingNames,
      });
      if (streams === null) return;
      if (!streams.length) {
        notify("至少填写一行完整的流信息", "error");
        $('input[name="batch_name"]', list)?.focus();
        return;
      }
      try {
        await runMutation({
          label: `批量新增 ${streams.length} 路 RTSP 流`,
          execute: () => request("/streams/batch", jsonRequest("POST", { streams })),
          refresh: ["streams", "stream-profiles", "runtime", "configuration"],
        });
      } catch (error) {
        const conflictNames = new Set(
          error instanceof ConfigApiError && error.code === "STREAM_BATCH_CONFLICT"
            ? error.details.map((detail) => detail?.name).filter(Boolean)
            : [],
        );
        const conflictInput = $$("input[name=\"batch_name\"]", list)
          .find((input) => conflictNames.has(input.value.trim()));
        if (!conflictInput) throw error;
        conflictInput.setCustomValidity("流名称已存在，请使用其他名称");
        conflictInput.reportValidity();
        conflictInput.focus();
        notify(`流名称已存在：${[...conflictNames].join("、")}`, "error");
        return;
      }
      closeEditor();
    },
  });
}

async function openStreamBatchEditor() {
  const selectedIds = selectedStreamItems().map((stream) => identifier(stream, "stream_id"));
  if (!selectedIds.length) return;
  const revealed = collection(
    await request("/streams?reveal_credentials=true"),
    "streams",
  );
  const byId = new Map(revealed.map((stream) => [identifier(stream, "stream_id"), stream]));
  const streams = selectedIds.map((streamId) => byId.get(streamId)).filter(Boolean);
  const { shell, list } = streamBatchEditorShell({
    streams,
    removable: false,
    addable: false,
  });
  openEditor({
    kicker: "批量修改",
    title: `修改 ${streams.length} 路 RTSP 流`,
    content: shell,
    onSubmit: async () => {
      const payload = validateStreamBatchRows(list, { ignoreBlank: false });
      if (payload === null) return;
      await runMutation({
        label: `批量修改 ${payload.length} 路 RTSP 流`,
        execute: () => request("/streams/batch", jsonRequest("PUT", { streams: payload })),
        refresh: ["streams", "stream-profiles", "runtime", "scenes", "configuration"],
      });
      closeEditor();
    },
  });
}

async function profileEditorData(profileId = "") {
  const tasks = [];
  tasks.push(state.summary ? Promise.resolve(state.summary) : request("/summary"));
  tasks.push(state.streams.length ? Promise.resolve({ streams: state.streams }) : request("/streams"));
  if (profileId) tasks.push(request(`/stream-profiles/${encodeURIComponent(profileId)}`));
  const [summary, streamsPayload, detail = null] = await Promise.all(tasks);
  state.summary = summary;
  state.streams = collection(streamsPayload, "streams");
  return { summary, detail };
}

async function openStreamProfileEditor(profile = null) {
  const profileId = identifier(profile || {}, "profile_id");
  notify("正在读取固定摄像头与流目录");
  const { summary, detail } = await profileEditorData(profileId);
  const source = detail || profile || {};
  const builtin = Boolean(source.is_builtin || profile?.is_builtin);
  const cameras = collection(summary, "cameras", "fixed_cameras", "camera_catalog");
  const bindings = collection(source, "bindings", "stream_bindings");
  const bindingMap = new Map(bindings.map((item) => [String(item.camera_id), String(item.stream_id)]));
  const name = textInput("name", source.name || "", { required: true, maxLength: 100 });
  const description = textInput("description", source.description || "", { multiline: true, maxLength: 500 });
  const content = [editorField("方案名称", name), editorField("说明", description)];

  const mapping = document.createElement("section");
  mapping.className = "system-binding-editor";
  const heading = document.createElement("div");
  heading.className = "system-editor-section-heading";
  const headingText = document.createElement("h3");
  headingText.textContent = `摄像头映射 (${cameras.length || 0})`;
  const hint = document.createElement("span");
  hint.textContent = "同一路流不可重复绑定";
  heading.append(headingText, hint);
  mapping.append(heading);

  if (!cameras.length) {
    const warning = document.createElement("p");
    warning.className = "system-inline-warning";
    warning.textContent = "服务未返回固定摄像头目录，暂时只能保存方案基本信息。";
    mapping.append(warning);
  }

  for (const camera of cameras) {
    const cameraId = identifier(camera, "camera_id");
    const row = document.createElement("label");
    row.className = "system-binding-row";
    const cameraName = document.createElement("span");
    cameraName.textContent = camera.display_name || camera.name || cameraId;
    const select = document.createElement("select");
    select.dataset.cameraId = cameraId;
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "未绑定";
    select.append(placeholder);
    for (const stream of state.streams) {
      const streamId = identifier(stream, "stream_id");
      const option = document.createElement("option");
      option.value = streamId;
      option.textContent = stream.name || streamId;
      option.disabled = stream.enabled === false;
      select.append(option);
    }
    select.value = bindingMap.get(cameraId) || "";
    row.append(cameraName, select);
    mapping.append(row);
  }
  content.push(mapping);

  openEditor({
    kicker: builtin ? "内置只读方案" : profile ? "流关联方案" : "新增方案",
    title: profile ? redactSecrets(source.name || profile.name) : "新建流关联方案",
    readonly: builtin,
    content,
    onSubmit: builtin ? null : async () => {
      const selected = $$('select[data-camera-id]', mapping)
        .filter((select) => select.value)
        .map((select) => ({ camera_id: select.dataset.cameraId, stream_id: select.value }));
      const streamIds = selected.map((item) => item.stream_id);
      if (new Set(streamIds).size !== streamIds.length) {
        notify("同一路流不能绑定给多个摄像头", "error");
        return;
      }
      const payload = {
        name: name.value.trim(),
        description: description.value.trim(),
        bindings: selected,
      };
      await runMutation({
        label: profile ? "更新流关联方案" : "新建流关联方案",
        execute: () => request(profile ? `/stream-profiles/${encodeURIComponent(profileId)}` : "/stream-profiles", jsonRequest(profile ? "PUT" : "POST", payload)),
        refresh: ["stream-profiles", "runtime"],
      });
      closeEditor();
    },
  });
}

function openTopologyCreator() {
  const name = textInput("name", "", { required: true, maxLength: 100 });
  const description = textInput("description", "", { multiline: true, maxLength: 500 });
  const note = document.createElement("p");
  note.className = "system-inline-note";
  note.textContent = "新拓扑创建后可在道路态势编辑器中配置底图、道路和摄像头。";
  openEditor({
    kicker: "新增方案",
    title: "新建道路拓扑",
    content: [editorField("方案名称", name), editorField("说明", description), note],
    onSubmit: async () => {
      await runMutation({
        label: "新建道路拓扑",
        execute: () => request("/topologies", jsonRequest("POST", {
          name: name.value.trim(),
          description: description.value.trim(),
        })),
        refresh: ["topologies", "runtime"],
      });
      closeEditor();
    },
  });
}

function showReport(title, payload) {
  const source = payload?.result || payload?.report || payload || {};
  const list = document.createElement("dl");
  list.className = "system-definition-list system-report-list";
  const entries = Object.entries(source).filter(([, value]) => (
    ["string", "number", "boolean"].includes(typeof value) || value === null
  ));
  for (const [key, value] of entries.slice(0, 24)) {
    const term = document.createElement("dt");
    term.textContent = key.replaceAll("_", " ");
    const detail = document.createElement("dd");
    detail.textContent = redactSecrets(value ?? "--");
    list.append(term, detail);
  }
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "system-inline-note";
    empty.textContent = "操作已完成，服务未返回额外明细。";
    list.append(empty);
  }
  openEditor({ kicker: "操作报告", title, readonly: true, content: list });
}

function confirmAction({ title, message, details = [], confirmLabel = "确认", acknowledgement = "" }) {
  elements.confirmDialog.returnValue = "";
  elements.confirmTitle.textContent = title;
  elements.confirmMessage.textContent = redactSecrets(message);
  elements.confirmDetails.replaceChildren();
  for (const detail of details.filter(Boolean)) {
    const item = document.createElement("li");
    item.textContent = redactSecrets(detail);
    elements.confirmDetails.append(item);
  }
  elements.confirmDetails.hidden = !details.filter(Boolean).length;
  elements.confirmCheckWrap.hidden = !acknowledgement;
  elements.confirmCheck.checked = false;
  elements.confirmCheckLabel.textContent = acknowledgement || "我已了解此操作的影响";
  elements.confirmSubmit.textContent = confirmLabel;
  elements.confirmSubmit.disabled = Boolean(acknowledgement);
  elements.confirmDialog.showModal();
  refreshIcons();
  return new Promise((resolve) => {
    elements.confirmDialog.addEventListener("close", () => {
      resolve(elements.confirmDialog.returnValue === "confirm");
    }, { once: true });
  });
}

function operationId(payload) {
  return payload?.operation_id || payload?.operation?.operation_id || "";
}

async function runMutation({ label, execute, refresh = [], onComplete = null }) {
  const result = await execute();
  const id = result instanceof Response ? "" : operationId(result);
  if (id) {
    watchOperation(id, { label, refresh, onComplete });
    return result;
  }
  if (onComplete) await onComplete(result);
  if (refresh.length) invalidate(refresh);
  notify(`${label}已完成`, "success");
  return result;
}

function watchOperation(id, context) {
  const key = String(id);
  state.operations.set(key, {
    ...context,
    startedAt: Date.now(),
    timer: null,
  });
  renderOperation({ operation_id: key, status: "pending", message: "等待配置服务开始处理" }, context.label);
  pollOperation(key);
}

async function pollOperation(id) {
  const context = state.operations.get(id);
  if (!context) return;
  try {
    const operation = await request(`/operations/${encodeURIComponent(id)}`);
    const status = String(operation?.status || "pending").toLowerCase();
    renderOperation(operation, context.label);
    if (FINAL_OPERATION_STATES.has(status)) {
      state.operations.delete(id);
      if (status === "succeeded") {
        if (context.onComplete) await context.onComplete(operation?.result || operation);
        if (context.refresh?.length) invalidate(context.refresh);
        notify(`${context.label}已完成`, "success");
      } else {
        notify(operation?.error_summary || operation?.message || `${context.label}${displayStatus(status)}`, "error");
      }
      window.setTimeout(() => {
        if (!state.operations.size) elements.operationBanner.hidden = true;
      }, 3200);
      return;
    }
    if (Date.now() - context.startedAt > 15 * 60 * 1000) {
      state.operations.delete(id);
      notify(`${context.label}状态查询超时，请在操作审计中确认结果`, "error");
      return;
    }
    context.timer = window.setTimeout(() => pollOperation(id), 1000);
  } catch (error) {
    state.operations.delete(id);
    renderOperation({ operation_id: id, status: "failed", message: errorDetail(error) }, context.label);
    notify(errorDetail(error), "error");
  }
}

function renderOperation(operation, fallbackTitle) {
  const status = String(operation?.status || "pending").toLowerCase();
  elements.operationBanner.hidden = false;
  elements.operationBanner.className = `system-operation-banner ${status}`;
  elements.operationTitle.textContent = redactSecrets(operation?.operation_type || fallbackTitle || "配置操作");
  elements.operationDetail.textContent = redactSecrets(
    operation?.stage_message || operation?.message || operation?.error_summary || operation?.operation_id || "正在处理",
  );
  elements.operationStatus.className = `status-chip ${statusTone(status)}`.trim();
  elements.operationStatus.textContent = displayStatus(status);
  refreshIcons();
}

function dependencyDetails(item) {
  const dependencies = collection(item, "dependencies", "references", "affected_entities");
  return dependencies.map((entry) => (
    typeof entry === "string" ? entry : entry.name || entry.id || entry.type || JSON.stringify(entry)
  ));
}

function showStreamProbeReport(report) {
  const summary = document.createElement("dl");
  summary.className = "system-definition-list system-report-list";
  for (const [label, value] of [
    ["总数", report.total],
    ["成功", report.succeeded],
    ["失败", report.failed],
    ["耗时", `${report.elapsed_ms || 0} ms`],
  ]) {
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = value;
    summary.append(term, detail);
  }

  const names = new Map(
    state.streams.map((stream) => [identifier(stream, "stream_id"), stream.name]),
  );
  const tableWrap = document.createElement("div");
  tableWrap.className = "table-scroll system-probe-report";
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["流", "状态", "结果", "耗时"]) {
    const cell = document.createElement("th");
    cell.textContent = label;
    headRow.append(cell);
  }
  head.append(headRow);
  const body = document.createElement("tbody");
  for (const result of collection(report, "results")) {
    const row = document.createElement("tr");
    row.append(
      identityCell(names.get(result.stream_id) || result.stream_id, result.stream_id),
      statusCell(result.ok ? "ok" : "failed", result.ok ? "成功" : "失败"),
      textCell(result.message || result.code || "--"),
      textCell(`${result.elapsed_ms || 0} ms`),
    );
    body.append(row);
  }
  table.append(head, body);
  tableWrap.append(table);
  openEditor({
    kicker: "批量探测报告",
    title: `${report.succeeded || 0} 成功 · ${report.failed || 0} 失败`,
    readonly: true,
    content: [summary, tableWrap],
  });
}

async function probeStreamBatch() {
  const selectedIds = selectedStreamItems().map((stream) => identifier(stream, "stream_id"));
  const targetIds = selectedIds.length
    ? selectedIds
    : state.streams.map((stream) => identifier(stream, "stream_id"));
  if (!targetIds.length) return;
  state.probingStreams = new Set(targetIds);
  renderStreams();
  try {
    const report = await request(
      "/streams/probe",
      jsonRequest("POST", { stream_ids: targetIds }),
    );
    await loadStreams();
    showStreamProbeReport(report);
    notify(
      `RTSP 探测完成：${report.succeeded || 0} 成功，${report.failed || 0} 失败`,
      report.failed ? "error" : "success",
    );
  } finally {
    state.probingStreams.clear();
    renderStreams();
  }
}

function showStreamDeleteConflict(error) {
  const tableWrap = document.createElement("div");
  tableWrap.className = "table-scroll system-probe-report";
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["流", "关联方案"]) {
    const cell = document.createElement("th");
    cell.textContent = label;
    headRow.append(cell);
  }
  head.append(headRow);
  const body = document.createElement("tbody");
  for (const detail of error.details) {
    const row = document.createElement("tr");
    const profiles = collection(detail, "profiles")
      .map((profile) => profile.name || profile.profile_id)
      .join("、");
    row.append(
      identityCell(detail.name || detail.stream_id, detail.stream_id),
      textCell(profiles || "--"),
    );
    body.append(row);
  }
  table.append(head, body);
  tableWrap.append(table);
  openEditor({
    kicker: "删除冲突",
    title: "所选流仍被关联方案引用",
    readonly: true,
    content: tableWrap,
  });
}

async function deleteStreamBatch() {
  const selected = selectedStreamItems();
  if (!selected.length) return;
  const ok = await confirmAction({
    title: "批量删除 RTSP 流",
    message: `确定删除所选 ${selected.length} 路 RTSP 流？`,
    details: selected.map((stream) => stream.name || identifier(stream, "stream_id")),
    confirmLabel: `删除 ${selected.length} 路流`,
  });
  if (!ok) return;
  const streamIds = selected.map((stream) => identifier(stream, "stream_id"));
  try {
    await runMutation({
      label: `批量删除 ${streamIds.length} 路 RTSP 流`,
      execute: () => request(
        "/streams/batch",
        jsonRequest("DELETE", { stream_ids: streamIds }),
      ),
      refresh: ["streams", "stream-profiles", "runtime", "configuration"],
    });
  } catch (error) {
    if (error instanceof ConfigApiError && error.code === "STREAM_BATCH_IN_USE") {
      showStreamDeleteConflict(error);
      notify(error.message, "error");
      return;
    }
    throw error;
  }
  for (const streamId of streamIds) state.selectedStreams.delete(streamId);
  renderStreams();
}

async function probeStream(streamId) {
  await runMutation({
    label: "RTSP 流探测",
    execute: () => request(`/streams/${encodeURIComponent(streamId)}/probe`, { method: "POST" }),
    refresh: ["streams", "runtime"],
    onComplete: (result) => showReport("RTSP 流探测结果", result),
  });
}

async function deleteStream(stream) {
  const ok = await confirmAction({
    title: "删除 RTSP 流",
    message: `确定删除“${stream.name || identifier(stream, "stream_id")}”？`,
    details: dependencyDetails(stream),
    confirmLabel: "删除",
  });
  if (!ok) return;
  await runMutation({
    label: "删除 RTSP 流",
    execute: () => request(`/streams/${encodeURIComponent(identifier(stream, "stream_id"))}`, { method: "DELETE" }),
    refresh: ["streams", "stream-profiles", "runtime"],
  });
}

async function cloneStreamProfile(profileId) {
  await runMutation({
    label: "复制流关联方案",
    execute: () => request(`/stream-profiles/${encodeURIComponent(profileId)}/clone`, { method: "POST" }),
    refresh: ["stream-profiles"],
  });
}

async function preflightStreamProfile(profileId) {
  await runMutation({
    label: "流关联方案预检",
    execute: () => request(`/stream-profiles/${encodeURIComponent(profileId)}/preflight`, { method: "POST" }),
    refresh: ["stream-profiles"],
    onComplete: (result) => showReport("流关联方案预检", result),
  });
}

async function activateStreamProfile(profile) {
  const profileId = identifier(profile, "profile_id");
  if (state.pendingStreamProfileActions.has(profileId)) {
    notify("该流关联方案正在预检或应用，请勿重复提交", "error");
    return;
  }
  const confirmAndActivate = async (report) => {
    if (!report?.ok || !report?.preflight_token) {
      const failed = collection(report, "streams").filter((item) => !item.ok);
      const message = failed.length
        ? `预检未通过：${failed.length} 路流连接失败`
        : "预检未通过，请检查方案完整性和流启用状态";
      renderOperation({ status: "failed", message }, "流关联方案预检");
      showReport("流关联方案预检", report);
      notify(message, "error");
      return;
    }
    renderOperation(
      { status: "succeeded", message: "全部关联流已通过预检，等待确认" },
      "流关联方案预检",
    );
    const reportDetails = importPreviewDetails(report);
    const ok = await confirmAction({
      title: "切换流关联方案",
      message: `“${profile.name}”已完成预检，是否应用新的摄像头与流映射？`,
      details: reportDetails.length
        ? reportDetails
        : ["全部关联流已通过结构与连通性检查。", "任一运行通道失败时服务将恢复旧映射。"],
      confirmLabel: "应用方案",
    });
    if (!ok) return;
    renderOperation(
      { status: "applying", message: "正在应用新的摄像头与流映射" },
      "切换流关联方案",
    );
    await runMutation({
      label: "切换流关联方案",
      execute: () => request(
        `/stream-profiles/${encodeURIComponent(profileId)}/activate`,
        jsonRequest("POST", { preflight_token: report.preflight_token }),
      ),
      refresh: ["stream-profiles", "runtime", "scenes"],
    });
  };

  state.pendingStreamProfileActions.add(profileId);
  renderStreamProfiles();
  renderOperation(
    { status: "preflighting", message: "正在并行探测方案中的 RTSP 流" },
    "流关联方案预检",
  );
  try {
    const preflight = await request(`/stream-profiles/${encodeURIComponent(profileId)}/preflight`, { method: "POST" });
    const preflightOperationId = operationId(preflight);
    if (preflightOperationId) {
      watchOperation(preflightOperationId, {
        label: "流关联方案预检",
        refresh: ["stream-profiles"],
        onComplete: confirmAndActivate,
      });
      return;
    }
    await confirmAndActivate(preflight);
  } catch (error) {
    renderOperation(
      { status: "failed", message: errorDetail(error) },
      "切换流关联方案",
    );
    throw error;
  } finally {
    state.pendingStreamProfileActions.delete(profileId);
    renderStreamProfiles();
  }
}

async function deleteStreamProfile(profile) {
  const ok = await confirmAction({
    title: "删除流关联方案",
    message: `确定删除“${profile.name}”？`,
    details: dependencyDetails(profile),
    confirmLabel: "删除",
  });
  if (!ok) return;
  await runMutation({
    label: "删除流关联方案",
    execute: () => request(`/stream-profiles/${encodeURIComponent(identifier(profile, "profile_id"))}`, { method: "DELETE" }),
    refresh: ["stream-profiles", "runtime"],
  });
}

async function cloneTopology(topologyId) {
  await runMutation({
    label: "复制道路拓扑",
    execute: () => request(`/topologies/${encodeURIComponent(topologyId)}/clone`, { method: "POST" }),
    refresh: ["topologies"],
  });
}

async function activateTopology(topology) {
  const affected = dependencyDetails(topology);
  const ok = await confirmAction({
    title: "切换道路拓扑",
    message: `确定激活“${topology.name}”修订 r${topology.revision || 1}？`,
    details: affected.length ? affected : ["不兼容的运行场景会被自动停用。", "道路轨迹与热度等瞬时状态会被清空。"],
    confirmLabel: "切换拓扑",
  });
  if (!ok) return;
  await runMutation({
    label: "切换道路拓扑",
    execute: () => request(`/topologies/${encodeURIComponent(identifier(topology, "topology_id"))}/activate`, { method: "POST" }),
    refresh: ["topologies", "runtime", "scenes"],
  });
}

async function deleteTopology(topology) {
  const ok = await confirmAction({
    title: "删除道路拓扑",
    message: `确定删除“${topology.name}”？`,
    details: dependencyDetails(topology),
    confirmLabel: "删除",
  });
  if (!ok) return;
  await runMutation({
    label: "删除道路拓扑",
    execute: () => request(`/topologies/${encodeURIComponent(identifier(topology, "topology_id"))}`, { method: "DELETE" }),
    refresh: ["topologies", "runtime", "scenes"],
  });
}

function openTopology(topology) {
  const activeId = identifier(activeReference("topology") || {}, "topology_id");
  const topologyId = identifier(topology, "topology_id");
  if (topologyId === activeId || topology.is_active || topology.active) {
    $('[data-view="map"]')?.click();
    notify("已进入当前激活拓扑的道路编辑器");
    return;
  }
  const details = document.createElement("dl");
  details.className = "system-definition-list system-report-list";
  const rows = [
    ["方案 ID", topologyId],
    ["修订", `r${topology.revision || 1}`],
    ["道路数量", topology.segment_count ?? topology.road_count ?? 0],
    ["摄像头数量", topology.camera_count ?? 0],
    ["状态", "非当前激活拓扑"],
  ];
  for (const [label, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = redactSecrets(value);
    details.append(dt, dd);
  }
  openEditor({ kicker: "拓扑方案", title: topology.name || topologyId, readonly: true, content: details });
}

async function activateScene(scene) {
  const ok = await confirmAction({
    title: `激活${SCENE_TYPE_LABELS[scene.scene_type] || ""}场景`,
    message: `确定激活“${scene.name}”？同类型当前运行场景将被替换。`,
    details: [`摄像头：${scene.camera_name || scene.camera_id || "--"}`, `拓扑：${scene.topology_name || scene.topology_id || "--"} · r${scene.topology_revision || "--"}`],
    confirmLabel: "激活场景",
  });
  if (!ok) return;
  await runMutation({
    label: "激活场景",
    execute: () => request(`/scenes/${encodeURIComponent(identifier(scene, "scene_id"))}/activate`, { method: "POST" }),
    refresh: ["scenes", "runtime"],
  });
}

async function deactivateScene(sceneType) {
  const ok = await confirmAction({
    title: `停用${SCENE_TYPE_LABELS[sceneType] || ""}场景`,
    message: "对应分析通道将停止，场景档案仍会保留。",
    confirmLabel: "停用",
  });
  if (!ok) return;
  await runMutation({
    label: "停用场景",
    execute: () => request(`/scene-types/${encodeURIComponent(sceneType)}/deactivate`, { method: "POST" }),
    refresh: ["scenes", "runtime"],
  });
}

function openSceneEditor(scene) {
  const view = scene.scene_type === "road_abnormal" ? "road-abnormal" : "no-parking";
  const selectId = scene.scene_type === "road_abnormal" ? "#road-abnormal-scene-select" : "#no-parking-scene-select";
  $(`[data-view="${view}"]`)?.click();
  let attempts = 0;
  const selectScene = () => {
    const select = $(selectId);
    const sceneId = identifier(scene, "scene_id");
    if (select && [...select.options].some((option) => option.value === sceneId)) {
      select.value = sceneId;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    attempts += 1;
    if (attempts < 20) window.setTimeout(selectScene, 150);
    else notify("场景编辑器已打开，但目标档案尚未加载", "error");
  };
  window.setTimeout(selectScene, 50);
}

function filenameFromResponse(response, fallback) {
  const disposition = response.headers.get("content-disposition") || "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  try {
    return encoded ? decodeURIComponent(encoded) : plain || fallback;
  } catch {
    return fallback;
  }
}

async function downloadResponse(response, fallback = "videotest-config.zip") {
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filenameFromResponse(response, fallback);
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function completeExport(result) {
  if (result instanceof Response) {
    await downloadResponse(result);
    return;
  }
  const url = result?.download_url || result?.url;
  if (!url) return;
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`配置包下载失败 (${response.status})`);
  await downloadResponse(response);
}

async function exportConfiguration() {
  const ok = await confirmAction({
    title: "导出全部业务配置",
    message: "配置包包含所有用户业务配置、资源文件和当前激活状态。",
    details: ["RTSP URL 及其中的用户名、密码或令牌会以明文写入 ZIP。", "请只将配置包保存到受控位置。"],
    confirmLabel: "创建配置包",
    acknowledgement: "我了解配置包包含明文 RTSP 凭据",
  });
  if (!ok) return;
  const result = await request("/exports", { method: "POST" });
  if (result instanceof Response) {
    await completeExport(result);
    notify("配置包已开始下载", "success");
  } else {
    const id = operationId(result);
    if (id) watchOperation(id, { label: "导出配置包", refresh: ["runtime", "audit"], onComplete: completeExport });
    else {
      await completeExport(result);
      notify("配置包已创建", "success");
    }
  }
}

function importPreviewDetails(preview) {
  const source = preview?.preview || preview?.result || preview || {};
  const changes = source.changes || source.counts || {};
  const details = [];
  for (const [key, value] of Object.entries(changes)) {
    if (["string", "number"].includes(typeof value)) details.push(`${key.replaceAll("_", " ")}：${value}`);
  }
  for (const [group, values] of Object.entries({
    "包内": source.incoming,
    "当前": source.current,
    "将替换或删除": source.deleted_or_replaced,
  })) {
    if (!values || typeof values !== "object") continue;
    for (const [key, value] of Object.entries(values)) {
      if (["string", "number"].includes(typeof value)) {
        details.push(`${group} ${key.replaceAll("_", " ")}：${value}`);
      }
    }
  }
  const warnings = collection(source, "warnings", "issues");
  details.push(...warnings.slice(0, 8).map((item) => (
    typeof item === "string" ? item : item.message || item.name || JSON.stringify(item)
  )));
  return details;
}

async function applyImportPreview(preview) {
  const source = preview?.preview || preview?.result || preview || {};
  const token = source.confirmation_token || source.token || preview?.confirmation_token || preview?.token;
  const details = importPreviewDetails(source);
  elements.importSummary.textContent = details.length
    ? `预检完成：${details.slice(0, 3).join("；")}`
    : "预检完成，可以确认全量替换。";
  if (!token) {
    showReport("配置包预检报告", source);
    notify("预检结果缺少确认令牌，无法继续导入", "error");
    return;
  }
  const ok = await confirmAction({
    title: "确认全量替换配置",
    message: "导入将替换全部用户配置，并恢复配置包内的激活状态。",
    details: details.length ? details : ["服务将在导入前创建自动备份。", "目标系统内置基线将保留。"],
    confirmLabel: "全量导入",
    acknowledgement: "我已核对预检结果并同意全量替换用户配置",
  });
  if (!ok) return;
  await runMutation({
    label: "导入配置包",
    execute: () => request(`/imports/${encodeURIComponent(token)}/apply`, jsonRequest("POST", { confirm: true })),
    refresh: TAB_ORDER,
  });
}

async function preflightImport(file) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  elements.importSummary.textContent = `正在预检 ${file.name}`;
  const result = await request("/imports/preflight", { method: "POST", body: form });
  const id = result instanceof Response ? "" : operationId(result);
  if (id) watchOperation(id, { label: "配置包预检", refresh: [], onComplete: applyImportPreview });
  else await applyImportPreview(result);
}

async function handleAction(button) {
  const action = button.dataset.systemAction;
  const id = button.dataset.id || "";
  if (action === "switch-tab") return activateTab(button.dataset.tab, { focus: true });
  if (action === "new-stream") return openStreamEditor();
  if (action === "new-stream-batch") return openStreamBatchCreator();
  if (action === "edit-stream-batch") return openStreamBatchEditor();
  if (action === "probe-stream-batch") return probeStreamBatch();
  if (action === "delete-stream-batch") return deleteStreamBatch();
  if (action === "new-stream-profile") return openStreamProfileEditor();
  if (action === "new-topology") return openTopologyCreator();
  if (action === "toggle-editor-secret") {
    const input = $('input[name="rtsp_url"]', elements.editorBody);
    if (input) input.type = input.type === "password" ? "text" : "password";
    return;
  }

  if (action === "toggle-stream-secret") {
    if (state.revealedStreams.has(id)) {
      state.revealedStreams.delete(id);
      const masked = collection(await request("/streams"), "streams");
      state.streams = masked;
    } else {
      const revealed = collection(await request("/streams?reveal_credentials=true"), "streams");
      state.streams = revealed;
      state.revealedStreams.add(id);
    }
    renderStreams();
    return;
  }

  const stream = state.streams.find((item) => identifier(item, "stream_id") === id);
  const profile = state.streamProfiles.find((item) => identifier(item, "profile_id") === id);
  const topology = state.topologies.find((item) => identifier(item, "topology_id") === id);
  const scene = state.scenes.find((item) => identifier(item, "scene_id") === id);

  if (action === "edit-stream" && stream) {
    const revealed = collection(await request("/streams?reveal_credentials=true"), "streams");
    const editable = revealed.find((item) => identifier(item, "stream_id") === id) || stream;
    return openStreamEditor(editable);
  }
  if (action === "probe-stream" && stream) return probeStream(id);
  if (action === "delete-stream" && stream) return deleteStream(stream);
  if (action === "edit-stream-profile" && profile) return openStreamProfileEditor(profile);
  if (action === "clone-stream-profile" && profile) return cloneStreamProfile(id);
  if (action === "preflight-stream-profile" && profile) return preflightStreamProfile(id);
  if (action === "activate-stream-profile" && profile) return activateStreamProfile(profile);
  if (action === "delete-stream-profile" && profile) return deleteStreamProfile(profile);
  if (action === "open-topology" && topology) return openTopology(topology);
  if (action === "clone-topology" && topology) return cloneTopology(id);
  if (action === "activate-topology" && topology) return activateTopology(topology);
  if (action === "delete-topology" && topology) return deleteTopology(topology);
  if (action === "edit-scene" && scene) return openSceneEditor(scene);
  if (action === "activate-scene" && scene) return activateScene(scene);
  if (action === "deactivate-scene" && scene) return deactivateScene(scene.scene_type);
}

function bindEvents() {
  elements.navButton?.addEventListener("click", () => {
    ensureTabLoaded(state.activeTab).catch((error) => showLoadError(state.activeTab, error));
  });
  $$('[data-view]').forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.view === "system" && state.activeTab === "devices") {
        startDevicePolling();
      } else if (button.dataset.view !== "system") {
        stopDevicePolling();
      }
    });
  });
  $$('[data-system-tab]').forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.systemTab));
    button.addEventListener("keydown", (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const current = TAB_ORDER.indexOf(button.dataset.systemTab);
      const next = event.key === "Home" ? 0
        : event.key === "End" ? TAB_ORDER.length - 1
          : (current + (event.key === "ArrowRight" ? 1 : -1) + TAB_ORDER.length) % TAB_ORDER.length;
      activateTab(TAB_ORDER[next], { focus: true });
    });
  });

  elements.refreshButton.addEventListener("click", () => {
    ensureTabLoaded(state.activeTab, true).catch((error) => showLoadError(state.activeTab, error));
  });
  elements.streamSelectAll?.addEventListener("change", () => {
    if (elements.streamSelectAll.checked) {
      state.selectedStreams = new Set(
        state.streams.map((stream) => identifier(stream, "stream_id")),
      );
    } else {
      state.selectedStreams.clear();
    }
    renderStreams();
  });
  elements.streamsBody?.addEventListener("change", (event) => {
    const checkbox = event.target.closest("input[data-stream-selection]");
    if (!checkbox) return;
    if (checkbox.checked) state.selectedStreams.add(checkbox.dataset.streamSelection);
    else state.selectedStreams.delete(checkbox.dataset.streamSelection);
    renderStreams();
  });
  elements.view.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-system-action]");
    if (!button || button.disabled) return;
    const wasDisabled = button.disabled;
    button.disabled = true;
    Promise.resolve(handleAction(button))
      .catch((error) => notify(errorDetail(error), "error"))
      .finally(() => {
        if (button.isConnected) button.disabled = wasDisabled;
      });
  });

  elements.sceneTypeFilter.addEventListener("change", () => {
    state.loaded.delete("scenes");
    ensureTabLoaded("scenes", true).catch((error) => showLoadError("scenes", error));
  });
  elements.auditResultFilter.addEventListener("change", () => {
    state.auditPage = 1;
    ensureTabLoaded("audit", true).catch((error) => showLoadError("audit", error));
  });
  elements.auditPrevious.addEventListener("click", () => {
    state.auditPage = Math.max(1, state.auditPage - 1);
    ensureTabLoaded("audit", true).catch((error) => showLoadError("audit", error));
  });
  elements.auditNext.addEventListener("click", () => {
    state.auditPage = Math.min(state.auditPages, state.auditPage + 1);
    ensureTabLoaded("audit", true).catch((error) => showLoadError("audit", error));
  });

  elements.exportButton.addEventListener("click", () => exportConfiguration().catch((error) => notify(errorDetail(error), "error")));
  elements.importButton.addEventListener("click", () => elements.importInput.click());
  elements.importInput.addEventListener("change", () => {
    const file = elements.importInput.files[0];
    elements.importInput.value = "";
    preflightImport(file).catch((error) => {
      elements.importSummary.textContent = errorDetail(error);
      notify(errorDetail(error), "error");
    });
  });

  elements.modelPipelineForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveModelPipelines().catch((error) => notify(errorDetail(error), "error"));
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopDevicePolling();
      return;
    }
    if (state.activeTab === "devices" && elements.view.classList.contains("active")) {
      startDevicePolling();
      loadDevices().catch((error) => showLoadError("devices", error));
    }
  });

  elements.editorForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!state.editorSubmit) return;
    elements.editorSubmit.disabled = true;
    Promise.resolve(state.editorSubmit())
      .catch((error) => notify(errorDetail(error), "error"))
      .finally(() => {
        if (elements.editorDialog.open && state.editorSubmit) elements.editorSubmit.disabled = false;
      });
  });
  elements.editorCancel.addEventListener("click", closeEditor);
  elements.editorClose.addEventListener("click", closeEditor);
  elements.editorDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeEditor();
  });
  elements.editorBody.addEventListener("click", (event) => {
    const button = event.target.closest('button[data-system-action="toggle-editor-secret"]');
    if (button) handleAction(button);
  });

  elements.confirmCheck.addEventListener("change", () => {
    elements.confirmSubmit.disabled = !elements.confirmCheck.checked;
  });
  elements.confirmDialog.addEventListener("cancel", () => {
    elements.confirmDialog.returnValue = "cancel";
  });
}

function initialize() {
  if (!elements.view) return;
  bindEvents();
  activateTab("runtime", { load: false });
  refreshIcons();
  if (elements.view.classList.contains("active")) {
    ensureTabLoaded("runtime").catch((error) => showLoadError("runtime", error));
  }
}

initialize();
