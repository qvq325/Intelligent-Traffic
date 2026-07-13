const COLORS = {
  clear: "#39b879",
  slow: "#e2b94f",
  busy: "#e5823f",
  jam: "#df5f61",
  uncovered: "#858c86",
  outline: "#121512",
  selected: "#f4cf68",
  camera: "#18aaa2",
  cameraSelected: "#e2b94f",
  amber: "#e2b94f",
  purple: "#aa82cf",
  text: "#f0f3ef",
};

const MIN_ROAD_WIDTH = 4 / 740;
const DEFAULT_ROAD_WIDTH = 36 / 740;
const MAX_ROAD_WIDTH = 120 / 740;

function clamp(value, min = 0, max = 1) {
  return Math.min(max, Math.max(min, value));
}

function distance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function heatColor(heat, covered) {
  if (!covered) return COLORS.uncovered;
  const value = clamp(heat);
  if (value < 0.35) return COLORS.clear;
  if (value < 0.65) return COLORS.slow;
  if (value < 0.85) return COLORS.busy;
  return COLORS.jam;
}

function pointToEdge(point, start, end) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const lengthSquared = dx * dx + dy * dy;
  if (!lengthSquared) return distance(point, start);
  const ratio = clamp(
    ((point.x - start.x) * dx + (point.y - start.y) * dy) / lengthSquared,
  );
  return distance(point, {
    x: start.x + dx * ratio,
    y: start.y + dy * ratio,
  });
}

function pointInPolygon(point, points) {
  let inside = false;
  for (let index = 0, previous = points.length - 1; index < points.length; previous = index, index += 1) {
    const start = points[index];
    const end = points[previous];
    const crosses = (start.y > point.y) !== (end.y > point.y)
      && point.x < ((end.x - start.x) * (point.y - start.y)) / (end.y - start.y) + start.x;
    if (crosses) inside = !inside;
  }
  return inside;
}

export class TrafficMapCanvas {
  constructor(canvas, frame, callbacks = {}) {
    this.canvas = canvas;
    this.frame = frame;
    this.context = canvas.getContext("2d");
    this.callbacks = callbacks;
    this.data = { segments: [], cameras: [], tracks: [], states: [] };
    this.selectedCamera = "";
    this.selectedSegment = "";
    this.mode = "";
    this.drawingPoints = [];
    this.draftPoints = [];
    this.draftGeometryType = "polyline";
    this.roadWidth = DEFAULT_ROAD_WIDTH;
    this.roadWidthSegment = "";
    this.cursorPoint = null;
    this.width = 1;
    this.height = 1;

    this.tooltip = document.createElement("div");
    this.tooltip.className = "canvas-tooltip";
    this.tooltip.hidden = true;
    this.frame.append(this.tooltip);

    this.canvas.tabIndex = 0;
    this.canvas.addEventListener("click", (event) => this.onClick(event));
    this.canvas.addEventListener("dblclick", (event) => this.onDoubleClick(event));
    this.canvas.addEventListener("pointermove", (event) => this.onPointerMove(event));
    this.canvas.addEventListener("pointerleave", () => this.onPointerLeave());
    this.canvas.addEventListener("keydown", (event) => this.onKeyDown(event));

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(frame);
    this.resize();
  }

  setData(data) {
    this.data = data || { segments: [], cameras: [], tracks: [], states: [] };
    if (this.selectedCamera && !this.data.cameras.some((item) => item.camera_id === this.selectedCamera)) {
      this.selectedCamera = "";
    }
    if (this.selectedSegment && !this.data.segments.some((item) => item.segment_id === this.selectedSegment)) {
      this.selectedSegment = "";
    }
    this.draw();
  }

  selectCamera(cameraId) {
    this.selectedCamera = cameraId || "";
    const camera = this.data.cameras.find((item) => item.camera_id === cameraId);
    if (camera) this.selectedSegment = camera.segment_id;
    this.draw();
  }

  selectSegment(segmentId) {
    this.selectedSegment = segmentId || "";
    this.draw();
  }

  setDraft(points, geometryType = "polyline") {
    this.draftPoints = (points || []).map((point) => ({ x: point[0], y: point[1] }));
    this.draftGeometryType = geometryType;
    this.draw();
  }

  setRoadWidth(width) {
    const normalized = Number(width);
    this.roadWidth = Number.isFinite(normalized)
      ? clamp(normalized, MIN_ROAD_WIDTH, MAX_ROAD_WIDTH)
      : DEFAULT_ROAD_WIDTH;
    this.roadWidthSegment = this.selectedSegment;
    this.draw();
  }

  startPlacement() {
    this.mode = "place-camera";
    this.drawingPoints = [];
    this.frame.classList.add("placing");
    this.frame.classList.remove("drawing");
    this.canvas.focus();
    this.draw();
  }

  startDrawing(mode) {
    this.mode = mode;
    this.drawingPoints = [];
    this.cursorPoint = null;
    this.frame.classList.add("drawing");
    this.frame.classList.remove("placing");
    this.canvas.focus();
    this.draw();
  }

  cancelMode() {
    this.mode = "";
    this.drawingPoints = [];
    this.cursorPoint = null;
    this.frame.classList.remove("drawing", "placing");
    this.callbacks.onModeChange?.(this.mode, []);
    this.draw();
  }

  finishDrawing() {
    if (!this.mode || this.mode === "place-camera") return false;
    const geometryType = this.mode === "polygon" ? "polygon" : "polyline";
    const minimumPoints = geometryType === "polygon" ? 3 : 2;
    if (this.drawingPoints.length < minimumPoints) return false;
    const points = this.drawingPoints.map((point) => [point.x, point.y]);
    this.mode = "";
    this.drawingPoints = [];
    this.cursorPoint = null;
    this.frame.classList.remove("drawing", "placing");
    this.callbacks.onDrawComplete?.(points, geometryType);
    this.callbacks.onModeChange?.(this.mode, []);
    this.draw();
    return true;
  }

  resize() {
    const bounds = this.frame.getBoundingClientRect();
    const width = Math.max(1, Math.round(bounds.width));
    const height = Math.max(1, Math.round(bounds.height));
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    this.canvas.width = Math.round(width * ratio);
    this.canvas.height = Math.round(height * ratio);
    this.canvas.style.width = `${width}px`;
    this.canvas.style.height = `${height}px`;
    this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.width = width;
    this.height = height;
    this.draw();
  }

  normalizedPoint(event) {
    const bounds = this.canvas.getBoundingClientRect();
    return {
      x: clamp((event.clientX - bounds.left) / Math.max(1, bounds.width)),
      y: clamp((event.clientY - bounds.top) / Math.max(1, bounds.height)),
    };
  }

  canvasPoint(point) {
    return { x: point[0] * this.width, y: point[1] * this.height };
  }

  onClick(event) {
    if (event.detail > 1) return;
    const point = this.normalizedPoint(event);
    if (this.mode === "place-camera") {
      this.callbacks.onCameraPlaced?.(point);
      this.cancelMode();
      return;
    }
    if (this.mode === "polyline" || this.mode === "curve" || this.mode === "polygon") {
      this.drawingPoints.push(point);
      this.callbacks.onModeChange?.(this.mode, this.drawingPoints);
      if (this.mode === "curve" && this.drawingPoints.length === 3) {
        this.drawingPoints = this.quadraticPoints(this.drawingPoints);
        this.finishDrawing();
      } else {
        this.draw();
      }
      return;
    }

    const camera = this.nearestCamera(point, 18);
    if (camera) {
      this.selectCamera(camera.camera_id);
      this.callbacks.onCameraSelect?.(camera.camera_id);
      return;
    }
    const segment = this.nearestSegment(point, 12);
    if (segment) {
      this.selectSegment(segment.segment_id);
      this.callbacks.onSegmentSelect?.(segment.segment_id);
    }
  }

  onDoubleClick(event) {
    if (this.mode !== "polyline" && this.mode !== "polygon") return;
    event.preventDefault();
    this.finishDrawing();
  }

  onPointerMove(event) {
    const point = this.normalizedPoint(event);
    if (this.mode === "polyline" || this.mode === "curve" || this.mode === "polygon") {
      this.cursorPoint = point;
      this.tooltip.hidden = true;
      this.draw();
      return;
    }

    const camera = this.nearestCamera(point, 14);
    if (!camera) {
      this.tooltip.hidden = true;
      return;
    }
    const segment = this.data.segments.find((item) => item.segment_id === camera.segment_id);
    this.tooltip.textContent = `${camera.camera_id} · ${segment?.name || "未关联"} · ${camera.heading.toFixed(0)}°`;
    const bounds = this.frame.getBoundingClientRect();
    const x = Math.min(event.clientX - bounds.left + 12, bounds.width - 220);
    const y = Math.min(event.clientY - bounds.top + 12, bounds.height - 42);
    this.tooltip.style.left = `${Math.max(8, x)}px`;
    this.tooltip.style.top = `${Math.max(8, y)}px`;
    this.tooltip.hidden = false;
  }

  onPointerLeave() {
    this.tooltip.hidden = true;
    if (this.cursorPoint) {
      this.cursorPoint = null;
      this.draw();
    }
  }

  onKeyDown(event) {
    if (!this.mode) return;
    if (event.key === "Escape") {
      event.preventDefault();
      this.cancelMode();
    } else if (event.key === "Enter") {
      event.preventDefault();
      this.finishDrawing();
    } else if (event.key === "Backspace" && this.drawingPoints.length) {
      event.preventDefault();
      this.drawingPoints.pop();
      this.callbacks.onModeChange?.(this.mode, this.drawingPoints);
      this.draw();
    }
  }

  nearestCamera(point, maxPixels) {
    const target = { x: point.x * this.width, y: point.y * this.height };
    let match = null;
    let best = maxPixels;
    for (const camera of this.data.cameras) {
      const candidate = this.canvasPoint([camera.x, camera.y]);
      const candidateDistance = distance(target, candidate);
      if (candidateDistance <= best) {
        best = candidateDistance;
        match = camera;
      }
    }
    return match;
  }

  nearestSegment(point, maxPixels) {
    const target = { x: point.x * this.width, y: point.y * this.height };
    let match = null;
    let best = Number.POSITIVE_INFINITY;
    for (const segment of this.data.segments) {
      const points = segment.points.map((item) => this.canvasPoint(item));
      const isPolygon = segment.geometry_type === "polygon";
      if (isPolygon && pointInPolygon(target, points)) return segment;
      const hitRadius = maxPixels + (isPolygon ? 0 : this.roadWidthPixels(segment) / 2);
      const edgeCount = isPolygon ? points.length : points.length - 1;
      for (let index = 0; index < edgeCount; index += 1) {
        const start = points[index];
        const end = points[(index + 1) % points.length];
        const candidateDistance = pointToEdge(target, start, end);
        if (candidateDistance <= hitRadius && candidateDistance < best) {
          best = candidateDistance;
          match = segment;
        }
      }
    }
    return match;
  }

  quadraticPoints(controlPoints) {
    const [start, control, end] = controlPoints;
    return Array.from({ length: 25 }, (_, index) => {
      const t = index / 24;
      const inverse = 1 - t;
      return {
        x: inverse * inverse * start.x + 2 * inverse * t * control.x + t * t * end.x,
        y: inverse * inverse * start.y + 2 * inverse * t * control.y + t * t * end.y,
      };
    });
  }

  roadWidthPixels(segment = null) {
    const storedWidth = Number(segment?.road_width);
    const width = !segment || segment.segment_id === this.roadWidthSegment
      ? this.roadWidth
      : Number.isFinite(storedWidth)
        ? storedWidth
        : DEFAULT_ROAD_WIDTH;
    return clamp(width, MIN_ROAD_WIDTH, MAX_ROAD_WIDTH) * this.height;
  }

  draw() {
    const context = this.context;
    context.clearRect(0, 0, this.width, this.height);
    const states = new Map(this.data.states.map((item) => [item.segment_id, item]));
    const covered = new Set(this.data.cameras.map((item) => item.segment_id));

    for (const segment of this.data.segments) {
      if (segment.points.length < 2) continue;
      const color = heatColor(states.get(segment.segment_id)?.heat || 0, covered.has(segment.segment_id));
      const selected = segment.segment_id === this.selectedSegment;
      if (segment.geometry_type === "polygon" && segment.points.length >= 3) {
        this.fillSegment(segment, color, selected);
      } else {
        const roadWidth = this.roadWidthPixels(segment);
        if (selected) this.strokeSegment(segment, COLORS.selected, roadWidth + 10, []);
        this.strokeSegment(segment, COLORS.outline, roadWidth + 4, []);
        this.strokeSegment(segment, color, roadWidth, segment.level === "bridge" ? [8, 6] : []);
        if (segment.direction !== "双向") this.drawDirection(segment, color);
      }
      if (selected) this.drawSegmentLabel(segment);
    }

    this.drawDraft();
    for (const track of this.data.tracks) this.drawTrack(track);
    for (const camera of this.data.cameras) this.drawCamera(camera);
    this.drawPreview();
  }

  strokeSegment(segment, color, width, dash) {
    const context = this.context;
    context.save();
    context.beginPath();
    segment.points.forEach((point, index) => {
      const current = this.canvasPoint(point);
      if (index === 0) context.moveTo(current.x, current.y);
      else context.lineTo(current.x, current.y);
    });
    context.strokeStyle = color;
    context.lineWidth = width;
    context.lineCap = "round";
    context.lineJoin = "round";
    context.setLineDash(dash);
    context.stroke();
    context.restore();
  }

  fillSegment(segment, color, selected) {
    const context = this.context;
    context.save();
    context.beginPath();
    segment.points.forEach((point, index) => {
      const current = this.canvasPoint(point);
      if (index === 0) context.moveTo(current.x, current.y);
      else context.lineTo(current.x, current.y);
    });
    context.closePath();
    if (selected) {
      context.strokeStyle = COLORS.selected;
      context.lineWidth = 7;
      context.lineJoin = "round";
      context.stroke();
    }
    context.globalAlpha = 0.48;
    context.fillStyle = color;
    context.fill();
    context.globalAlpha = 1;
    context.strokeStyle = COLORS.outline;
    context.lineWidth = 2;
    context.lineJoin = "round";
    context.setLineDash(segment.level === "bridge" ? [8, 6] : []);
    context.stroke();
    context.restore();
  }

  drawDirection(segment, color) {
    if (segment.points.length < 2) return;
    let longest = null;
    for (let index = 0; index < segment.points.length - 1; index += 1) {
      const start = this.canvasPoint(segment.points[index]);
      const end = this.canvasPoint(segment.points[index + 1]);
      const length = distance(start, end);
      if (!longest || length > longest.length) longest = { start, end, length };
    }
    if (!longest || longest.length < 14) return;
    const ux = (longest.end.x - longest.start.x) / longest.length;
    const uy = (longest.end.y - longest.start.y) / longest.length;
    const center = {
      x: (longest.start.x + longest.end.x) / 2,
      y: (longest.start.y + longest.end.y) / 2,
    };
    const context = this.context;
    context.save();
    context.beginPath();
    context.moveTo(center.x + ux * 7, center.y + uy * 7);
    context.lineTo(center.x - ux * 5 - uy * 4, center.y - uy * 5 + ux * 4);
    context.lineTo(center.x - ux * 5 + uy * 4, center.y - uy * 5 - ux * 4);
    context.closePath();
    context.fillStyle = color;
    context.strokeStyle = COLORS.outline;
    context.lineWidth = 1;
    context.fill();
    context.stroke();
    context.restore();
  }

  drawSegmentLabel(segment) {
    const point = this.canvasPoint(segment.points[Math.floor(segment.points.length / 2)]);
    const label = `${segment.name} · ${segment.direction}`;
    const context = this.context;
    context.save();
    context.font = '600 11px "Microsoft YaHei UI", sans-serif';
    const labelWidth = context.measureText(label).width;
    let labelX = point.x + 8;
    if (labelX + labelWidth > this.width - 8) {
      labelX = point.x - labelWidth - 8;
    }
    labelX = clamp(labelX, 8, Math.max(8, this.width - labelWidth - 8));
    const labelY = clamp(point.y - 8, 14, this.height - 8);
    context.lineWidth = 3;
    context.strokeStyle = "#121512";
    context.fillStyle = "#ffe29a";
    context.strokeText(label, labelX, labelY);
    context.fillText(label, labelX, labelY);
    context.restore();
  }

  drawTrack(track) {
    const palette = {
      car: "#31b8e3",
      motorcycle: COLORS.amber,
      bus: "#ef756c",
      truck: COLORS.purple,
    };
    const color = palette[track.vehicle_class] || COLORS.text;
    const context = this.context;
    if (track.history?.length > 1) {
      context.save();
      context.beginPath();
      track.history.forEach((point, index) => {
        const current = this.canvasPoint(point);
        if (index === 0) context.moveTo(current.x, current.y);
        else context.lineTo(current.x, current.y);
      });
      context.strokeStyle = color;
      context.globalAlpha = 0.55;
      context.lineWidth = 2;
      context.stroke();
      context.restore();
    }

    const point = this.canvasPoint([track.x, track.y]);
    context.save();
    context.beginPath();
    context.arc(point.x, point.y, 6, 0, Math.PI * 2);
    context.fillStyle = color;
    context.strokeStyle = COLORS.outline;
    context.lineWidth = 2;
    context.fill();
    context.stroke();
    context.font = '10px "Microsoft YaHei UI", sans-serif';
    context.lineWidth = 3;
    context.strokeStyle = COLORS.outline;
    context.fillStyle = COLORS.text;
    const label = track.plate_text || track.global_id;
    context.strokeText(label, point.x + 8, point.y + 4);
    context.fillText(label, point.x + 8, point.y + 4);
    context.restore();
  }

  drawCamera(camera) {
    const selected = camera.camera_id === this.selectedCamera;
    const point = this.canvasPoint([camera.x, camera.y]);
    const angle = (camera.heading * Math.PI) / 180 - Math.PI / 2;
    if (selected) {
      const radius = camera.view_range * this.height;
      const context = this.context;
      context.save();
      context.beginPath();
      context.moveTo(point.x, point.y);
      context.arc(point.x, point.y, radius, angle - 0.42, angle + 0.42);
      context.closePath();
      context.fillStyle = "rgb(24 170 162 / 18%)";
      context.strokeStyle = "rgb(24 170 162 / 75%)";
      context.lineWidth = 1;
      context.fill();
      context.stroke();
      context.restore();
    }

    const context = this.context;
    context.save();
    context.beginPath();
    context.arc(point.x, point.y, selected ? 7 : 6, 0, Math.PI * 2);
    context.fillStyle = selected ? COLORS.cameraSelected : COLORS.camera;
    context.strokeStyle = COLORS.outline;
    context.lineWidth = 2;
    context.fill();
    context.stroke();
    if (selected) {
      context.font = '600 10px "Microsoft YaHei UI", sans-serif';
      context.lineWidth = 3;
      context.strokeStyle = COLORS.outline;
      context.fillStyle = COLORS.text;
      context.strokeText(camera.camera_id, point.x + 9, point.y - 7);
      context.fillText(camera.camera_id, point.x + 9, point.y - 7);
    }
    context.restore();
  }

  drawPreview() {
    if (this.mode !== "polyline" && this.mode !== "curve" && this.mode !== "polygon") return;
    const points = [...this.drawingPoints];
    if (this.cursorPoint) points.push(this.cursorPoint);
    const context = this.context;
    context.save();
    context.strokeStyle = COLORS.selected;
    context.fillStyle = COLORS.selected;
    context.lineCap = "round";
    context.lineJoin = "round";
    if (points.length > 1) {
      context.beginPath();
      points.forEach((point, index) => {
        const current = { x: point.x * this.width, y: point.y * this.height };
        if (index === 0) context.moveTo(current.x, current.y);
        else context.lineTo(current.x, current.y);
      });
      if (this.mode === "polygon" && points.length >= 3) {
        context.closePath();
        context.globalAlpha = 0.25;
        context.fill();
        context.globalAlpha = 1;
        context.lineWidth = 3;
        context.setLineDash([7, 5]);
        context.stroke();
      } else if (this.mode === "polygon") {
        context.lineWidth = 3;
        context.setLineDash([7, 5]);
        context.stroke();
      } else {
        context.globalAlpha = 0.3;
        context.lineWidth = this.roadWidthPixels();
        context.setLineDash([]);
        context.stroke();
        context.globalAlpha = 1;
        context.lineWidth = 2;
        context.setLineDash([7, 5]);
        context.stroke();
      }
    }
    context.setLineDash([]);
    for (const point of this.drawingPoints) {
      context.beginPath();
      context.arc(point.x * this.width, point.y * this.height, 4, 0, Math.PI * 2);
      context.fill();
    }
    context.restore();
  }

  drawDraft() {
    const isPolygon = this.draftGeometryType === "polygon";
    if (this.draftPoints.length < (isPolygon ? 3 : 2)) return;
    const context = this.context;
    context.save();
    context.beginPath();
    this.draftPoints.forEach((point, index) => {
      const current = { x: point.x * this.width, y: point.y * this.height };
      if (index === 0) context.moveTo(current.x, current.y);
      else context.lineTo(current.x, current.y);
    });
    if (isPolygon) {
      context.closePath();
      context.globalAlpha = 0.25;
      context.fillStyle = COLORS.selected;
      context.fill();
      context.globalAlpha = 1;
    }
    context.strokeStyle = COLORS.selected;
    context.lineCap = "round";
    context.lineJoin = "round";
    if (isPolygon) {
      context.lineWidth = 5;
      context.setLineDash([9, 6]);
      context.stroke();
    } else {
      context.globalAlpha = 0.3;
      context.lineWidth = this.roadWidthPixels();
      context.setLineDash([]);
      context.stroke();
      context.globalAlpha = 1;
      context.lineWidth = 2;
      context.setLineDash([9, 6]);
      context.stroke();
    }
    context.restore();
  }
}
