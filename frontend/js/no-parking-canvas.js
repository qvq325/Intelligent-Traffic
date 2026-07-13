const clamp = (value, minimum = 0, maximum = 1) => Math.min(maximum, Math.max(minimum, value));

export class VideoRegionEditor {
  constructor(canvas, frame, callbacks = {}) {
    this.canvas = canvas;
    this.frame = frame;
    this.context = canvas.getContext("2d");
    this.callbacks = callbacks;
    this.labels = {
      idle: callbacks.labels?.idle || "禁停区域",
      running: callbacks.labels?.running || "监测中",
      alarm: callbacks.labels?.alarm || "违规停留",
    };
    this.media = null;
    this.points = [];
    this.previousPoints = [];
    this.cursor = null;
    this.drawing = false;
    this.running = false;
    this.alarm = false;
    this.width = 1;
    this.height = 1;

    this.canvas.tabIndex = 0;
    this.canvas.addEventListener("click", (event) => this.onClick(event));
    this.canvas.addEventListener("dblclick", (event) => {
      event.preventDefault();
      this.finishDrawing();
    });
    this.canvas.addEventListener("pointermove", (event) => this.onPointerMove(event));
    this.canvas.addEventListener("pointerleave", () => {
      this.cursor = null;
      this.draw();
    });
    this.canvas.addEventListener("keydown", (event) => this.onKeyDown(event));

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(frame);
    this.resize();
  }

  setMedia(media) {
    this.media = media || null;
    this.draw();
  }

  setPoints(points) {
    this.points = (points || []).map(([x, y]) => ({ x: clamp(Number(x)), y: clamp(Number(y)) }));
    this.previousPoints = [];
    this.draw();
  }

  getPoints() {
    return this.points.map((point) => [point.x, point.y]);
  }

  setActivity({ running = false, alarm = false } = {}) {
    this.running = Boolean(running);
    this.alarm = Boolean(alarm);
    this.draw();
  }

  startDrawing() {
    if (this.drawing) return;
    this.previousPoints = this.points.map((point) => ({ ...point }));
    this.points = [];
    this.cursor = null;
    this.drawing = true;
    this.frame.classList.add("drawing");
    this.canvas.focus();
    this.callbacks.onModeChange?.(true);
    this.callbacks.onChange?.(this.getPoints());
    this.draw();
  }

  finishDrawing() {
    if (!this.drawing || this.points.length < 3) return false;
    this.drawing = false;
    this.previousPoints = [];
    this.cursor = null;
    this.frame.classList.remove("drawing");
    const points = this.getPoints();
    this.callbacks.onComplete?.(points);
    this.callbacks.onModeChange?.(false);
    this.draw();
    return true;
  }

  cancelDrawing() {
    if (!this.drawing) return;
    this.points = this.previousPoints.map((point) => ({ ...point }));
    this.previousPoints = [];
    this.cursor = null;
    this.drawing = false;
    this.frame.classList.remove("drawing");
    this.callbacks.onChange?.(this.getPoints());
    this.callbacks.onModeChange?.(false);
    this.draw();
  }

  undo() {
    if (!this.points.length) return;
    this.points.pop();
    this.callbacks.onChange?.(this.getPoints());
    this.draw();
  }

  clear() {
    this.points = [];
    this.previousPoints = [];
    this.callbacks.onChange?.([]);
    this.draw();
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

  mediaRect() {
    const sourceWidth = Number(this.media?.naturalWidth || 0);
    const sourceHeight = Number(this.media?.naturalHeight || 0);
    if (!sourceWidth || !sourceHeight) {
      return { left: 0, top: 0, width: this.width, height: this.height };
    }
    const scale = Math.min(this.width / sourceWidth, this.height / sourceHeight);
    const width = sourceWidth * scale;
    const height = sourceHeight * scale;
    return {
      left: (this.width - width) / 2,
      top: (this.height - height) / 2,
      width,
      height,
    };
  }

  normalizedPoint(event) {
    const bounds = this.canvas.getBoundingClientRect();
    const rect = this.mediaRect();
    const x = event.clientX - bounds.left - rect.left;
    const y = event.clientY - bounds.top - rect.top;
    if (x < 0 || y < 0 || x > rect.width || y > rect.height) return null;
    return { x: clamp(x / Math.max(1, rect.width)), y: clamp(y / Math.max(1, rect.height)) };
  }

  canvasPoint(point) {
    const rect = this.mediaRect();
    return {
      x: rect.left + point.x * rect.width,
      y: rect.top + point.y * rect.height,
    };
  }

  onClick(event) {
    if (!this.drawing || event.detail > 1) return;
    const point = this.normalizedPoint(event);
    if (!point) return;
    this.points.push(point);
    this.callbacks.onChange?.(this.getPoints());
    this.draw();
  }

  onPointerMove(event) {
    if (!this.drawing) return;
    this.cursor = this.normalizedPoint(event);
    this.draw();
  }

  onKeyDown(event) {
    if (!this.drawing) return;
    if (event.key === "Enter") {
      event.preventDefault();
      this.finishDrawing();
    } else if (event.key === "Escape") {
      event.preventDefault();
      this.cancelDrawing();
    } else if (event.key === "Backspace" || event.key === "Delete") {
      event.preventDefault();
      this.undo();
    }
  }

  draw() {
    this.context.clearRect(0, 0, this.width, this.height);
    if (!this.points.length && !this.cursor) return;

    const points = this.points.map((point) => this.canvasPoint(point));
    const color = this.alarm ? "#df5f61" : this.running ? "#e2b94f" : "#ef7a72";
    if (points.length) {
      this.context.beginPath();
      this.context.moveTo(points[0].x, points[0].y);
      for (const point of points.slice(1)) this.context.lineTo(point.x, point.y);
      if (!this.drawing && points.length >= 3) this.context.closePath();
      this.context.fillStyle = this.alarm ? "rgb(223 95 97 / 25%)" : "rgb(223 95 97 / 15%)";
      if (points.length >= 3) this.context.fill();
      this.context.strokeStyle = color;
      this.context.lineWidth = this.alarm ? 3 : 2;
      this.context.setLineDash(this.running ? [9, 5] : []);
      this.context.stroke();
      this.context.setLineDash([]);
    }

    if (this.drawing && this.cursor && points.length) {
      const cursor = this.canvasPoint(this.cursor);
      const last = points[points.length - 1];
      this.context.beginPath();
      this.context.moveTo(last.x, last.y);
      this.context.lineTo(cursor.x, cursor.y);
      this.context.strokeStyle = "rgb(238 241 237 / 72%)";
      this.context.lineWidth = 1;
      this.context.setLineDash([5, 5]);
      this.context.stroke();
      this.context.setLineDash([]);
    }

    for (const point of points) {
      this.context.beginPath();
      this.context.arc(point.x, point.y, 4, 0, Math.PI * 2);
      this.context.fillStyle = "#eef1ed";
      this.context.fill();
      this.context.strokeStyle = color;
      this.context.lineWidth = 2;
      this.context.stroke();
    }

    if (!this.drawing && points.length >= 3) {
      const left = Math.min(...points.map((point) => point.x));
      const top = Math.min(...points.map((point) => point.y));
      const label = this.labels[this.alarm ? "alarm" : this.running ? "running" : "idle"];
      this.context.font = '600 12px "Microsoft YaHei UI", sans-serif';
      const width = this.context.measureText(label).width + 16;
      this.context.fillStyle = color;
      this.context.fillRect(left, Math.max(0, top - 26), width, 22);
      this.context.fillStyle = "#111310";
      this.context.fillText(label, left + 8, Math.max(15, top - 11));
    }
  }
}
