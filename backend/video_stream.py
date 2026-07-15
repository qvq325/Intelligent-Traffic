"""Threaded video capture, inference, and MJPEG frame publication."""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np

from detection_processor import DetectionProcessor, DetectionResult
from whitelist_manager import WhitelistManager

from .model_pipelines import ModelPipelineOptions


DetectionCallback = Callable[[str, list[DetectionResult], tuple[int, int]], None]
FrameProcessor = Callable[[str, np.ndarray], np.ndarray]
ProcessorFactory = Callable[[ModelPipelineOptions], DetectionProcessor]


def _default_pipeline_options(scene_key: str) -> ModelPipelineOptions:
    return ModelPipelineOptions(
        scene_key=scene_key,
        preset="legacy",
        enabled=False,
        device_preference="cpu",
        yolo_threshold=0.5,
        lpr_threshold=0.7,
        frame_interval=5,
        inference_size=640,
        parking_move_threshold=0.03,
        mog_history=500,
        mog_variance_threshold=25.0,
        mog_min_area=150,
        mog_min_duration=2.0,
        mog_max_duration=5.0,
        mog_warmup_frames=50,
        revision=0,
        vehicle_model_path=Path(__file__).resolve().parent.parent / "yolo11m.pt",
        plate_model_path=None,
        plate_mode="pose",
        no_parking_mode="dwell",
        road_abnormal_mode="mog2",
    )


def _default_processor_factory(options: ModelPipelineOptions) -> DetectionProcessor:
    return DetectionProcessor(
        yolo_conf=options.yolo_threshold,
        lpr_conf=options.lpr_threshold,
        device=options.device_preference,
        vehicle_model_path=options.vehicle_model_path,
        plate_model_path=options.plate_model_path,
        inference_size=options.inference_size,
        lpr_mode=options.plate_mode,
    )


def _redact_processor_error(
    error: object,
    options: ModelPipelineOptions,
) -> str:
    message = str(error or "unknown error").strip() or "unknown error"
    for path in (options.vehicle_model_path, options.plate_model_path):
        if path is None:
            continue
        values: set[str] = set()
        for candidate in (path, path.absolute(), path.resolve(strict=False)):
            values.update((str(candidate), candidate.as_posix()))
        for value in sorted(values, key=len, reverse=True):
            if not value:
                continue
            pattern = r"[\\/]".join(
                re.escape(component) for component in re.split(r"[\\/]", value)
            )
            message = re.sub(pattern, "[model]", message, flags=re.IGNORECASE)
    return message[:500]


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


@dataclass(frozen=True, slots=True)
class _ProcessingSnapshot:
    enabled: bool
    options: ModelPipelineOptions
    processor: DetectionProcessor | None
    generation: int
    epoch: int


@dataclass(frozen=True, slots=True)
class _InferenceWork:
    source: StreamSource
    source_revision: int
    frame: np.ndarray


@dataclass(frozen=True, slots=True)
class _DetectionOverlay:
    results: tuple[DetectionResult, ...]
    processing: _ProcessingSnapshot
    source_revision: int


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
        *,
        scene_key: str = "realtime",
        processor_factory: ProcessorFactory | None = None,
        external_inference: bool = False,
    ) -> None:
        if not scene_key:
            raise ValueError("scene_key must not be empty")
        self.whitelist_manager = whitelist_manager
        self.on_detections = on_detections
        self.frame_processor = frame_processor
        self.scene_key = scene_key
        self._processor_factory = processor_factory or _default_processor_factory
        self._external_inference = bool(external_inference)
        self._detection_listeners: list[DetectionCallback] = []

        self._condition = threading.Condition(threading.RLock())
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._inference_thread: threading.Thread | None = None
        self._pending_inference: _InferenceWork | None = None
        self._detection_overlay: _DetectionOverlay | None = None
        self._inference_tracker_processor: DetectionProcessor | None = None
        self._inference_tracker_source_revision = -1

        self._requested_source: StreamSource | None = None
        self._source_revision = 0
        self._paused = False
        self._connected = False
        self._message = "就绪"

        self._desired_processor_options = _default_pipeline_options(scene_key)
        self._desired_processor_generation = 0
        self._active_processor_options: ModelPipelineOptions | None = None
        self._active_processor_generation = -1
        self._failed_processor_options: ModelPipelineOptions | None = None
        self._failed_processor_generation: int | None = None
        self._loading_processor_options: ModelPipelineOptions | None = None
        self._loading_processor_generation: int | None = None
        self._inference_epoch = 0
        self._processor: DetectionProcessor | None = None
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
            self._pending_inference = None
            inference_thread = None
            if not self._inference_thread or not self._inference_thread.is_alive():
                self._inference_thread = threading.Thread(
                    target=self._run_inference,
                    name="video-inference-worker",
                    daemon=True,
                )
                inference_thread = self._inference_thread
            self._thread = threading.Thread(
                target=self._run,
                name="video-stream-worker",
                daemon=True,
            )
            if inference_thread is not None:
                inference_thread.start()
            self._thread.start()

    def stop(self, timeout: float = 8.0) -> None:
        self._stop_event.set()
        with self._condition:
            self._pending_inference = None
            self._condition.notify_all()
            thread = self._thread
            inference_thread = self._inference_thread
        deadline = time.monotonic() + timeout
        for worker in (thread, inference_thread):
            if worker and worker.is_alive():
                worker.join(timeout=max(0.0, deadline - time.monotonic()))
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
            current = self._desired_processor_options
            updated = replace(
                current,
                enabled=current.enabled if enabled is None else bool(enabled),
                yolo_threshold=(
                    current.yolo_threshold
                    if yolo_threshold is None
                    else max(0.05, min(1.0, float(yolo_threshold)))
                ),
                lpr_threshold=(
                    current.lpr_threshold
                    if lpr_threshold is None
                    else max(0.05, min(1.0, float(lpr_threshold)))
                ),
                frame_interval=(
                    current.frame_interval
                    if interval is None
                    else max(1, min(60, int(interval)))
                ),
                device_preference=(
                    current.device_preference if device is None else device
                ),
            )
            if updated == current:
                return

            retry_failed_options = (
                self._failed_processor_generation
                == self._desired_processor_generation
                and self._failed_processor_options != updated
            )
            if (
                updated.device_preference != current.device_preference
                or retry_failed_options
            ):
                self._desired_processor_generation += 1
            self._desired_processor_options = updated
            self._advance_inference_epoch_locked()

            if not updated.enabled:
                self._detection_status = "未启用"
                self._results = []
            elif self._external_inference:
                self._detection_status = "由场景分析器处理"
            elif (
                self._processor is None
                or self._active_processor_generation
                != self._desired_processor_generation
            ):
                self._detection_status = "等待加载模型"
            else:
                lpr_status = "可用" if self._processor.has_lpr else "不可用"
                self._detection_status = f"模型已就绪 · LPR {lpr_status}"
            self._condition.notify_all()

    def apply_model_pipeline_options(
        self,
        options: ModelPipelineOptions,
    ) -> bool:
        """Converge this stream to immutable options for its fixed scene."""
        if not isinstance(options, ModelPipelineOptions):
            raise TypeError("options must be ModelPipelineOptions")
        if options.scene_key != self.scene_key:
            raise ValueError(
                f"scene options {options.scene_key!r} do not match "
                f"service {self.scene_key!r}"
            )

        with self._condition:
            if options == self._desired_processor_options:
                return False
            self._desired_processor_options = options
            self._desired_processor_generation += 1
            self._advance_inference_epoch_locked()
            if not options.enabled:
                self._detection_status = "未启用"
                self._results = []
            elif self._external_inference:
                self._detection_status = "由场景分析器处理"
            else:
                self._detection_status = "等待加载模型"
            self._condition.notify_all()
            return True

    def add_detection_listener(self, listener: DetectionCallback) -> None:
        """Register an additional consumer without changing the primary callback."""
        with self._condition:
            if listener not in self._detection_listeners:
                self._detection_listeners.append(listener)

    def status(self) -> dict:
        with self._condition:
            source = self._requested_source
            results = [dict(result) for result in self._results]
            desired = self._desired_processor_options
            active = self._active_processor_options
            settings = {
                "enabled": desired.enabled,
                "yolo_threshold": desired.yolo_threshold,
                "lpr_threshold": desired.lpr_threshold,
                "interval": desired.frame_interval,
                "device": desired.device_preference,
                "preset": desired.preset,
                "desired_revision": desired.revision,
                "active_revision": active.revision if active is not None else None,
            }
            resolution = (
                {"width": self._resolution[0], "height": self._resolution[1]}
                if self._resolution
                else None
            )
            return {
                "scene_key": self.scene_key,
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

        while not self._stop_event.is_set():
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
            processing = self._processing_snapshot()
            options = processing.options
            if active_source is not None:
                if processing.enabled and frame_count % options.frame_interval == 0:
                    self._submit_inference(active_source, local_revision, frame)
                annotated, annotated_snapshot = self._compose_frame(
                    active_source,
                    local_revision,
                    frame,
                    processing,
                )
            else:
                annotated = frame
                annotated_snapshot = None

            if (
                annotated_snapshot is not None
                and not self._processing_snapshot_current(annotated_snapshot)
            ):
                annotated = frame
                annotated_snapshot = None
            if not self._publish_frame(annotated, annotated_snapshot):
                if annotated_snapshot is not None:
                    self._publish_frame(frame)

            if active_source and self._is_local_file(active_source.url) and source_fps > 0:
                elapsed = time.monotonic() - frame_started
                self._stop_event.wait(max(0.001, (1.0 / source_fps) - elapsed))
            else:
                self._stop_event.wait(0.005)

        if capture is not None:
            capture.release()

    def _compose_frame(
        self,
        source: StreamSource,
        source_revision: int,
        frame: np.ndarray,
        processing: _ProcessingSnapshot,
    ) -> tuple[np.ndarray, _ProcessingSnapshot | None]:
        annotated = frame
        if self.frame_processor is not None:
            try:
                annotated = self.frame_processor(source.name, frame)
            except Exception as exc:
                self._set_detection_status(f"画面分析异常: {exc}")
                return frame, None

        annotated_snapshot = None
        if processing.enabled:
            cached_results = self._cached_detection_results(
                processing,
                source_revision,
            )
            if cached_results is not None:
                annotated = self._draw_cached_results(annotated, cached_results)
                annotated_snapshot = processing
        return annotated, annotated_snapshot

    def _submit_inference(
        self,
        source: StreamSource,
        source_revision: int,
        frame: np.ndarray,
    ) -> bool:
        if self._external_inference:
            return False
        work = _InferenceWork(
            source=source,
            source_revision=source_revision,
            frame=frame.copy(),
        )
        with self._condition:
            if (
                self._stop_event.is_set()
                or not self._desired_processor_options.enabled
                or source_revision != self._source_revision
                or source != self._requested_source
            ):
                return False
            self._pending_inference = work
            self._condition.notify_all()
            return True

    def _run_inference(self) -> None:
        while not self._stop_event.is_set():
            with self._condition:
                while (
                    self._pending_inference is None
                    and not self._stop_event.is_set()
                ):
                    self._condition.wait()
                if self._stop_event.is_set():
                    break
                work = self._pending_inference
                self._pending_inference = None
            if work is not None:
                self._process_inference_work(work)

    def _process_inference_work(self, work: _InferenceWork) -> bool:
        if not self._inference_work_current(work):
            return False

        self._ensure_processor()
        with self._condition:
            if self._pending_inference is not None:
                work = self._pending_inference
                self._pending_inference = None
        processing = self._processing_snapshot()
        processor = processing.processor
        if (
            not processing.enabled
            or processor is None
            or not processor.is_initialized
            or not self._processing_snapshot_current(processing)
            or not self._inference_work_current(work)
        ):
            return False

        try:
            if (
                processor is not self._inference_tracker_processor
                or work.source_revision != self._inference_tracker_source_revision
            ):
                processor.reset_tracking()
                self._inference_tracker_processor = processor
                self._inference_tracker_source_revision = work.source_revision
            processor.yolo_threshold = processing.options.yolo_threshold
            processor.lpr_threshold = processing.options.lpr_threshold
            _, candidate_results = processor.process(
                work.frame,
                camera_id=work.source.name,
            )
            results = list(candidate_results)
        except Exception as exc:
            self._set_detection_status(f"检测异常: {exc}")
            return False

        if (
            not self._processing_snapshot_current(processing)
            or not self._inference_work_current(work)
            or not self._publish_results(
                work.source,
                results,
                work.frame,
                processing,
            )
        ):
            return False

        with self._condition:
            if (
                work.source_revision != self._source_revision
                or work.source != self._requested_source
                or not self._processing_snapshot_current(processing)
            ):
                return False
            self._detection_overlay = _DetectionOverlay(
                results=tuple(results),
                processing=processing,
                source_revision=work.source_revision,
            )
            return True

    def _inference_work_current(self, work: _InferenceWork) -> bool:
        with self._condition:
            return (
                not self._stop_event.is_set()
                and self._desired_processor_options.enabled
                and work.source_revision == self._source_revision
                and work.source == self._requested_source
            )

    def _cached_detection_results(
        self,
        processing: _ProcessingSnapshot,
        source_revision: int,
    ) -> list[DetectionResult] | None:
        with self._condition:
            overlay = self._detection_overlay
            if (
                overlay is None
                or overlay.source_revision != source_revision
                or not self._same_processing_token(processing, overlay.processing)
                or not self._processing_snapshot_current(processing)
            ):
                return None
            return list(overlay.results)

    def _advance_inference_epoch_locked(self) -> None:
        self._inference_epoch += 1
        self._results = []

    def _processing_snapshot(self) -> _ProcessingSnapshot:
        with self._condition:
            desired = self._desired_processor_options
            processor = self._processor
            options = desired
            generation = self._desired_processor_generation
            if processor is not None and self._active_processor_options is not None:
                generation = self._active_processor_generation
                if generation != self._desired_processor_generation:
                    options = self._active_processor_options
            return _ProcessingSnapshot(
                enabled=desired.enabled and not self._external_inference,
                options=options,
                processor=processor,
                generation=generation,
                epoch=self._inference_epoch,
            )

    def _processing_snapshot_current(self, snapshot: _ProcessingSnapshot) -> bool:
        with self._condition:
            return (
                self._desired_processor_options.enabled
                and self._inference_epoch == snapshot.epoch
                and self._processor is snapshot.processor
                and self._active_processor_generation == snapshot.generation
            )

    @staticmethod
    def _same_processing_token(
        first: _ProcessingSnapshot,
        second: _ProcessingSnapshot,
    ) -> bool:
        return (
            first.epoch == second.epoch
            and first.generation == second.generation
            and first.processor is second.processor
        )

    def _settings_snapshot(self) -> DetectionSettings:
        snapshot = self._processing_snapshot()
        options = snapshot.options
        return DetectionSettings(
            enabled=snapshot.enabled,
            yolo_threshold=options.yolo_threshold,
            lpr_threshold=options.lpr_threshold,
            interval=options.frame_interval,
            device=options.device_preference,
        )

    def _ensure_processor(self) -> None:
        with self._condition:
            if self._external_inference:
                return
            options = self._desired_processor_options
            generation = self._desired_processor_generation
            if not options.enabled:
                return
            if (
                self._processor is not None
                and self._processor.is_initialized
                and self._active_processor_generation == generation
            ):
                return
            if (
                self._failed_processor_generation == generation
                and self._failed_processor_options == options
            ):
                return
            if self._loading_processor_generation is not None:
                return
            self._loading_processor_generation = generation
            self._loading_processor_options = options
            self._detection_status = (
                f"正在加载模型 (device={options.device_preference})"
            )

        processor: DetectionProcessor | None = None
        initialized = False
        candidate_has_lpr = False
        load_error: object = "unknown error"
        try:
            processor = self._processor_factory(options)
            processor.whitelist_manager = self.whitelist_manager
            initialize = getattr(processor, "initialize", None)
            if not callable(initialize):
                raise TypeError("processor protocol invalid: missing initialize")
            initialized = bool(initialize())
            if initialized:
                missing = [
                    name
                    for name in ("process", "reset_tracking")
                    if not callable(getattr(processor, name, None))
                ]
                for name in (
                    "is_initialized",
                    "has_lpr",
                    "yolo_threshold",
                    "lpr_threshold",
                ):
                    if not hasattr(processor, name):
                        missing.append(name)
                if missing:
                    raise TypeError(
                        "processor protocol invalid: missing "
                        + ", ".join(sorted(missing))
                    )
                if not bool(processor.is_initialized):
                    raise TypeError(
                        "processor protocol invalid: is_initialized is false"
                    )
                processor.yolo_threshold = options.yolo_threshold
                processor.lpr_threshold = options.lpr_threshold
                candidate_has_lpr = bool(processor.has_lpr)
            else:
                load_error = getattr(processor, "init_error", load_error)
        except Exception as exc:
            initialized = False
            load_error = exc

        with self._condition:
            if (
                self._loading_processor_generation == generation
                and self._loading_processor_options == options
            ):
                self._loading_processor_generation = None
                self._loading_processor_options = None
            if (
                generation != self._desired_processor_generation
                or options != self._desired_processor_options
            ):
                self._condition.notify_all()
                return
            if initialized and processor is not None:
                self._advance_inference_epoch_locked()
                self._processor = processor
                self._active_processor_generation = generation
                self._active_processor_options = options
                self._failed_processor_generation = None
                self._failed_processor_options = None
                lpr_status = "可用" if candidate_has_lpr else "不可用"
                self._detection_status = f"模型已就绪 · LPR {lpr_status}"
            else:
                self._failed_processor_generation = generation
                self._failed_processor_options = options
                self._detection_status = (
                    f"模型加载失败: {_redact_processor_error(load_error, options)}"
                )
            self._condition.notify_all()

    def _publish_results(
        self,
        source: StreamSource | None,
        results: Iterable[DetectionResult],
        frame: np.ndarray,
        snapshot: _ProcessingSnapshot,
    ) -> bool:
        result_list = list(results)
        with self._condition:
            if not (
                self._desired_processor_options.enabled
                and self._inference_epoch == snapshot.epoch
                and self._processor is snapshot.processor
                and self._active_processor_generation == snapshot.generation
            ):
                return False
            self._results = [serialize_detection(result) for result in result_list]
            listeners = list(self._detection_listeners)
            primary_callback = self.on_detections
        if source is None:
            return True
        height, width = frame.shape[:2]
        callbacks = ([primary_callback] if primary_callback else []) + listeners
        for callback in callbacks:
            if not self._processing_snapshot_current(snapshot):
                return False
            try:
                callback(source.name, result_list, (width, height))
            except Exception as exc:
                self._set_detection_status(f"检测结果处理异常: {exc}")
        return self._processing_snapshot_current(snapshot)

    def _publish_frame(
        self,
        frame: np.ndarray,
        snapshot: _ProcessingSnapshot | None = None,
    ) -> bool:
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), 86],
        )
        if not ok:
            return False
        jpeg = encoded.tobytes()
        now = time.monotonic()
        height, width = frame.shape[:2]
        with self._condition:
            if snapshot is not None and not (
                self._desired_processor_options.enabled
                and self._inference_epoch == snapshot.epoch
                and self._processor is snapshot.processor
                and self._active_processor_generation == snapshot.generation
            ):
                return False
            if self._last_publish_time:
                instant_fps = 1.0 / max(0.001, now - self._last_publish_time)
                self._display_fps = (
                    instant_fps
                    if not self._display_fps
                    else self._display_fps * 0.82 + instant_fps * 0.18
                )
            self._last_publish_time = now
            self._latest_jpeg = jpeg
            self._resolution = (width, height)
            self._frame_sequence += 1
            self._condition.notify_all()
            return True

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
        lines = [f"车辆检测: {vehicles} 辆  |  车牌识别: {plates} 个"]
        if self.whitelist_manager.enabled and self.whitelist_manager.count:
            matched = sum(1 for result in result_list if result.whitelisted)
            lines.append(
                f"白名单匹配: {matched}/{vehicles}  |  "
                f"白名单总数: {self.whitelist_manager.count}"
            )
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
