"""Adapter for trained box-only plate detectors and the legacy OCR model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Protocol, Sequence, Tuple

import numpy as np

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

from lpr_recognizer import LPRRecognizer, PlateRecognition


@dataclass(frozen=True)
class PlateBoxDetection:
    bbox: Tuple[float, float, float, float]
    confidence: float


class PlateDetector(Protocol):
    def detect(self, frame: np.ndarray) -> Sequence[PlateBoxDetection]: ...


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
        detections: List[PlateBoxDetection] = []
        results = self.model.predict(
            frame,
            conf=self.conf_threshold,
            device=self.device,
            imgsz=self.inference_size,
            verbose=False,
        )
        for result in results or []:
            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                continue
            coordinates = boxes.xyxy.detach().cpu().numpy()
            confidences = boxes.conf.detach().cpu().numpy()
            detections.extend(
                PlateBoxDetection(
                    bbox=tuple(float(value) for value in box),
                    confidence=float(confidence),
                )
                for box, confidence in zip(coordinates, confidences)
            )
        detections.sort(key=lambda item: item.confidence, reverse=True)
        return detections

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
