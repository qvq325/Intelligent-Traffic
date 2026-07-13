import { api } from "./api.js?v=20260713-1";
import { TrafficMapCanvas } from "./map-canvas.js?v=20260712-4";
import { VideoRegionEditor } from "./no-parking-canvas.js?v=20260713-1";

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

  noParkingSummary: $("#no-parking-summary"),
  noParkingStatusDot: $("#no-parking-status-dot"),
  noParkingStatusLabel: $("#no-parking-status-label"),
  noParkingStartButton: $("#no-parking-start-button"),
  noParkingStopButton: $("#no-parking-stop-button"),
  noParkingSourceSelect: $("#no-parking-source-select"),
  noParkingConnectButton: $("#no-parking-connect-button"),
  noParkingUploadButton: $("#no-parking-upload-button"),
  noParkingReferenceButton: $("#no-parking-reference-button"),
  noParkingFileInput: $("#no-parking-file-input"),
  noParkingSourceBadge: $("#no-parking-source-badge"),
  noParkingSourceName: $("#no-parking-source-name"),
  noParkingStage: $("#no-parking-stage"),
  noParkingFeed: $("#no-parking-feed"),
  noParkingReference: $("#no-parking-reference"),
  noParkingEmpty: $("#no-parking-empty"),
  noParkingDrawButton: $("#no-parking-draw-button"),
  noParkingFinishDrawButton: $("#no-parking-finish-draw-button"),
  noParkingUndoButton: $("#no-parking-undo-button"),
  noParkingClearZoneButton: $("#no-parking-clear-zone-button"),
  noParkingZoneCount: $("#no-parking-zone-count"),
  noParkingTrackCount: $("#no-parking-track-count"),
  noParkingAlarmCount: $("#no-parking-alarm-count"),
  noParkingEventCount: $("#no-parking-event-count"),
  noParkingConfigForm: $("#no-parking-config-form"),
  noParkingSceneSelect: $("#no-parking-scene-select"),
  noParkingNewSceneButton: $("#no-parking-new-scene-button"),
  noParkingDeleteSceneButton: $("#no-parking-delete-scene-button"),
  noParkingSceneName: $("#no-parking-scene-name"),
  noParkingZoneName: $("#no-parking-zone-name"),
  noParkingDwellSeconds: $("#no-parking-dwell-seconds"),
  noParkingLostSeconds: $("#no-parking-lost-seconds"),
  noParkingReferenceStatus: $("#no-parking-reference-status"),
  noParkingSaveButton: $("#no-parking-save-button"),
  noParkingActiveCount: $("#no-parking-active-count"),
  noParkingTrackBody: $("#no-parking-track-body"),
  noParkingTrackEmpty: $("#no-parking-track-empty"),
  noParkingEventBody: $("#no-parking-event-body"),
  noParkingEventEmpty: $("#no-parking-event-empty"),
  noParkingClearEventsButton: $("#no-parking-clear-events-button"),

  mapSummary: $("#map-summary"),
  mapFrame: $("#map-frame"),
  mapBackground: $("#map-background"),
  mapBackgroundToggle: $("#map-background-toggle"),
  mapFileInput: $("#map-file-input"),
  uploadMapButton: $("#upload-map-button"),
  refreshMapButton: $("#refresh-map-button"),
  resetMapButton: $("#reset-map-button"),
  mapAnalysisCameraSelect: $("#map-analysis-camera-select"),
  mapAnalysisStartButton: $("#map-analysis-start-button"),
  mapAnalysisPauseButton: $("#map-analysis-pause-button"),
  mapAnalysisStopButton: $("#map-analysis-stop-button"),
  mapAnalysisDot: $("#map-analysis-dot"),
  mapAnalysisStatus: $("#map-analysis-status"),
  mapAnalysisRoad: $("#map-analysis-road"),
  mapAnalysisModel: $("#map-analysis-model"),
  roadVideoPreview: $("#road-video-preview"),
  roadVideoPreviewFeed: $("#road-video-preview-feed"),
  roadVideoPreviewPlaceholder: $("#road-video-preview-placeholder"),
  roadVideoPreviewStatus: $("#road-video-preview-status"),
  roadVideoPreviewRoad: $("#road-video-preview-road"),
  roadVideoPreviewCamera: $("#road-video-preview-camera"),
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
  roadWidthField: $("#road-width-field"),
  roadWidth: $("#road-width"),
  roadWidthOutput: $("#road-width-output"),
  newRoadButton: $("#new-road-button"),
  deleteRoadButton: $("#delete-road-button"),
  drawPolylineButton: $("#draw-polyline-button"),
  drawCurveButton: $("#draw-curve-button"),
  drawPolygonButton: $("#draw-polygon-button"),
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
  mapAnalysis: null,
  mapAnalysisBusy: false,
  whitelist: null,
  selectedCamera: "",
  selectedSegment: "",
  creatingRoad: false,
  roadDraftPoints: null,
  roadDraftGeometry: "polyline",
  settingsDirty: false,
  apiOnline: false,
  lastMapImage: "",
  monitorMode: "single",
  multiCameraSources: [],
  multiCameraPage: 0,
  singleModeSnapshot: null,
  modeTransitioning: false,
  sourceSwitchToken: 0,
  noParkingCatalog: [],
  noParkingStatus: null,
  noParkingSceneId: "",
  noParkingReference: null,
  noParkingPoints: [],
  noParkingDrawing: false,
  noParkingBusy: false,
};

const MULTI_CAMERA_PAGE_SIZE = 6;
const MAP_REFERENCE_HEIGHT = 740;
const DEFAULT_ROAD_WIDTH_PIXELS = 36;
const ROAD_PREVIEW_HOVER_DELAY = 160;

let roadPreviewTimer = null;
let pendingRoadPreview = null;

const mapCanvas = new TrafficMapCanvas(
  $("#traffic-map-canvas"),
  elements.mapFrame,
  {
    onCameraSelect: (cameraId) => selectCamera(cameraId, true),
    onSegmentSelect: (segmentId) => selectSegment(segmentId, true),
    onCameraPlaced: (point) => placeCamera(point),
    onDrawComplete: (points, geometryType) => completeRoadDrawing(points, geometryType),
    onModeChange: (mode, points) => renderDrawMode(mode, points),
  },
);

mapCanvas.canvas.addEventListener("pointermove", (event) => scheduleRoadVideoPreview(event));
mapCanvas.canvas.addEventListener("pointerleave", () => closeRoadVideoPreview());

const noParkingCanvas = new VideoRegionEditor(
  $("#no-parking-canvas"),
  elements.noParkingStage,
  {
    onChange: (points) => {
      state.noParkingPoints = points;
      renderNoParkingControls();
    },
    onComplete: (points) => {
      state.noParkingPoints = points;
      renderNoParkingControls();
      showToast("禁停区域已完成", "success");
    },
    onModeChange: (drawing) => {
      state.noParkingDrawing = drawing;
      renderNoParkingControls();
    },
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
  const previousView = state.activeView;
  state.activeView = viewName;
  $$(".nav-button[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewName);
  });
  $$(".view").forEach((view) => {
    view.classList.toggle("active", view.id === `view-${viewName}`);
  });
  $(".main-content").scrollTop = 0;
  if (viewName === "map") {
    mapCanvas.resize();
    loadMap({ syncEditors: false }).catch(reportError);
  } else if (viewName === "no-parking") {
    noParkingCanvas.resize();
    loadNoParking({ syncEditor: true }).catch(reportError);
    ensureNoParkingFeed(true);
  } else if (viewName === "whitelist") {
    loadWhitelist().catch(reportError);
  }
  if (previousView === "no-parking" && viewName !== "no-parking") stopNoParkingFeed();
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

function activateNoParkingTab(tabName) {
  $$('[data-no-parking-tab]').forEach((button) => {
    button.classList.toggle("active", button.dataset.noParkingTab === tabName);
  });
  elements.noParkingConfigForm.classList.toggle("active", tabName === "config");
  $("#no-parking-results-pane").classList.toggle("active", tabName === "results");
}

function populateSystem(system) {
  state.system = system;
  const currentSource = elements.sourceSelect.value;
  const currentNoParkingSource = elements.noParkingSourceSelect.value;
  elements.sourceSelect.replaceChildren(
    ...system.sources.map((source) => option(source.id, source.name)),
  );
  elements.noParkingSourceSelect.replaceChildren(
    ...system.sources.map((source) => option(source.id, source.name)),
  );
  if (system.sources.some((source) => source.id === currentSource)) {
    elements.sourceSelect.value = currentSource;
  }
  if (system.sources.some((source) => source.id === currentNoParkingSource)) {
    elements.noParkingSourceSelect.value = currentNoParkingSource;
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

function currentNoParkingScene() {
  return state.noParkingCatalog.find((scene) => scene.scene_id === state.noParkingSceneId) || null;
}

function formatParkingDuration(value) {
  const seconds = Math.max(0, Number(value) || 0);
  return seconds >= 60
    ? `${Math.floor(seconds / 60)}分${Math.round(seconds % 60)}秒`
    : `${seconds.toFixed(seconds < 10 ? 1 : 0)}秒`;
}

function formatParkingTime(value) {
  if (!value) return "--";
  return new Date(Number(value) * 1000).toLocaleTimeString("zh-CN", { hour12: false });
}

function ensureNoParkingFeed(force = false) {
  if (state.activeView !== "no-parking") return;
  const sourceId = state.stream?.active_source?.id;
  if (!sourceId) return;
  if (
    !force
    && elements.noParkingFeed.dataset.sourceId === sourceId
    && elements.noParkingFeed.getAttribute("src")
  ) return;
  elements.noParkingFeed.dataset.sourceId = sourceId;
  elements.noParkingFeed.src = `/api/video/feed?view=no-parking&client=${Date.now()}`;
}

function stopNoParkingFeed() {
  elements.noParkingFeed.removeAttribute("src");
  delete elements.noParkingFeed.dataset.sourceId;
}

function showNoParkingLive() {
  elements.noParkingReference.hidden = true;
  elements.noParkingFeed.hidden = false;
  noParkingCanvas.setMedia(elements.noParkingFeed);
  ensureNoParkingFeed();
}

function showNoParkingReference(reference) {
  state.noParkingReference = reference;
  elements.noParkingFeed.hidden = true;
  elements.noParkingReference.hidden = false;
  elements.noParkingReference.src = `${reference.url}?v=${reference.captured_at || Date.now()}`;
  elements.noParkingEmpty.hidden = true;
  noParkingCanvas.setMedia(elements.noParkingReference);
}

function renderNoParkingSceneOptions() {
  const selected = state.noParkingSceneId;
  elements.noParkingSceneSelect.replaceChildren(
    option("", "新建场景"),
    ...state.noParkingCatalog.map((scene) => option(scene.scene_id, scene.name)),
  );
  elements.noParkingSceneSelect.value = state.noParkingCatalog.some(
    (scene) => scene.scene_id === selected,
  ) ? selected : "";
}

function selectNoParkingScene(sceneId) {
  state.noParkingSceneId = sceneId || "";
  const scene = currentNoParkingScene();
  const zone = scene?.zones?.[0] || null;
  elements.noParkingSceneSelect.value = scene?.scene_id || "";
  elements.noParkingSceneName.value = scene?.name || "禁停监控场景";
  elements.noParkingZoneName.value = zone?.name || "禁停区域";
  elements.noParkingDwellSeconds.value = zone?.dwell_seconds || 10;
  elements.noParkingLostSeconds.value = zone?.lost_tolerance_seconds || 2;
  if (scene && state.system?.sources.some((source) => source.id === scene.camera_id)) {
    elements.noParkingSourceSelect.value = scene.camera_id;
  }

  state.noParkingPoints = zone?.points || [];
  noParkingCanvas.setPoints(state.noParkingPoints);
  if (scene?.reference_url) {
    showNoParkingReference({
      filename: scene.reference_image,
      url: scene.reference_url,
      camera_id: scene.camera_id,
      width: scene.reference_width,
      height: scene.reference_height,
      captured_at: scene.updated_at,
    });
  } else {
    state.noParkingReference = null;
    elements.noParkingReference.hidden = true;
    elements.noParkingReference.removeAttribute("src");
    elements.noParkingFeed.hidden = false;
    noParkingCanvas.setMedia(elements.noParkingFeed);
  }
  renderNoParkingControls();
}

function renderNoParkingControls() {
  const scene = currentNoParkingScene();
  const status = state.noParkingStatus || { running: false, metrics: {} };
  const selectedSource = elements.noParkingSourceSelect.value;
  const activeSource = state.stream?.active_source;
  const sourceReady = Boolean(activeSource && activeSource.id === selectedSource);
  const referenceReady = Boolean(
    state.noParkingReference?.filename
    && state.noParkingReference.camera_id === selectedSource,
  );
  const zoneReady = state.noParkingPoints.length >= 3;
  const monitoring = Boolean(status.running);
  const busy = state.noParkingBusy;

  elements.noParkingConnectButton.disabled = busy || !selectedSource;
  elements.noParkingUploadButton.disabled = busy || !selectedSource;
  elements.noParkingReferenceButton.disabled = busy || monitoring || !sourceReady || !state.stream?.resolution;
  elements.noParkingDrawButton.disabled = busy || monitoring || !referenceReady || state.noParkingDrawing;
  elements.noParkingFinishDrawButton.disabled = busy || !state.noParkingDrawing || state.noParkingPoints.length < 3;
  elements.noParkingUndoButton.disabled = busy || !state.noParkingDrawing || !state.noParkingPoints.length;
  elements.noParkingClearZoneButton.disabled = busy || monitoring || !state.noParkingPoints.length;
  elements.noParkingSaveButton.disabled = busy || monitoring || !referenceReady || !zoneReady;
  elements.noParkingDeleteSceneButton.disabled = busy || monitoring || !scene;
  elements.noParkingStartButton.disabled = busy || monitoring || !scene || !sourceReady;
  elements.noParkingStopButton.disabled = busy || !monitoring;

  if (activeSource) {
    elements.noParkingSourceName.textContent = activeSource.display_name || activeSource.name;
    elements.noParkingSourceBadge.textContent = state.stream.paused
      ? "已暂停"
      : state.stream.connected ? "实时" : "连接中";
    elements.noParkingSourceBadge.className = `live-badge ${state.stream.paused ? "paused" : state.stream.connected ? "live" : ""}`;
  } else {
    elements.noParkingSourceName.textContent = "未选择视频源";
    elements.noParkingSourceBadge.textContent = "待机";
    elements.noParkingSourceBadge.className = "live-badge";
  }

  elements.noParkingReferenceStatus.textContent = referenceReady
    ? `${state.noParkingReference.width} x ${state.noParkingReference.height}`
    : "未截取";
  elements.noParkingSummary.textContent = monitoring && status.active_scene
    ? `${status.active_scene.name} · ${status.active_scene.camera_id}`
    : `${state.noParkingCatalog.length} 个已保存场景`;
  elements.noParkingStatusLabel.textContent = monitoring
    ? (status.metrics?.active_alarms ? "检测到违规停留" : "监控运行中")
    : "未启动";
  elements.noParkingStatusDot.className = `status-dot ${
    monitoring ? (status.metrics?.active_alarms ? "alarm" : "running") : ""
  }`;

  const readiness = [sourceReady, referenceReady, zoneReady, monitoring];
  let currentAssigned = false;
  $$('[data-no-parking-step]').forEach((step, index) => {
    step.classList.toggle("ready", readiness[index]);
    const current = !readiness[index] && !currentAssigned;
    step.classList.toggle("current", current);
    if (current) currentAssigned = true;
  });
  noParkingCanvas.setActivity({
    running: monitoring,
    alarm: Boolean(status.metrics?.active_alarms),
  });
}

function renderNoParkingStatus(status) {
  state.noParkingStatus = status;
  const metrics = status?.metrics || {};
  elements.noParkingZoneCount.textContent = metrics.zones || 0;
  elements.noParkingTrackCount.textContent = metrics.active_tracks || 0;
  elements.noParkingAlarmCount.textContent = metrics.active_alarms || 0;
  elements.noParkingEventCount.textContent = metrics.total_events || 0;
  elements.noParkingActiveCount.textContent = status?.tracks?.length || 0;

  elements.noParkingTrackBody.replaceChildren();
  for (const track of status?.tracks || []) {
    const row = document.createElement("tr");
    row.append(
      cell(track.plate_text || `#${track.track_id}`),
      cell(track.zone_name),
      cell(`${formatParkingDuration(track.dwell_seconds)} / ${Math.round(track.threshold_seconds)}秒`),
    );
    const stateCell = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `parking-state ${track.status === "alarmed" ? "alarm" : ""}`;
    badge.textContent = track.status === "alarmed" ? "已告警" : "计时中";
    stateCell.append(badge);
    row.append(stateCell);
    elements.noParkingTrackBody.append(row);
  }
  elements.noParkingTrackEmpty.hidden = Boolean(status?.tracks?.length);

  elements.noParkingEventBody.replaceChildren();
  for (const event of status?.events || []) {
    const row = document.createElement("tr");
    row.append(
      cell(formatParkingTime(event.triggered_at)),
      cell(event.zone_name),
      cell(event.plate_text || `#${event.track_id}`),
    );
    const stateCell = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `parking-state ${event.ended_at ? "" : "alarm"}`;
    badge.textContent = event.ended_at ? formatParkingDuration(event.duration_seconds) : "持续中";
    stateCell.append(badge);
    row.append(stateCell);
    elements.noParkingEventBody.append(row);
  }
  elements.noParkingEventEmpty.hidden = Boolean(status?.events?.length);
  renderNoParkingControls();
}

async function loadNoParking({ syncEditor = false } = {}) {
  const payload = await api.noParking();
  state.noParkingCatalog = payload.scenes || [];
  if (
    !state.noParkingSceneId
    && payload.status?.active_scene_id
    && state.noParkingCatalog.some((scene) => scene.scene_id === payload.status.active_scene_id)
  ) {
    state.noParkingSceneId = payload.status.active_scene_id;
  }
  renderNoParkingSceneOptions();
  if (syncEditor) selectNoParkingScene(state.noParkingSceneId);
  renderNoParkingStatus(payload.status);
  if (payload.status?.running) showNoParkingLive();
}

async function connectNoParkingSource() {
  const sourceId = elements.noParkingSourceSelect.value;
  if (!sourceId) return;
  state.noParkingBusy = true;
  renderNoParkingControls();
  try {
    const stream = await api.selectStream(sourceId);
    renderStream(stream, { syncSettings: true });
    showNoParkingLive();
    ensureNoParkingFeed(true);
  } finally {
    state.noParkingBusy = false;
    renderNoParkingControls();
  }
}

async function uploadNoParkingVideo(file) {
  if (!file) return;
  const cameraId = elements.noParkingSourceSelect.value;
  if (!cameraId) return;
  state.noParkingBusy = true;
  renderNoParkingControls();
  showToast(`正在上传 ${file.name}`);
  try {
    const response = await api.uploadVideo(file, cameraId);
    renderStream(response.stream, { syncSettings: true });
    showNoParkingLive();
    ensureNoParkingFeed(true);
    showToast("本地视频已载入禁停工作台", "success");
  } finally {
    state.noParkingBusy = false;
    renderNoParkingControls();
  }
}

async function captureNoParkingReference() {
  const cameraId = elements.noParkingSourceSelect.value;
  if (!cameraId) return;
  if (state.stream?.active_source?.id !== cameraId) await connectNoParkingSource();
  state.noParkingBusy = true;
  renderNoParkingControls();
  try {
    renderStream(await api.pauseStream(true));
    const reference = await api.captureNoParkingReference(cameraId);
    state.noParkingPoints = [];
    noParkingCanvas.setPoints([]);
    showNoParkingReference(reference);
    activateNoParkingTab("config");
    showToast("参考帧已冻结，可以绘制禁停区域", "success");
  } finally {
    state.noParkingBusy = false;
    renderNoParkingControls();
  }
}

async function saveNoParkingScene(event) {
  event.preventDefault();
  if (
    !state.noParkingReference
    || state.noParkingReference.camera_id !== elements.noParkingSourceSelect.value
    || state.noParkingPoints.length < 3
  ) return;
  const existing = currentNoParkingScene();
  const existingZone = existing?.zones?.[0];
  state.noParkingBusy = true;
  renderNoParkingControls();
  try {
    const scene = await api.saveNoParkingScene({
      scene_id: existing?.scene_id || "",
      name: elements.noParkingSceneName.value.trim() || "禁停监控场景",
      camera_id: elements.noParkingSourceSelect.value,
      reference_image: state.noParkingReference.filename,
      reference_width: state.noParkingReference.width,
      reference_height: state.noParkingReference.height,
      zones: [{
        zone_id: existingZone?.zone_id || "",
        name: elements.noParkingZoneName.value.trim() || "禁停区域",
        points: state.noParkingPoints,
        dwell_seconds: Number(elements.noParkingDwellSeconds.value),
        lost_tolerance_seconds: Number(elements.noParkingLostSeconds.value),
        enabled: true,
        vehicle_classes: ["car", "motorcycle", "bus", "truck"],
      }],
    });
    state.noParkingSceneId = scene.scene_id;
    await loadNoParking({ syncEditor: true });
    showToast("禁停场景配置已保存", "success");
  } finally {
    state.noParkingBusy = false;
    renderNoParkingControls();
  }
}

function newNoParkingScene() {
  if (state.noParkingDrawing) noParkingCanvas.cancelDrawing();
  selectNoParkingScene("");
  elements.noParkingSceneName.focus();
}

async function deleteNoParkingScene() {
  const scene = currentNoParkingScene();
  if (!scene || !window.confirm(`确定删除场景“${scene.name}”？`)) return;
  await api.deleteNoParkingScene(scene.scene_id);
  state.noParkingSceneId = "";
  await loadNoParking({ syncEditor: true });
  showToast("禁停场景已删除", "success");
}

async function startNoParking() {
  const scene = currentNoParkingScene();
  if (!scene) return;
  if (state.stream?.active_source?.id !== scene.camera_id) {
    elements.noParkingSourceSelect.value = scene.camera_id;
    await connectNoParkingSource();
  }
  state.noParkingBusy = true;
  renderNoParkingControls();
  try {
    renderNoParkingStatus(await api.startNoParking(scene.scene_id));
    renderStream(await api.streamStatus(), { syncSettings: true });
    showNoParkingLive();
    ensureNoParkingFeed(true);
    activateNoParkingTab("results");
    showToast("禁停监控已启动", "success");
  } finally {
    state.noParkingBusy = false;
    renderNoParkingControls();
  }
}

async function stopNoParking() {
  state.noParkingBusy = true;
  renderNoParkingControls();
  try {
    renderNoParkingStatus(await api.stopNoParking());
    if (state.noParkingReference) showNoParkingReference(state.noParkingReference);
    showToast("禁停监控已停止", "success");
  } finally {
    state.noParkingBusy = false;
    renderNoParkingControls();
  }
}

async function clearNoParkingEvents() {
  if (!window.confirm("确定清空禁停事件记录？")) return;
  renderNoParkingStatus(await api.clearNoParkingEvents());
  showToast("事件记录已清空", "success");
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
  if (state.selectedSegment && !map.segments.some(
    (segment) => segment.segment_id === state.selectedSegment
  )) {
    state.selectedSegment = "";
  }
  if (!state.creatingRoad && !state.selectedSegment && map.segments.length) {
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
  populateMapAnalysisSources();
}

function mapAnalysisBinding(sourceId) {
  const camera = state.map?.cameras.find((item) => item.camera_id === sourceId);
  const segment = state.map?.segments.find((item) => item.segment_id === camera?.segment_id);
  return { camera, segment };
}

function populateMapAnalysisSources() {
  if (!state.system || !state.map) return;
  const currentValue = elements.mapAnalysisCameraSelect.value;
  const activeSourceId = state.mapAnalysis?.active_source?.id || "";
  const placeholder = option("", "选择已绑定摄像头");
  const sourceOptions = state.system.sources.map((source) => {
    const { segment } = mapAnalysisBinding(source.id);
    const item = option(
      source.id,
      segment ? `${source.name} · ${segment.name}` : `${source.name} · 未绑定道路`,
    );
    item.disabled = !segment;
    return item;
  });
  elements.mapAnalysisCameraSelect.replaceChildren(placeholder, ...sourceOptions);
  const preferredValue = currentValue || activeSourceId;
  if (sourceOptions.some((item) => item.value === preferredValue)) {
    elements.mapAnalysisCameraSelect.value = preferredValue;
  }
  renderMapAnalysis(state.mapAnalysis);
}

function mapAnalysisCanStart() {
  const selected = elements.mapAnalysisCameraSelect.selectedOptions[0];
  return Boolean(selected?.value && !selected.disabled);
}

function roadVideoPreviewEnabled() {
  return state.activeView === "map";
}

function camerasForSegment(segmentId) {
  const sourceIds = new Set((state.system?.sources || []).map((source) => source.id));
  return (state.map?.cameras || []).filter(
    (camera) => camera.segment_id === segmentId && sourceIds.has(camera.camera_id),
  );
}

function positionRoadVideoPreview(clientX, clientY) {
  const preview = elements.roadVideoPreview;
  if (preview.hidden) return;
  const bounds = elements.mapFrame.getBoundingClientRect();
  const gap = 14;
  const margin = 8;
  const width = preview.offsetWidth;
  const height = preview.offsetHeight;
  const pointerX = clientX - bounds.left;
  const pointerY = clientY - bounds.top;
  const left = pointerX + gap + width <= bounds.width - margin
    ? pointerX + gap
    : pointerX - width - gap;
  const top = pointerY + gap + height <= bounds.height - margin
    ? pointerY + gap
    : pointerY - height - gap;
  preview.style.left = `${Math.max(margin, Math.min(left, bounds.width - width - margin))}px`;
  preview.style.top = `${Math.max(margin, Math.min(top, bounds.height - height - margin))}px`;
}

function openRoadVideoPreview(segment, cameras, camera, clientX, clientY) {
  if (!roadVideoPreviewEnabled()) return;
  const preview = elements.roadVideoPreview;
  const feed = elements.roadVideoPreviewFeed;
  const cameraCount = cameras.length > 1 ? ` · ${cameras.length} 个摄像头` : "";

  preview.dataset.segmentId = segment.segment_id;
  preview.dataset.cameraId = camera.camera_id;
  preview.classList.remove("feed-ready", "feed-error");
  elements.roadVideoPreviewRoad.textContent = segment.name;
  elements.roadVideoPreviewCamera.textContent = `${camera.camera_id}${cameraCount}`;
  elements.roadVideoPreviewPlaceholder.querySelector("span").textContent = "正在连接视频";
  elements.roadVideoPreviewStatus.textContent = "实时";
  preview.hidden = false;
  positionRoadVideoPreview(clientX, clientY);

  feed.onload = () => {
    if (preview.dataset.cameraId !== camera.camera_id) return;
    preview.classList.add("feed-ready");
    preview.classList.remove("feed-error");
    if (feed.dataset.phase === "snapshot") {
      elements.roadVideoPreviewStatus.textContent = "正在连接";
      feed.dataset.phase = "stream";
      window.setTimeout(() => {
        if (!preview.hidden && feed.dataset.streamUrl) feed.src = feed.dataset.streamUrl;
      }, 0);
      return;
    }
    elements.roadVideoPreviewStatus.textContent = "实时";
  };
  feed.onerror = () => {
    if (preview.dataset.cameraId !== camera.camera_id) return;
    if (feed.dataset.phase === "snapshot") {
      feed.dataset.phase = "stream";
      if (!preview.hidden && feed.dataset.streamUrl) feed.src = feed.dataset.streamUrl;
      return;
    }
    preview.classList.remove("feed-ready");
    preview.classList.add("feed-error");
    elements.roadVideoPreviewPlaceholder.querySelector("span").textContent = "视频暂不可用";
    elements.roadVideoPreviewStatus.textContent = "连接异常";
  };
  const sourceId = encodeURIComponent(camera.camera_id);
  feed.dataset.phase = "snapshot";
  feed.dataset.streamUrl = `/api/video/preview?source_id=${sourceId}&client=${Date.now()}`;
  feed.src = `/api/video/preview/snapshot?source_id=${sourceId}&cached_only=true&client=${Date.now()}`;
}

function closeRoadVideoPreview() {
  if (roadPreviewTimer) window.clearTimeout(roadPreviewTimer);
  roadPreviewTimer = null;
  pendingRoadPreview = null;
  const preview = elements.roadVideoPreview;
  const feed = elements.roadVideoPreviewFeed;
  preview.hidden = true;
  preview.classList.remove("feed-ready", "feed-error");
  delete preview.dataset.segmentId;
  delete preview.dataset.cameraId;
  feed.onload = null;
  feed.onerror = null;
  feed.removeAttribute("src");
  delete feed.dataset.phase;
  delete feed.dataset.streamUrl;
}

function scheduleRoadVideoPreview(event) {
  if (!roadVideoPreviewEnabled() || mapCanvas.mode) {
    closeRoadVideoPreview();
    return;
  }
  const point = mapCanvas.normalizedPoint(event);
  const camera = mapCanvas.nearestCamera(point, 20);
  const segment = state.map?.segments.find(
    (item) => item.segment_id === camera?.segment_id,
  );
  const cameras = segment ? camerasForSegment(segment.segment_id) : [];
  if (!camera || !segment || !cameras.some((item) => item.camera_id === camera.camera_id)) {
    closeRoadVideoPreview();
    return;
  }

  mapCanvas.tooltip.hidden = true;
  const key = `${segment.segment_id}:${camera.camera_id}`;
  const preview = elements.roadVideoPreview;
  if (!preview.hidden && preview.dataset.segmentId === segment.segment_id
      && preview.dataset.cameraId === camera.camera_id) {
    positionRoadVideoPreview(event.clientX, event.clientY);
    return;
  }
  if (pendingRoadPreview?.key === key) {
    pendingRoadPreview.clientX = event.clientX;
    pendingRoadPreview.clientY = event.clientY;
    return;
  }

  closeRoadVideoPreview();
  pendingRoadPreview = {
    key,
    segment,
    cameras: [camera],
    camera,
    clientX: event.clientX,
    clientY: event.clientY,
  };
  roadPreviewTimer = window.setTimeout(() => {
    const pending = pendingRoadPreview;
    roadPreviewTimer = null;
    pendingRoadPreview = null;
    if (!pending) return;
    openRoadVideoPreview(
      pending.segment,
      pending.cameras,
      pending.camera,
      pending.clientX,
      pending.clientY,
    );
  }, ROAD_PREVIEW_HOVER_DELAY);
}

function syncRoadVideoPreview() {
  if (!roadVideoPreviewEnabled()) {
    closeRoadVideoPreview();
    return;
  }
  const preview = elements.roadVideoPreview;
  if (preview.hidden) return;
  const cameras = camerasForSegment(preview.dataset.segmentId);
  if (!cameras.some((camera) => camera.camera_id === preview.dataset.cameraId)) {
    closeRoadVideoPreview();
    return;
  }
  if (!preview.classList.contains("feed-error") && elements.roadVideoPreviewFeed.dataset.phase !== "snapshot") {
    elements.roadVideoPreviewStatus.textContent = "实时";
  }
}

function renderMapAnalysis(analysis) {
  state.mapAnalysis = analysis;
  syncRoadVideoPreview();
  const activeSource = analysis?.active_source;
  if (
    !elements.mapAnalysisCameraSelect.value
    && activeSource
    && [...elements.mapAnalysisCameraSelect.options].some(
      (item) => item.value === activeSource.id
    )
  ) {
    elements.mapAnalysisCameraSelect.value = activeSource.id;
  }

  elements.mapAnalysisDot.className = "map-analysis-dot";
  if (analysis?.connected) elements.mapAnalysisDot.classList.add("connected");
  else if (activeSource) elements.mapAnalysisDot.classList.add("connecting");

  if (!activeSource) {
    elements.mapAnalysisStatus.textContent = "未启动";
  } else if (analysis.paused) {
    elements.mapAnalysisStatus.textContent = "已暂停";
  } else if (analysis.connected) {
    elements.mapAnalysisStatus.textContent = "分析运行中";
  } else {
    elements.mapAnalysisStatus.textContent = analysis.message || "正在连接";
  }
  elements.mapAnalysisRoad.textContent = analysis?.segment
    ? `道路 · ${analysis.segment.name}`
    : "未选择道路";
  elements.mapAnalysisModel.textContent = analysis?.detection?.status || "模型未启用";

  elements.mapAnalysisStartButton.disabled = state.mapAnalysisBusy || !mapAnalysisCanStart();
  elements.mapAnalysisPauseButton.disabled = state.mapAnalysisBusy || !activeSource;
  elements.mapAnalysisStopButton.disabled = state.mapAnalysisBusy || !activeSource;
  setIcon(elements.mapAnalysisPauseButton, analysis?.paused ? "play" : "pause");
  elements.mapAnalysisPauseButton.title = analysis?.paused ? "继续热力分析" : "暂停热力分析";
}

async function startMapAnalysis() {
  const sourceId = elements.mapAnalysisCameraSelect.value;
  if (!mapAnalysisCanStart()) {
    showToast("请先选择已绑定道路的摄像头", "error");
    return;
  }
  state.mapAnalysisBusy = true;
  renderMapAnalysis(state.mapAnalysis);
  try {
    const analysis = await api.selectMapAnalysis(sourceId);
    renderMapAnalysis(analysis);
    if (analysis.segment) selectSegment(analysis.segment.id, true);
    showToast(`已启动 ${sourceId} 的道路热力分析`, "success");
  } finally {
    state.mapAnalysisBusy = false;
    renderMapAnalysis(state.mapAnalysis);
  }
}

async function toggleMapAnalysisPause() {
  if (!state.mapAnalysis?.active_source) return;
  state.mapAnalysisBusy = true;
  renderMapAnalysis(state.mapAnalysis);
  try {
    renderMapAnalysis(await api.pauseMapAnalysis(!state.mapAnalysis.paused));
  } finally {
    state.mapAnalysisBusy = false;
    renderMapAnalysis(state.mapAnalysis);
  }
}

async function stopMapAnalysis() {
  state.mapAnalysisBusy = true;
  renderMapAnalysis(state.mapAnalysis);
  try {
    renderMapAnalysis(await api.stopMapAnalysis());
    showToast("道路热力分析已停止", "success");
  } finally {
    state.mapAnalysisBusy = false;
    renderMapAnalysis(state.mapAnalysis);
  }
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
  state.creatingRoad = false;
  state.roadDraftPoints = null;
  state.roadDraftGeometry = "polyline";
  mapCanvas.setDraft([]);
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
  elements.roadWidth.value = Math.round(
    Number(segment?.road_width || DEFAULT_ROAD_WIDTH_PIXELS / MAP_REFERENCE_HEIGHT)
      * MAP_REFERENCE_HEIGHT,
  );
  elements.deleteRoadButton.disabled = !segment;
  updateRoadWidthEditor();
}

function updateRoadWidthEditor() {
  const segment = state.map?.segments.find((item) => item.segment_id === state.selectedSegment);
  const geometryType = mapCanvas.mode === "polygon"
    ? "polygon"
    : mapCanvas.mode === "polyline" || mapCanvas.mode === "curve"
      ? "polyline"
      : state.roadDraftPoints
        ? state.roadDraftGeometry
        : segment?.geometry_type || state.roadDraftGeometry;
  const widthPixels = Number(elements.roadWidth.value) || DEFAULT_ROAD_WIDTH_PIXELS;
  elements.roadWidthOutput.value = `${Math.round(widthPixels)} px`;
  elements.roadWidth.disabled = geometryType === "polygon";
  elements.roadWidthField.classList.toggle("disabled", geometryType === "polygon");
  mapCanvas.setRoadWidth(widthPixels / MAP_REFERENCE_HEIGHT);
}

function newRoad() {
  state.selectedSegment = "";
  state.creatingRoad = true;
  state.roadDraftPoints = null;
  state.roadDraftGeometry = "polyline";
  mapCanvas.setDraft([]);
  mapCanvas.selectSegment("");
  fillRoadEditor();
  elements.roadName.focus();
}

function startRoadDrawing(mode) {
  state.roadDraftPoints = null;
  state.roadDraftGeometry = mode === "polygon" ? "polygon" : "polyline";
  mapCanvas.setDraft([]);
  mapCanvas.startDrawing(mode);
  renderDrawMode(mode, []);
  updateRoadWidthEditor();
}

function renderDrawMode(mode, points = []) {
  const active = mode === "polyline" || mode === "curve" || mode === "polygon" || mode === "place-camera";
  elements.drawStatus.hidden = !active;
  elements.finishDrawButton.hidden = mode !== "polyline" && mode !== "polygon";
  elements.drawPolylineButton.classList.toggle("active", mode === "polyline");
  elements.drawCurveButton.classList.toggle("active", mode === "curve");
  elements.drawPolygonButton?.classList.toggle("active", mode === "polygon");
  if (mode === "place-camera") {
    elements.drawStatusText.textContent = "摄像头定位";
  } else if (mode === "curve") {
    elements.drawStatusText.textContent = `曲线节点 ${points.length}/3`;
  } else if (mode === "polygon") {
    elements.drawStatusText.textContent = `道路区域节点 ${points.length}`;
  } else if (mode === "polyline") {
    elements.drawStatusText.textContent = `折线节点 ${points.length}`;
  }
  updateRoadWidthEditor();
}

function completeRoadDrawing(points, geometryType) {
  state.roadDraftPoints = points;
  state.roadDraftGeometry = geometryType;
  mapCanvas.setDraft(points, geometryType);
  updateRoadWidthEditor();
  const label = geometryType === "polygon" ? "道路区域节点" : "道路节点";
  showToast(`已记录 ${points.length} 个${label}`, "success");
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
    geometry_type: state.roadDraftPoints
      ? state.roadDraftGeometry
      : existing?.geometry_type || "polyline",
    road_width: Number(elements.roadWidth.value) / MAP_REFERENCE_HEIGHT,
  };
  const segment = existing
    ? await api.updateSegment(existing.segment_id, payload)
    : await api.createSegment(payload);
  state.selectedSegment = segment.segment_id;
  state.creatingRoad = false;
  state.roadDraftPoints = null;
  state.roadDraftGeometry = "polyline";
  mapCanvas.setDraft([]);
  await loadMap({ syncEditors: true });
  showToast("道路配置已保存", "success");
}

async function deleteRoad() {
  if (!state.selectedSegment || !window.confirm("确定删除当前道路？")) return;
  await api.deleteSegment(state.selectedSegment);
  state.selectedSegment = "";
  state.creatingRoad = false;
  state.roadDraftPoints = null;
  state.roadDraftGeometry = "polyline";
  mapCanvas.setDraft([]);
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
  $$('[data-no-parking-tab]').forEach((button) => {
    button.addEventListener("click", () => activateNoParkingTab(button.dataset.noParkingTab));
  });

  elements.noParkingSourceSelect.addEventListener("change", renderNoParkingControls);
  elements.noParkingConnectButton.addEventListener("click", () => connectNoParkingSource().catch(reportError));
  elements.noParkingUploadButton.addEventListener("click", () => elements.noParkingFileInput.click());
  elements.noParkingFileInput.addEventListener("change", () => {
    uploadNoParkingVideo(elements.noParkingFileInput.files[0]).catch(reportError);
    elements.noParkingFileInput.value = "";
  });
  elements.noParkingReferenceButton.addEventListener("click", () => captureNoParkingReference().catch(reportError));
  elements.noParkingFeed.addEventListener("load", () => {
    elements.noParkingEmpty.hidden = true;
    if (elements.noParkingReference.hidden) noParkingCanvas.setMedia(elements.noParkingFeed);
    noParkingCanvas.draw();
  });
  elements.noParkingFeed.addEventListener("error", () => {
    if (!elements.noParkingReference.hidden) return;
    elements.noParkingEmpty.hidden = false;
    window.setTimeout(() => {
      if (state.activeView === "no-parking" && state.stream?.active_source) {
        ensureNoParkingFeed(true);
      }
    }, 1200);
  });
  elements.noParkingReference.addEventListener("load", () => {
    if (!elements.noParkingReference.hidden) noParkingCanvas.setMedia(elements.noParkingReference);
    noParkingCanvas.draw();
  });
  elements.noParkingSceneSelect.addEventListener("change", () => {
    selectNoParkingScene(elements.noParkingSceneSelect.value);
  });
  elements.noParkingNewSceneButton.addEventListener("click", newNoParkingScene);
  elements.noParkingDeleteSceneButton.addEventListener("click", () => deleteNoParkingScene().catch(reportError));
  elements.noParkingDrawButton.addEventListener("click", () => noParkingCanvas.startDrawing());
  elements.noParkingFinishDrawButton.addEventListener("click", () => {
    if (!noParkingCanvas.finishDrawing()) showToast("禁停区域至少需要三个节点", "error");
  });
  elements.noParkingUndoButton.addEventListener("click", () => noParkingCanvas.undo());
  elements.noParkingClearZoneButton.addEventListener("click", () => noParkingCanvas.clear());
  elements.noParkingConfigForm.addEventListener("submit", (event) => saveNoParkingScene(event).catch(reportError));
  elements.noParkingStartButton.addEventListener("click", () => startNoParking().catch(reportError));
  elements.noParkingStopButton.addEventListener("click", () => stopNoParking().catch(reportError));
  elements.noParkingClearEventsButton.addEventListener("click", () => clearNoParkingEvents().catch(reportError));

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
  elements.mapAnalysisCameraSelect.addEventListener("change", () => {
    elements.mapAnalysisStartButton.disabled = state.mapAnalysisBusy || !mapAnalysisCanStart();
  });
  elements.mapAnalysisStartButton.addEventListener("click", () => startMapAnalysis().catch(reportError));
  elements.mapAnalysisPauseButton.addEventListener("click", () => toggleMapAnalysisPause().catch(reportError));
  elements.mapAnalysisStopButton.addEventListener("click", () => stopMapAnalysis().catch(reportError));
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
  elements.roadWidth.addEventListener("input", updateRoadWidthEditor);
  elements.newRoadButton.addEventListener("click", newRoad);
  elements.deleteRoadButton.addEventListener("click", () => deleteRoad().catch(reportError));
  elements.drawPolylineButton.addEventListener("click", () => startRoadDrawing("polyline"));
  elements.drawCurveButton.addEventListener("click", () => startRoadDrawing("curve"));
  elements.drawPolygonButton?.addEventListener("click", () => startRoadDrawing("polygon"));
  elements.finishDrawButton.addEventListener("click", () => {
    if (!mapCanvas.finishDrawing()) {
      const message = mapCanvas.mode === "polygon"
        ? "道路区域至少需要三个节点"
        : "道路至少需要两个节点";
      showToast(message, "error");
    }
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

async function pollMapAnalysis() {
  try {
    renderMapAnalysis(await api.mapAnalysisStatus());
  } catch (error) {
    console.error(error);
  } finally {
    const interval = state.activeView === "map" ? 1000 : 3000;
    window.setTimeout(pollMapAnalysis, document.hidden ? 5000 : interval);
  }
}

async function pollNoParking() {
  if (state.activeView === "no-parking") {
    try {
      renderNoParkingStatus(await api.noParkingStatus());
    } catch (error) {
      console.error(error);
    }
  }
  const interval = state.activeView === "no-parking" ? 1000 : 4000;
  window.setTimeout(pollNoParking, document.hidden ? 5000 : interval);
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
pollMapAnalysis();
pollNoParking();
