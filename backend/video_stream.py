"""Threaded video capture, inference, and MJPEG frame publication."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np

from detection_processor import DetectionProcessor, DetectionResult
from whitelist_manager import WhitelistManager


DetectionCallback = Callable[[str, list[DetectionResult], tuple[int, int]], None]
FrameProcessor = Callable[[str, np.ndarray], np.ndarray]


@dataclass(frozen=True, slots=True)
class StreamSource:
    source_id: str
    name: str
    display_name: str
    url: str


@dataclass(slots=True)
class DetectionSettings:
    enabled: bool = False
    yolo_threshold: float = 0.5
    lpr_threshold: float = 0.7
    interval: int = 5
    device: str = "cpu"


def serialize_detection(result: DetectionResult) -> dict:
    payload = asdict(result)
    payload["has_plate"] = result.has_plate
    return payload


class VideoStreamService:
    """Owns the capture thread and exposes thread-safe control methods."""

    def __init__(
        self,
        whitelist_manager: WhitelistManager,
        on_detections: DetectionCallback | None = None,
        frame_processor: FrameProcessor | None = None,
    ) -> None:
        self.whitelist_manager = whitelist_manager
        self.on_detections = on_detections
        self.frame_processor = frame_processor
        self._detection_listeners: list[DetectionCallback] = []

        self._condition = threading.Condition(threading.RLock())
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._requested_source: StreamSource | None = None
        self._source_revision = 0
        self._paused = False
        self._connected = False
        self._message = "就绪"

        self._settings = DetectionSettings()
        self._settings_revision = 0
        self._processor: DetectionProcessor | None = None
        self._processor_device = ""
        self._detection_status = "未启用"

        self._latest_jpeg: bytes | None = None
        self._frame_sequence = 0
        self._resolution: tuple[int, int] | None = None
        self._display_fps = 0.0
        self._last_publish_time = 0.0
        self._results: list[dict] = []

    def start(self) -> None:
        with self._condition:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="video-stream-worker",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 8.0) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        with self._condition:
            self._connected = False
            self._message = "服务已停止"
            self._condition.notify_all()

    def select_source(
        self,
        source_id: str,
        name: str,
        url: str,
        display_name: str | None = None,
    ) -> None:
        source = StreamSource(
            source_id=source_id,
            name=name,
            display_name=display_name or name,
            url=url,
        )
        with self._condition:
            self._requested_source = source
            self._source_revision += 1
            self._paused = False
            self._connected = False
            self._message = f"正在连接 {source.display_name} ..."
            self._clear_frame_locked()
            self._condition.notify_all()

    def stop_stream(self) -> None:
        with self._condition:
            self._requested_source = None
            self._source_revision += 1
            self._paused = False
            self._connected = False
            self._message = "视频流已停止"
            self._clear_frame_locked()
            self._condition.notify_all()

    def restart_source(self) -> bool:
        """Reopen the selected source, primarily for completed local files."""
        with self._condition:
            if self._requested_source is None:
                return False
            self._source_revision += 1
            self._paused = False
            self._connected = False
            self._message = f"正在重新打开 {self._requested_source.display_name} ..."
            self._clear_frame_locked()
            self._condition.notify_all()
            return True

    def set_paused(self, paused: bool) -> None:
        with self._condition:
            self._paused = bool(paused)
            source = self._requested_source
            if source:
                action = "已暂停" if self._paused else "继续播放"
                self._message = f"{action}: {source.display_name}"
            self._condition.notify_all()

    def is_paused(self) -> bool:
        with self._condition:
            return self._paused

    def update_detection_settings(
        self,
        *,
        enabled: bool | None = None,
        yolo_threshold: float | None = None,
        lpr_threshold: float | None = None,
        interval: int | None = None,
        device: str | None = None,
    ) -> None:
        with self._condition:
            if enabled is not None:
                self._settings.enabled = bool(enabled)
            if yolo_threshold is not None:
                self._settings.yolo_threshold = max(0.05, min(1.0, float(yolo_threshold)))
            if lpr_threshold is not None:
                self._settings.lpr_threshold = max(0.05, min(1.0, float(lpr_threshold)))
            if interval is not None:
                self._settings.interval = max(1, min(60, int(interval)))
            if device is not None:
                self._settings.device = device
            self._settings_revision += 1
            if not self._settings.enabled:
                self._detection_status = "未启用"
                self._results = []
            elif self._processor is None or self._processor_device != self._settings.device:
                self._detection_status = "等待加载模型"
            self._condition.notify_all()

    def add_detection_listener(self, listener: DetectionCallback) -> None:
        """Register an additional consumer without changing the primary callback."""
        with self._condition:
            if listener not in self._detection_listeners:
                self._detection_listeners.append(listener)

    def status(self) -> dict:
        with self._condition:
            source = self._requested_source
            results = [dict(result) for result in self._results]
            settings = asdict(self._settings)
            resolution = (
                {"width": self._resolution[0], "height": self._resolution[1]}
                if self._resolution
                else None
            )
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "connected": self._connected,
                "paused": self._paused,
                "message": self._message,
                "active_source": (
                    {
                        "id": source.source_id,
                        "name": source.name,
                        "display_name": source.display_name,
                        "local": self._is_local_file(source.url),
                    }
                    if source
                    else None
                ),
                "resolution": resolution,
                "fps": round(self._display_fps, 1),
                "frame_sequence": self._frame_sequence,
                "detection": {
                    **settings,
                    "status": self._detection_status,
                },
                "results": results,
                "metrics": {
                    "vehicles": len(results),
                    "plates": sum(1 for item in results if item["has_plate"]),
                    "whitelisted": sum(1 for item in results if item["whitelisted"]),
                },
            }

    def wait_for_frame(
        self,
        after_sequence: int,
        timeout: float = 10.0,
    ) -> tuple[int, bytes | None]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while (
                self._frame_sequence <= after_sequence
                and not self._stop_event.is_set()
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            return self._frame_sequence, self._latest_jpeg

    def latest_frame(self) -> bytes | None:
        with self._condition:
            return self._latest_jpeg

    def _clear_frame_locked(self) -> None:
        self._latest_jpeg = None
        self._resolution = None
        self._display_fps = 0.0
        self._last_publish_time = 0.0
        self._results = []
        self._frame_sequence += 1

    def _run(self) -> None:
        capture: cv2.VideoCapture | None = None
        local_revision = -1
        active_source: StreamSource | None = None
        frame_count = 0
        source_fps = 0.0
        last_results: list[DetectionResult] = []

        while not self._stop_event.is_set():
            self._ensure_processor()

            with self._condition:
                requested_revision = self._source_revision
                requested_source = self._requested_source
                paused = self._paused

            if requested_revision != local_revision:
                if capture is not None:
                    capture.release()
                capture = None
                local_revision = requested_revision
                active_source = requested_source
                frame_count = 0
                source_fps = 0.0
                last_results = []
                if self._processor:
                    self._processor.reset_tracking()
                if active_source is not None:
                    capture = self._open_capture(active_source)
                    if capture is not None and self._is_local_file(active_source.url):
                        source_fps = capture.get(cv2.CAP_PROP_FPS) or 30.0

            if capture is None:
                self._stop_event.wait(0.1)
                continue

            if paused:
                self._stop_event.wait(0.05)
                continue

            frame_started = time.monotonic()
            try:
                ok, frame = capture.read()
            except Exception as exc:
                ok, frame = False, None
                self._set_connection(False, f"读取视频异常: {exc}")

            if not ok or frame is None:
                capture.release()
                capture = None
                if active_source and self._is_local_file(active_source.url):
                    self._set_connection(False, "本地视频播放完毕")
                    continue
                self._set_connection(False, "读取帧失败，正在重连...")
                if self._stop_event.wait(1.0):
                    break
                with self._condition:
                    source_still_active = local_revision == self._source_revision
                if source_still_active and active_source:
                    capture = self._open_capture(active_source)
                continue

            frame_count += 1
            annotated = frame
            settings = self._settings_snapshot()
            processor = self._processor
            if (
                settings.enabled
                and processor is not None
                and processor.is_initialized
                and frame_count % settings.interval == 0
            ):
                try:
                    processor.yolo_threshold = settings.yolo_threshold
                    processor.lpr_threshold = settings.lpr_threshold
                    annotated, last_results = processor.process(
                        frame,
                        camera_id=active_source.name if active_source else "",
                    )
                    self._publish_results(active_source, last_results, frame)
                except Exception as exc:
                    self._set_detection_status(f"检测异常: {exc}")
                    annotated = frame
            elif settings.enabled and last_results:
                annotated = self._draw_cached_results(frame, last_results)

            if active_source is not None and self.frame_processor is not None:
                try:
                    annotated = self.frame_processor(active_source.name, annotated)
                except Exception as exc:
                    self._set_detection_status(f"画面分析异常: {exc}")

            self._publish_frame(annotated)

            if active_source and self._is_local_file(active_source.url) and source_fps > 0:
                elapsed = time.monotonic() - frame_started
                self._stop_event.wait(max(0.001, (1.0 / source_fps) - elapsed))
            else:
                self._stop_event.wait(0.005)

        if capture is not None:
            capture.release()

    def _settings_snapshot(self) -> DetectionSettings:
        with self._condition:
            return DetectionSettings(**asdict(self._settings))

    def _ensure_processor(self) -> None:
        settings = self._settings_snapshot()
        if not settings.enabled:
            return
        if self._processor and self._processor_device == settings.device:
            return

        with self._condition:
            revision = self._settings_revision
            self._detection_status = f"正在加载模型 (device={settings.device})"

        processor = DetectionProcessor(
            yolo_conf=settings.yolo_threshold,
            lpr_conf=settings.lpr_threshold,
            device=settings.device,
        )
        processor.whitelist_manager = self.whitelist_manager
        success = processor.initialize()

        with self._condition:
            current_device = self._settings.device
            if revision != self._settings_revision and current_device != settings.device:
                return
            if success:
                self._processor = processor
                self._processor_device = settings.device
                lpr_status = "可用" if processor.has_lpr else "不可用"
                self._detection_status = f"模型已就绪 · LPR {lpr_status}"
            else:
                self._settings.enabled = False
                self._detection_status = f"模型加载失败: {processor.init_error}"

    def _publish_results(
        self,
        source: StreamSource | None,
        results: Iterable[DetectionResult],
        frame: np.ndarray,
    ) -> None:
        result_list = list(results)
        with self._condition:
            self._results = [serialize_detection(result) for result in result_list]
            listeners = list(self._detection_listeners)
            primary_callback = self.on_detections
        if source is None:
            return
        height, width = frame.shape[:2]
        callbacks = ([primary_callback] if primary_callback else []) + listeners
        for callback in callbacks:
            try:
                callback(source.name, result_list, (width, height))
            except Exception as exc:
                self._set_detection_status(f"检测结果处理异常: {exc}")

    def _publish_frame(self, frame: np.ndarray) -> None:
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), 86],
        )
        if not ok:
            return
        now = time.monotonic()
        height, width = frame.shape[:2]
        with self._condition:
            if self._last_publish_time:
                instant_fps = 1.0 / max(0.001, now - self._last_publish_time)
                self._display_fps = (
                    instant_fps
                    if not self._display_fps
                    else self._display_fps * 0.82 + instant_fps * 0.18
                )
            self._last_publish_time = now
            self._latest_jpeg = encoded.tobytes()
            self._resolution = (width, height)
            self._frame_sequence += 1
            self._condition.notify_all()

    def _open_capture(self, source: StreamSource) -> cv2.VideoCapture | None:
        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
            5000,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC,
            3000,
        ]
        decoder = "CPU FFmpeg"
        if os.name == "nt" and hasattr(cv2, "VIDEO_ACCELERATION_D3D11"):
            params.extend(
                [
                    cv2.CAP_PROP_HW_ACCELERATION,
                    cv2.VIDEO_ACCELERATION_D3D11,
                    cv2.CAP_PROP_HW_DEVICE,
                    0,
                ]
            )
            decoder = "GPU D3D11VA"

        try:
            capture = cv2.VideoCapture(source.url, cv2.CAP_FFMPEG, params)
        except (cv2.error, TypeError):
            capture = cv2.VideoCapture(source.url, cv2.CAP_FFMPEG)
            decoder = "CPU FFmpeg"

        if not capture.isOpened() and decoder != "CPU FFmpeg":
            capture.release()
            capture = cv2.VideoCapture(source.url, cv2.CAP_FFMPEG)
            decoder = "CPU FFmpeg（硬件解码回退）"
        if not capture.isOpened():
            capture.release()
            self._set_connection(False, f"无法打开视频源: {source.display_name}")
            return None

        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        source_type = "本地文件" if self._is_local_file(source.url) else "视频流"
        self._set_connection(True, f"已连接 · {source_type} · {decoder}")
        return capture

    def _draw_cached_results(
        self,
        frame: np.ndarray,
        results: Iterable[DetectionResult],
    ) -> np.ndarray:
        from draw_utils import (
            COLOR_BLUE,
            COLOR_GREEN,
            COLOR_ORANGE,
            draw_info_panel,
            draw_plate_info,
            draw_vehicle_box,
        )

        annotated = frame.copy()
        result_list = list(results)
        for result in result_list:
            color = (
                COLOR_GREEN
                if result.whitelisted
                else COLOR_ORANGE if result.has_plate else COLOR_BLUE
            )
            track_label = f"#{result.track_id} " if result.track_id >= 0 else ""
            label = f"{track_label}{result.vehicle_class_cn} {result.yolo_confidence:.0%}"
            draw_vehicle_box(annotated, result.vehicle_bbox, label, color)
            if result.has_plate:
                draw_plate_info(
                    annotated,
                    result.vehicle_bbox,
                    result.plate_text,
                    result.plate_confidence,
                    result.whitelisted,
                )

        vehicles = len(result_list)
        plates = sum(1 for result in result_list if result.has_plate)
        lines = [f"车辆: {vehicles} 辆  |  车牌: {plates} 个 (缓存)"]
        if self.whitelist_manager.enabled and self.whitelist_manager.count:
            matched = sum(1 for result in result_list if result.whitelisted)
            lines.append(f"白名单: {matched}/{vehicles}  |  总数: {self.whitelist_manager.count}")
        draw_info_panel(annotated, lines, position=(10, 10), font_size=14)
        return annotated

    def _set_connection(self, connected: bool, message: str) -> None:
        with self._condition:
            self._connected = connected
            self._message = message

    def _set_detection_status(self, message: str) -> None:
        with self._condition:
            self._detection_status = message

    @staticmethod
    def _is_local_file(url: str) -> bool:
        if not url or url.lower().startswith(
            ("rtsp://", "rtmp://", "http://", "https://", "udp://")
        ):
            return False
        return Path(url).is_file()
