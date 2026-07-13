"""Road-abnormal scene configuration, candidate fusion, and event tracking."""

from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence
from uuid import uuid4

import cv2
import numpy as np

from backend.no_parking import point_in_polygon


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

    def __init__(self, model_path: Path, device: str = "cpu") -> None:
        self.model_path = Path(model_path)
        self.device = device
        self._model = None

    def detect(self, frame: np.ndarray, threshold: float) -> list[dict]:
        if self._model is None:
            from ultralytics import YOLO

            self._model = YOLO(str(self.model_path))
        results = self._model.predict(
            frame,
            classes=list(ROAD_OBJECT_CLASSES),
            conf=threshold,
            device=self.device,
            imgsz=640,
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
    ) -> None:
        self.root_dir = Path(root_dir)
        self.references_dir = self.root_dir / "references"
        self.snapshots_dir = self.root_dir / "snapshots"
        self.scenes_file = self.root_dir / "scenes.json"
        self.events_file = self.root_dir / "events.json"
        self._detector = detector or RoadObjectDetector(model_path, device)
        self._lock = threading.RLock()
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
        self._load()

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
        self._events = self._events[-500:]
        self._write_json(self.events_file, {"version": 1, "events": self._events})

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
        with self._lock:
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
            self._scenes[scene_id] = scene
            if self._active_scene_id == scene_id:
                self._reset_runtime(scene)
            self._save_scenes()
            return self._scene_payload(scene) or {}

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

    def _reset_runtime(self, scene: RoadAbnormalScene) -> None:
        self._close_all_candidates(time.time())
        self._candidates.clear()
        self._frame_count = 0
        self._known_objects = []
        self._last_error = ""
        self._background = cv2.createBackgroundSubtractorMOG2(
            history=scene.history,
            varThreshold=scene.variance_threshold,
            detectShadows=scene.detect_shadows,
        )

    def start(self, scene_id: str) -> dict:
        with self._lock:
            scene = self._scenes.get(scene_id)
            if scene is None:
                raise ValueError("道路异常场景不存在")
            self._active_scene_id = scene_id
            self._last_camera_id = ""
            self._running = True
            self._reset_runtime(scene)
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            changed = self._close_all_candidates(time.time())
            self._candidates.clear()
            self._running = False
            self._background = None
            if changed:
                self._save_events()
            return self.status()

    def clear_events(self) -> dict:
        with self._lock:
            self._events.clear()
            self._save_events()
            return self.status()

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
                        first_seen=observed_at,
                        last_seen=observed_at,
                    )
                    self._candidates[state.candidate_id] = state
                else:
                    gap = max(0.0, observed_at - state.last_seen)
                    if gap > scene.lost_tolerance_seconds:
                        changed = self._close_event(state, state.last_seen) or changed
                        state.first_seen = observed_at
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
                    snapshot = self._save_snapshot(frame, event_id)
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
            if changed:
                self._save_events()
            return new_events

    def _foreground_candidates(
        self, frame: np.ndarray, scene: RoadAbnormalScene, known_objects: list[dict]
    ) -> list[dict]:
        if self._background is None:
            self._reset_runtime(scene)
        learning_rate = (
            scene.learning_rate
            if self._frame_count < scene.warmup_frames
            else 0.0
        )
        mask = self._background.apply(frame, learningRate=learning_rate)
        self._frame_count += 1
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
        if self._frame_count <= scene.warmup_frames:
            return []
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
        return candidates

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

    def process_frame(
        self, camera_id: str, frame: np.ndarray, now: float | None = None
    ) -> np.ndarray:
        with self._lock:
            scene = self._scenes.get(self._active_scene_id)
            if not self._running or scene is None or scene.camera_id != camera_id:
                return frame
            inference_due = self._frame_count % scene.inference_interval == 0
            try:
                if inference_due:
                    self._known_objects = self._detector.detect(
                        frame, scene.yolo_threshold
                    )
                foreground = self._foreground_candidates(
                    frame, scene, self._known_objects
                )
                known = self._known_anomaly_candidates(self._known_objects, scene)
                height, width = frame.shape[:2]
                self.update_candidates(
                    camera_id,
                    [*known, *foreground],
                    (width, height),
                    frame=frame,
                    now=now,
                )
                self._last_error = ""
            except Exception as exc:
                self._last_error = f"道路异常检测失败: {exc}"
            return self._draw_overlay(frame, scene)

    def _draw_overlay(self, frame: np.ndarray, scene: RoadAbnormalScene) -> np.ndarray:
        annotated = frame.copy()
        height, width = frame.shape[:2]
        for zone in scene.zones:
            points = np.array(
                [[round(x * width), round(y * height)] for x, y in zone.points],
                dtype=np.int32,
            )
            cv2.polylines(annotated, [points], True, (0, 196, 255), 2, cv2.LINE_AA)
        for state in self._candidates.values():
            x1, y1, x2, y2 = (
                round(state.bbox[0] * width),
                round(state.bbox[1] * height),
                round(state.bbox[2] * width),
                round(state.bbox[3] * height),
            )
            color = (40, 40, 230) if state.event_id else (0, 170, 255)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            label = f"{state.class_name} {state.duration_seconds:.1f}s {state.lane_name}"
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
        with self._lock:
            scene = self._scenes.get(self._active_scene_id)
            if expire and self._running and scene:
                observed_at = time.time() if now is None else float(now)
                changed = False
                for candidate_id, state in list(self._candidates.items()):
                    if observed_at - state.last_seen > scene.lost_tolerance_seconds:
                        changed = self._close_event(state, state.last_seen) or changed
                        del self._candidates[candidate_id]
                if changed:
                    self._save_events()
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
            return {
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
