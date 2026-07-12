import { api } from "./api.js";
import { TrafficMapCanvas } from "./map-canvas.js";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const elements = {
  connectionDot: $("#connection-dot"),
  connectionLabel: $("#connection-label"),
  activeSourceLabel: $("#active-source-label"),
  clock: $("#system-clock"),
  sourceSelect: $("#source-select"),
  playButton: $("#play-button"),
  pauseButton: $("#pause-button"),
  stopButton: $("#stop-button"),
  uploadVideoButton: $("#upload-video-button"),
  snapshotButton: $("#snapshot-button"),
  videoFileInput: $("#video-file-input"),
  videoStage: $("#video-stage"),
  videoFeed: $("#video-feed"),
  videoSwitchPreview: $("#video-switch-preview"),
  videoEmpty: $("#video-empty"),
  videoEmptyDetail: $("#video-empty-detail"),
  pausedOverlay: $("#paused-overlay"),
  streamMessage: $("#stream-message"),
  videoSourceName: $("#video-source-name"),
  liveBadge: $("#live-badge"),
  resolutionLabel: $("#resolution-label"),
  fpsLabel: $("#fps-label"),
  metricVehicles: $("#metric-vehicles"),
  metricPlates: $("#metric-plates"),
  metricWhitelisted: $("#metric-whitelisted"),
  detectionStatus: $("#detection-status"),
  detectionToggle: $("#detection-toggle"),
  singleMonitorLayout: $("#single-monitor-layout"),
  multiCameraWorkspace: $("#multi-camera-workspace"),
  multiCameraGrid: $("#multi-camera-grid"),
  multiCameraPrevious: $("#multi-camera-previous"),
  multiCameraNext: $("#multi-camera-next"),
  multiCameraPageLabel: $("#multi-camera-page-label"),
  resultsBody: $("#results-body"),
  resultsEmpty: $("#results-empty"),
  resultCount: $("#result-count"),
  settingsForm: $("#monitor-settings-pane"),
  deviceSelect: $("#device-select"),
  yoloThreshold: $("#yolo-threshold"),
  yoloOutput: $("#yolo-output"),
  lprThreshold: $("#lpr-threshold"),
  lprOutput: $("#lpr-output"),
  detectInterval: $("#detect-interval"),

  mapSummary: $("#map-summary"),
  mapFrame: $("#map-frame"),
  mapBackground: $("#map-background"),
  mapBackgroundToggle: $("#map-background-toggle"),
  mapFileInput: $("#map-file-input"),
  uploadMapButton: $("#upload-map-button"),
  refreshMapButton: $("#refresh-map-button"),
  resetMapButton: $("#reset-map-button"),
  drawStatus: $("#draw-status"),
  drawStatusText: $("#draw-status-text"),
  finishDrawButton: $("#finish-draw-button"),
  cancelDrawButton: $("#cancel-draw-button"),
  cameraEditor: $("#camera-editor"),
  cameraSelect: $("#camera-select"),
  cameraSegmentSelect: $("#camera-segment-select"),
  cameraX: $("#camera-x"),
  cameraY: $("#camera-y"),
  cameraHeading: $("#camera-heading"),
  cameraHeadingOutput: $("#camera-heading-output"),
  cameraRange: $("#camera-range"),
  cameraRangeOutput: $("#camera-range-output"),
  placeCameraButton: $("#place-camera-button"),
  roadEditor: $("#road-editor"),
  roadSelect: $("#road-select"),
  roadName: $("#road-name"),
  roadCapacity: $("#road-capacity"),
  roadLevel: $("#road-level"),
  roadDirection: $("#road-direction"),
  newRoadButton: $("#new-road-button"),
  deleteRoadButton: $("#delete-road-button"),
  drawPolylineButton: $("#draw-polyline-button"),
  drawCurveButton: $("#draw-curve-button"),
  roadStatsBody: $("#road-stats-body"),
  roadStatsUpdated: $("#road-stats-updated"),

  whitelistCount: $("#whitelist-count"),
  whitelistToggle: $("#whitelist-toggle"),
  whitelistForm: $("#whitelist-form"),
  whitelistPlate: $("#whitelist-plate"),
  whitelistNote: $("#whitelist-note"),
  whitelistBody: $("#whitelist-body"),
  whitelistEmpty: $("#whitelist-empty"),
  clearWhitelistButton: $("#clear-whitelist-button"),
  toastRegion: $("#toast-region"),
};

const state = {
  activeView: "monitor",
  system: null,
  stream: null,
  map: null,
  whitelist: null,
  selectedCamera: "",
  selectedSegment: "",
  roadDraftPoints: null,
  settingsDirty: false,
  apiOnline: false,
  lastMapImage: "",
  monitorMode: "single",
  multiCameraSources: [],
  multiCameraPage: 0,
  singleModeSnapshot: null,
  modeTransitioning: false,
  sourceSwitchToken: 0,
};

const MULTI_CAMERA_PAGE_SIZE = 6;

const mapCanvas = new TrafficMapCanvas(
  $("#traffic-map-canvas"),
  elements.mapFrame,
  {
    onCameraSelect: (cameraId) => selectCamera(cameraId, true),
    onSegmentSelect: (segmentId) => selectSegment(segmentId, true),
    onCameraPlaced: (point) => placeCamera(point),
    onDrawComplete: (points) => completeRoadDrawing(points),
    onModeChange: (mode, points) => renderDrawMode(mode, points),
  },
);

function refreshIcons() {
  window.lucide?.createIcons();
}

function setIcon(button, name) {
  if (button.dataset.icon === name) return;
  button.dataset.icon = name;
  button.replaceChildren();
  const icon = document.createElement("i");
  icon.dataset.lucide = name;
  button.append(icon);
  refreshIcons();
}

function option(value, label) {
  const item = document.createElement("option");
  item.value = value;
  item.textContent = label;
  return item;
}

function cell(text, className = "") {
  const item = document.createElement("td");
  item.textContent = text;
  if (className) item.className = className;
  return item;
}

function showToast(message, type = "info") {
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  const icon = document.createElement("i");
  icon.dataset.lucide = type === "error" ? "circle-alert" : type === "success" ? "circle-check" : "info";
  const text = document.createElement("span");
  text.textContent = message;
  toast.append(icon, text);
  elements.toastRegion.append(toast);
  refreshIcons();
  window.setTimeout(() => toast.remove(), 3600);
}

function reportError(error) {
  showToast(error instanceof Error ? error.message : String(error), "error");
}

function setApiOnline(online) {
  state.apiOnline = online;
  if (!online) {
    elements.connectionDot.className = "status-dot";
    elements.connectionLabel.textContent = "服务离线";
  }
}

function switchView(viewName) {
  state.activeView = viewName;
  $$(".nav-button[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewName);
  });
  $$(".view").forEach((view) => {
    view.classList.toggle("active", view.id === `view-${viewName}`);
  });
  if (viewName === "map") {
    mapCanvas.resize();
    loadMap({ syncEditors: false }).catch(reportError);
  } else if (viewName === "whitelist") {
    loadWhitelist().catch(reportError);
  }
}

function activateMonitorTab(tabName) {
  $$('[data-monitor-tab]').forEach((button) => {
    button.classList.toggle("active", button.dataset.monitorTab === tabName);
  });
  $("#monitor-results-pane").classList.toggle("active", tabName === "results");
  elements.settingsForm.classList.toggle("active", tabName === "settings");
}

function activateMapTab(tabName) {
  $$('[data-map-tab]').forEach((button) => {
    button.classList.toggle("active", button.dataset.mapTab === tabName);
  });
  elements.cameraEditor.classList.toggle("active", tabName === "camera");
  elements.roadEditor.classList.toggle("active", tabName === "road");
}

function populateSystem(system) {
  state.system = system;
  const currentSource = elements.sourceSelect.value;
  elements.sourceSelect.replaceChildren(
    ...system.sources.map((source) => option(source.id, source.name)),
  );
  if (system.sources.some((source) => source.id === currentSource)) {
    elements.sourceSelect.value = currentSource;
  }

  elements.deviceSelect.replaceChildren(
    ...system.devices.map((device) => option(device.id, device.name)),
  );
  state.multiCameraSources = [...system.sources];
  const lastPage = Math.max(0, Math.ceil(system.sources.length / MULTI_CAMERA_PAGE_SIZE) - 1);
  state.multiCameraPage = Math.min(state.multiCameraPage, lastPage);
  renderMultiCameraGrid();
}

function createMultiCameraTile(source, index) {
  const tile = document.createElement("figure");
  tile.className = "multi-camera-tile";

  const stage = document.createElement("div");
  stage.className = "multi-camera-stage";

  const placeholder = document.createElement("div");
  placeholder.className = "multi-camera-placeholder";
  const placeholderIcon = document.createElement("i");
  placeholderIcon.dataset.lucide = source ? "loader-circle" : "video-off";
  const placeholderText = document.createElement("span");
  placeholderText.textContent = source ? "正在连接" : "未配置摄像头";
  placeholder.append(placeholderIcon, placeholderText);
  stage.append(placeholder);

  const caption = document.createElement("figcaption");
  const identity = document.createElement("div");
  identity.className = "multi-camera-identity";
  const cameraNumber = document.createElement("span");
  cameraNumber.textContent = `摄像头 ${String(index + 1).padStart(2, "0")}`;
  const cameraName = document.createElement("strong");
  cameraName.textContent = source?.name || "暂无配置";
  identity.append(cameraNumber, cameraName);
  const status = document.createElement("span");
  status.className = "multi-camera-status";
  status.textContent = source ? "待机" : "离线";
  caption.append(identity, status);

  if (source) {
    tile.classList.add("interactive");
    tile.tabIndex = 0;
    tile.setAttribute("role", "button");
    tile.setAttribute("aria-label", `在单画面中查看 ${source.name}`);
    const openInSingleMode = () => {
      if (state.monitorMode !== "multi" || state.modeTransitioning) return;
      setMonitorMode("single", { sourceId: source.id }).catch(reportError);
    };
    tile.addEventListener("click", openInSingleMode);
    tile.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      openInSingleMode();
    });

    const feed = document.createElement("img");
    feed.alt = `${source.name} 实时画面`;
    feed.dataset.sourceId = source.id;
    feed.addEventListener("load", () => {
      tile.classList.add("feed-ready");
      if (feed.dataset.phase === "snapshot") {
        status.classList.remove("online");
        status.textContent = "连接中";
        feed.dataset.phase = "stream";
        window.setTimeout(() => {
          if (
            state.monitorMode === "multi"
            && feed.isConnected
            && feed.dataset.streamUrl
          ) {
            feed.src = feed.dataset.streamUrl;
          }
        }, 0);
      } else {
        status.classList.add("online");
        status.textContent = "实时";
      }
    });
    feed.addEventListener("error", () => {
      tile.classList.remove("feed-ready");
      status.classList.remove("online");
      if (feed.dataset.phase === "snapshot") {
        status.textContent = "连接中";
        feed.dataset.phase = "stream";
        if (state.monitorMode === "multi" && feed.dataset.streamUrl) {
          feed.src = feed.dataset.streamUrl;
        }
        return;
      }
      status.textContent = "重连中";
      window.setTimeout(() => {
        if (
          state.monitorMode === "multi"
          && feed.isConnected
          && feed.dataset.streamUrl
        ) {
          feed.dataset.phase = "stream";
          feed.src = feed.dataset.streamUrl;
        }
      }, 2500);
    });
    stage.prepend(feed);
  }

  tile.append(stage, caption);
  return tile;
}

function setMultiCameraFeedsActive(active) {
  for (const feed of elements.multiCameraGrid.querySelectorAll("img[data-source-id]")) {
    const tile = feed.closest(".multi-camera-tile");
    const status = tile.querySelector(".multi-camera-status");
    if (active) {
      status.textContent = "连接中";
      const sourceId = encodeURIComponent(feed.dataset.sourceId);
      feed.dataset.phase = "snapshot";
      feed.dataset.streamUrl = `/api/video/preview?source_id=${sourceId}&client=${Date.now()}`;
      feed.src = `/api/video/preview/snapshot?source_id=${sourceId}&cached_only=true&client=${Date.now()}`;
    } else {
      feed.removeAttribute("src");
      delete feed.dataset.phase;
      delete feed.dataset.streamUrl;
      tile.classList.remove("feed-ready");
      status.classList.remove("online");
      status.textContent = "待机";
    }
  }
}

function renderMultiCameraGrid() {
  setMultiCameraFeedsActive(false);
  const totalPages = Math.max(
    1,
    Math.ceil(state.multiCameraSources.length / MULTI_CAMERA_PAGE_SIZE),
  );
  state.multiCameraPage = Math.max(0, Math.min(state.multiCameraPage, totalPages - 1));
  const start = state.multiCameraPage * MULTI_CAMERA_PAGE_SIZE;
  const sources = state.multiCameraSources.slice(start, start + MULTI_CAMERA_PAGE_SIZE);
  const tiles = Array.from(
    { length: MULTI_CAMERA_PAGE_SIZE },
    (_, index) => createMultiCameraTile(sources[index], start + index),
  );
  elements.multiCameraGrid.replaceChildren(...tiles);
  elements.multiCameraPageLabel.textContent = `${state.multiCameraPage + 1} / ${totalPages}`;
  elements.multiCameraPrevious.disabled = state.multiCameraPage === 0;
  elements.multiCameraNext.disabled = state.multiCameraPage >= totalPages - 1;
  refreshIcons();
  if (state.monitorMode === "multi") setMultiCameraFeedsActive(true);
}

function changeMultiCameraPage(offset) {
  const totalPages = Math.max(
    1,
    Math.ceil(state.multiCameraSources.length / MULTI_CAMERA_PAGE_SIZE),
  );
  const nextPage = Math.max(0, Math.min(state.multiCameraPage + offset, totalPages - 1));
  if (nextPage === state.multiCameraPage) return;
  state.multiCameraPage = nextPage;
  renderMultiCameraGrid();
}

async function setMonitorMode(mode, { sourceId = "" } = {}) {
  const isMulti = mode === "multi";
  const nextMode = isMulti ? "multi" : "single";
  if (state.modeTransitioning || state.monitorMode === nextMode) return;

  state.modeTransitioning = true;
  $$('[data-monitor-mode]').forEach((button) => { button.disabled = true; });
  if (isMulti) {
    state.singleModeSnapshot = {
      paused: Boolean(state.stream?.paused),
      detectionEnabled: Boolean(state.stream?.detection?.enabled),
    };
  }

  state.monitorMode = isMulti ? "multi" : "single";
  document.body.classList.toggle("multi-camera-mode", isMulti);
  elements.singleMonitorLayout.hidden = isMulti;
  elements.multiCameraWorkspace.hidden = !isMulti;

  $$('[data-monitor-mode]').forEach((button) => {
    const active = button.dataset.monitorMode === state.monitorMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });

  try {
    if (isMulti) {
      cancelSourceSwitchPreview();
      elements.videoFeed.removeAttribute("src");
      elements.videoFeed.dataset.active = "true";
      setMultiCameraFeedsActive(true);
      if (state.singleModeSnapshot.detectionEnabled) {
        await api.updateDetection({ enabled: false });
      }
      if (state.stream?.active_source && !state.singleModeSnapshot.paused) {
        renderStream(await api.pauseStream(true));
      }
    } else {
      setMultiCameraFeedsActive(false);
      elements.videoFeed.dataset.active = "false";
      const snapshot = state.singleModeSnapshot;
      if (sourceId) {
        elements.sourceSelect.value = sourceId;
        const switchToken = beginSourceSwitchPreview(sourceId);
        try {
          const stream = await api.selectStream(sourceId);
          renderStream(stream, { syncSettings: true });
          completeSourceSwitch(sourceId, switchToken).catch(reportError);
        } catch (error) {
          clearSourceSwitchPreview(switchToken);
          throw error;
        }
      } else if (state.stream?.active_source && snapshot && !snapshot.paused) {
        renderStream(await api.pauseStream(false));
      } else {
        ensureVideoFeed(true);
      }
      if (snapshot?.detectionEnabled) {
        await api.updateDetection({ enabled: true });
      }
      state.singleModeSnapshot = null;
    }
  } finally {
    state.modeTransitioning = false;
    $$('[data-monitor-mode]').forEach((button) => { button.disabled = false; });
  }
}

function ensureVideoFeed(force = false) {
  const sourceId = state.stream?.active_source?.id;
  if (!sourceId) return;
  const sourceChanged = elements.videoFeed.dataset.sourceId !== sourceId;
  if (!force && !sourceChanged && elements.videoFeed.dataset.active === "true") return;
  elements.videoFeed.dataset.active = "true";
  elements.videoFeed.dataset.sourceId = sourceId;
  delete elements.videoFeed.dataset.readySourceId;
  elements.videoFeed.src = `/api/video/feed?client=${Date.now()}`;
}

function stopVideoFeed() {
  elements.videoFeed.dataset.active = "false";
  delete elements.videoFeed.dataset.sourceId;
  delete elements.videoFeed.dataset.readySourceId;
  elements.videoFeed.removeAttribute("src");
}

function renderStream(stream, { syncSettings = false } = {}) {
  state.stream = stream;
  const source = stream.active_source;
  const connected = Boolean(stream.connected);
  const hasFrame = Boolean(stream.resolution);
  const feedReady = elements.videoFeed.dataset.readySourceId === source?.id;
  const playbackEnded = Boolean(source?.local && stream.message.includes("播放完毕"));

  if (source) ensureVideoFeed();
  elements.streamMessage.textContent = stream.message;
  elements.videoSourceName.textContent = source?.display_name || "未选择视频源";
  elements.activeSourceLabel.textContent = source
    ? `${source.name} · ${stream.message}`
    : "视频与道路态势监控";
  elements.videoEmpty.hidden = hasFrame && feedReady;
  elements.videoEmptyDetail.textContent = stream.message || "请选择视频源";
  elements.pausedOverlay.hidden = !stream.paused;
  elements.pauseButton.disabled = !source || playbackEnded;
  elements.stopButton.disabled = !source;
  elements.snapshotButton.disabled = !hasFrame;

  if (source && state.system?.sources.some((item) => item.id === source.id)) {
    elements.sourceSelect.value = source.id;
  }

  elements.liveBadge.className = "live-badge";
  if (playbackEnded) {
    elements.liveBadge.textContent = "结束";
    elements.pauseButton.title = "暂停";
    setIcon(elements.pauseButton, "pause");
  } else if (stream.paused) {
    elements.liveBadge.textContent = "暂停";
    elements.liveBadge.classList.add("paused");
    elements.pauseButton.title = "继续播放";
    setIcon(elements.pauseButton, "play");
  } else if (connected) {
    elements.liveBadge.textContent = "实时";
    elements.liveBadge.classList.add("live");
    elements.pauseButton.title = "暂停";
    setIcon(elements.pauseButton, "pause");
  } else {
    elements.liveBadge.textContent = source ? "连接中" : "待机";
    elements.pauseButton.title = "暂停";
    setIcon(elements.pauseButton, "pause");
  }

  if (state.apiOnline) {
    elements.connectionDot.className = `status-dot ${connected ? "online" : "warning"}`;
    elements.connectionLabel.textContent = connected
      ? "视频已连接"
      : playbackEnded ? "视频已结束" : source ? "视频连接中" : "服务在线";
  }

  elements.resolutionLabel.textContent = stream.resolution
    ? `${stream.resolution.width} × ${stream.resolution.height}`
    : "-- × --";
  elements.fpsLabel.textContent = `${Number(stream.fps || 0).toFixed(1)} FPS`;
  elements.metricVehicles.textContent = stream.metrics.vehicles;
  elements.metricPlates.textContent = stream.metrics.plates;
  elements.metricWhitelisted.textContent = stream.metrics.whitelisted;
  elements.detectionStatus.textContent = stream.detection.status;
  elements.detectionToggle.checked = stream.detection.enabled;

  if (syncSettings || !state.settingsDirty) {
    elements.deviceSelect.value = stream.detection.device;
    elements.yoloThreshold.value = stream.detection.yolo_threshold;
    elements.lprThreshold.value = stream.detection.lpr_threshold;
    elements.detectInterval.value = stream.detection.interval;
    updateRangeOutputs();
  }
  renderResults(stream.results);
}

function renderResults(results) {
  elements.resultsBody.replaceChildren();
  elements.resultsEmpty.hidden = results.length > 0;
  elements.resultCount.textContent = results.length;
  for (const result of results) {
    const row = document.createElement("tr");
    row.append(
      cell(`${result.track_id >= 0 ? `#${result.track_id} ` : ""}${result.vehicle_class_cn}`),
      cell(result.plate_text || "--", result.plate_text ? "plate-number" : ""),
      cell(`${Math.round(result.yolo_confidence * 100)}%`),
    );
    const statusCell = document.createElement("td");
    const chip = document.createElement("span");
    chip.className = `status-chip ${result.whitelisted ? "allowed" : result.has_plate ? "denied" : ""}`;
    chip.textContent = result.whitelisted ? "白名单" : result.has_plate ? "未匹配" : "无车牌";
    statusCell.append(chip);
    row.append(statusCell);
    elements.resultsBody.append(row);
  }
}

function updateRangeOutputs() {
  elements.yoloOutput.value = Number(elements.yoloThreshold.value).toFixed(2);
  elements.lprOutput.value = Number(elements.lprThreshold.value).toFixed(2);
}

function clearSourceSwitchPreview(token = state.sourceSwitchToken) {
  if (token !== state.sourceSwitchToken) return;
  elements.videoSwitchPreview.onload = null;
  elements.videoSwitchPreview.onerror = null;
  elements.videoSwitchPreview.hidden = true;
  elements.videoSwitchPreview.removeAttribute("src");
  elements.videoStage.classList.remove("switch-preview-ready");
}

function cancelSourceSwitchPreview() {
  state.sourceSwitchToken += 1;
  clearSourceSwitchPreview();
}

function beginSourceSwitchPreview(sourceId) {
  const token = ++state.sourceSwitchToken;
  const preview = elements.videoSwitchPreview;
  preview.hidden = true;
  elements.videoStage.classList.remove("switch-preview-ready");
  preview.onload = () => {
    if (token !== state.sourceSwitchToken) return;
    preview.hidden = false;
    elements.videoStage.classList.add("switch-preview-ready");
  };
  preview.onerror = () => {
    if (token === state.sourceSwitchToken) clearSourceSwitchPreview(token);
  };
  preview.src = `/api/video/preview/snapshot?source_id=${encodeURIComponent(sourceId)}&cached_only=true&client=${Date.now()}`;
  return token;
}

async function completeSourceSwitch(sourceId, token) {
  let attempts = 0;
  while (token === state.sourceSwitchToken && state.monitorMode === "single") {
    const delay = attempts < 80 ? 250 : 1000;
    await new Promise((resolve) => window.setTimeout(resolve, delay));
    attempts += 1;
    try {
      const stream = await api.streamStatus();
      if (token !== state.sourceSwitchToken) return;
      if (
        stream.active_source?.id === sourceId
        && stream.connected
        && stream.resolution
      ) {
        renderStream(stream);
        if (elements.videoFeed.dataset.readySourceId === sourceId) {
          clearSourceSwitchPreview(token);
          return;
        }
      }
    } catch {
      // The regular status poll reports connection errors to the page.
    }
  }
}

async function playSelectedSource() {
  const sourceId = elements.sourceSelect.value;
  if (!sourceId) return;
  if (
    state.stream?.active_source?.id === sourceId
    && state.stream.connected
  ) {
    ensureVideoFeed();
    return;
  }
  const switchToken = beginSourceSwitchPreview(sourceId);
  elements.sourceSelect.disabled = true;
  elements.playButton.disabled = true;
  try {
    const stream = await api.selectStream(sourceId);
    renderStream(stream, { syncSettings: true });
    completeSourceSwitch(sourceId, switchToken).catch(reportError);
  } catch (error) {
    clearSourceSwitchPreview(switchToken);
    throw error;
  } finally {
    elements.sourceSelect.disabled = false;
    elements.playButton.disabled = !elements.sourceSelect.value;
  }
}

async function togglePause() {
  if (!state.stream?.active_source) return;
  renderStream(await api.pauseStream(!state.stream.paused));
}

async function stopStream() {
  const stream = await api.stopStream();
  stopVideoFeed();
  renderStream(stream);
}

async function uploadVideo(file) {
  if (!file) return;
  showToast(`正在上传 ${file.name}`);
  const response = await api.uploadVideo(file, elements.sourceSelect.value);
  stopVideoFeed();
  renderStream(response.stream, { syncSettings: true });
  ensureVideoFeed(true);
  showToast("本地视频已加载", "success");
}

async function saveDetectionSettings(event) {
  event.preventDefault();
  const settings = {
    device: elements.deviceSelect.value,
    yolo_threshold: Number(elements.yoloThreshold.value),
    lpr_threshold: Number(elements.lprThreshold.value),
    interval: Number(elements.detectInterval.value),
  };
  await api.updateDetection(settings);
  state.settingsDirty = false;
  showToast("检测参数已应用", "success");
}

async function loadWhitelist() {
  state.whitelist = await api.whitelist();
  renderWhitelist();
}

function renderWhitelist() {
  const whitelist = state.whitelist;
  if (!whitelist) return;
  elements.whitelistToggle.checked = whitelist.enabled;
  elements.whitelistCount.textContent = whitelist.count;
  elements.whitelistBody.replaceChildren();
  elements.whitelistEmpty.hidden = whitelist.entries.length > 0;

  for (const entry of whitelist.entries) {
    const row = document.createElement("tr");
    row.append(
      cell(entry.plate, "plate-number"),
      cell(entry.note || "--"),
      cell(entry.added_at || "--"),
    );
    const actionCell = document.createElement("td");
    actionCell.className = "action-cell";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "table-action";
    remove.title = "移除";
    remove.dataset.plate = entry.plate;
    const icon = document.createElement("i");
    icon.dataset.lucide = "trash-2";
    remove.append(icon);
    actionCell.append(remove);
    row.append(actionCell);
    elements.whitelistBody.append(row);
  }
  refreshIcons();
}

async function saveWhitelistEntry(event) {
  event.preventDefault();
  const plate = elements.whitelistPlate.value.trim().toUpperCase();
  if (!plate) return;
  const response = await api.saveWhitelist({
    plate,
    note: elements.whitelistNote.value.trim(),
  });
  elements.whitelistForm.reset();
  await loadWhitelist();
  showToast(response.created ? "车辆已加入白名单" : "白名单备注已更新", "success");
}

async function deleteWhitelistEntry(plate) {
  await api.deleteWhitelist(plate);
  await loadWhitelist();
  showToast("白名单条目已移除", "success");
}

async function clearWhitelist() {
  if (!state.whitelist?.count || !window.confirm("确定清空全部白名单记录？")) return;
  await api.clearWhitelist();
  await loadWhitelist();
  showToast("白名单已清空", "success");
}

async function loadMap({ syncEditors = false } = {}) {
  const map = await api.map();
  state.map = map;
  mapCanvas.setData(map);
  if (map.image_url !== state.lastMapImage) {
    state.lastMapImage = map.image_url;
    elements.mapBackground.src = map.image_url;
  }

  if (!state.selectedCamera && map.cameras.length) {
    state.selectedCamera = map.cameras[0].camera_id;
    syncEditors = true;
  }
  if (!state.selectedSegment && map.segments.length) {
    const selectedCamera = map.cameras.find(
      (camera) => camera.camera_id === state.selectedCamera,
    );
    state.selectedSegment = selectedCamera?.segment_id || map.segments[0].segment_id;
    syncEditors = true;
  }

  populateMapSelects();
  mapCanvas.selectCamera(state.selectedCamera);
  mapCanvas.selectSegment(state.selectedSegment);
  if (syncEditors) {
    fillCameraEditor();
    fillRoadEditor();
  }
  renderRoadStats();
  elements.mapSummary.textContent = `${map.segments.length} 条道路 · ${map.tracks.length} 个目标`;
  elements.roadStatsUpdated.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function populateMapSelects() {
  if (!state.map) return;
  elements.cameraSelect.replaceChildren(
    ...state.map.cameras.map((camera) => option(camera.camera_id, camera.camera_id)),
  );
  elements.cameraSelect.value = state.selectedCamera;

  const roadOptions = state.map.segments.map((segment) => (
    option(segment.segment_id, `${segment.name} · ${segment.direction}`)
  ));
  elements.cameraSegmentSelect.replaceChildren(...roadOptions.map((item) => item.cloneNode(true)));
  const selectedCamera = state.map.cameras.find(
    (camera) => camera.camera_id === state.selectedCamera,
  );
  elements.cameraSegmentSelect.value = selectedCamera?.segment_id || "";
  elements.roadSelect.replaceChildren(
    option("", "新建道路"),
    ...roadOptions,
  );
  elements.roadSelect.value = state.selectedSegment;
}

function selectCamera(cameraId, switchTab = false) {
  state.selectedCamera = cameraId;
  const camera = state.map?.cameras.find((item) => item.camera_id === cameraId);
  if (camera) state.selectedSegment = camera.segment_id;
  mapCanvas.selectCamera(cameraId);
  elements.cameraSelect.value = cameraId;
  fillCameraEditor();
  if (switchTab) activateMapTab("camera");
}

function fillCameraEditor() {
  const camera = state.map?.cameras.find((item) => item.camera_id === state.selectedCamera);
  if (!camera) return;
  elements.cameraSelect.value = camera.camera_id;
  elements.cameraSegmentSelect.value = camera.segment_id;
  elements.cameraX.value = Number(camera.x).toFixed(3);
  elements.cameraY.value = Number(camera.y).toFixed(3);
  elements.cameraHeading.value = camera.heading;
  elements.cameraRange.value = camera.view_range;
  updateCameraOutputs();
}

function updateCameraOutputs() {
  elements.cameraHeadingOutput.value = `${Math.round(Number(elements.cameraHeading.value))}°`;
  elements.cameraRangeOutput.value = Number(elements.cameraRange.value).toFixed(2);
}

async function saveCamera(event) {
  event.preventDefault();
  const cameraId = elements.cameraSelect.value;
  await api.updateCamera(cameraId, cameraPayload());
  await loadMap({ syncEditors: true });
  showToast("摄像头配置已保存", "success");
}

function cameraPayload(overrides = {}) {
  return {
    x: Number(elements.cameraX.value),
    y: Number(elements.cameraY.value),
    heading: Number(elements.cameraHeading.value),
    view_range: Number(elements.cameraRange.value),
    segment_id: elements.cameraSegmentSelect.value,
    ...overrides,
  };
}

async function placeCamera(point) {
  elements.cameraX.value = point.x.toFixed(3);
  elements.cameraY.value = point.y.toFixed(3);
  await api.updateCamera(elements.cameraSelect.value, cameraPayload({ x: point.x, y: point.y }));
  await loadMap({ syncEditors: true });
  showToast("摄像头位置已更新", "success");
}

function selectSegment(segmentId, switchTab = false) {
  state.selectedSegment = segmentId;
  state.roadDraftPoints = null;
  mapCanvas.selectSegment(segmentId);
  elements.roadSelect.value = segmentId;
  fillRoadEditor();
  renderRoadStats();
  if (switchTab) activateMapTab("road");
}

function fillRoadEditor() {
  const segment = state.map?.segments.find((item) => item.segment_id === state.selectedSegment);
  elements.roadSelect.value = segment?.segment_id || "";
  elements.roadName.value = segment?.name || "";
  elements.roadCapacity.value = segment?.capacity || 4;
  elements.roadLevel.value = segment?.level || "ground";
  elements.roadDirection.value = segment?.direction || "双向";
  elements.deleteRoadButton.disabled = !segment;
}

function newRoad() {
  state.selectedSegment = "";
  state.roadDraftPoints = null;
  mapCanvas.selectSegment("");
  fillRoadEditor();
  elements.roadName.focus();
}

function startRoadDrawing(mode) {
  state.roadDraftPoints = null;
  mapCanvas.startDrawing(mode);
  renderDrawMode(mode, []);
}

function renderDrawMode(mode, points = []) {
  const active = mode === "polyline" || mode === "curve" || mode === "place-camera";
  elements.drawStatus.hidden = !active;
  elements.finishDrawButton.hidden = mode !== "polyline";
  elements.drawPolylineButton.classList.toggle("active", mode === "polyline");
  elements.drawCurveButton.classList.toggle("active", mode === "curve");
  if (mode === "place-camera") {
    elements.drawStatusText.textContent = "摄像头定位";
  } else if (mode === "curve") {
    elements.drawStatusText.textContent = `曲线节点 ${points.length}/3`;
  } else if (mode === "polyline") {
    elements.drawStatusText.textContent = `折线节点 ${points.length}`;
  }
}

function completeRoadDrawing(points) {
  state.roadDraftPoints = points;
  mapCanvas.setDraft(points);
  showToast(`已记录 ${points.length} 个道路节点`, "success");
}

async function saveRoad(event) {
  event.preventDefault();
  const existing = state.map?.segments.find((item) => item.segment_id === state.selectedSegment);
  const points = state.roadDraftPoints || existing?.points;
  if (!points || points.length < 2) {
    showToast("请先绘制道路", "error");
    return;
  }
  const payload = {
    segment_id: existing?.segment_id || "",
    name: elements.roadName.value.trim() || "新建道路",
    points,
    capacity: Number(elements.roadCapacity.value),
    level: elements.roadLevel.value,
    direction: elements.roadDirection.value.trim() || "双向",
  };
  const segment = existing
    ? await api.updateSegment(existing.segment_id, payload)
    : await api.createSegment(payload);
  state.selectedSegment = segment.segment_id;
  state.roadDraftPoints = null;
  mapCanvas.setDraft([]);
  await loadMap({ syncEditors: true });
  showToast("道路配置已保存", "success");
}

async function deleteRoad() {
  if (!state.selectedSegment || !window.confirm("确定删除当前道路？")) return;
  await api.deleteSegment(state.selectedSegment);
  state.selectedSegment = "";
  state.roadDraftPoints = null;
  await loadMap({ syncEditors: true });
  showToast("道路已删除", "success");
}

function renderRoadStats() {
  if (!state.map) return;
  const states = new Map(state.map.states.map((item) => [item.segment_id, item]));
  const covered = new Set(state.map.cameras.map((item) => item.segment_id));
  elements.roadStatsBody.replaceChildren();
  for (const segment of state.map.segments) {
    const segmentState = states.get(segment.segment_id) || {
      vehicle_count: 0,
      flow_per_minute: 0,
      occupancy: 0,
      heat: 0,
    };
    const row = document.createElement("tr");
    if (segment.segment_id === state.selectedSegment) row.classList.add("selected");
    row.dataset.segmentId = segment.segment_id;
    row.append(
      cell(segment.name),
      cell(segment.direction),
      cell(String(segmentState.vehicle_count)),
      cell(String(segmentState.flow_per_minute)),
      cell(`${Math.round(segmentState.occupancy * 100)}%`),
    );
    const heatCell = document.createElement("td");
    const bar = document.createElement("span");
    bar.className = "heat-bar";
    const fill = document.createElement("i");
    const heat = covered.has(segment.segment_id) ? segmentState.heat : 0;
    fill.style.width = `${Math.max(4, Math.round(heat * 100))}%`;
    if (!covered.has(segment.segment_id)) fill.style.background = "#858c86";
    else if (heat >= 0.85) fill.className = "jam";
    else if (heat >= 0.65) fill.className = "busy";
    else if (heat >= 0.35) fill.className = "slow";
    bar.append(fill);
    heatCell.append(bar);
    row.append(heatCell);
    elements.roadStatsBody.append(row);
  }
}

async function uploadMapImage(file) {
  if (!file) return;
  await api.uploadMap(file);
  state.lastMapImage = "";
  await loadMap({ syncEditors: false });
  showToast("地图底图已更新", "success");
}

function bindEvents() {
  $$(".nav-button[data-view]").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  $$('[data-monitor-tab]').forEach((button) => {
    button.addEventListener("click", () => activateMonitorTab(button.dataset.monitorTab));
  });
  $$('[data-monitor-mode]').forEach((button) => {
    button.addEventListener("click", () => setMonitorMode(button.dataset.monitorMode).catch(reportError));
  });
  elements.multiCameraPrevious.addEventListener("click", () => changeMultiCameraPage(-1));
  elements.multiCameraNext.addEventListener("click", () => changeMultiCameraPage(1));
  $$('[data-map-tab]').forEach((button) => {
    button.addEventListener("click", () => activateMapTab(button.dataset.mapTab));
  });

  elements.sourceSelect.addEventListener("change", () => playSelectedSource().catch(reportError));
  elements.playButton.addEventListener("click", () => playSelectedSource().catch(reportError));
  elements.videoFeed.addEventListener("load", () => {
    const sourceId = elements.videoFeed.dataset.sourceId;
    if (
      state.monitorMode === "single"
      && state.stream?.active_source?.id === sourceId
    ) {
      elements.videoFeed.dataset.readySourceId = sourceId;
      elements.videoFeed.dataset.active = "true";
      elements.videoEmpty.hidden = true;
    }
  });
  elements.videoFeed.addEventListener("error", () => {
    const sourceId = elements.videoFeed.dataset.sourceId;
    elements.videoFeed.dataset.active = "false";
    delete elements.videoFeed.dataset.readySourceId;
    if (state.stream?.active_source?.id === sourceId) {
      elements.videoEmpty.hidden = false;
    }
    window.setTimeout(() => {
      if (
        state.monitorMode === "single"
        && state.stream?.active_source?.id === sourceId
        && elements.videoFeed.dataset.active === "false"
      ) {
        ensureVideoFeed(true);
      }
    }, 1000);
  });
  elements.pauseButton.addEventListener("click", () => togglePause().catch(reportError));
  elements.stopButton.addEventListener("click", () => {
    cancelSourceSwitchPreview();
    stopStream().catch(reportError);
  });
  elements.uploadVideoButton.addEventListener("click", () => elements.videoFileInput.click());
  elements.videoFileInput.addEventListener("change", () => {
    cancelSourceSwitchPreview();
    uploadVideo(elements.videoFileInput.files[0]).catch(reportError);
    elements.videoFileInput.value = "";
  });
  elements.snapshotButton.addEventListener("click", () => {
    const link = document.createElement("a");
    link.href = "/api/video/snapshot";
    link.click();
  });
  elements.detectionToggle.addEventListener("change", async () => {
    try {
      await api.updateDetection({ enabled: elements.detectionToggle.checked });
    } catch (error) {
      elements.detectionToggle.checked = !elements.detectionToggle.checked;
      reportError(error);
    }
  });
  elements.settingsForm.addEventListener("submit", (event) => saveDetectionSettings(event).catch(reportError));
  [elements.yoloThreshold, elements.lprThreshold].forEach((input) => {
    input.addEventListener("input", () => {
      state.settingsDirty = true;
      updateRangeOutputs();
    });
  });
  [elements.deviceSelect, elements.detectInterval].forEach((input) => {
    input.addEventListener("change", () => { state.settingsDirty = true; });
  });

  elements.whitelistForm.addEventListener("submit", (event) => saveWhitelistEntry(event).catch(reportError));
  elements.whitelistToggle.addEventListener("change", async () => {
    try {
      await api.setWhitelistEnabled(elements.whitelistToggle.checked);
      await loadWhitelist();
    } catch (error) {
      elements.whitelistToggle.checked = !elements.whitelistToggle.checked;
      reportError(error);
    }
  });
  elements.whitelistBody.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-plate]");
    if (button) deleteWhitelistEntry(button.dataset.plate).catch(reportError);
  });
  elements.clearWhitelistButton.addEventListener("click", () => clearWhitelist().catch(reportError));

  elements.mapBackgroundToggle.addEventListener("change", () => {
    elements.mapBackground.classList.toggle("hidden", !elements.mapBackgroundToggle.checked);
  });
  elements.uploadMapButton.addEventListener("click", () => elements.mapFileInput.click());
  elements.mapFileInput.addEventListener("change", () => {
    uploadMapImage(elements.mapFileInput.files[0]).catch(reportError);
    elements.mapFileInput.value = "";
  });
  elements.refreshMapButton.addEventListener("click", () => loadMap({ syncEditors: false }).catch(reportError));
  elements.resetMapButton.addEventListener("click", async () => {
    try {
      await api.resetMap();
      await loadMap({ syncEditors: false });
      showToast("运行轨迹已清除", "success");
    } catch (error) {
      reportError(error);
    }
  });
  elements.cameraSelect.addEventListener("change", () => selectCamera(elements.cameraSelect.value));
  elements.cameraEditor.addEventListener("submit", (event) => saveCamera(event).catch(reportError));
  elements.cameraHeading.addEventListener("input", updateCameraOutputs);
  elements.cameraRange.addEventListener("input", updateCameraOutputs);
  elements.placeCameraButton.addEventListener("click", () => {
    mapCanvas.startPlacement();
    renderDrawMode("place-camera", []);
  });
  elements.roadSelect.addEventListener("change", () => {
    if (elements.roadSelect.value) selectSegment(elements.roadSelect.value);
    else newRoad();
  });
  elements.roadEditor.addEventListener("submit", (event) => saveRoad(event).catch(reportError));
  elements.newRoadButton.addEventListener("click", newRoad);
  elements.deleteRoadButton.addEventListener("click", () => deleteRoad().catch(reportError));
  elements.drawPolylineButton.addEventListener("click", () => startRoadDrawing("polyline"));
  elements.drawCurveButton.addEventListener("click", () => startRoadDrawing("curve"));
  elements.finishDrawButton.addEventListener("click", () => {
    if (!mapCanvas.finishDrawing()) showToast("道路至少需要两个节点", "error");
  });
  elements.cancelDrawButton.addEventListener("click", () => mapCanvas.cancelMode());
  elements.roadStatsBody.addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-segment-id]");
    if (row) selectSegment(row.dataset.segmentId, true);
  });
}

async function pollStream() {
  try {
    const stream = await api.streamStatus();
    setApiOnline(true);
    renderStream(stream);
  } catch {
    setApiOnline(false);
  } finally {
    window.setTimeout(pollStream, document.hidden ? 3000 : 1000);
  }
}

async function pollMap() {
  if (state.activeView === "map") {
    try {
      await loadMap({ syncEditors: false });
    } catch (error) {
      console.error(error);
    }
  }
  window.setTimeout(pollMap, document.hidden ? 5000 : 1800);
}

function startClock() {
  const update = () => {
    elements.clock.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  };
  update();
  window.setInterval(update, 1000);
}

async function initialize() {
  bindEvents();
  refreshIcons();
  startClock();
  updateRangeOutputs();

  try {
    const [system, stream, whitelist, map] = await Promise.all([
      api.system(),
      api.streamStatus(),
      api.whitelist(),
      api.map(),
    ]);
    setApiOnline(true);
    populateSystem(system);
    state.whitelist = whitelist;
    renderWhitelist();
    state.map = map;
    await loadMap({ syncEditors: true });
    renderStream(stream, { syncSettings: true });
  } catch (error) {
    setApiOnline(false);
    reportError(error);
  }

  pollStream();
  pollMap();
}

initialize();
