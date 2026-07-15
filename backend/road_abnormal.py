"""Road-abnormal scene configuration, candidate fusion, and event tracking."""

from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence
from uuid import uuid4

import cv2
import numpy as np

from backend.no_parking import point_in_polygon

from .model_pipelines import ModelPipelineOptions


Point = tuple[float, float]
BBox = tuple[float, float, float, float]

ROAD_OBJECT_CLASSES = {
    0: ("person", "行人"),
    1: ("bicycle", "非机动车"),
    2: ("car", "小汽车"),
    3: ("motorcycle", "摩托车"),
    5: ("bus", "公交车"),
    7: ("truck", "卡车"),
}
DEFAULT_ANOMALY_CLASSES = ["person", "bicycle", "motorcycle"]
DEFAULT_NORMAL_CLASSES = ["car", "bus", "truck"]


@dataclass(slots=True)
class RoadAbnormalZone:
    zone_id: str
    name: str
    lane_name: str
    points: list[Point]
    enabled: bool = True


@dataclass(slots=True)
class RoadAbnormalScene:
    scene_id: str
    name: str
    camera_id: str
    reference_image: str
    reference_width: int
    reference_height: int
    zones: list[RoadAbnormalZone]
    persistence_seconds: float = 3.0
    lost_tolerance_seconds: float = 1.0
    min_area_ratio: float = 0.001
    history: int = 500
    variance_threshold: float = 25.0
    detect_shadows: bool = True
    warmup_frames: int = 30
    learning_rate: float = 0.002
    inference_interval: int = 5
    yolo_threshold: float = 0.45
    anomaly_classes: list[str] = field(
        default_factory=lambda: list(DEFAULT_ANOMALY_CLASSES)
    )
    normal_classes: list[str] = field(
        default_factory=lambda: list(DEFAULT_NORMAL_CLASSES)
    )
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass(slots=True)
class AbnormalCandidateState:
    candidate_id: str
    zone_id: str
    zone_name: str
    lane_name: str
    source: str
    anomaly_type: str
    class_name: str
    class_name_cn: str
    confidence: float
    bbox: BBox
    first_seen: float
    last_seen: float
    duration_seconds: float = 0.0
    event_id: str = ""


def _identifier(value: str, prefix: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip()).strip("_")
    return normalized[:80] or f"{prefix}_{uuid4().hex[:10]}"


def _polygon_area(points: Sequence[Point]) -> float:
    return abs(
        sum(
            start[0] * end[1] - end[0] * start[1]
            for start, end in zip(points, [*points[1:], points[0]])
        )
    ) / 2.0


def _iou(first: BBox, second: BBox) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    if intersection <= 0:
        return 0.0
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    return intersection / max(1e-9, first_area + second_area - intersection)


def _center_distance(first: BBox, second: BBox) -> float:
    first_center = ((first[0] + first[2]) / 2.0, (first[1] + first[3]) / 2.0)
    second_center = ((second[0] + second[2]) / 2.0, (second[1] + second[3]) / 2.0)
    return math.dist(first_center, second_center)


class RoadObjectDetector:
    """Lazy YOLO wrapper used only while road-abnormal monitoring is active."""

    def __init__(
        self, model_path: Path, device: str = "cpu", inference_size: int = 640
    ) -> None:
        self.model_path = Path(model_path)
        self.device = device
        self.inference_size = int(inference_size)
        self._model = None

    def prepare(self) -> "RoadObjectDetector":
        if self._model is not None:
            return self
        from ultralytics import YOLO

        model = YOLO(str(self.model_path))
        move_to_device = getattr(model, "to", None)
        if callable(move_to_device):
            prepared_model = move_to_device(self.device)
            if prepared_model is not None:
                model = prepared_model
        self._model = model
        return self

    def detect(self, frame: np.ndarray, threshold: float) -> list[dict]:
        model = self.prepare()._model
        results = model.predict(
            frame,
            classes=list(ROAD_OBJECT_CLASSES),
            conf=threshold,
            device=self.device,
            imgsz=self.inference_size,
            verbose=False,
        )
        if not results or results[0].boxes is None:
            return []
        objects: list[dict] = []
        for box in results[0].boxes:
            class_id = int(box.cls.item())
            class_name, class_name_cn = ROAD_OBJECT_CLASSES.get(
                class_id, ("unknown", "未知目标")
            )
            coordinates = tuple(float(value) for value in box.xyxy[0].tolist())
            objects.append(
                {
                    "bbox": coordinates,
                    "class_name": class_name,
                    "class_name_cn": class_name_cn,
                    "confidence": float(box.conf.item()),
                    "track_id": -1,
                }
            )
        return objects


class RoadAbnormalMonitor:
    """Persist fixed-camera profiles and evaluate fused abnormal candidates."""

    def __init__(
        self,
        root_dir: Path,
        model_path: Path,
        device: str = "cpu",
        detector: RoadObjectDetector | None = None,
        detector_factory: Callable[[Path, str, int], object] | None = None,
        mog_factory: Callable[..., object] | None = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.references_dir = self.root_dir / "references"
        self.snapshots_dir = self.root_dir / "snapshots"
        self.scenes_file = self.root_dir / "scenes.json"
        self.events_file = self.root_dir / "events.json"
        if detector_factory is not None:
            self._detector_factory = detector_factory
        elif detector is not None:
            self._detector_factory = lambda _path, _device, _size: detector
        else:
            self._detector_factory = (
                lambda path, selected_device, size: RoadObjectDetector(
                    path, selected_device, size
                )
            )
        self._mog_factory = mog_factory or self._default_mog_factory
        self._detector = detector or self._detector_factory(model_path, device, 640)
        self._mog_engine = None
        self._pipeline_options: ModelPipelineOptions | None = None
        self._road_abnormal_mode = "mog2"
        self._lock = threading.RLock()
        self._inference_lock = threading.Lock()
        self._event_io_lock = threading.Lock()
        self._runtime_generation = 0
        self._scenes: dict[str, RoadAbnormalScene] = {}
        self._events: list[dict] = []
        self._candidates: dict[str, AbnormalCandidateState] = {}
        self._active_scene_id = ""
        self._running = False
        self._last_camera_id = ""
        self._last_error = ""
        self._background = None
        self._frame_count = 0
        self._known_objects: list[dict] = []
        self._known_objects_frame = -1
        self._load()

    @staticmethod
    def _default_mog_factory(**kwargs):
        from .trained_mog import MOGAnomalyEngine

        return MOGAnomalyEngine(**kwargs)

    @staticmethod
    def _set_mog_rois(mog_engine: object, polygons: list[list[Point]]) -> None:
        set_rois = getattr(mog_engine, "set_rois", None)
        if callable(set_rois):
            set_rois(polygons)
            return
        set_roi = getattr(mog_engine, "set_roi", None)
        if not callable(set_roi):
            raise TypeError("MOG engine does not support ROI setup")
        set_roi(polygons)

    def apply_model_pipeline_options(self, options: ModelPipelineOptions) -> bool:
        if not isinstance(options, ModelPipelineOptions):
            raise TypeError("options must be ModelPipelineOptions")
        if options.scene_key != "road_abnormal":
            raise ValueError("model pipeline scene must be road_abnormal")
        if options.road_abnormal_mode not in {"mog2", "mog"}:
            raise ValueError("unsupported road-abnormal pipeline mode")
        with self._lock:
            if options == self._pipeline_options:
                return False

            baseline_options = self._pipeline_options
            baseline_scene_id = self._active_scene_id
            baseline_scene = self._scenes.get(baseline_scene_id)
            baseline_detector = self._detector
            baseline_mog = self._mog_engine
            scene_snapshot = None
            if baseline_scene is not None:
                scene_snapshot = {
                    "history": int(baseline_scene.history),
                    "variance_threshold": float(
                        baseline_scene.variance_threshold
                    ),
                    "detect_shadows": bool(baseline_scene.detect_shadows),
                    "width": max(1, int(baseline_scene.reference_width)),
                    "height": max(1, int(baseline_scene.reference_height)),
                    "rois": tuple(
                        tuple((float(x), float(y)) for x, y in zone.points)
                        for zone in baseline_scene.zones
                        if zone.enabled
                    ),
                }

        try:
            prepared_detector = self._detector_factory(
                options.vehicle_model_path,
                options.device_preference,
                options.inference_size,
            )
            if prepared_detector is baseline_detector:
                raise RuntimeError("detector factory returned the active detector")
            prepare_detector = getattr(prepared_detector, "prepare", None)
            if callable(prepare_detector):
                prepare_detector()
            prepared_mog = None
            prepared_background = None
            if options.road_abnormal_mode == "mog":
                prepared_mog = self._mog_factory(
                    history=options.mog_history,
                    var_threshold=options.mog_variance_threshold,
                    min_area=options.mog_min_area,
                    min_duration=options.mog_min_duration,
                    max_duration=options.mog_max_duration,
                    warmup_frames=options.mog_warmup_frames,
                )
                if prepared_mog is baseline_mog:
                    raise RuntimeError("MOG factory returned the active engine")
                prepared_mog.reset()
                if scene_snapshot is not None:
                    width = scene_snapshot["width"]
                    height = scene_snapshot["height"]
                    polygons = [
                        [
                            (round(x * width), round(y * height))
                            for x, y in points
                        ]
                        for points in scene_snapshot["rois"]
                    ]
                    self._set_mog_rois(prepared_mog, polygons)
            elif scene_snapshot is not None:
                prepared_background = cv2.createBackgroundSubtractorMOG2(
                    history=scene_snapshot["history"],
                    varThreshold=scene_snapshot["variance_threshold"],
                    detectShadows=scene_snapshot["detect_shadows"],
                )
        except Exception as exc:
            with self._lock:
                if (
                    self._pipeline_options == baseline_options
                    and self._active_scene_id == baseline_scene_id
                    and self._scenes.get(baseline_scene_id) is baseline_scene
                ):
                    self._last_error = (
                        "道路异常模型管线更新失败: " + type(exc).__name__
                    )
            return False

        with self._lock:
            if options == self._pipeline_options:
                return False
            if (
                self._pipeline_options != baseline_options
                or self._active_scene_id != baseline_scene_id
                or self._scenes.get(baseline_scene_id) is not baseline_scene
            ):
                return False
            changed = self._close_all_candidates(time.time())
            self._candidates.clear()
            self._detector = prepared_detector
            self._mog_engine = prepared_mog
            self._pipeline_options = options
            self._road_abnormal_mode = options.road_abnormal_mode
            self._background = prepared_background
            self._frame_count = 0
            self._known_objects = []
            self._known_objects_frame = -1
            self._runtime_generation += 1
            self._last_error = ""
        if changed:
            self._save_events()
        return True

    def _load(self) -> None:
        with self._lock:
            try:
                payload = json.loads(self.scenes_file.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                payload = {}
            for raw_scene in payload.get("scenes", []):
                try:
                    zones = [
                        RoadAbnormalZone(
                            zone_id=str(zone["zone_id"]),
                            name=str(zone.get("name") or zone["zone_id"]),
                            lane_name=str(zone.get("lane_name") or zone.get("name") or "机动车道"),
                            points=[(float(point[0]), float(point[1])) for point in zone["points"]],
                            enabled=bool(zone.get("enabled", True)),
                        )
                        for zone in raw_scene.get("zones", [])
                    ]
                    defaults = {
                        "persistence_seconds": 3.0,
                        "lost_tolerance_seconds": 1.0,
                        "min_area_ratio": 0.001,
                        "history": 500,
                        "variance_threshold": 25.0,
                        "detect_shadows": True,
                        "warmup_frames": 30,
                        "learning_rate": 0.002,
                        "inference_interval": 5,
                        "yolo_threshold": 0.45,
                    }
                    scene = RoadAbnormalScene(
                        scene_id=str(raw_scene["scene_id"]),
                        name=str(raw_scene.get("name") or raw_scene["scene_id"]),
                        camera_id=str(raw_scene["camera_id"]),
                        reference_image=str(raw_scene.get("reference_image", "")),
                        reference_width=max(1, int(raw_scene.get("reference_width", 1))),
                        reference_height=max(1, int(raw_scene.get("reference_height", 1))),
                        zones=zones,
                        **{
                            key: type(value)(raw_scene.get(key, value))
                            for key, value in defaults.items()
                        },
                        anomaly_classes=list(
                            raw_scene.get("anomaly_classes") or DEFAULT_ANOMALY_CLASSES
                        ),
                        normal_classes=list(
                            raw_scene.get("normal_classes") or DEFAULT_NORMAL_CLASSES
                        ),
                        created_at=float(raw_scene.get("created_at", time.time())),
                        updated_at=float(raw_scene.get("updated_at", time.time())),
                    )
                    self._scenes[scene.scene_id] = scene
                except (KeyError, TypeError, ValueError, IndexError):
                    continue
            try:
                event_payload = json.loads(self.events_file.read_text(encoding="utf-8"))
                self._events = [
                    dict(event)
                    for event in event_payload.get("events", [])
                    if isinstance(event, dict)
                ][-500:]
            except (OSError, ValueError, TypeError):
                self._events = []

    def _write_json(self, path: Path, payload: dict) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    def _save_scenes(self) -> None:
        self._write_json(
            self.scenes_file,
            {"version": 1, "scenes": [asdict(scene) for scene in self._scenes.values()]},
        )

    def _save_events(self) -> None:
        with self._event_io_lock:
            with self._lock:
                self._events = self._events[-500:]
                payload = {
                    "version": 1,
                    "events": [dict(event) for event in self._events],
                }
            self._write_json(self.events_file, payload)

    @staticmethod
    def _scene_payload(scene: RoadAbnormalScene | None) -> dict | None:
        if scene is None:
            return None
        return {
            **asdict(scene),
            "reference_url": (
                f"/api/road-abnormal/references/{scene.reference_image}"
                if scene.reference_image
                else ""
            ),
        }

    def catalog(self) -> dict:
        with self._lock:
            return {
                "scenes": [
                    self._scene_payload(scene)
                    for scene in sorted(
                        self._scenes.values(), key=lambda item: item.updated_at, reverse=True
                    )
                ],
                "active_scene_id": self._active_scene_id,
            }

    def get_scene(self, scene_id: str) -> dict | None:
        with self._lock:
            return self._scene_payload(self._scenes.get(scene_id))

    def capture_reference(
        self, jpeg: bytes, camera_id: str, width: int, height: int
    ) -> dict:
        if not jpeg:
            raise ValueError("参考帧为空")
        filename = f"reference_{uuid4().hex}.jpg"
        with self._lock:
            self.references_dir.mkdir(parents=True, exist_ok=True)
            (self.references_dir / filename).write_bytes(jpeg)
        return {
            "filename": filename,
            "url": f"/api/road-abnormal/references/{filename}",
            "camera_id": camera_id,
            "width": int(width),
            "height": int(height),
            "captured_at": time.time(),
        }

    def reference_path(self, filename: str) -> Path | None:
        if Path(filename).name != filename or not filename.lower().endswith(".jpg"):
            return None
        path = self.references_dir / filename
        return path if path.is_file() else None

    def snapshot_path(self, filename: str) -> Path | None:
        if Path(filename).name != filename or not filename.lower().endswith(".jpg"):
            return None
        path = self.snapshots_dir / filename
        return path if path.is_file() else None

    def upsert_scene(self, payload: dict) -> dict:
        reference_image = str(payload.get("reference_image", ""))
        if self.reference_path(reference_image) is None:
            raise ValueError("参考帧不存在，请重新截取")
        zones: list[RoadAbnormalZone] = []
        for raw_zone in payload.get("zones", []):
            points = [(float(x), float(y)) for x, y in raw_zone.get("points", [])]
            if len(points) > 3 and math.dist(points[0], points[-1]) < 1e-8:
                points.pop()
            if len(points) < 3:
                raise ValueError("道路检测区域至少需要三个点")
            if any(
                not math.isfinite(value) or value < 0.0 or value > 1.0
                for point in points
                for value in point
            ):
                raise ValueError("道路检测区域坐标必须位于画面范围内")
            if _polygon_area(points) < 1e-5:
                raise ValueError("道路检测区域不能在同一直线上")
            name = str(raw_zone.get("name", "")).strip() or "道路检测区域"
            zones.append(
                RoadAbnormalZone(
                    zone_id=_identifier(str(raw_zone.get("zone_id", "")), "zone"),
                    name=name,
                    lane_name=str(raw_zone.get("lane_name", "")).strip() or name,
                    points=points,
                    enabled=bool(raw_zone.get("enabled", True)),
                )
            )
        if not zones:
            raise ValueError("至少需要配置一个道路检测区域")

        anomaly_classes = [
            item
            for item in payload.get("anomaly_classes", [])
            if item in {value[0] for value in ROAD_OBJECT_CLASSES.values()}
        ] or list(DEFAULT_ANOMALY_CLASSES)
        normal_classes = [
            item
            for item in payload.get("normal_classes", [])
            if item in {value[0] for value in ROAD_OBJECT_CLASSES.values()}
        ] or list(DEFAULT_NORMAL_CLASSES)
        now = time.time()
        scene_id = _identifier(str(payload.get("scene_id", "")), "scene")
        events_changed = False
        with self._inference_lock, self._lock:
            existing = self._scenes.get(scene_id)
            scene = RoadAbnormalScene(
                scene_id=scene_id,
                name=str(payload.get("name", "")).strip() or "道路异常监控场景",
                camera_id=str(payload.get("camera_id", "")).strip(),
                reference_image=reference_image,
                reference_width=max(1, int(payload.get("reference_width", 1))),
                reference_height=max(1, int(payload.get("reference_height", 1))),
                zones=zones,
                persistence_seconds=max(0.1, float(payload.get("persistence_seconds", 3.0))),
                lost_tolerance_seconds=max(0.1, float(payload.get("lost_tolerance_seconds", 1.0))),
                min_area_ratio=max(0.00001, min(0.25, float(payload.get("min_area_ratio", 0.001)))),
                history=max(10, min(5000, int(payload.get("history", 500)))),
                variance_threshold=max(1.0, min(255.0, float(payload.get("variance_threshold", 25.0)))),
                detect_shadows=bool(payload.get("detect_shadows", True)),
                warmup_frames=max(0, min(1000, int(payload.get("warmup_frames", 30)))),
                learning_rate=max(-1.0, min(1.0, float(payload.get("learning_rate", 0.002)))),
                inference_interval=max(1, min(60, int(payload.get("inference_interval", 5)))),
                yolo_threshold=max(0.05, min(1.0, float(payload.get("yolo_threshold", 0.45)))),
                anomaly_classes=anomaly_classes,
                normal_classes=normal_classes,
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
            if self._active_scene_id == scene_id:
                events_changed = self._reset_runtime(scene)
            self._scenes[scene_id] = scene
            self._save_scenes()
            result = self._scene_payload(scene) or {}
        if events_changed:
            self._save_events()
        return result

    def delete_scene(self, scene_id: str) -> bool:
        with self._lock:
            scene = self._scenes.get(scene_id)
            if scene is None:
                return False
            if self._active_scene_id == scene_id:
                self.stop()
            del self._scenes[scene_id]
            self._save_scenes()
            if not any(
                item.reference_image == scene.reference_image for item in self._scenes.values()
            ):
                reference = self.reference_path(scene.reference_image)
                if reference:
                    reference.unlink(missing_ok=True)
            return True

    def _reset_runtime(self, scene: RoadAbnormalScene) -> bool:
        if self._road_abnormal_mode == "mog":
            prepared_background = None
            if self._mog_engine is not None:
                self._mog_engine.reset()
        else:
            prepared_background = cv2.createBackgroundSubtractorMOG2(
                history=scene.history,
                varThreshold=scene.variance_threshold,
                detectShadows=scene.detect_shadows,
            )
        changed = self._close_all_candidates(time.time())
        self._candidates.clear()
        self._frame_count = 0
        self._known_objects = []
        self._known_objects_frame = -1
        self._runtime_generation += 1
        self._last_error = ""
        self._background = prepared_background
        return changed

    def start(self, scene_id: str) -> dict:
        with self._inference_lock:
            with self._lock:
                scene = self._scenes.get(scene_id)
                if scene is None:
                    raise ValueError("道路异常场景不存在")
                events_changed = self._reset_runtime(scene)
                self._active_scene_id = scene_id
                self._last_camera_id = ""
                self._running = True
                result = self.status()
        if events_changed:
            self._save_events()
        return result

    def stop(self) -> dict:
        with self._lock:
            changed = self._close_all_candidates(time.time())
            self._candidates.clear()
            self._running = False
            self._background = None
            self._runtime_generation += 1
            result = self.status()
        if changed:
            self._save_events()
        return result

    def clear_events(self) -> dict:
        with self._lock:
            self._events.clear()
            result = self.status()
        self._save_events()
        return result

    def _close_event(self, state: AbnormalCandidateState, ended_at: float) -> bool:
        if not state.event_id:
            return False
        for event in reversed(self._events):
            if event.get("event_id") != state.event_id or event.get("ended_at") is not None:
                continue
            event["ended_at"] = ended_at
            event["duration_seconds"] = round(state.duration_seconds, 2)
            return True
        return False

    def _close_all_candidates(self, now: float) -> bool:
        changed = False
        for state in self._candidates.values():
            changed = self._close_event(state, min(now, state.last_seen)) or changed
        return changed

    def _save_snapshot(self, frame: np.ndarray | None, event_id: str) -> str:
        if frame is None:
            return ""
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{event_id}.jpg"
        if cv2.imwrite(str(self.snapshots_dir / filename), frame):
            return filename
        return ""

    def _matching_candidate(
        self,
        zone: RoadAbnormalZone,
        source: str,
        class_name: str,
        bbox: BBox,
        claimed: set[str],
    ) -> AbnormalCandidateState | None:
        matches = [
            state
            for state in self._candidates.values()
            if state.candidate_id not in claimed
            and state.zone_id == zone.zone_id
            and state.source == source
            and state.class_name == class_name
        ]
        if not matches:
            return None
        best = max(matches, key=lambda item: (_iou(item.bbox, bbox), -_center_distance(item.bbox, bbox)))
        if _iou(best.bbox, bbox) >= 0.15 or _center_distance(best.bbox, bbox) <= 0.08:
            return best
        return None

    def update_candidates(
        self,
        camera_id: str,
        candidates: Iterable[dict],
        frame_size: tuple[int, int],
        *,
        frame: np.ndarray | None = None,
        now: float | None = None,
        _pending_io: list | None = None,
    ) -> list[dict]:
        observed_at = time.time() if now is None else float(now)
        width, height = frame_size
        if width <= 0 or height <= 0:
            return []
        with self._lock:
            scene = self._scenes.get(self._active_scene_id)
            self._last_camera_id = camera_id
            if not self._running or scene is None or scene.camera_id != camera_id:
                return []

            claimed: set[str] = set()
            new_events: list[dict] = []
            snapshot_tasks: list[tuple[str, np.ndarray]] = []
            changed = False
            for candidate in candidates:
                raw_bbox = tuple(float(value) for value in candidate.get("bbox", ()))
                if len(raw_bbox) != 4:
                    continue
                bbox = (
                    max(0.0, min(1.0, raw_bbox[0] / width)),
                    max(0.0, min(1.0, raw_bbox[1] / height)),
                    max(0.0, min(1.0, raw_bbox[2] / width)),
                    max(0.0, min(1.0, raw_bbox[3] / height)),
                )
                if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                    continue
                anchor = ((bbox[0] + bbox[2]) / 2.0, bbox[3])
                zone = next(
                    (
                        item
                        for item in scene.zones
                        if item.enabled and point_in_polygon(anchor, item.points)
                    ),
                    None,
                )
                if zone is None:
                    continue
                source = str(candidate.get("source", "MOG2"))
                observed_duration = (
                    max(
                        0.0,
                        float(candidate.get("observed_duration", 0.0)),
                    )
                    if source == "MOG"
                    else 0.0
                )
                class_name = str(candidate.get("class_name", "unknown"))
                track_id = int(candidate.get("track_id", -1))
                state = None
                if track_id >= 0:
                    deterministic_id = f"{zone.zone_id}:{source}:{class_name}:{track_id}"
                    state = self._candidates.get(deterministic_id)
                else:
                    deterministic_id = ""
                    state = self._matching_candidate(
                        zone, source, class_name, bbox, claimed
                    )
                if state is None:
                    state = AbnormalCandidateState(
                        candidate_id=deterministic_id or f"candidate_{uuid4().hex}",
                        zone_id=zone.zone_id,
                        zone_name=zone.name,
                        lane_name=zone.lane_name,
                        source=source,
                        anomaly_type=str(candidate.get("anomaly_type", "unknown_obstacle")),
                        class_name=class_name,
                        class_name_cn=str(candidate.get("class_name_cn", "未知障碍物")),
                        confidence=float(candidate.get("confidence", 0.5)),
                        bbox=bbox,
                        first_seen=observed_at - observed_duration,
                        last_seen=observed_at,
                        duration_seconds=observed_duration,
                    )
                    self._candidates[state.candidate_id] = state
                else:
                    gap = max(0.0, observed_at - state.last_seen)
                    if gap > scene.lost_tolerance_seconds:
                        changed = self._close_event(state, state.last_seen) or changed
                        state.first_seen = observed_at - observed_duration
                        state.duration_seconds = observed_duration
                        state.event_id = ""
                    state.last_seen = observed_at
                    state.duration_seconds = max(0.0, observed_at - state.first_seen)
                    state.bbox = bbox
                    state.confidence = max(
                        float(candidate.get("confidence", state.confidence)),
                        state.confidence * 0.9,
                    )
                    state.anomaly_type = str(candidate.get("anomaly_type", state.anomaly_type))
                    state.class_name_cn = str(candidate.get("class_name_cn", state.class_name_cn))
                claimed.add(state.candidate_id)

                if not state.event_id and state.duration_seconds >= scene.persistence_seconds:
                    event_id = f"event_{uuid4().hex}"
                    snapshot = f"{event_id}.jpg" if frame is not None else ""
                    if frame is not None:
                        snapshot_tasks.append((event_id, frame))
                    event = {
                        "event_id": event_id,
                        "scene_id": scene.scene_id,
                        "scene_name": scene.name,
                        "camera_id": camera_id,
                        "zone_id": state.zone_id,
                        "zone_name": state.zone_name,
                        "lane_name": state.lane_name,
                        "source": state.source,
                        "anomaly_type": state.anomaly_type,
                        "class_name": state.class_name,
                        "class_name_cn": state.class_name_cn,
                        "confidence": round(state.confidence, 4),
                        "bbox": state.bbox,
                        "first_seen": state.first_seen,
                        "triggered_at": observed_at,
                        "ended_at": None,
                        "duration_seconds": round(state.duration_seconds, 2),
                        "snapshot": snapshot,
                        "snapshot_url": (
                            f"/api/road-abnormal/snapshots/{snapshot}" if snapshot else ""
                        ),
                    }
                    state.event_id = event_id
                    self._events.append(event)
                    new_events.append(dict(event))
                    changed = True

            for candidate_id, state in list(self._candidates.items()):
                if candidate_id in claimed:
                    continue
                if observed_at - state.last_seen > scene.lost_tolerance_seconds:
                    changed = self._close_event(state, state.last_seen) or changed
                    del self._candidates[candidate_id]
            pending_io = (new_events, snapshot_tasks, changed)
        if _pending_io is not None:
            _pending_io.append(pending_io)
        else:
            self._finalize_candidate_io(*pending_io)
        return new_events

    def _finalize_candidate_io(
        self,
        new_events: list[dict],
        snapshot_tasks: list[tuple[str, np.ndarray]],
        changed: bool,
    ) -> None:
        failed_snapshots: set[str] = set()
        for event_id, snapshot_frame in snapshot_tasks:
            try:
                if not self._save_snapshot(snapshot_frame, event_id):
                    failed_snapshots.add(event_id)
            except Exception:
                failed_snapshots.add(event_id)
        if failed_snapshots:
            with self._lock:
                for event in self._events:
                    if event.get("event_id") in failed_snapshots:
                        event["snapshot"] = ""
                        event["snapshot_url"] = ""
                for event in new_events:
                    if event.get("event_id") in failed_snapshots:
                        event["snapshot"] = ""
                        event["snapshot_url"] = ""
        if changed:
            self._save_events()

    def _foreground_candidates(
        self, frame: np.ndarray, scene: RoadAbnormalScene, known_objects: list[dict]
    ) -> list[dict]:
        if self._background is None:
            if self._reset_runtime(scene):
                self._save_events()
        candidates, next_frame_count = self._legacy_foreground_candidates(
            frame,
            scene,
            known_objects,
            self._background,
            self._frame_count,
        )
        self._frame_count = next_frame_count
        return candidates

    @staticmethod
    def _legacy_foreground_candidates(
        frame: np.ndarray,
        scene: RoadAbnormalScene,
        known_objects: list[dict],
        background: object,
        frame_count: int,
    ) -> tuple[list[dict], int]:
        if background is None:
            raise RuntimeError("legacy MOG2 background is unavailable")
        learning_rate = (
            scene.learning_rate
            if frame_count < scene.warmup_frames
            else 0.0
        )
        mask = background.apply(frame, learningRate=learning_rate)
        next_frame_count = frame_count + 1
        if scene.detect_shadows:
            _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
        roi_mask = np.zeros(mask.shape, dtype=np.uint8)
        height, width = mask.shape
        for zone in scene.zones:
            if not zone.enabled:
                continue
            points = np.array(
                [[round(x * width), round(y * height)] for x, y in zone.points],
                dtype=np.int32,
            )
            cv2.fillPoly(roi_mask, [points], 255)
        mask = cv2.bitwise_and(mask, roi_mask)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        if next_frame_count <= scene.warmup_frames:
            return [], next_frame_count
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_area = width * height * scene.min_area_ratio
        normal_boxes = [
            tuple(item["bbox"])
            for item in known_objects
            if item.get("class_name") in scene.normal_classes
        ]
        known_anomaly_boxes = [
            tuple(item["bbox"])
            for item in known_objects
            if item.get("class_name") in scene.anomaly_classes
        ]
        candidates: list[dict] = []
        for contour in contours:
            if cv2.contourArea(contour) < min_area:
                continue
            x, y, box_width, box_height = cv2.boundingRect(contour)
            bbox = (float(x), float(y), float(x + box_width), float(y + box_height))
            if any(_iou(bbox, item) >= 0.25 for item in normal_boxes):
                continue
            if any(_iou(bbox, item) >= 0.25 for item in known_anomaly_boxes):
                continue
            area_ratio = box_width * box_height / max(1.0, width * height)
            candidates.append(
                {
                    "source": "MOG2",
                    "anomaly_type": "unknown_obstacle",
                    "class_name": "unknown",
                    "class_name_cn": "未知障碍物",
                    "confidence": min(0.95, 0.45 + area_ratio * 10.0),
                    "bbox": bbox,
                    "track_id": -1,
                }
            )
        return candidates, next_frame_count

    def _trained_mog_candidates(
        self,
        frame: np.ndarray,
        scene: RoadAbnormalScene,
        known_objects: list[dict],
        observed_at: float,
    ) -> list[dict]:
        candidates, next_frame_count = self._trained_mog_candidates_for_runtime(
            frame,
            scene,
            known_objects,
            observed_at,
            self._mog_engine,
            self._frame_count,
        )
        self._frame_count = next_frame_count
        return candidates

    @staticmethod
    def _trained_mog_candidates_for_runtime(
        frame: np.ndarray,
        scene: RoadAbnormalScene,
        known_objects: list[dict],
        observed_at: float,
        mog_engine: object,
        frame_count: int,
    ) -> tuple[list[dict], int]:
        if mog_engine is None:
            raise RuntimeError("trained MOG engine is unavailable")
        height, width = frame.shape[:2]
        polygons = [
            [
                (round(x * width), round(y * height))
                for x, y in zone.points
            ]
            for zone in scene.zones
            if zone.enabled
        ]
        RoadAbnormalMonitor._set_mog_rois(mog_engine, polygons)
        normal_boxes = [
            (
                *tuple(item["bbox"]),
                float(item.get("confidence", 1.0)),
            )
            for item in known_objects
            if item.get("class_name") in scene.normal_classes
        ]
        alerts = mog_engine.process(
            frame,
            normal_boxes,
            frame_id=frame_count,
            timestamp=observed_at,
        )
        next_frame_count = frame_count + 1
        candidates: list[dict] = []
        for alert in alerts:
            if isinstance(alert, dict):
                position = alert.get("position", ())
                anomaly_type = str(alert.get("anomaly_type", "unknown_obstacle"))
                confidence = float(alert.get("confidence", 0.5))
                observed_duration = max(
                    0.0,
                    float(alert.get("observed_duration", 0.0)),
                )
            else:
                position = getattr(alert, "position", ())
                anomaly_type = str(
                    getattr(alert, "anomaly_type", "unknown_obstacle")
                )
                confidence = float(getattr(alert, "confidence", 0.5))
                observed_duration = max(
                    0.0,
                    float(getattr(alert, "observed_duration", 0.0)),
                )
            if len(position) != 4:
                continue
            x, y, box_width, box_height = (float(value) for value in position)
            candidates.append(
                {
                    "source": "MOG",
                    "anomaly_type": anomaly_type,
                    "class_name": "unknown",
                    "class_name_cn": "未知障碍物",
                    "confidence": confidence,
                    "bbox": (x, y, x + box_width, y + box_height),
                    "track_id": -1,
                    "observed_duration": observed_duration,
                }
            )
        return candidates, next_frame_count

    @staticmethod
    def _known_anomaly_candidates(
        objects: Iterable[dict], scene: RoadAbnormalScene
    ) -> list[dict]:
        return [
            {
                **item,
                "source": "YOLO",
                "anomaly_type": "prohibited_road_user",
            }
            for item in objects
            if item.get("class_name") in scene.anomaly_classes
        ]

    @staticmethod
    def _candidate_is_covered(
        candidate_bbox: BBox,
        object_bbox: BBox,
    ) -> bool:
        left = max(candidate_bbox[0], object_bbox[0])
        top = max(candidate_bbox[1], object_bbox[1])
        right = min(candidate_bbox[2], object_bbox[2])
        bottom = min(candidate_bbox[3], object_bbox[3])
        intersection = max(0.0, right - left) * max(0.0, bottom - top)
        candidate_area = max(
            1.0,
            (candidate_bbox[2] - candidate_bbox[0])
            * (candidate_bbox[3] - candidate_bbox[1]),
        )
        center = (
            (candidate_bbox[0] + candidate_bbox[2]) / 2.0,
            (candidate_bbox[1] + candidate_bbox[3]) / 2.0,
        )
        return (
            intersection / candidate_area > 0.5
            or object_bbox[0] <= center[0] <= object_bbox[2]
            and object_bbox[1] <= center[1] <= object_bbox[3]
        )

    @classmethod
    def _mog_candidates_not_covered_by_known_objects(
        cls,
        candidates: Iterable[dict],
        objects: Iterable[dict],
    ) -> list[dict]:
        object_boxes = [tuple(item["bbox"]) for item in objects]
        return [
            candidate
            for candidate in candidates
            if not any(
                cls._candidate_is_covered(
                    tuple(candidate["bbox"]),
                    object_bbox,
                )
                for object_bbox in object_boxes
            )
        ]

    def process_frame(
        self, camera_id: str, frame: np.ndarray, now: float | None = None
    ) -> np.ndarray:
        observed_at = time.time() if now is None else float(now)
        with self._lock:
            scene_id = self._active_scene_id
            scene = self._scenes.get(scene_id)
            if not self._running or scene is None or scene.camera_id != camera_id:
                return frame
            generation = self._runtime_generation
            options = self._pipeline_options

        pipeline_enabled = options.enabled if options is not None else True
        if not pipeline_enabled:
            return frame
        detector_interval = (
            options.frame_interval
            if options is not None
            else scene.inference_interval
        )
        detector_threshold = (
            options.yolo_threshold
            if options is not None
            else scene.yolo_threshold
        )

        pending_candidate_io: list = []
        overlay_candidates: list[dict] = []
        with self._inference_lock:
            with self._lock:
                if (
                    self._runtime_generation != generation
                    or not self._running
                    or self._active_scene_id != scene_id
                    or self._scenes.get(scene_id) is not scene
                ):
                    return frame
                detector = self._detector
                mog_engine = self._mog_engine
                background = self._background
                road_abnormal_mode = self._road_abnormal_mode
                frame_count = self._frame_count
                known_objects = [dict(item) for item in self._known_objects]
                known_objects_frame = self._known_objects_frame

            detector_error = ""
            mog_error = ""
            detected_this_frame = False
            if frame_count % detector_interval == 0:
                try:
                    known_objects = detector.detect(frame, detector_threshold)
                    known_objects_frame = frame_count
                    detected_this_frame = True
                except Exception as exc:
                    detector_error = (
                        "道路目标检测降级: " + type(exc).__name__
                    )

            try:
                if road_abnormal_mode == "mog":
                    foreground, next_frame_count = (
                        self._trained_mog_candidates_for_runtime(
                            frame,
                            scene,
                            known_objects,
                            observed_at,
                            mog_engine,
                            frame_count,
                        )
                    )
                else:
                    foreground, next_frame_count = (
                        self._legacy_foreground_candidates(
                            frame,
                            scene,
                            known_objects,
                            background,
                            frame_count,
                        )
                    )
            except Exception as exc:
                foreground = []
                next_frame_count = frame_count + 1
                mog_error = "道路异常检测失败: " + type(exc).__name__

            if (
                road_abnormal_mode == "mog"
                and foreground
                and frame_count - known_objects_frame > 2
            ):
                try:
                    known_objects = detector.detect(frame, detector_threshold)
                    known_objects_frame = frame_count
                    detected_this_frame = True
                    detector_error = ""
                except Exception as exc:
                    detector_error = (
                        "道路目标检测降级: " + type(exc).__name__
                    )

            if road_abnormal_mode == "mog" and detected_this_frame:
                foreground = self._mog_candidates_not_covered_by_known_objects(
                    foreground,
                    known_objects,
                )
            known = self._known_anomaly_candidates(known_objects, scene)
            combined_candidates = [*known, *foreground]
            runtime_error = mog_error or detector_error

            with self._lock:
                if (
                    self._runtime_generation != generation
                    or not self._running
                    or self._active_scene_id != scene_id
                    or self._scenes.get(scene_id) is not scene
                ):
                    return frame
                self._known_objects = known_objects
                self._known_objects_frame = known_objects_frame
                self._frame_count = next_frame_count
                try:
                    height, width = frame.shape[:2]
                    self.update_candidates(
                        camera_id,
                        combined_candidates,
                        (width, height),
                        frame=frame,
                        now=observed_at,
                        _pending_io=pending_candidate_io,
                    )
                    self._last_error = runtime_error
                except Exception as exc:
                    self._last_error = (
                        "道路异常检测失败: " + type(exc).__name__
                        )
                overlay_candidates = [
                    {
                        "bbox": tuple(state.bbox),
                        "event_id": state.event_id,
                        "class_name": state.class_name,
                        "duration_seconds": state.duration_seconds,
                        "lane_name": state.lane_name,
                    }
                    for state in self._candidates.values()
                ]
        for pending_io in pending_candidate_io:
            try:
                self._finalize_candidate_io(*pending_io)
            except Exception as exc:
                with self._lock:
                    if self._runtime_generation == generation:
                        self._last_error = (
                            "道路异常检测失败: " + type(exc).__name__
                        )
        return self._draw_overlay(frame, scene, overlay_candidates)

    def _draw_overlay(
        self,
        frame: np.ndarray,
        scene: RoadAbnormalScene,
        candidates: Iterable[dict] | None = None,
    ) -> np.ndarray:
        if candidates is None:
            with self._lock:
                candidates = [
                    {
                        "bbox": tuple(state.bbox),
                        "event_id": state.event_id,
                        "class_name": state.class_name,
                        "duration_seconds": state.duration_seconds,
                        "lane_name": state.lane_name,
                    }
                    for state in self._candidates.values()
                ]
        annotated = frame.copy()
        height, width = frame.shape[:2]
        for zone in scene.zones:
            points = np.array(
                [[round(x * width), round(y * height)] for x, y in zone.points],
                dtype=np.int32,
            )
            cv2.polylines(annotated, [points], True, (0, 196, 255), 2, cv2.LINE_AA)
        for state in candidates:
            x1, y1, x2, y2 = (
                round(state["bbox"][0] * width),
                round(state["bbox"][1] * height),
                round(state["bbox"][2] * width),
                round(state["bbox"][3] * height),
            )
            color = (40, 40, 230) if state["event_id"] else (0, 170, 255)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            label = (
                f"{state['class_name']} {state['duration_seconds']:.1f}s "
                f"{state['lane_name']}"
            )
            cv2.putText(
                annotated,
                label,
                (x1, max(18, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )
        return annotated

    def status(self, *, expire: bool = False, now: float | None = None) -> dict:
        changed = False
        expired = False
        with self._lock:
            scene = self._scenes.get(self._active_scene_id)
            if expire and self._running and scene:
                observed_at = time.time() if now is None else float(now)
                for candidate_id, state in list(self._candidates.items()):
                    if observed_at - state.last_seen > scene.lost_tolerance_seconds:
                        changed = self._close_event(state, state.last_seen) or changed
                        del self._candidates[candidate_id]
                        expired = True
                if expired:
                    self._runtime_generation += 1
            candidates = [
                {
                    **asdict(state),
                    "status": "alarmed" if state.event_id else "pending",
                    "threshold_seconds": scene.persistence_seconds if scene else 0.0,
                }
                for state in sorted(
                    self._candidates.values(),
                    key=lambda item: item.duration_seconds,
                    reverse=True,
                )
            ]
            events = [dict(event) for event in reversed(self._events[-100:])]
            result = {
                "running": self._running,
                "active_scene_id": self._active_scene_id,
                "active_scene": self._scene_payload(scene),
                "last_camera_id": self._last_camera_id,
                "last_error": self._last_error,
                "warming_up": bool(
                    self._running and scene and self._frame_count <= scene.warmup_frames
                ),
                "warmup_progress": (
                    min(1.0, self._frame_count / max(1, scene.warmup_frames))
                    if scene and scene.warmup_frames
                    else 1.0
                ),
                "candidates": candidates,
                "events": events,
                "metrics": {
                    "zones": len(scene.zones) if scene else 0,
                    "active_candidates": len(candidates),
                    "active_alarms": sum(1 for item in candidates if item["status"] == "alarmed"),
                    "total_events": len(self._events),
                },
            }
        if changed:
            self._save_events()
        return result
