"""Adapter for trained box-only plate detectors and the legacy OCR model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Protocol, Sequence, Tuple

import numpy as np

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

from lpr_recognizer import LPRRecognizer, PlateRecognition
from plate_temporal_fusion import (
    PlateTemporalFusion,
    PlateTrackObservation,
    TrackedVehicle,
)


@dataclass(frozen=True)
class PlateBoxDetection:
    bbox: Tuple[float, float, float, float]
    confidence: float


@dataclass(frozen=True, slots=True)
class _LocalizedPlate:
    bbox: tuple[int, int, int, int]
    confidence: float
    vehicle_index: int


class PlateDetector(Protocol):
    def detect(self, frame: np.ndarray) -> Sequence[PlateBoxDetection]: ...

    def detect_batch(
        self,
        frames: Sequence[np.ndarray],
        *,
        conf_threshold: float | None = None,
    ) -> Sequence[Sequence[PlateBoxDetection]]: ...


class PlateOCR(Protocol):
    def recognize_crops(
        self,
        crops: Sequence[np.ndarray],
        bboxes: Sequence[Tuple[int, int, int, int]],
    ) -> List[PlateRecognition]: ...


class UltralyticsBoxPlateDetector:
    """Small Ultralytics boundary for a trusted project-owned detect weight."""

    def __init__(
        self,
        model_path: str | Path,
        conf_threshold: float = 0.7,
        device: str = "cpu",
        inference_size: int = 640,
    ) -> None:
        if YOLO is None:
            raise ImportError("未安装 ultralytics 库，请运行: uv sync")

        trusted_path = Path(model_path)
        if not trusted_path.is_file():
            raise FileNotFoundError(f"车牌检测模型不存在: {trusted_path}")

        self.conf_threshold = conf_threshold
        self.device = device
        self.inference_size = inference_size
        self.model = YOLO(str(trusted_path))

    def detect(self, frame: np.ndarray) -> List[PlateBoxDetection]:
        batches = self.detect_batch([frame], conf_threshold=self.conf_threshold)
        return batches[0] if batches else []

    def detect_batch(
        self,
        frames: Sequence[np.ndarray],
        *,
        conf_threshold: float | None = None,
    ) -> List[List[PlateBoxDetection]]:
        if not frames:
            return []
        threshold = (
            self.conf_threshold
            if conf_threshold is None
            else float(conf_threshold)
        )
        results = self.model.predict(
            list(frames),
            conf=threshold,
            device=self.device,
            imgsz=self.inference_size,
            verbose=False,
        )
        batches = [self._detections_from_result(result) for result in results or []]
        missing = max(0, len(frames) - len(batches))
        return [*batches, *([[]] * missing)]

    @staticmethod
    def _detections_from_result(result: object) -> List[PlateBoxDetection]:
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []
        coordinates = boxes.xyxy.detach().cpu().numpy()
        confidences = boxes.conf.detach().cpu().numpy()
        detections = [
            PlateBoxDetection(
                bbox=tuple(float(value) for value in box),
                confidence=float(confidence),
            )
            for box, confidence in zip(coordinates, confidences)
        ]
        return sorted(
            detections,
            key=lambda item: item.confidence,
            reverse=True,
        )

    @property
    def threshold(self) -> float:
        return self.conf_threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        self.conf_threshold = max(0.0, min(1.0, value))


class BoxPlateRecognizer:
    """Crop trained box detections and decode them with the existing OCR model."""

    def __init__(
        self,
        model_path: str | Path,
        conf_threshold: float = 0.7,
        device: str = "cpu",
        inference_size: int = 640,
        *,
        detector: PlateDetector | None = None,
        ocr: PlateOCR | None = None,
        fusion: PlateTemporalFusion | None = None,
        crop_padding: float = 0.20,
        max_crop_batch: int = 16,
        min_aspect_ratio: float = 1.5,
        max_aspect_ratio: float = 8.0,
        max_plate_vehicle_area_ratio: float = 0.35,
    ) -> None:
        self.conf_threshold = conf_threshold
        self.detector = detector or UltralyticsBoxPlateDetector(
            model_path=model_path,
            conf_threshold=conf_threshold,
            device=device,
            inference_size=inference_size,
        )
        self.ocr = ocr or LPRRecognizer(
            conf_threshold=conf_threshold,
            device=device,
            detector_model=None,
        )
        self._fusion = fusion or PlateTemporalFusion()
        self.crop_padding = max(0.0, float(crop_padding))
        self.max_crop_batch = max(1, int(max_crop_batch))
        self.min_aspect_ratio = max(0.0, float(min_aspect_ratio))
        self.max_aspect_ratio = max(
            self.min_aspect_ratio,
            float(max_aspect_ratio),
        )
        self.max_plate_vehicle_area_ratio = max(
            0.0,
            float(max_plate_vehicle_area_ratio),
        )
        self.last_warning = ""

    def recognize(self, frame: np.ndarray) -> List[PlateRecognition]:
        if frame is None or frame.size == 0:
            return []

        height, width = frame.shape[:2]
        crops = []
        bboxes = []
        for detection in self.detector.detect(frame):
            x1, y1, x2, y2 = (int(value) for value in detection.bbox)
            bbox = (
                max(0, min(x1, width)),
                max(0, min(y1, height)),
                max(0, min(x2, width)),
                max(0, min(y2, height)),
            )
            left, top, right, bottom = bbox
            if right <= left or bottom <= top:
                continue
            crops.append(frame[top:bottom, left:right])
            bboxes.append(bbox)

        if not crops:
            return []
        recognized = self.ocr.recognize_crops(crops, bboxes)
        recognized.sort(key=lambda item: item.confidence, reverse=True)
        return recognized

    def recognize_for_vehicles(
        self,
        frame: np.ndarray,
        vehicles: Sequence[TrackedVehicle],
        *,
        camera_id: str,
    ) -> List[PlateRecognition]:
        self.last_warning = ""
        if frame is None or frame.size == 0:
            return []
        full = self._safe_full_detections(frame)
        localized = self._associate_and_filter(full, vehicles, frame.shape)
        unmatched = self._unmatched_vehicle_indices(localized, vehicles)
        localized.extend(self._safe_crop_recovery(frame, vehicles, unmatched))
        localized = self._deduplicate(localized)
        observations, immediate = self._recognize_candidates(
            frame,
            vehicles,
            localized,
        )
        fused = self._fusion.resolve(camera_id, vehicles, observations)
        return sorted(
            [*fused, *immediate],
            key=lambda item: item.confidence,
            reverse=True,
        )

    def reset(self, camera_id: str | None = None) -> None:
        self._fusion.reset(camera_id)

    def _safe_full_detections(
        self,
        frame: np.ndarray,
    ) -> List[PlateBoxDetection]:
        try:
            return list(self.detector.detect(frame))
        except Exception as exc:
            self.last_warning = type(exc).__name__
            return []

    def _associate_and_filter(
        self,
        detections: Sequence[PlateBoxDetection],
        vehicles: Sequence[TrackedVehicle],
        frame_shape: tuple[int, ...],
    ) -> List[_LocalizedPlate]:
        height, width = frame_shape[:2]
        localized = []
        for detection in detections:
            x1, y1, x2, y2 = (round(value) for value in detection.bbox)
            bbox = (
                max(0, min(width, x1)),
                max(0, min(height, y1)),
                max(0, min(width, x2)),
                max(0, min(height, y2)),
            )
            matching = [
                index
                for index, vehicle in enumerate(vehicles)
                if self._valid_for_vehicle(bbox, vehicle.bbox)
            ]
            if not matching:
                continue
            vehicle_index = min(
                matching,
                key=lambda index: self._bbox_area(vehicles[index].bbox),
            )
            localized.append(
                _LocalizedPlate(
                    bbox,
                    float(detection.confidence),
                    vehicle_index,
                )
            )
        return localized

    def _valid_for_vehicle(
        self,
        plate_bbox: tuple[int, int, int, int],
        vehicle_bbox: tuple[int, int, int, int],
    ) -> bool:
        px1, py1, px2, py2 = plate_bbox
        vx1, vy1, vx2, vy2 = vehicle_bbox
        width, height = px2 - px1, py2 - py1
        if width < 8 or height < 4:
            return False
        aspect = width / max(1, height)
        if not self.min_aspect_ratio <= aspect <= self.max_aspect_ratio:
            return False
        if self._bbox_area(plate_bbox) > (
            self._bbox_area(vehicle_bbox) * self.max_plate_vehicle_area_ratio
        ):
            return False
        center = ((px1 + px2) / 2.0, (py1 + py2) / 2.0)
        return (
            vx1 <= center[0] <= vx2
            and vy1 <= center[1] <= vy2
        )

    def _safe_crop_recovery(
        self,
        frame: np.ndarray,
        vehicles: Sequence[TrackedVehicle],
        unmatched: Sequence[int],
    ) -> List[_LocalizedPlate]:
        height, width = frame.shape[:2]
        selected = sorted(
            unmatched,
            key=lambda index: (
                vehicles[index].confidence,
                self._bbox_area(vehicles[index].bbox),
            ),
            reverse=True,
        )[: self.max_crop_batch]
        records = []
        crops = []
        for vehicle_index in selected:
            vx1, vy1, vx2, vy2 = vehicles[vehicle_index].bbox
            pad_x = round((vx2 - vx1) * self.crop_padding)
            pad_y = round((vy2 - vy1) * self.crop_padding)
            crop_bbox = (
                max(0, vx1 - pad_x),
                max(0, vy1 - pad_y),
                min(width, vx2 + pad_x),
                min(height, vy2 + pad_y),
            )
            left, top, right, bottom = crop_bbox
            if right <= left or bottom <= top:
                continue
            records.append((vehicle_index, crop_bbox))
            crops.append(frame[top:bottom, left:right])
        if not crops:
            return []
        try:
            batches = self.detector.detect_batch(
                crops,
                conf_threshold=max(
                    0.08,
                    min(0.20, self.conf_threshold * 0.6),
                ),
            )
        except Exception as exc:
            self.last_warning = type(exc).__name__
            return []
        recovered = []
        for (
            vehicle_index,
            (left, top, _right, _bottom),
        ), detections in zip(records, batches):
            for detection in detections:
                x1, y1, x2, y2 = detection.bbox
                bbox = (
                    round(left + x1),
                    round(top + y1),
                    round(left + x2),
                    round(top + y2),
                )
                if self._valid_for_vehicle(
                    bbox,
                    vehicles[vehicle_index].bbox,
                ):
                    recovered.append(
                        _LocalizedPlate(
                            bbox,
                            float(detection.confidence),
                            vehicle_index,
                        )
                    )
        return recovered

    def _deduplicate(
        self,
        candidates: Sequence[_LocalizedPlate],
    ) -> List[_LocalizedPlate]:
        kept = []
        for candidate in sorted(
            candidates,
            key=lambda item: item.confidence,
            reverse=True,
        ):
            duplicate = any(
                candidate.vehicle_index == existing.vehicle_index
                and self._iou(candidate.bbox, existing.bbox) >= 0.5
                for existing in kept
            )
            if not duplicate:
                kept.append(candidate)
        return kept

    def _recognize_candidates(
        self,
        frame: np.ndarray,
        vehicles: Sequence[TrackedVehicle],
        candidates: Sequence[_LocalizedPlate],
    ) -> tuple[
        dict[int, PlateTrackObservation],
        List[PlateRecognition],
    ]:
        if not candidates:
            return {}, []
        crops = [
            frame[item.bbox[1] : item.bbox[3], item.bbox[0] : item.bbox[2]]
            for item in candidates
        ]
        bboxes = [item.bbox for item in candidates]
        try:
            recognized = self.ocr.recognize_crops(crops, bboxes)
        except Exception as exc:
            self.last_warning = type(exc).__name__
            return {}, []
        by_bbox = {item.bbox: item for item in candidates}
        observations = {}
        immediate = []
        for recognition in recognized:
            candidate = by_bbox.get(tuple(recognition.bbox))
            if candidate is None:
                continue
            vehicle = vehicles[candidate.vehicle_index]
            if vehicle.track_id < 0:
                immediate.append(recognition)
                continue
            observation = PlateTrackObservation.from_absolute(
                recognition,
                detector_confidence=candidate.confidence,
                vehicle_bbox=vehicle.bbox,
            )
            current = observations.get(vehicle.track_id)
            if current is None or (
                observation.detector_confidence
                * observation.recognition.confidence
                > current.detector_confidence
                * current.recognition.confidence
            ):
                observations[vehicle.track_id] = observation
        return observations, immediate

    @staticmethod
    def _unmatched_vehicle_indices(
        candidates: Sequence[_LocalizedPlate],
        vehicles: Sequence[TrackedVehicle],
    ) -> List[int]:
        matched = {item.vehicle_index for item in candidates}
        return [
            index for index in range(len(vehicles)) if index not in matched
        ]

    @staticmethod
    def _bbox_area(bbox: tuple[int, int, int, int]) -> int:
        return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])

    @classmethod
    def _iou(
        cls,
        first: tuple[int, int, int, int],
        second: tuple[int, int, int, int],
    ) -> float:
        left, top = max(first[0], second[0]), max(first[1], second[1])
        right, bottom = min(first[2], second[2]), min(first[3], second[3])
        intersection = max(0, right - left) * max(0, bottom - top)
        union = cls._bbox_area(first) + cls._bbox_area(second) - intersection
        return intersection / max(1, union)

    @property
    def threshold(self) -> float:
        return self.conf_threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        self.conf_threshold = max(0.0, min(1.0, value))
        if hasattr(self.detector, "threshold"):
            self.detector.threshold = self.conf_threshold
        if hasattr(self.ocr, "threshold"):
            self.ocr.threshold = self.conf_threshold


__all__ = [
    "BoxPlateRecognizer",
    "PlateBoxDetection",
    "UltralyticsBoxPlateDetector",
]
