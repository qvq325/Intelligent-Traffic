"""No-parking scene configuration and dwell-time event tracking."""

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


Point = tuple[float, float]
DEFAULT_VEHICLE_CLASSES = ["car", "motorcycle", "bus", "truck"]


@dataclass(slots=True)
class NoParkingZone:
    zone_id: str
    name: str
    points: list[Point]
    dwell_seconds: float = 10.0
    lost_tolerance_seconds: float = 2.0
    enabled: bool = True
    vehicle_classes: list[str] = field(
        default_factory=lambda: list(DEFAULT_VEHICLE_CLASSES)
    )


@dataclass(slots=True)
class NoParkingScene:
    scene_id: str
    name: str
    camera_id: str
    reference_image: str
    reference_width: int
    reference_height: int
    zones: list[NoParkingZone]
    created_at: float
    updated_at: float


@dataclass(slots=True)
class ParkingTrackState:
    zone_id: str
    zone_name: str
    track_id: int
    vehicle_class: str
    plate_text: str
    bbox: tuple[float, float, float, float]
    anchor: Point
    entered_at: float
    last_seen: float
    dwell_seconds: float = 0.0
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


def point_in_polygon(point: Point, points: Sequence[Point]) -> bool:
    inside = False
    x, y = point
    for start, end in zip(points, [*points[1:], points[0]]):
        if (start[1] > y) == (end[1] > y):
            continue
        intersection_x = (
            (end[0] - start[0]) * (y - start[1]) / (end[1] - start[1])
            + start[0]
        )
        if x < intersection_x:
            inside = not inside
    return inside


class NoParkingMonitor:
    """Persist scene profiles and evaluate tracked vehicle dwell time."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.references_dir = self.root_dir / "references"
        self.scenes_file = self.root_dir / "scenes.json"
        self.events_file = self.root_dir / "events.json"
        self._lock = threading.RLock()
        self._scenes: dict[str, NoParkingScene] = {}
        self._events: list[dict] = []
        self._tracks: dict[tuple[str, int], ParkingTrackState] = {}
        self._active_scene_id = ""
        self._running = False
        self._last_camera_id = ""
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
                        NoParkingZone(
                            zone_id=str(raw_zone["zone_id"]),
                            name=str(raw_zone.get("name") or raw_zone["zone_id"]),
                            points=[
                                (float(point[0]), float(point[1]))
                                for point in raw_zone["points"]
                            ],
                            dwell_seconds=float(raw_zone.get("dwell_seconds", 10.0)),
                            lost_tolerance_seconds=float(
                                raw_zone.get("lost_tolerance_seconds", 2.0)
                            ),
                            enabled=bool(raw_zone.get("enabled", True)),
                            vehicle_classes=list(
                                raw_zone.get("vehicle_classes")
                                or DEFAULT_VEHICLE_CLASSES
                            ),
                        )
                        for raw_zone in raw_scene.get("zones", [])
                    ]
                    scene = NoParkingScene(
                        scene_id=str(raw_scene["scene_id"]),
                        name=str(raw_scene.get("name") or raw_scene["scene_id"]),
                        camera_id=str(raw_scene["camera_id"]),
                        reference_image=str(raw_scene.get("reference_image", "")),
                        reference_width=int(raw_scene.get("reference_width", 1)),
                        reference_height=int(raw_scene.get("reference_height", 1)),
                        zones=zones,
                        created_at=float(raw_scene.get("created_at", time.time())),
                        updated_at=float(raw_scene.get("updated_at", time.time())),
                    )
                    self._scenes[scene.scene_id] = scene
                except (KeyError, TypeError, ValueError, IndexError):
                    continue

            try:
                event_payload = json.loads(self.events_file.read_text(encoding="utf-8"))
                self._events = [
                    dict(event) for event in event_payload.get("events", [])
                    if isinstance(event, dict)
                ][-500:]
            except (OSError, ValueError, TypeError):
                self._events = []

    def _write_json(self, path: Path, payload: dict) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _save_scenes(self) -> None:
        self._write_json(
            self.scenes_file,
            {"version": 1, "scenes": [self._scene_dict(scene) for scene in self._scenes.values()]},
        )

    def _save_events(self) -> None:
        self._events = self._events[-500:]
        self._write_json(self.events_file, {"version": 1, "events": self._events})

    @staticmethod
    def _scene_dict(scene: NoParkingScene) -> dict:
        return {
            **asdict(scene),
            "zones": [asdict(zone) for zone in scene.zones],
        }

    def _scene_payload(self, scene: NoParkingScene) -> dict:
        return {
            **self._scene_dict(scene),
            "reference_url": (
                f"/api/no-parking/references/{scene.reference_image}"
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
            scene = self._scenes.get(scene_id)
            return self._scene_payload(scene) if scene else None

    def capture_reference(
        self,
        jpeg: bytes,
        camera_id: str,
        width: int,
        height: int,
    ) -> dict:
        if not jpeg:
            raise ValueError("参考帧为空")
        filename = f"reference_{uuid4().hex}.jpg"
        with self._lock:
            self.references_dir.mkdir(parents=True, exist_ok=True)
            (self.references_dir / filename).write_bytes(jpeg)
        return {
            "filename": filename,
            "url": f"/api/no-parking/references/{filename}",
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

    def upsert_scene(self, payload: dict) -> dict:
        now = time.time()
        reference_image = str(payload.get("reference_image", ""))
        if self.reference_path(reference_image) is None:
            raise ValueError("参考帧不存在，请重新截取")

        zones: list[NoParkingZone] = []
        for raw_zone in payload.get("zones", []):
            points = [(float(x), float(y)) for x, y in raw_zone.get("points", [])]
            if len(points) > 3 and math.dist(points[0], points[-1]) < 1e-8:
                points.pop()
            if len(points) < 3:
                raise ValueError("禁停区域至少需要三个点")
            if any(
                not math.isfinite(value) or value < 0.0 or value > 1.0
                for point in points
                for value in point
            ):
                raise ValueError("禁停区域坐标必须位于画面范围内")
            if _polygon_area(points) < 1e-5:
                raise ValueError("禁停区域不能在同一直线上")
            classes = [
                item for item in raw_zone.get("vehicle_classes", [])
                if item in DEFAULT_VEHICLE_CLASSES
            ] or list(DEFAULT_VEHICLE_CLASSES)
            zones.append(
                NoParkingZone(
                    zone_id=_identifier(str(raw_zone.get("zone_id", "")), "zone"),
                    name=str(raw_zone.get("name", "")).strip() or "禁停区域",
                    points=points,
                    dwell_seconds=max(1.0, float(raw_zone.get("dwell_seconds", 10.0))),
                    lost_tolerance_seconds=max(
                        0.1, float(raw_zone.get("lost_tolerance_seconds", 2.0))
                    ),
                    enabled=bool(raw_zone.get("enabled", True)),
                    vehicle_classes=classes,
                )
            )
        if not zones:
            raise ValueError("至少需要配置一个禁停区域")

        requested_id = str(payload.get("scene_id", ""))
        scene_id = _identifier(requested_id, "scene")
        with self._lock:
            existing = self._scenes.get(scene_id)
            if existing and self._active_scene_id == scene_id:
                if self._close_all_tracks(now):
                    self._save_events()
                self._tracks.clear()
            scene = NoParkingScene(
                scene_id=scene_id,
                name=str(payload.get("name", "")).strip() or "禁停监控场景",
                camera_id=str(payload.get("camera_id", "")).strip(),
                reference_image=reference_image,
                reference_width=max(1, int(payload.get("reference_width", 1))),
                reference_height=max(1, int(payload.get("reference_height", 1))),
                zones=zones,
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
            self._scenes[scene_id] = scene
            self._save_scenes()
            return self._scene_payload(scene)

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
                item.reference_image == scene.reference_image
                for item in self._scenes.values()
            ):
                path = self.reference_path(scene.reference_image)
                if path:
                    path.unlink(missing_ok=True)
            return True

    def start(self, scene_id: str) -> dict:
        with self._lock:
            if scene_id not in self._scenes:
                raise ValueError("禁停场景不存在")
            if self._close_all_tracks(time.time()):
                self._save_events()
            self._tracks.clear()
            self._active_scene_id = scene_id
            self._last_camera_id = ""
            self._running = True
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            if self._close_all_tracks(time.time()):
                self._save_events()
            self._tracks.clear()
            self._running = False
            return self.status()

    def clear_events(self) -> dict:
        with self._lock:
            self._events.clear()
            self._save_events()
            return self.status()

    def _close_event(self, state: ParkingTrackState, ended_at: float) -> bool:
        if not state.event_id:
            return False
        for event in reversed(self._events):
            if event.get("event_id") != state.event_id or event.get("ended_at") is not None:
                continue
            event["ended_at"] = ended_at
            event["duration_seconds"] = round(state.dwell_seconds, 2)
            return True
        return False

    def _close_all_tracks(self, now: float) -> bool:
        changed = False
        for state in self._tracks.values():
            changed = self._close_event(state, min(now, state.last_seen)) or changed
        return changed

    def _expire_tracks(self, scene: NoParkingScene, now: float) -> bool:
        events_changed = False
        for key, state in list(self._tracks.items()):
            zone = next(
                (item for item in scene.zones if item.zone_id == state.zone_id),
                None,
            )
            tolerance = zone.lost_tolerance_seconds if zone else 0.1
            if now - state.last_seen > tolerance:
                events_changed = self._close_event(state, state.last_seen) or events_changed
                del self._tracks[key]
        return events_changed

    def update_detections(
        self,
        camera_id: str,
        detections: Iterable[object],
        frame_size: tuple[int, int],
        now: float | None = None,
    ) -> list[dict]:
        observed_at = time.time() if now is None else float(now)
        width, height = frame_size
        if width <= 0 or height <= 0:
            return []

        with self._lock:
            self._last_camera_id = camera_id
            scene = self._scenes.get(self._active_scene_id)
            if not self._running or scene is None or scene.camera_id != camera_id:
                return []

            seen: dict[tuple[str, int], tuple[NoParkingZone, dict]] = {}
            for detection in detections:
                track_id = int(getattr(detection, "track_id", -1))
                if track_id < 0:
                    continue
                bbox = tuple(float(value) for value in getattr(detection, "vehicle_bbox"))
                normalized_bbox = (
                    bbox[0] / width,
                    bbox[1] / height,
                    bbox[2] / width,
                    bbox[3] / height,
                )
                anchor = (
                    (normalized_bbox[0] + normalized_bbox[2]) / 2.0,
                    normalized_bbox[3],
                )
                vehicle_class = str(getattr(detection, "vehicle_class", "vehicle"))
                details = {
                    "vehicle_class": vehicle_class,
                    "plate_text": str(getattr(detection, "plate_text", "") or ""),
                    "bbox": normalized_bbox,
                    "anchor": anchor,
                }
                for zone in scene.zones:
                    if (
                        zone.enabled
                        and vehicle_class in zone.vehicle_classes
                        and point_in_polygon(anchor, zone.points)
                    ):
                        seen[(zone.zone_id, track_id)] = (zone, details)

            new_events: list[dict] = []
            events_changed = False
            for key, (zone, details) in seen.items():
                state = self._tracks.get(key)
                if state is None:
                    state = ParkingTrackState(
                        zone_id=zone.zone_id,
                        zone_name=zone.name,
                        track_id=key[1],
                        vehicle_class=details["vehicle_class"],
                        plate_text=details["plate_text"],
                        bbox=details["bbox"],
                        anchor=details["anchor"],
                        entered_at=observed_at,
                        last_seen=observed_at,
                    )
                    self._tracks[key] = state
                else:
                    gap = max(0.0, observed_at - state.last_seen)
                    if gap > zone.lost_tolerance_seconds:
                        events_changed = self._close_event(state, state.last_seen) or events_changed
                        state.entered_at = observed_at
                        state.dwell_seconds = 0.0
                        state.event_id = ""
                    else:
                        state.dwell_seconds += gap
                    state.last_seen = observed_at
                    state.vehicle_class = details["vehicle_class"]
                    state.plate_text = details["plate_text"] or state.plate_text
                    state.bbox = details["bbox"]
                    state.anchor = details["anchor"]

                if not state.event_id and state.dwell_seconds >= zone.dwell_seconds:
                    event = {
                        "event_id": f"event_{uuid4().hex}",
                        "scene_id": scene.scene_id,
                        "scene_name": scene.name,
                        "camera_id": camera_id,
                        "zone_id": zone.zone_id,
                        "zone_name": zone.name,
                        "track_id": state.track_id,
                        "vehicle_class": state.vehicle_class,
                        "plate_text": state.plate_text,
                        "entered_at": state.entered_at,
                        "triggered_at": observed_at,
                        "ended_at": None,
                        "duration_seconds": round(state.dwell_seconds, 2),
                    }
                    state.event_id = event["event_id"]
                    self._events.append(event)
                    new_events.append(dict(event))
                    events_changed = True

            for key, state in list(self._tracks.items()):
                if key in seen:
                    continue
                zone = next(
                    (item for item in scene.zones if item.zone_id == state.zone_id),
                    None,
                )
                tolerance = zone.lost_tolerance_seconds if zone else 0.1
                if observed_at - state.last_seen > tolerance:
                    events_changed = self._close_event(state, state.last_seen) or events_changed
                    del self._tracks[key]

            if events_changed:
                self._save_events()
            return new_events

    def _event_payload(self, event: dict) -> dict:
        payload = dict(event)
        if payload.get("ended_at") is None:
            state = next(
                (
                    track for track in self._tracks.values()
                    if track.event_id == payload.get("event_id")
                ),
                None,
            )
            if state:
                payload["duration_seconds"] = round(state.dwell_seconds, 2)
        return payload

    def status(self, *, expire: bool = False, now: float | None = None) -> dict:
        with self._lock:
            scene = self._scenes.get(self._active_scene_id)
            observed_at = time.time() if now is None else float(now)
            if expire and self._running and scene and self._expire_tracks(scene, observed_at):
                self._save_events()
            tracks = [
                {
                    **asdict(track),
                    "status": "alarmed" if track.event_id else "pending",
                    "threshold_seconds": next(
                        (
                            zone.dwell_seconds for zone in scene.zones
                            if zone.zone_id == track.zone_id
                        ),
                        0.0,
                    ) if scene else 0.0,
                }
                for track in sorted(
                    self._tracks.values(), key=lambda item: item.dwell_seconds, reverse=True
                )
            ]
            events = [
                self._event_payload(event)
                for event in reversed(self._events[-100:])
            ]
            return {
                "running": self._running,
                "active_scene_id": self._active_scene_id,
                "active_scene": self._scene_payload(scene) if scene else None,
                "last_camera_id": self._last_camera_id,
                "tracks": tracks,
                "events": events,
                "metrics": {
                    "zones": len(scene.zones) if scene else 0,
                    "active_tracks": len(tracks),
                    "active_alarms": sum(1 for track in tracks if track["status"] == "alarmed"),
                    "total_events": len(self._events),
                },
            }
