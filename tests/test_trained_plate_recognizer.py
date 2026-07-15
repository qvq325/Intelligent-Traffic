from collections import deque
from pathlib import Path

import numpy as np

from lpr_recognizer import PlateRecognition
from plate_temporal_fusion import TrackedVehicle
from trained_plate_recognizer import BoxPlateRecognizer, PlateBoxDetection


class BatchDetectorDouble:
    def __init__(self, full_results, batch_results=None, *, batch_error=False):
        self.full_results = deque(full_results)
        self.batch_results = batch_results or []
        self.batch_error = batch_error
        self.batch_sizes = []
        self.batch_thresholds = []

    def detect(self, _frame):
        return self.full_results.popleft() if self.full_results else []

    def detect_batch(self, frames, *, conf_threshold=None):
        self.batch_sizes.append(len(frames))
        self.batch_thresholds.append(conf_threshold)
        if self.batch_error:
            raise RuntimeError("crop batch failed")
        return [list(items) for items in self.batch_results]


class OCRDouble:
    threshold = 0.3

    def recognize_crops(self, _crops, bboxes):
        return [
            PlateRecognition(
                plate_text=f"京A{index:05d}",
                confidence=0.9,
                bbox=bbox,
                plate_color="蓝色",
            )
            for index, bbox in enumerate(bboxes, start=1)
        ]


def test_vehicle_aware_recognition_batches_only_unmatched_vehicle_crops():
    detector = BatchDetectorDouble(
        full_results=[[PlateBoxDetection((20, 40, 60, 55), 0.8)]],
        batch_results=[[PlateBoxDetection((10, 35, 45, 49), 0.18)]],
    )
    recognizer = BoxPlateRecognizer(
        model_path=Path("trusted.pt"), detector=detector, ocr=OCRDouble()
    )
    vehicles = [
        TrackedVehicle((0, 0, 90, 80), 1, 0.95),
        TrackedVehicle((100, 0, 190, 80), 2, 0.90),
    ]
    results = recognizer.recognize_for_vehicles(
        np.zeros((100, 200, 3), dtype=np.uint8),
        vehicles,
        camera_id="camera-a",
    )
    assert detector.batch_sizes == [1]
    assert len(results) == 2


def test_crop_recovery_uses_one_batch_and_caps_sixteen_vehicles():
    detector = BatchDetectorDouble(
        full_results=[[]],
        batch_results=[[] for _index in range(16)],
    )
    recognizer = BoxPlateRecognizer(
        model_path=Path("trusted.pt"), detector=detector, ocr=OCRDouble()
    )
    vehicles = [
        TrackedVehicle((index * 100, 0, index * 100 + 90, 80), index, 0.9)
        for index in range(20)
    ]
    recognizer.recognize_for_vehicles(
        np.zeros((100, 2100, 3), dtype=np.uint8),
        vehicles,
        camera_id="camera-a",
    )
    assert detector.batch_sizes == [16]


def test_square_oversized_and_duplicate_plate_candidates_are_rejected():
    detector = BatchDetectorDouble(
        full_results=[
            [
                PlateBoxDetection((20, 50, 60, 65), 0.80),
                PlateBoxDetection((21, 50, 61, 65), 0.75),
                PlateBoxDetection((10, 10, 40, 40), 0.99),
                PlateBoxDetection((0, 0, 95, 75), 0.98),
            ]
        ],
    )
    recognizer = BoxPlateRecognizer(
        model_path=Path("trusted.pt"), detector=detector, ocr=OCRDouble()
    )
    results = recognizer.recognize_for_vehicles(
        np.zeros((100, 100, 3), dtype=np.uint8),
        [TrackedVehicle((0, 0, 100, 80), 1, 0.9)],
        camera_id="camera-a",
    )
    assert len(results) == 1
    assert results[0].bbox == (20, 50, 60, 65)


def test_crop_failure_keeps_full_frame_result_and_temporal_hold():
    detector = BatchDetectorDouble(
        full_results=[
            [PlateBoxDetection((20, 50, 60, 65), 0.8)],
            [],
        ],
        batch_error=True,
    )
    recognizer = BoxPlateRecognizer(
        model_path=Path("trusted.pt"), detector=detector, ocr=OCRDouble()
    )
    vehicles = [
        TrackedVehicle((0, 0, 100, 80), 1, 0.9),
        TrackedVehicle((100, 0, 200, 80), 2, 0.8),
    ]
    first = recognizer.recognize_for_vehicles(
        np.zeros((100, 200, 3), dtype=np.uint8),
        vehicles,
        camera_id="camera-a",
    )
    second = recognizer.recognize_for_vehicles(
        np.zeros((100, 200, 3), dtype=np.uint8),
        vehicles,
        camera_id="camera-a",
    )
    assert [item.plate_text for item in first] == ["京A00001"]
    assert [item.plate_text for item in second] == ["京A00001"]
    assert recognizer.last_warning == "RuntimeError"


def test_legacy_recognize_still_runs_full_frame_only():
    detector = BatchDetectorDouble(
        full_results=[[PlateBoxDetection((20, 50, 60, 65), 0.8)]],
    )
    recognizer = BoxPlateRecognizer(
        model_path=Path("trusted.pt"), detector=detector, ocr=OCRDouble()
    )
    results = recognizer.recognize(np.zeros((100, 100, 3), dtype=np.uint8))
    assert len(results) == 1
    assert detector.batch_sizes == []
