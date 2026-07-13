"""Road topology, camera placement, vehicle tracks, and heat aggregation."""

from __future__ import annotations

import json
import math
import re
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


Point = Tuple[float, float]
TOPOLOGY_VERSION = 3
MAP_SOURCE_SIZE = (1105.0, 740.0)
MIN_ROAD_WIDTH = 4.0 / MAP_SOURCE_SIZE[1]
DEFAULT_ROAD_WIDTH = 36.0 / MAP_SOURCE_SIZE[1]
MAX_ROAD_WIDTH = 120.0 / MAP_SOURCE_SIZE[1]


@dataclass
class RoadSegment:
    segment_id: str
    name: str
    points: List[Point]
    capacity: int = 4
    level: str = "ground"
    direction: str = "双向"
    geometry_type: str = "polyline"
    road_width: float = DEFAULT_ROAD_WIDTH


@dataclass
class CameraPlacement:
    camera_id: str
    x: float
    y: float
    heading: float = 0.0
    view_range: float = 0.12
    segment_id: str = ""


@dataclass
class MapTrack:
    global_id: str
    camera_id: str
    local_track_id: int
    segment_id: str
    x: float
    y: float
    vehicle_class: str
    plate_text: str = ""
    last_seen: float = 0.0
    history: List[Point] = field(default_factory=list)


@dataclass
class SegmentState:
    segment_id: str
    vehicle_count: int
    flow_per_minute: int
    occupancy: float
    heat: float


def _map_points(*points: Tuple[int, int]) -> List[Point]:
    width, height = MAP_SOURCE_SIZE
    return [(x / width, y / height) for x, y in points]


def _map_point(x: int, y: int) -> Point:
    return _map_points((x, y))[0]


def default_segments() -> List[RoadSegment]:
    """Directional lane topology traced from sandpan/沙盘平面图2.png."""
    return [
        RoadSegment(
            "parking_west_clockwise", "停车场西环", _map_points(
                (136, 170), (126, 195), (126, 465), (140, 495), (185, 505),
                (215, 485), (215, 200), (200, 170), (136, 170),
            ), 5, "parking", "顺时针",
        ),
        RoadSegment(
            "parking_west_counterclockwise", "停车场西环", _map_points(
                (150, 185), (190, 185), (200, 205), (200, 465), (188, 488),
                (150, 480), (140, 460), (140, 205), (150, 185),
            ), 5, "parking", "逆时针",
        ),
        RoadSegment(
            "parking_east_clockwise", "停车场东环", _map_points(
                (270, 170), (250, 190), (250, 470), (270, 495), (365, 495),
                (390, 475), (390, 190), (370, 170), (270, 170),
            ), 6, "parking", "顺时针",
        ),
        RoadSegment(
            "parking_east_counterclockwise", "停车场东环", _map_points(
                (280, 185), (365, 185), (375, 200), (375, 460), (360, 480),
                (275, 480), (265, 465), (265, 200), (280, 185),
            ), 6, "parking", "逆时针",
        ),
        RoadSegment(
            "north_eastbound", "顶部横路", _map_points(
                (60, 135), (180, 135), (320, 135), (440, 135), (490, 135),
            ), 8, "ground", "东行",
        ),
        RoadSegment(
            "north_westbound", "顶部横路", _map_points(
                (490, 160), (440, 160), (320, 160), (180, 160), (60, 160),
            ), 8, "ground", "西行",
        ),
        RoadSegment(
            "main_clockwise", "右侧主环路", _map_points(
                (520, 520), (580, 545), (895, 545), (940, 520), (965, 470),
                (975, 200), (955, 145), (910, 110), (870, 95), (590, 95),
                (530, 110), (500, 145), (485, 205), (485, 455), (500, 500),
                (520, 520),
            ), 12, "ground", "顺时针",
        ),
        RoadSegment(
            "main_counterclockwise", "右侧主环路", _map_points(
                (510, 500), (505, 455), (505, 215), (515, 170), (550, 130),
                (600, 115), (865, 115), (905, 130), (935, 165), (950, 210),
                (950, 455), (930, 495), (890, 520), (580, 520), (535, 510),
                (510, 500),
            ), 12, "ground", "逆时针",
        ),
        RoadSegment(
            "middle_eastbound", "中部连接路", _map_points(
                (60, 545), (180, 545), (320, 545), (440, 545), (580, 545),
            ), 10, "ground", "东行",
        ),
        RoadSegment(
            "middle_westbound", "中部连接路", _map_points(
                (580, 570), (440, 570), (320, 570), (180, 570), (60, 570),
            ), 10, "ground", "西行",
        ),
        RoadSegment(
            "service_clockwise", "消防医院环路", _map_points(
                (590, 570), (910, 570), (940, 590), (945, 640), (920, 675),
                (590, 675), (565, 655), (565, 600), (590, 570),
            ), 8, "service", "顺时针",
        ),
        RoadSegment(
            "service_counterclockwise", "消防医院环路", _map_points(
                (600, 590), (585, 605), (585, 640), (600, 655), (910, 655),
                (925, 640), (925, 605), (910, 590), (600, 590),
            ), 8, "service", "逆时针",
        ),
        RoadSegment(
            "west_southbound", "西侧纵路", _map_points(
                (105, 180), (105, 330), (105, 480), (105, 585), (105, 690),
            ), 6, "ground", "南行",
        ),
        RoadSegment(
            "west_northbound", "西侧纵路", _map_points(
                (130, 690), (130, 585), (130, 480), (130, 330), (130, 180),
            ), 6, "ground", "北行",
        ),
        RoadSegment(
            "center_southbound", "中央纵路", _map_points(
                (455, 70), (455, 180), (455, 330), (455, 480), (455, 690),
            ), 8, "ground", "南行",
        ),
        RoadSegment(
            "center_northbound", "中央纵路", _map_points(
                (480, 690), (480, 480), (480, 330), (480, 180), (480, 70),
            ), 8, "ground", "北行",
        ),
        RoadSegment(
            "east_southbound", "东侧纵路", _map_points(
                (1010, 280), (1010, 420), (1010, 540), (1010, 690),
            ), 6, "ground", "南行",
        ),
        RoadSegment(
            "east_northbound", "东侧纵路", _map_points(
                (1040, 690), (1040, 540), (1040, 420), (1040, 280),
            ), 6, "ground", "北行",
        ),
    ]


DEFAULT_CAMERA_DATA = {
    "桥面": (*_map_point(700, 105), 90.0, "main_clockwise"),
    "停车场出口": (*_map_point(375, 450), 0.0, "parking_east_counterclockwise"),
    "行人": (*_map_point(455, 470), 180.0, "center_southbound"),
    "消防车识别": (*_map_point(650, 575), 90.0, "service_clockwise"),
    "桥出口": (*_map_point(950, 430), 180.0, "main_counterclockwise"),
    "桥入口": (*_map_point(530, 125), 90.0, "main_clockwise"),
    "道路2": (*_map_point(1010, 510), 180.0, "east_southbound"),
    "隧道(事故识别)": (*_map_point(105, 330), 180.0, "west_southbound"),
    "隧道(车辆数量)": (*_map_point(130, 470), 0.0, "west_northbound"),
    "道路3": (*_map_point(720, 545), 90.0, "middle_eastbound"),
    "停车场入口": (*_map_point(136, 220), 180.0, "parking_west_clockwise"),
    "道路1": (*_map_point(300, 135), 90.0, "north_eastbound"),
}


class TrafficMapModel:
    """Mutable runtime state backed by a small JSON topology configuration."""

    def __init__(self, config_path: Path, camera_ids: Iterable[str] = ()) -> None:
        self.config_path = Path(config_path)
        self.map_image_path = ""
        self.segments: Dict[str, RoadSegment] = {}
        self.cameras: Dict[str, CameraPlacement] = {}
        self.tracks: Dict[str, MapTrack] = {}
        self._local_to_global: Dict[Tuple[str, int], str] = {}
        self._plate_to_global: Dict[str, str] = {}
        self._flow_events: Dict[str, deque] = defaultdict(deque)
        self._last_segment_by_track: Dict[str, str] = {}
        self._heat: Dict[str, float] = defaultdict(float)
        self._next_global_id = 1
        self.track_timeout = 4.0
        self.load(camera_ids)

    def load(self, camera_ids: Iterable[str] = ()) -> None:
        data = {}
        if self.config_path.is_file():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                data = {}

        topology_is_current = data.get("version") == TOPOLOGY_VERSION
        self.map_image_path = str(data.get("map_image", "") or "")
        if topology_is_current and "segments" in data:
            raw_segments = data.get("segments") or []
        else:
            raw_segments = [asdict(segment) for segment in default_segments()]
        self.segments = {
            item["segment_id"]: RoadSegment(
                segment_id=item["segment_id"],
                name=item.get("name", item["segment_id"]),
                points=[(float(x), float(y)) for x, y in item["points"]],
                capacity=max(1, int(item.get("capacity", 4))),
                level=item.get("level", "ground"),
                direction=item.get("direction", "双向"),
                geometry_type=item.get("geometry_type", "polyline"),
                road_width=min(
                    MAX_ROAD_WIDTH,
                    max(MIN_ROAD_WIDTH, float(item.get("road_width", DEFAULT_ROAD_WIDTH))),
                ),
            )
            for item in raw_segments
        }

        self.cameras = {}
        raw_cameras = data.get("cameras", []) if topology_is_current else []
        for item in raw_cameras:
            placement = CameraPlacement(**item)
            self.cameras[placement.camera_id] = placement
        for camera_id in camera_ids:
            self.ensure_camera(camera_id)
        for camera in self.cameras.values():
            if camera.segment_id and camera.segment_id not in self.segments:
                camera.segment_id = self.nearest_segment((camera.x, camera.y))[0]

    def ensure_camera(self, camera_id: str) -> CameraPlacement:
        if camera_id not in self.cameras:
            x, y, heading, segment_id = DEFAULT_CAMERA_DATA.get(
                camera_id, (0.5, 0.5, 0.0, self.nearest_segment((0.5, 0.5))[0])
            )
            self.cameras[camera_id] = CameraPlacement(
                camera_id=camera_id,
                x=x,
                y=y,
                heading=heading,
                view_range=0.12,
                segment_id=segment_id,
            )
        return self.cameras[camera_id]

    def save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": TOPOLOGY_VERSION,
            "map_image": self.map_image_path,
            "segments": [asdict(segment) for segment in self.segments.values()],
            "cameras": [asdict(camera) for camera in self.cameras.values()],
        }
        self.config_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def next_segment_id(self) -> str:
        index = 1
        while f"custom_{index:03d}" in self.segments:
            index += 1
        return f"custom_{index:03d}"

    def upsert_segment(
        self,
        segment_id: str,
        name: str,
        points: Sequence[Point],
        capacity: int = 4,
        level: str = "ground",
        direction: str = "双向",
        geometry_type: str = "polyline",
        road_width: float = DEFAULT_ROAD_WIDTH,
    ) -> RoadSegment:
        segment_id = re.sub(r"[^A-Za-z0-9_-]+", "_", segment_id.strip())
        if not segment_id:
            segment_id = self.next_segment_id()
        geometry_type = geometry_type.strip().lower()
        if geometry_type not in {"polyline", "polygon"}:
            raise ValueError("道路形状必须是中心线或道路区域")
        minimum_points = 3 if geometry_type == "polygon" else 2
        if len(points) < minimum_points:
            message = (
                "道路区域至少需要三个点"
                if geometry_type == "polygon"
                else "道路至少需要两个点"
            )
            raise ValueError(message)
        normalized = [
            (min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y))))
            for x, y in points
        ]
        if geometry_type == "polygon" and math.dist(normalized[0], normalized[-1]) < 1e-6:
            normalized.pop()
        if len(normalized) < minimum_points:
            raise ValueError("道路区域至少需要三个不同的点")
        if all(math.dist(normalized[0], point) < 1e-6 for point in normalized[1:]):
            raise ValueError("道路起点和终点不能重合")
        if geometry_type == "polygon" and self._polygon_area(normalized) < 1e-6:
            raise ValueError("道路区域不能在同一直线上")
        road_width = float(road_width)
        if not math.isfinite(road_width):
            road_width = DEFAULT_ROAD_WIDTH
        road_width = min(MAX_ROAD_WIDTH, max(MIN_ROAD_WIDTH, road_width))
        segment = RoadSegment(
            segment_id=segment_id,
            name=name.strip() or segment_id,
            points=normalized,
            capacity=max(1, int(capacity)),
            level=level.strip() or "ground",
            direction=direction.strip() or "双向",
            geometry_type=geometry_type,
            road_width=road_width,
        )
        self.segments[segment_id] = segment
        return segment

    def delete_segment(self, segment_id: str) -> bool:
        if segment_id not in self.segments:
            return False
        del self.segments[segment_id]
        self._heat.pop(segment_id, None)
        self._flow_events.pop(segment_id, None)
        for camera in self.cameras.values():
            if camera.segment_id == segment_id:
                camera.segment_id = (
                    self.nearest_segment((camera.x, camera.y))[0]
                    if self.segments
                    else ""
                )
        if not self.segments:
            self.reset_runtime()
            return True
        for track in self.tracks.values():
            if track.segment_id == segment_id:
                replacement, point, _ = self.nearest_segment((track.x, track.y))
                track.segment_id = replacement
                track.x, track.y = point
        return True

    def set_camera(
        self,
        camera_id: str,
        x: float,
        y: float,
        heading: Optional[float] = None,
        view_range: Optional[float] = None,
        segment_id: Optional[str] = None,
    ) -> CameraPlacement:
        camera = self.ensure_camera(camera_id)
        camera.x = min(1.0, max(0.0, float(x)))
        camera.y = min(1.0, max(0.0, float(y)))
        if heading is not None:
            camera.heading = float(heading) % 360.0
        if view_range is not None:
            camera.view_range = min(0.5, max(0.01, float(view_range)))
        if segment_id is not None and segment_id in self.segments:
            camera.segment_id = segment_id
        elif not camera.segment_id:
            camera.segment_id = self.nearest_segment((camera.x, camera.y))[0]
        return camera

    @staticmethod
    def _project_to_line(point: Point, start: Point, end: Point) -> Tuple[Point, float, float]:
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            return start, 0.0, math.dist(point, start)
        ratio = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq
        ratio = min(1.0, max(0.0, ratio))
        projected = (start[0] + ratio * dx, start[1] + ratio * dy)
        return projected, ratio, math.dist(point, projected)

    @classmethod
    def _project_to_polyline(cls, point: Point, points: Sequence[Point]):
        best = (points[0], 0.0, float("inf"), 0, 0.0)
        distance_before = 0.0
        for index, (start, end) in enumerate(zip(points, points[1:])):
            projected, ratio, distance = cls._project_to_line(point, start, end)
            edge_length = math.dist(start, end)
            if distance < best[2]:
                best = (projected, distance_before + ratio * edge_length, distance, index, ratio)
            distance_before += edge_length
        return best

    def nearest_segment(self, point: Point) -> Tuple[str, Point, float]:
        best_id = ""
        best_point = point
        best_distance = float("inf")
        for segment in self.segments.values():
            projected, _, distance, _, _ = self._project_to_polyline(point, segment.points)
            if distance < best_distance:
                best_id, best_point, best_distance = segment.segment_id, projected, distance
        return best_id, best_point, best_distance

    @staticmethod
    def _polyline_length(points: Sequence[Point]) -> float:
        return sum(math.dist(a, b) for a, b in zip(points, points[1:]))

    @staticmethod
    def _point_at_distance(points: Sequence[Point], distance: float) -> Point:
        remaining = max(0.0, distance)
        for start, end in zip(points, points[1:]):
            length = math.dist(start, end)
            if remaining <= length:
                ratio = remaining / length if length else 0.0
                return (
                    start[0] + (end[0] - start[0]) * ratio,
                    start[1] + (end[1] - start[1]) * ratio,
                )
            remaining -= length
        return points[-1]

    @staticmethod
    def _polygon_area(points: Sequence[Point]) -> float:
        return abs(sum(
            start[0] * end[1] - end[0] * start[1]
            for start, end in zip(points, [*points[1:], points[0]])
        )) / 2.0

    @classmethod
    def _point_in_polygon(cls, point: Point, points: Sequence[Point]) -> bool:
        inside = False
        x, y = point
        for start, end in zip(points, [*points[1:], points[0]]):
            projected, _, distance = cls._project_to_line(point, start, end)
            if distance < 1e-9 and math.dist(projected, point) < 1e-9:
                return True
            if (start[1] > y) == (end[1] > y):
                continue
            intersection_x = (
                (end[0] - start[0]) * (y - start[1]) / (end[1] - start[1]) + start[0]
            )
            if x < intersection_x:
                inside = not inside
        return inside

    @staticmethod
    def _polygon_centroid(points: Sequence[Point]) -> Point:
        return (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )

    @classmethod
    def _nearest_polygon_point(cls, point: Point, points: Sequence[Point]) -> Point:
        candidates = [
            cls._project_to_line(point, start, end)
            for start, end in zip(points, [*points[1:], points[0]])
        ]
        return min(candidates, key=lambda item: item[2])[0]

    def map_detection(
        self,
        camera_id: str,
        bbox: Tuple[int, int, int, int],
        frame_size: Tuple[int, int],
    ) -> Tuple[str, Point]:
        if not self.segments:
            raise ValueError("尚未绘制道路")
        camera = self.ensure_camera(camera_id)
        segment = self.segments.get(camera.segment_id)
        if segment is None:
            segment_id, _, _ = self.nearest_segment((camera.x, camera.y))
            segment = self.segments[segment_id]
            camera.segment_id = segment_id

        width, height = frame_size
        bottom_y = min(1.0, max(0.0, bbox[3] / max(1, height)))
        heading_rad = math.radians(camera.heading)
        heading_vector = (math.sin(heading_rad), -math.cos(heading_rad))
        forward_distance = (1.0 - bottom_y) * camera.view_range
        if segment.geometry_type == "polygon":
            anchor = (camera.x, camera.y)
            if not self._point_in_polygon(anchor, segment.points):
                anchor = self._polygon_centroid(segment.points)
            candidate = (
                anchor[0] + heading_vector[0] * forward_distance,
                anchor[1] + heading_vector[1] * forward_distance,
            )
            if not self._point_in_polygon(candidate, segment.points):
                candidate = self._nearest_polygon_point(candidate, segment.points)
            return segment.segment_id, candidate

        projected, anchor_distance, _, edge_index, _ = self._project_to_polyline(
            (camera.x, camera.y), segment.points
        )
        start, end = segment.points[edge_index], segment.points[edge_index + 1]
        tangent = (end[0] - start[0], end[1] - start[1])
        sign = 1.0 if tangent[0] * heading_vector[0] + tangent[1] * heading_vector[1] >= 0 else -1.0
        total_length = self._polyline_length(segment.points)
        track_distance = min(total_length, max(0.0, anchor_distance + sign * forward_distance))
        return segment.segment_id, self._point_at_distance(segment.points, track_distance)

    def _allocate_global_id(
        self,
        camera_id: str,
        local_track_id: int,
        plate_text: str,
        vehicle_class: str,
        point: Point,
        now: float,
    ) -> str:
        key = (camera_id, local_track_id)
        if key in self._local_to_global:
            return self._local_to_global[key]
        if plate_text and plate_text in self._plate_to_global:
            global_id = self._plate_to_global[plate_text]
        else:
            candidates = [
                track for track in self.tracks.values()
                if track.camera_id != camera_id
                and track.vehicle_class == vehicle_class
                and 0.5 <= now - track.last_seen <= 8.0
                and math.dist((track.x, track.y), point) <= 0.18
            ]
            if candidates:
                global_id = min(candidates, key=lambda track: math.dist((track.x, track.y), point)).global_id
            else:
                global_id = f"V{self._next_global_id:04d}"
                self._next_global_id += 1
        self._local_to_global[key] = global_id
        if plate_text:
            self._plate_to_global[plate_text] = global_id
        return global_id

    def update_detections(
        self,
        camera_id: str,
        detections: Iterable[object],
        frame_size: Tuple[int, int],
        now: Optional[float] = None,
    ) -> None:
        now = time.time() if now is None else float(now)
        if not self.segments:
            return
        self.ensure_camera(camera_id)
        for fallback_id, detection in enumerate(detections, start=1):
            local_id = int(getattr(detection, "track_id", -1))
            if local_id < 0:
                local_id = fallback_id
            segment_id, point = self.map_detection(camera_id, detection.vehicle_bbox, frame_size)
            plate_text = getattr(detection, "plate_text", "") or ""
            vehicle_class = getattr(detection, "vehicle_class", "vehicle")
            global_id = self._allocate_global_id(
                camera_id, local_id, plate_text, vehicle_class, point, now
            )
            track = self.tracks.get(global_id)
            if track is None:
                track = MapTrack(
                    global_id=global_id,
                    camera_id=camera_id,
                    local_track_id=local_id,
                    segment_id=segment_id,
                    x=point[0],
                    y=point[1],
                    vehicle_class=vehicle_class,
                    plate_text=plate_text,
                    last_seen=now,
                )
                self.tracks[global_id] = track
            track.camera_id = camera_id
            track.local_track_id = local_id
            track.segment_id = segment_id
            track.x, track.y = point
            track.vehicle_class = vehicle_class
            track.plate_text = plate_text or track.plate_text
            track.last_seen = now
            track.history.append(point)
            track.history = track.history[-40:]

            if self._last_segment_by_track.get(global_id) != segment_id:
                self._flow_events[segment_id].append((now, global_id))
                self._last_segment_by_track[global_id] = segment_id
        self.prune(now)

    def prune(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else float(now)
        stale_ids = [
            global_id for global_id, track in self.tracks.items()
            if now - track.last_seen > self.track_timeout
        ]
        for global_id in stale_ids:
            del self.tracks[global_id]
        for events in self._flow_events.values():
            while events and now - events[0][0] > 60.0:
                events.popleft()

    def segment_states(self, now: Optional[float] = None) -> Dict[str, SegmentState]:
        now = time.time() if now is None else float(now)
        self.prune(now)
        counts = defaultdict(int)
        for track in self.tracks.values():
            counts[track.segment_id] += 1
        states = {}
        for segment_id, segment in self.segments.items():
            count = counts[segment_id]
            occupancy = min(1.0, count / max(1, segment.capacity))
            self._heat[segment_id] = self._heat[segment_id] * 0.72 + occupancy * 0.28
            states[segment_id] = SegmentState(
                segment_id=segment_id,
                vehicle_count=count,
                flow_per_minute=len(self._flow_events[segment_id]),
                occupancy=occupancy,
                heat=self._heat[segment_id],
            )
        return states

    def reset_runtime(self) -> None:
        self.tracks.clear()
        self._local_to_global.clear()
        self._plate_to_global.clear()
        self._flow_events.clear()
        self._last_segment_by_track.clear()
        self._heat.clear()
        self._next_global_id = 1
