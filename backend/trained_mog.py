"""Headless seven-layer MOG2 anomaly detection for road monitoring."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from numbers import Real
from typing import Iterable, Sequence

import cv2
import numpy as np


Point = tuple[int, int]
BBoxXYWH = tuple[int, int, int, int]
BBoxXYXY = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class TrainedMOGAlert:
    """Stable, render-free alert produced by :class:`MOGAnomalyEngine`."""

    anomaly_type: str
    position: BBoxXYWH
    lane: str
    alert_time: float
    confidence: float = 0.5
    frame_id: int = 0
    observed_duration: float = 0.0


@dataclass(slots=True)
class _TrackedObject:
    bbox: BBoxXYWH
    centroid: Point
    area: float
    first_seen: float
    last_seen: float
    duration: float = 0.0
    confirmed: bool = False
    stability: float = 0.0
    miss_count: int = 0
    active_count: int = 1
    total_count: int = 1


class MOGAnomalyEngine:
    """MOG2 foreground detection with the trained seven-layer policy."""

    def __init__(
        self,
        history: int = 500,
        var_threshold: float = 25.0,
        min_area: int = 150,
        min_duration: float = 2.0,
        max_duration: float = 5.0,
        warmup_frames: int = 50,
    ) -> None:
        if int(history) < 1:
            raise ValueError("history must be positive")
        if float(var_threshold) <= 0:
            raise ValueError("var_threshold must be positive")
        if int(min_area) < 1:
            raise ValueError("min_area must be positive")
        if float(min_duration) < 0 or float(max_duration) < 0:
            raise ValueError("durations must be non-negative")
        if float(max_duration) < float(min_duration):
            raise ValueError("max_duration must not be less than min_duration")
        if int(warmup_frames) < 0:
            raise ValueError("warmup_frames must be non-negative")

        self.history = int(history)
        self.var_threshold = float(var_threshold)
        self.min_area = int(min_area)
        self.min_duration = float(min_duration)
        self.max_duration = float(max_duration)
        self.warmup_frames = int(warmup_frames)

        self._rois: tuple[tuple[Point, ...], ...] = ()
        self._roi_mask: np.ndarray | None = None
        self._roi_mask_shape: tuple[int, int] | None = None
        self._tracked: dict[int, _TrackedObject] = {}
        self._next_track_id = 1
        self._recent_vehicle_mask: np.ndarray | None = None
        self._vehicle_mask_decay = 0.92
        self._open_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (3, 3)
        )
        self._close_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (5, 5)
        )
        self._shadow_hsv_low = np.array([0, 0, 0], dtype=np.uint8)
        self._shadow_hsv_high = np.array([180, 60, 80], dtype=np.uint8)
        self.mog = self._new_background_model()
        self.frame_count = 0
        self.is_warmed_up = self.warmup_frames == 0
        self.alerts: list[TrainedMOGAlert] = []

    def _new_background_model(self):
        return cv2.createBackgroundSubtractorMOG2(
            history=self.history,
            varThreshold=self.var_threshold,
            detectShadows=True,
        )

    def set_roi(self, polygon: Iterable[Sequence[Real]] | None) -> None:
        """Set one ROI, while accepting the legacy nested multi-ROI shape."""

        if polygon is None:
            self.set_rois(None)
            return
        items = list(polygon)
        if not items:
            self.set_rois([])
            return
        first = items[0]
        if (
            isinstance(first, Sequence)
            and len(first) == 2
            and all(isinstance(value, Real) for value in first)
        ):
            self.set_rois([items])
        else:
            self.set_rois(items)  # type: ignore[arg-type]

    def set_rois(
        self,
        polygons: Iterable[Iterable[Sequence[Real]]] | None,
    ) -> None:
        """Set multiple ROI polygons whose masks are combined as a union."""

        normalized: list[tuple[Point, ...]] = []
        for polygon in polygons or ():
            points = tuple(
                (int(round(float(point[0]))), int(round(float(point[1]))))
                for point in polygon
                if len(point) >= 2
            )
            if len(points) >= 3:
                normalized.append(points)
        rois = tuple(normalized)
        if rois != self._rois:
            self._rois = rois
            self._roi_mask = None
            self._roi_mask_shape = None

    def reset(self) -> None:
        """Reset background, candidates, vehicle memory, and warmup state."""

        self.mog = self._new_background_model()
        self._tracked.clear()
        self._next_track_id = 1
        self._recent_vehicle_mask = None
        self._roi_mask = None
        self._roi_mask_shape = None
        self.frame_count = 0
        self.is_warmed_up = self.warmup_frames == 0
        self.alerts = []

    def process(
        self,
        frame: np.ndarray,
        yolo_boxes: Iterable[Sequence[Real]],
        frame_id: int = 0,
        timestamp: float | None = None,
    ) -> list[TrainedMOGAlert]:
        """Process one BGR frame and return current confirmed anomalies."""

        if frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0:
            raise ValueError("frame must be a non-empty BGR image")
        observed_at = time.time() if timestamp is None else float(timestamp)
        self.frame_count += 1
        self.alerts = []

        # Layer 7: establish a stable background before producing candidates.
        if self.frame_count <= self.warmup_frames:
            self.mog.apply(frame, learningRate=-1)
            if self.frame_count == self.warmup_frames:
                self.is_warmed_up = True
            return []
        self.is_warmed_up = True

        height, width = frame.shape[:2]
        foreground = self.mog.apply(frame, learningRate=0.001)

        # Layer 4: discard MOG2 shadows, then suppress dark low-saturation pixels.
        _, foreground = cv2.threshold(
            foreground, 200, 255, cv2.THRESH_BINARY
        )
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        dark_shadow = cv2.inRange(
            hsv, self._shadow_hsv_low, self._shadow_hsv_high
        )
        foreground[dark_shadow > 0] = 0

        # Layer 1: keep only the union of enabled road ROIs.
        roi_mask = self._mask_for_shape(height, width)
        if roi_mask is not None:
            foreground = cv2.bitwise_and(foreground, roi_mask)

        # Layer 5: remove isolated noise and reconnect fragmented objects.
        foreground = cv2.morphologyEx(
            foreground, cv2.MORPH_OPEN, self._open_kernel
        )
        foreground = cv2.morphologyEx(
            foreground, cv2.MORPH_CLOSE, self._close_kernel
        )

        vehicle_boxes = self._normalized_vehicle_boxes(
            yolo_boxes, width, height
        )
        self._update_recent_vehicle_mask(vehicle_boxes, width, height)
        for tracked in self._tracked.values():
            tracked.total_count += 1

        # Layer 2: reject small noise, frame-wide changes, edge artifacts, and
        # implausibly thin contours before candidate tracking.
        contours, _ = cv2.findContours(
            foreground, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        analysis_area = (
            int(np.count_nonzero(roi_mask))
            if roi_mask is not None
            else width * height
        )
        foreground_ratio = float(np.count_nonzero(foreground)) / max(
            1,
            analysis_area,
        )
        if foreground_ratio >= 0.05 and len(contours) >= 30:
            self.mog.apply(frame, learningRate=0.05)
            self._tracked.clear()
            self._recent_vehicle_mask = None
            return []

        active_tracks: set[int] = set()
        edge_margin = 5
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < self.min_area or area > width * height * 0.6:
                continue
            x, y, box_width, box_height = cv2.boundingRect(contour)
            if (
                x <= edge_margin
                or y <= edge_margin
                or x + box_width >= width - edge_margin
                or y + box_height >= height - edge_margin
            ):
                continue
            aspect_ratio = box_width / max(1, box_height)
            if aspect_ratio > 8.0 or aspect_ratio < 0.1:
                continue

            track_id = self._matching_track(
                (x, y, box_width, box_height), active_tracks
            )
            if track_id is None:
                track_id = self._next_track_id
                self._next_track_id += 1
                self._tracked[track_id] = _TrackedObject(
                    bbox=(x, y, box_width, box_height),
                    centroid=(x + box_width // 2, y + box_height // 2),
                    area=area,
                    first_seen=observed_at,
                    last_seen=observed_at,
                )
            else:
                self._update_track(
                    self._tracked[track_id],
                    (x, y, box_width, box_height),
                    area,
                    observed_at,
                )
            active_tracks.add(track_id)

        # Layer 3: require duration and a consistent detection rate.
        for track_id, tracked in list(self._tracked.items()):
            tracked.duration = max(0.0, observed_at - tracked.first_seen)
            if track_id not in active_tracks:
                tracked.miss_count += 1
                if tracked.miss_count > 5:
                    del self._tracked[track_id]
                    continue
            else:
                tracked.miss_count = 0
            if tracked.confirmed:
                continue
            required_duration = (
                self.max_duration
                if self._recent_vehicle_overlap(tracked.bbox)
                else self.min_duration
            )
            detection_rate = tracked.active_count / max(1, tracked.total_count)
            if (
                tracked.duration >= required_duration
                and detection_rate >= 0.6
            ):
                tracked.confirmed = True

        # Layer 6: YOLO-confirmed normal vehicles never become MOG alerts.
        for tracked in self._tracked.values():
            if not tracked.confirmed:
                continue
            tracked_xyxy = self._xywh_to_xyxy(tracked.bbox)
            if any(
                self._boxes_overlap(tracked_xyxy, vehicle_box)
                for vehicle_box in vehicle_boxes
            ):
                continue
            self.alerts.append(
                TrainedMOGAlert(
                    anomaly_type=self._classify(tracked),
                    position=tracked.bbox,
                    lane=self._lane(tracked.centroid, width),
                    alert_time=observed_at,
                    confidence=self._confidence(tracked),
                    frame_id=int(frame_id),
                    observed_duration=tracked.duration,
                )
            )
        return list(self.alerts)

    def _mask_for_shape(self, height: int, width: int) -> np.ndarray | None:
        if not self._rois:
            return None
        shape = (height, width)
        if self._roi_mask is None or self._roi_mask_shape != shape:
            mask = np.zeros(shape, dtype=np.uint8)
            cv2.fillPoly(
                mask,
                [np.asarray(polygon, dtype=np.int32) for polygon in self._rois],
                255,
            )
            self._roi_mask = mask
            self._roi_mask_shape = shape
        return self._roi_mask

    @staticmethod
    def _normalized_vehicle_boxes(
        boxes: Iterable[Sequence[Real]], width: int, height: int
    ) -> list[BBoxXYXY]:
        normalized: list[BBoxXYXY] = []
        for box in boxes:
            if len(box) < 4:
                continue
            x1, y1, x2, y2 = (int(round(float(box[index]))) for index in range(4))
            clipped = (
                max(0, min(width, x1)),
                max(0, min(height, y1)),
                max(0, min(width, x2)),
                max(0, min(height, y2)),
            )
            if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
                normalized.append(clipped)
        return normalized

    def _update_recent_vehicle_mask(
        self, boxes: Iterable[BBoxXYXY], width: int, height: int
    ) -> None:
        if (
            self._recent_vehicle_mask is None
            or self._recent_vehicle_mask.shape != (height, width)
        ):
            self._recent_vehicle_mask = np.zeros(
                (height, width), dtype=np.float32
            )
        self._recent_vehicle_mask *= self._vehicle_mask_decay
        for x1, y1, x2, y2 in boxes:
            self._recent_vehicle_mask[y1:y2, x1:x2] = 1.0

    def _matching_track(
        self, bbox: BBoxXYWH, claimed: set[int]
    ) -> int | None:
        candidate_xyxy = self._xywh_to_xyxy(bbox)
        candidate_center = (
            bbox[0] + bbox[2] // 2,
            bbox[1] + bbox[3] // 2,
        )
        best: tuple[float, int] | None = None
        for track_id, tracked in self._tracked.items():
            if track_id in claimed:
                continue
            overlap = self._iou(candidate_xyxy, self._xywh_to_xyxy(tracked.bbox))
            distance = math.dist(candidate_center, tracked.centroid)
            distance_limit = max(
                20.0,
                0.75 * max(bbox[2], bbox[3], tracked.bbox[2], tracked.bbox[3]),
            )
            if overlap < 0.1 and distance > distance_limit:
                continue
            score = overlap - distance / max(1.0, distance_limit) * 0.1
            if best is None or score > best[0]:
                best = (score, track_id)
        return None if best is None else best[1]

    @staticmethod
    def _update_track(
        tracked: _TrackedObject,
        bbox: BBoxXYWH,
        area: float,
        observed_at: float,
    ) -> None:
        alpha = 0.3
        old_x, old_y, old_width, old_height = tracked.bbox
        x, y, width, height = bbox
        smoothed = (
            int(round(old_x * (1.0 - alpha) + x * alpha)),
            int(round(old_y * (1.0 - alpha) + y * alpha)),
            int(round(old_width * (1.0 - alpha) + width * alpha)),
            int(round(old_height * (1.0 - alpha) + height * alpha)),
        )
        centroid = (
            smoothed[0] + smoothed[2] // 2,
            smoothed[1] + smoothed[3] // 2,
        )
        displacement = math.dist(centroid, tracked.centroid)
        tracked.bbox = smoothed
        tracked.centroid = centroid
        tracked.area = area
        tracked.last_seen = observed_at
        tracked.duration = max(0.0, observed_at - tracked.first_seen)
        tracked.stability = max(0.0, 1.0 - displacement / 50.0)
        tracked.active_count += 1

    def _recent_vehicle_overlap(self, bbox: BBoxXYWH) -> bool:
        if self._recent_vehicle_mask is None:
            return False
        x, y, width, height = bbox
        region = self._recent_vehicle_mask[y : y + height, x : x + width]
        return bool(region.size and float(np.mean(region)) > 0.3)

    @staticmethod
    def _xywh_to_xyxy(bbox: BBoxXYWH) -> BBoxXYXY:
        x, y, width, height = bbox
        return x, y, x + width, y + height

    @staticmethod
    def _boxes_overlap(first: BBoxXYXY, second: BBoxXYXY) -> bool:
        left = max(first[0], second[0])
        top = max(first[1], second[1])
        right = min(first[2], second[2])
        bottom = min(first[3], second[3])
        if left >= right or top >= bottom:
            return False
        intersection = (right - left) * (bottom - top)
        first_area = max(1, (first[2] - first[0]) * (first[3] - first[1]))
        if intersection / first_area > 0.5:
            return True
        center_x = (first[0] + first[2]) // 2
        center_y = (first[1] + first[3]) // 2
        return (
            second[0] <= center_x <= second[2]
            and second[1] <= center_y <= second[3]
        )

    @staticmethod
    def _iou(first: BBoxXYXY, second: BBoxXYXY) -> float:
        left = max(first[0], second[0])
        top = max(first[1], second[1])
        right = min(first[2], second[2])
        bottom = min(first[3], second[3])
        intersection = max(0, right - left) * max(0, bottom - top)
        if intersection == 0:
            return 0.0
        first_area = max(0, first[2] - first[0]) * max(0, first[3] - first[1])
        second_area = max(0, second[2] - second[0]) * max(0, second[3] - second[1])
        return intersection / max(1, first_area + second_area - intersection)

    @staticmethod
    def _classify(tracked: _TrackedObject) -> str:
        aspect_ratio = tracked.bbox[2] / max(1, tracked.bbox[3])
        if tracked.area < 500:
            return "small_debris"
        if tracked.area < 2000:
            return "medium_object"
        if aspect_ratio > 3.0 or aspect_ratio < 0.33:
            return "elongated_object"
        return "large_object"

    def _confidence(self, tracked: _TrackedObject) -> float:
        duration_scale = max(self.max_duration, self.min_duration, 1e-6)
        duration_score = min(tracked.duration / duration_scale, 1.0)
        return round(
            min(1.0, 0.4 + 0.4 * duration_score + 0.2 * tracked.stability),
            2,
        )

    @staticmethod
    def _lane(centroid: Point, frame_width: int) -> str:
        if centroid[0] < frame_width * 0.33:
            return "left"
        if centroid[0] < frame_width * 0.66:
            return "middle"
        return "right"


__all__ = ["MOGAnomalyEngine", "TrainedMOGAlert"]
