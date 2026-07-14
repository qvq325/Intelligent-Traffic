const JSON_HEADERS = { "Content-Type": "application/json" };

async function request(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
  });

  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const payload = await response.json();
      if (typeof payload.error?.message === "string") {
        message = payload.error.message;
      } else if (typeof payload.detail === "string") {
        message = payload.detail;
      } else if (Array.isArray(payload.detail) && payload.detail[0]?.msg) {
        message = payload.detail[0].msg;
      }
    } catch {
      // Keep the HTTP fallback message.
    }
    throw new Error(message);
  }

  const contentType = response.headers.get("content-type") || "";
  return contentType.includes("application/json") ? response.json() : response;
}

function json(method, body) {
  return {
    method,
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  };
}

export const api = {
  health: () => request("/api/health"),
  system: () => request("/api/system"),
  streamStatus: () => request("/api/stream/status"),
  selectStream: (sourceId) => request("/api/stream/select", json("POST", { source_id: sourceId })),
  pauseStream: (paused) => request("/api/stream/pause", json("POST", { paused })),
  stopStream: () => request("/api/stream/stop", { method: "POST" }),
  updateDetection: (settings) => request("/api/detection/settings", json("PUT", settings)),

  uploadVideo: (file, cameraId) => {
    const form = new FormData();
    form.append("file", file);
    const query = cameraId ? `?camera_id=${encodeURIComponent(cameraId)}` : "";
    return request(`/api/stream/upload${query}`, { method: "POST", body: form });
  },

  noParking: () => request("/api/no-parking"),
  noParkingStatus: () => request("/api/no-parking/status"),
  captureNoParkingReference: (cameraId) => request(
    "/api/no-parking/reference",
    json("POST", { camera_id: cameraId }),
  ),
  saveNoParkingScene: (scene) => request(
    "/api/no-parking/scenes",
    json("POST", scene),
  ),
  deleteNoParkingScene: (sceneId) => request(
    `/api/no-parking/scenes/${encodeURIComponent(sceneId)}`,
    { method: "DELETE" },
  ),
  startNoParking: (sceneId) => request(
    "/api/no-parking/start",
    json("POST", { scene_id: sceneId }),
  ),
  stopNoParking: () => request("/api/no-parking/stop", { method: "POST" }),
  clearNoParkingEvents: () => request("/api/no-parking/events", { method: "DELETE" }),

  roadAbnormal: () => request("/api/road-abnormal"),
  roadAbnormalStatus: () => request("/api/road-abnormal/status"),
  captureRoadAbnormalReference: (cameraId) => request(
    "/api/road-abnormal/reference",
    json("POST", { camera_id: cameraId }),
  ),
  saveRoadAbnormalScene: (scene) => request(
    "/api/road-abnormal/scenes",
    json("POST", scene),
  ),
  deleteRoadAbnormalScene: (sceneId) => request(
    `/api/road-abnormal/scenes/${encodeURIComponent(sceneId)}`,
    { method: "DELETE" },
  ),
  startRoadAbnormal: (sceneId) => request(
    "/api/road-abnormal/start",
    json("POST", { scene_id: sceneId }),
  ),
  stopRoadAbnormal: () => request("/api/road-abnormal/stop", { method: "POST" }),
  clearRoadAbnormalEvents: () => request("/api/road-abnormal/events", { method: "DELETE" }),

  whitelist: () => request("/api/whitelist"),
  saveWhitelist: (entry) => request("/api/whitelist", json("POST", entry)),
  deleteWhitelist: (plate) => request(`/api/whitelist/${encodeURIComponent(plate)}`, { method: "DELETE" }),
  clearWhitelist: () => request("/api/whitelist", { method: "DELETE" }),
  setWhitelistEnabled: (enabled) => request("/api/whitelist/enabled", json("PATCH", { enabled })),

  map: () => request("/api/map"),
  mapAnalysisStatus: () => request("/api/map/analysis/status"),
  selectMapAnalysis: (sourceId) => request(
    "/api/map/analysis/select",
    json("POST", { source_id: sourceId }),
  ),
  pauseMapAnalysis: (paused) => request(
    "/api/map/analysis/pause",
    json("POST", { paused }),
  ),
  stopMapAnalysis: () => request("/api/map/analysis/stop", { method: "POST" }),
  updateCamera: (cameraId, camera) => request(
    `/api/map/cameras/${encodeURIComponent(cameraId)}`,
    json("PUT", camera),
  ),
  createSegment: (segment) => request("/api/map/segments", json("POST", segment)),
  updateSegment: (segmentId, segment) => request(
    `/api/map/segments/${encodeURIComponent(segmentId)}`,
    json("PUT", segment),
  ),
  deleteSegment: (segmentId) => request(
    `/api/map/segments/${encodeURIComponent(segmentId)}`,
    { method: "DELETE" },
  ),
  resetMap: () => request("/api/map/reset-runtime", { method: "POST" }),

  uploadMap: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/api/map/image", { method: "POST", body: form });
  },
};
