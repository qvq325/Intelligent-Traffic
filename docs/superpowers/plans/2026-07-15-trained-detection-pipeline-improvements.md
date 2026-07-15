# Trained Detection Pipeline Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the trained vehicle, plate, and seven-layer MOG algorithms to the production vehicle/plate and road-abnormal scenes with higher recall, steadier output, fewer normal-vehicle false alarms, and no duplicate road model owner.

**Architecture:** The trained plate path becomes a full-frame-first cascade with one batched crop fallback and a bounded per-track temporal fusion cache. The road-abnormal monitor becomes the sole scene inference owner, receives pristine frames, runs scheduled YOLO plus alert-triggered verification, and carries MOG observation time into the existing event lifecycle.

**Tech Stack:** Python 3.11, FastAPI, OpenCV, NumPy, PyTorch 2.11, Ultralytics 8.4, ByteTrack, pytest, vanilla JavaScript, GitNexus.

---

## File Structure

- Create `plate_temporal_fusion.py`: bounded exact-text voting, hold/decay, relative box projection, camera and track isolation.
- Modify `trained_plate_recognizer.py`: batch-capable detector boundary, vehicle-aware full/crop cascade, geometry filtering, OCR batching, and fusion delegation.
- Modify `detection_processor.py`: send tracked vehicles into the trained plate cascade and reset its temporal state with ByteTrack.
- Modify `backend/video_stream.py`: external inference ownership and guaranteed pristine frame input for scene frame processors.
- Modify `backend/state.py`: construct the road stream with external inference ownership and stop overriding the persisted road pipeline enable flag.
- Modify `backend/trained_mog.py`: expose confirmed-object observation duration without changing external event contracts.
- Modify `backend/road_abnormal.py`: honor pipeline enable/model parameters, perform current-frame verification, preserve MOG on detector failure, and seed event duration.
- Create `scripts/benchmark_trained_pipeline.py`: repeatable local-video plate continuity, latency, FPS, and peak CUDA-memory measurement.
- Create `tests/test_plate_temporal_fusion.py`: deterministic temporal fusion coverage.
- Create `tests/test_trained_plate_recognizer.py`: deterministic cascade, batching, filtering, and fallback coverage.
- Modify `tests/test_detection_pipeline.py`: processor integration and reset behavior.
- Modify `tests/test_video_service.py`: external owner and pristine frame ordering.
- Modify `tests/test_model_pipeline_runtime.py`: application-state road ownership wiring.
- Modify `tests/test_road_abnormal.py`: duration handoff, scheduling, verification, fail-open behavior, and disabled pipeline coverage.

## Task 1: Freeze Baseline And Add A Repeatable Benchmark

**Files:**
- Create: `scripts/benchmark_trained_pipeline.py`

- [ ] **Step 1: Reconfirm the worktree and exact symbol risk before any production edit**

Run:

```powershell
git status --short
node .gitnexus/run.cjs status
node .gitnexus/run.cjs impact process --direction upstream --repo VideoTest --file detection_processor.py --kind Method --depth 3 --include-tests
node .gitnexus/run.cjs impact recognize --direction upstream --repo VideoTest --file trained_plate_recognizer.py --kind Method --depth 3 --include-tests
node .gitnexus/run.cjs impact _run --direction upstream --repo VideoTest --file backend/video_stream.py --kind Method --depth 3 --include-tests
node .gitnexus/run.cjs impact process_frame --direction upstream --repo VideoTest --file backend/road_abnormal.py --kind Method --depth 3 --include-tests
node .gitnexus/run.cjs impact process --direction upstream --repo VideoTest --file backend/trained_mog.py --kind Method --depth 3 --include-tests
```

Expected: `.gitignore` is the only pre-existing worktree change; no query returns HIGH or CRITICAL. If one does, report it and stop before editing that symbol.

- [ ] **Step 2: Run the focused baseline tests**

Run:

```powershell
uv run pytest tests/test_detection_pipeline.py tests/test_model_pipeline_runtime.py tests/test_video_service.py tests/test_road_abnormal.py -q
```

Expected: all focused tests pass before changes.

- [ ] **Step 3: Add the benchmark utility**

Create `scripts/benchmark_trained_pipeline.py` with this interface and metric calculation:

```python
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from detection_processor import DetectionProcessor


def percentile(values: list[float], value: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), value)) if values else 0.0


def run(source: Path, frames: int, interval: int, device: str) -> dict:
    processor = DetectionProcessor(
        yolo_conf=0.65,
        lpr_conf=0.30,
        device=device,
        vehicle_model_path=PROJECT_ROOT / "训练后模型" / "yolo26x.pt",
        plate_model_path=PROJECT_ROOT / "训练后模型" / "license_plate_best.pt",
        inference_size=640,
        lpr_mode="box",
    )
    if not processor.initialize():
        raise RuntimeError(processor.init_error or "processor initialization failed")

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open source: {source}")

    if device.startswith("cuda"):
        torch.cuda.reset_peak_memory_stats(device)
    latencies: list[float] = []
    vehicle_observations = 0
    plate_observations = 0
    text_switches = 0
    last_text: dict[int, str] = {}
    decoded = 0
    sampled = 0
    started = time.perf_counter()
    try:
        while decoded < frames:
            ok, frame = capture.read()
            if not ok:
                break
            decoded += 1
            if decoded % interval:
                continue
            before = time.perf_counter()
            _, results = processor.process(frame, camera_id=source.name)
            latencies.append((time.perf_counter() - before) * 1000.0)
            sampled += 1
            vehicle_observations += len(results)
            for result in results:
                if not result.has_plate:
                    continue
                plate_observations += 1
                previous = last_text.get(result.track_id)
                if previous and previous != result.plate_text:
                    text_switches += 1
                if result.track_id >= 0:
                    last_text[result.track_id] = result.plate_text
    finally:
        capture.release()

    elapsed = time.perf_counter() - started
    peak_memory = (
        int(torch.cuda.max_memory_allocated(device))
        if device.startswith("cuda")
        else 0
    )
    return {
        "source": str(source),
        "decoded_frames": decoded,
        "sampled_frames": sampled,
        "vehicle_observations": vehicle_observations,
        "plate_observations": plate_observations,
        "plate_continuity": round(plate_observations / max(1, vehicle_observations), 4),
        "unique_plate_tracks": len(last_text),
        "plate_text_switches": text_switches,
        "latency_ms_p50": round(percentile(latencies, 50), 2),
        "latency_ms_p95": round(percentile(latencies, 95), 2),
        "sampled_fps": round(sampled / max(elapsed, 1e-9), 2),
        "peak_cuda_memory_bytes": peak_memory,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = run(args.source, max(1, args.frames), max(1, args.interval), args.device)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Syntax-check and capture the before metrics**

Run:

```powershell
uv run python -m py_compile scripts/benchmark_trained_pipeline.py
uv run python scripts/benchmark_trained_pipeline.py "runtime/uploads/601da8ae6ff24d0f9843e4876fb782a4_车牌识别.mp4" --frames 300 --interval 5 --device cuda:0 --output runtime/benchmarks/trained-plate-before.json
```

Expected: the command emits JSON with nonzero `sampled_frames`, latency metrics, and CUDA memory; `runtime/benchmarks/` remains ignored.

- [ ] **Step 5: Detect staged scope and commit the benchmark utility**

Run:

```powershell
git add -- scripts/benchmark_trained_pipeline.py
git diff --cached --check
node .gitnexus/run.cjs detect-changes --scope staged --repo VideoTest
git commit -m "test: add trained pipeline benchmark"
```

Expected: only the new script is committed and no production execution flow is affected.

## Task 2: Add Bounded Plate Temporal Fusion

**Files:**
- Create: `plate_temporal_fusion.py`
- Create: `tests/test_plate_temporal_fusion.py`

- [ ] **Step 1: Write failing temporal fusion tests**

Add tests that construct `TrackedVehicle`, `PlateTrackObservation`, and `PlateTemporalFusion` directly:

```python
from lpr_recognizer import PlateRecognition
from plate_temporal_fusion import (
    PlateTemporalFusion,
    PlateTrackObservation,
    TrackedVehicle,
)


def vehicle(track_id: int, bbox=(10, 10, 110, 70)) -> TrackedVehicle:
    return TrackedVehicle(bbox=bbox, track_id=track_id, confidence=0.9)


def observation(text: str, detector_confidence: float, bbox=(30, 40, 80, 55)):
    return PlateTrackObservation.from_absolute(
        PlateRecognition(text, 0.9, bbox, "蓝色"),
        detector_confidence=detector_confidence,
        vehicle_bbox=(10, 10, 110, 70),
    )


def test_weighted_vote_stabilizes_exact_plate_text():
    fusion = PlateTemporalFusion(window_size=5, hold_frames=5, max_tracks=16)
    target = vehicle(7)
    fusion.resolve("camera-a", [target], {7: observation("京A12345", 0.9)})
    fusion.resolve("camera-a", [target], {7: observation("京A1234S", 0.2)})
    results = fusion.resolve("camera-a", [target], {7: observation("京A12345", 0.8)})
    assert [item.plate_text for item in results] == ["京A12345"]


def test_missing_observation_is_held_then_expires_and_bbox_is_projected():
    fusion = PlateTemporalFusion(window_size=3, hold_frames=2, max_tracks=16)
    fusion.resolve("camera-a", [vehicle(3)], {3: observation("京A00003", 0.9)})
    moved = vehicle(3, bbox=(110, 110, 210, 170))
    held = fusion.resolve("camera-a", [moved], {})
    assert held[0].plate_text == "京A00003"
    assert 110 <= held[0].bbox[0] < held[0].bbox[2] <= 210
    fusion.resolve("camera-a", [moved], {})
    assert fusion.resolve("camera-a", [moved], {}) == []


def test_camera_reset_and_lru_bound_are_isolated():
    fusion = PlateTemporalFusion(window_size=2, hold_frames=2, max_tracks=2)
    for track_id in (1, 2, 3):
        fusion.resolve("camera-a", [vehicle(track_id)], {track_id: observation(f"京A0000{track_id}", 0.9)})
    assert fusion.cache_size == 2
    fusion.resolve("camera-b", [vehicle(8)], {8: observation("京B00008", 0.9)})
    fusion.reset("camera-a")
    assert [item.plate_text for item in fusion.resolve("camera-b", [vehicle(8)], {})] == ["京B00008"]
```

- [ ] **Step 2: Run tests and confirm the missing module failure**

Run:

```powershell
uv run pytest tests/test_plate_temporal_fusion.py -q
```

Expected: collection fails because `plate_temporal_fusion` does not exist.

- [ ] **Step 3: Implement the focused fusion module**

Implement the complete bounded cache in `plate_temporal_fusion.py`:

```python
@dataclass(frozen=True, slots=True)
class TrackedVehicle:
    bbox: tuple[int, int, int, int]
    track_id: int
    confidence: float


@dataclass(frozen=True, slots=True)
class PlateTrackObservation:
    recognition: PlateRecognition
    detector_confidence: float
    relative_bbox: tuple[float, float, float, float]

    @classmethod
    def from_absolute(cls, recognition, *, detector_confidence, vehicle_bbox):
        vx1, vy1, vx2, vy2 = vehicle_bbox
        width = max(1, vx2 - vx1)
        height = max(1, vy2 - vy1)
        x1, y1, x2, y2 = recognition.bbox
        return cls(
            recognition=recognition,
            detector_confidence=float(detector_confidence),
            relative_bbox=(
                (x1 - vx1) / width,
                (y1 - vy1) / height,
                (x2 - vx1) / width,
                (y2 - vy1) / height,
            ),
        )


@dataclass(slots=True)
class _TrackState:
    history: deque[tuple[int, PlateTrackObservation]]
    last_active_tick: int
    last_observed_tick: int


class PlateTemporalFusion:
    def __init__(
        self,
        window_size: int = 5,
        hold_frames: int = 5,
        max_tracks: int = 256,
        decay: float = 0.9,
    ) -> None:
        self.window_size = max(1, int(window_size))
        self.hold_frames = max(0, int(hold_frames))
        self.max_tracks = max(1, int(max_tracks))
        self.decay = max(0.0, min(1.0, float(decay)))
        self._ticks: dict[str, int] = {}
        self._tracks: OrderedDict[tuple[str, int], _TrackState] = OrderedDict()

    def resolve(
        self,
        camera_id: str,
        vehicles: Sequence[TrackedVehicle],
        observations: Mapping[int, PlateTrackObservation],
    ) -> list[PlateRecognition]:
        tick = self._ticks.get(camera_id, 0) + 1
        self._ticks[camera_id] = tick
        resolved: list[PlateRecognition] = []
        for vehicle in vehicles:
            if vehicle.track_id < 0:
                continue
            key = (camera_id, vehicle.track_id)
            observation = observations.get(vehicle.track_id)
            state = self._tracks.get(key)
            if state is None and observation is None:
                continue
            if state is None:
                state = _TrackState(
                    history=deque(maxlen=self.window_size),
                    last_active_tick=tick,
                    last_observed_tick=tick,
                )
                self._tracks[key] = state
            state.last_active_tick = tick
            if observation is not None:
                state.history.append((tick, observation))
                state.last_observed_tick = tick
            self._tracks.move_to_end(key)
            missed = tick - state.last_observed_tick
            if state.history and missed <= self.hold_frames:
                resolved.append(self._render(state, vehicle, missed))

        for key, state in list(self._tracks.items()):
            if key[0] == camera_id and tick - state.last_active_tick > self.hold_frames:
                del self._tracks[key]
        while len(self._tracks) > self.max_tracks:
            self._tracks.popitem(last=False)
        return resolved

    def _render(
        self,
        state: _TrackState,
        vehicle: TrackedVehicle,
        missed: int,
    ) -> PlateRecognition:
        scores: dict[str, float] = defaultdict(float)
        newest: dict[str, int] = {}
        for observed_tick, item in state.history:
            text = item.recognition.plate_text
            scores[text] += item.detector_confidence * item.recognition.confidence
            newest[text] = max(newest.get(text, 0), observed_tick)
        winner = max(scores, key=lambda text: (scores[text], newest[text]))
        observed_tick, representative = max(
            (
                (observed_tick, item)
                for observed_tick, item in state.history
                if item.recognition.plate_text == winner
            ),
            key=lambda pair: (
                pair[1].detector_confidence * pair[1].recognition.confidence,
                pair[0],
            ),
        )
        vx1, vy1, vx2, vy2 = vehicle.bbox
        width = max(1, vx2 - vx1)
        height = max(1, vy2 - vy1)
        rx1, ry1, rx2, ry2 = representative.relative_bbox
        bbox = (
            max(vx1, min(vx2, round(vx1 + rx1 * width))),
            max(vy1, min(vy2, round(vy1 + ry1 * height))),
            max(vx1, min(vx2, round(vx1 + rx2 * width))),
            max(vy1, min(vy2, round(vy1 + ry2 * height))),
        )
        return PlateRecognition(
            plate_text=winner,
            confidence=representative.recognition.confidence * self.decay**missed,
            bbox=bbox,
            plate_color=representative.recognition.plate_color,
        )

    def reset(self, camera_id: str | None = None) -> None:
        if camera_id is None:
            self._ticks.clear()
            self._tracks.clear()
            return
        self._ticks.pop(camera_id, None)
        for key in [key for key in self._tracks if key[0] == camera_id]:
            del self._tracks[key]

    @property
    def cache_size(self) -> int:
        return len(self._tracks)
```

Import `defaultdict`, `deque`, `OrderedDict`, `dataclass`, `Mapping`, `Sequence`, and `PlateRecognition`. The unused `observed_tick` returned with the representative is intentionally retained for deterministic tie-breaking during implementation and may be named `_observed_tick` to satisfy linting.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
uv run pytest tests/test_plate_temporal_fusion.py -q
```

Expected: all temporal fusion tests pass.

- [ ] **Step 5: Detect scope and commit**

Run:

```powershell
git add -- plate_temporal_fusion.py tests/test_plate_temporal_fusion.py
git diff --cached --check
node .gitnexus/run.cjs detect-changes --scope staged --repo VideoTest
git commit -m "feat: stabilize trained plate tracks"
```

Expected: only the new fusion unit and its tests are committed.

## Task 3: Implement Full-Frame-First Batched Plate Recovery

**Files:**
- Modify: `trained_plate_recognizer.py`
- Create: `tests/test_trained_plate_recognizer.py`
- Modify: `tests/test_model_pipeline_runtime.py`

- [ ] **Step 1: Run exact impact queries for the detector and recognizer methods**

Run:

```powershell
node .gitnexus/run.cjs impact detect --direction upstream --repo VideoTest --file trained_plate_recognizer.py --kind Method --depth 3 --include-tests
node .gitnexus/run.cjs impact recognize --direction upstream --repo VideoTest --file trained_plate_recognizer.py --kind Method --depth 3 --include-tests
```

Expected: LOW or MEDIUM. Report and stop if HIGH or CRITICAL.

- [ ] **Step 2: Write failing cascade tests**

Add these doubles and deterministic tests:

```python
from collections import deque


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
        np.zeros((100, 200, 3), dtype=np.uint8), vehicles, camera_id="camera-a"
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
        full_results=[[
            PlateBoxDetection((20, 50, 60, 65), 0.80),
            PlateBoxDetection((21, 50, 61, 65), 0.75),
            PlateBoxDetection((10, 10, 40, 40), 0.99),
            PlateBoxDetection((0, 0, 95, 75), 0.98),
        ]],
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
        np.zeros((100, 200, 3), dtype=np.uint8), vehicles, camera_id="camera-a"
    )
    second = recognizer.recognize_for_vehicles(
        np.zeros((100, 200, 3), dtype=np.uint8), vehicles, camera_id="camera-a"
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
```

Update the existing default-detector test to assert both `detect()` and `detect_batch()` pass `device`, `imgsz`, and the requested threshold to one Ultralytics `predict` call per input batch.

- [ ] **Step 3: Run the tests and observe missing APIs**

Run:

```powershell
uv run pytest tests/test_trained_plate_recognizer.py tests/test_model_pipeline_runtime.py -q
```

Expected: failures identify missing batch detection and `recognize_for_vehicles`.

- [ ] **Step 4: Implement the batch detector boundary**

Refactor `UltralyticsBoxPlateDetector` so `detect()` delegates to `detect_batch()`:

```python
def detect(self, frame: np.ndarray) -> list[PlateBoxDetection]:
    batches = self.detect_batch([frame], conf_threshold=self.conf_threshold)
    return batches[0] if batches else []

def detect_batch(
    self,
    frames: Sequence[np.ndarray],
    *,
    conf_threshold: float | None = None,
) -> list[list[PlateBoxDetection]]:
    if not frames:
        return []
    threshold = self.conf_threshold if conf_threshold is None else float(conf_threshold)
    results = self.model.predict(
        list(frames), conf=threshold, device=self.device,
        imgsz=self.inference_size, verbose=False,
    )
    batches = [self._detections_from_result(result) for result in results or []]
    return [*batches, *([[]] * max(0, len(frames) - len(batches)))]

@staticmethod
def _detections_from_result(result: object) -> list[PlateBoxDetection]:
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
    return sorted(detections, key=lambda item: item.confidence, reverse=True)
```

The padding ensures one output list for every input crop even when a model double returns fewer result objects.

- [ ] **Step 5: Implement the vehicle-aware cascade**

Add constructor defaults `crop_padding=0.20`, `max_crop_batch=16`, `min_aspect_ratio=1.5`, `max_aspect_ratio=8.0`, `max_plate_vehicle_area_ratio=0.35`, and an injected `PlateTemporalFusion`. Implement:

```python
def recognize_for_vehicles(
    self,
    frame: np.ndarray,
    vehicles: Sequence[TrackedVehicle],
    *,
    camera_id: str,
) -> list[PlateRecognition]:
    self.last_warning = ""
    full = self._safe_full_detections(frame)
    localized = self._associate_and_filter(full, vehicles, frame.shape)
    unmatched = self._unmatched_vehicle_indices(localized, vehicles)
    localized.extend(self._safe_crop_recovery(frame, vehicles, unmatched))
    localized = self._deduplicate(localized)
    observations, immediate = self._recognize_candidates(frame, vehicles, localized)
    fused = self._fusion.resolve(camera_id, vehicles, observations)
    return sorted([*fused, *immediate], key=lambda item: item.confidence, reverse=True)

def reset(self, camera_id: str | None = None) -> None:
    self._fusion.reset(camera_id)
```

Add the internal candidate type and implement the helpers with these exact invariants:

```python
@dataclass(frozen=True, slots=True)
class _LocalizedPlate:
    bbox: tuple[int, int, int, int]
    confidence: float
    vehicle_index: int


def _safe_full_detections(self, frame: np.ndarray) -> list[PlateBoxDetection]:
    try:
        return list(self.detector.detect(frame))
    except Exception as exc:
        self.last_warning = type(exc).__name__
        return []

def _associate_and_filter(self, detections, vehicles, frame_shape):
    height, width = frame_shape[:2]
    localized = []
    for detection in detections:
        x1, y1, x2, y2 = (round(value) for value in detection.bbox)
        bbox = (
            max(0, min(width, x1)), max(0, min(height, y1)),
            max(0, min(width, x2)), max(0, min(height, y2)),
        )
        matching = [
            index for index, vehicle in enumerate(vehicles)
            if self._valid_for_vehicle(bbox, vehicle.bbox)
        ]
        if not matching:
            continue
        vehicle_index = min(
            matching,
            key=lambda index: self._bbox_area(vehicles[index].bbox),
        )
        localized.append(
            _LocalizedPlate(bbox, float(detection.confidence), vehicle_index)
        )
    return localized

def _valid_for_vehicle(self, plate_bbox, vehicle_bbox):
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
    return vx1 <= center[0] <= vx2 and vy1 <= center[1] <= vy2

def _safe_crop_recovery(self, frame, vehicles, unmatched):
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
            max(0, vx1 - pad_x), max(0, vy1 - pad_y),
            min(width, vx2 + pad_x), min(height, vy2 + pad_y),
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
            conf_threshold=max(0.08, min(0.20, self.conf_threshold * 0.6)),
        )
    except Exception as exc:
        self.last_warning = type(exc).__name__
        return []
    recovered = []
    for (vehicle_index, (left, top, _right, _bottom)), detections in zip(
        records, batches
    ):
        for detection in detections:
            x1, y1, x2, y2 = detection.bbox
            bbox = (
                round(left + x1), round(top + y1),
                round(left + x2), round(top + y2),
            )
            if self._valid_for_vehicle(bbox, vehicles[vehicle_index].bbox):
                recovered.append(
                    _LocalizedPlate(bbox, detection.confidence, vehicle_index)
                )
    return recovered

def _deduplicate(self, candidates):
    kept = []
    for candidate in sorted(candidates, key=lambda item: item.confidence, reverse=True):
        duplicate = any(
            candidate.vehicle_index == existing.vehicle_index
            and self._iou(candidate.bbox, existing.bbox) >= 0.5
            for existing in kept
        )
        if not duplicate:
            kept.append(candidate)
    return kept

def _recognize_candidates(self, frame, vehicles, candidates):
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
            observation.detector_confidence * observation.recognition.confidence
            > current.detector_confidence * current.recognition.confidence
        ):
            observations[vehicle.track_id] = observation
    return observations, immediate
```

Use these pure helpers:

```python
@staticmethod
def _unmatched_vehicle_indices(candidates, vehicles):
    matched = {item.vehicle_index for item in candidates}
    return [index for index in range(len(vehicles)) if index not in matched]

@staticmethod
def _bbox_area(bbox):
    return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])

@classmethod
def _iou(cls, first, second):
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    union = cls._bbox_area(first) + cls._bbox_area(second) - intersection
    return intersection / max(1, union)
```

Store only exception class names in `last_warning`; a full, crop, or OCR failure must not discard a valid held track.

- [ ] **Step 6: Run focused and existing adapter tests**

Run:

```powershell
uv run pytest tests/test_trained_plate_recognizer.py tests/test_model_pipeline_runtime.py -q
```

Expected: all tests pass, including existing clamping and constructor contracts.

- [ ] **Step 7: Detect scope and commit**

Run:

```powershell
git add -- trained_plate_recognizer.py tests/test_trained_plate_recognizer.py tests/test_model_pipeline_runtime.py
git diff --cached --check
node .gitnexus/run.cjs detect-changes --scope staged --repo VideoTest
git commit -m "feat: add batched trained plate recovery"
```

Expected: trained plate adapter and tests only.

## Task 4: Connect Tracked Vehicles To Plate Fusion

**Files:**
- Modify: `detection_processor.py`
- Modify: `tests/test_detection_pipeline.py`

- [ ] **Step 1: Query exact method impacts**

Run:

```powershell
node .gitnexus/run.cjs impact process --direction upstream --repo VideoTest --file detection_processor.py --kind Method --depth 3 --include-tests
node .gitnexus/run.cjs impact reset_tracking --direction upstream --repo VideoTest --file detection_processor.py --kind Method --depth 3 --include-tests
```

Expected: MEDIUM or lower; report direct consumers and affected processes before editing.

- [ ] **Step 2: Write failing integration tests**

Add a trained recognizer double that records vehicle regions and returns one plate. Assert box mode calls `recognize_for_vehicles(frame, vehicles, camera_id="camera-a")`, pose mode still calls `recognize(frame)`, and `reset_tracking()` clears both ByteTrack and trained plate fusion:

Add `import numpy as np` plus test imports for `VehicleDetection` and `WhitelistManager`; `DetectionProcessor` and `PlateRecognition` are already imported by this test module.

```python
def ready_processor(vehicle_detector, recognizer, mode):
    processor = object.__new__(DetectionProcessor)
    processor._initialized = True
    processor._lpr_mode = mode
    processor.vehicle_detector = vehicle_detector
    processor.lpr_recognizer = recognizer
    processor.whitelist_manager = WhitelistManager()
    processor.total_frames_processed = 0
    processor.total_vehicles_detected = 0
    processor.total_plates_recognized = 0
    return processor


class VehicleDetectorDouble:
    def __init__(self):
        self.reset_calls = 0

    def detect(self, _frame, tracker_key="default"):
        assert tracker_key == "camera-a"
        return [
            VehicleDetection(
                bbox=(10, 10, 110, 80), confidence=0.9,
                class_id=2, class_name="car", class_name_cn="小汽车",
                track_id=7,
            )
        ]

    def reset_tracking(self):
        self.reset_calls += 1


class BoxRecognizerDouble:
    def __init__(self):
        self.calls = []
        self.reset_calls = 0

    def recognize_for_vehicles(self, frame, vehicles, *, camera_id):
        self.calls.append((frame, list(vehicles), camera_id))
        return [PlateRecognition("京A12345", 0.9, (30, 50, 80, 65), "蓝色")]

    def recognize(self, _frame):
        raise AssertionError("box mode must use vehicle-aware recognition")

    def reset(self):
        self.reset_calls += 1


def test_box_mode_receives_tracked_vehicle_regions_and_camera_id():
    vehicle_detector = VehicleDetectorDouble()
    recognizer = BoxRecognizerDouble()
    processor = ready_processor(vehicle_detector, recognizer, "box")
    frame = np.zeros((100, 120, 3), dtype=np.uint8)
    _, results = processor.process(frame, camera_id="camera-a")
    assert recognizer.calls[0][2] == "camera-a"
    assert recognizer.calls[0][1][0].track_id == 7
    assert results[0].plate_text == "京A12345"


def test_pose_mode_keeps_existing_full_frame_recognition():
    class PoseRecognizerDouble:
        def __init__(self):
            self.calls = 0

        def recognize(self, _frame):
            self.calls += 1
            return []

    recognizer = PoseRecognizerDouble()
    processor = ready_processor(VehicleDetectorDouble(), recognizer, "pose")
    processor.process(np.zeros((100, 120, 3), dtype=np.uint8), camera_id="camera-a")
    assert recognizer.calls == 1


def test_tracking_reset_also_resets_trained_plate_fusion():
    vehicle_detector = VehicleDetectorDouble()
    recognizer = BoxRecognizerDouble()
    processor = ready_processor(vehicle_detector, recognizer, "box")
    processor.reset_tracking()
    assert vehicle_detector.reset_calls == 1
    assert recognizer.reset_calls == 1
```

- [ ] **Step 3: Run and confirm the box-mode failure**

Run:

```powershell
uv run pytest tests/test_detection_pipeline.py -q
```

Expected: the box-mode double reports that `recognize()` was called or the new method was not called.

- [ ] **Step 4: Implement mode-specific recognition and reset**

In `DetectionProcessor.process()` replace the unconditional plate call with:

```python
if self.lpr_recognizer is None:
    plates = []
elif self._lpr_mode == "box":
    tracked = [
        TrackedVehicle(
            bbox=vehicle.bbox,
            track_id=vehicle.track_id,
            confidence=vehicle.confidence,
        )
        for vehicle in vehicles
    ]
    plates = self.lpr_recognizer.recognize_for_vehicles(
        frame, tracked, camera_id=camera_id or "default"
    )
else:
    plates = self.lpr_recognizer.recognize(frame)
```

Import `TrackedVehicle`. In `reset_tracking()`, call the trained recognizer's `reset()` when present after clearing vehicle trackers.

- [ ] **Step 5: Run detection and runtime regressions**

Run:

```powershell
uv run pytest tests/test_detection_pipeline.py tests/test_model_pipeline_runtime.py tests/test_video_service.py -q
```

Expected: all pass.

- [ ] **Step 6: Detect scope and commit**

Run:

```powershell
git add -- detection_processor.py tests/test_detection_pipeline.py
git diff --cached --check
node .gitnexus/run.cjs detect-changes --scope staged --repo VideoTest
git commit -m "feat: fuse trained plates by vehicle track"
```

## Task 5: Give Road Analysis Exclusive Inference Ownership

**Files:**
- Modify: `backend/video_stream.py`
- Modify: `backend/state.py`
- Modify: `tests/test_video_service.py`
- Modify: `tests/test_model_pipeline_runtime.py`

- [ ] **Step 1: Query impacts for every shared method to edit**

Run:

```powershell
node .gitnexus/run.cjs impact __init__ --direction upstream --repo VideoTest --file backend/video_stream.py --kind Method --depth 3 --include-tests
node .gitnexus/run.cjs impact apply_model_pipeline_options --direction upstream --repo VideoTest --file backend/video_stream.py --kind Method --depth 3 --include-tests
node .gitnexus/run.cjs impact _run --direction upstream --repo VideoTest --file backend/video_stream.py --kind Method --depth 3 --include-tests
node .gitnexus/run.cjs impact __init__ --direction upstream --repo VideoTest --file backend/state.py --kind Method --depth 3 --include-tests
node .gitnexus/run.cjs impact activate_scene_runtime --direction upstream --repo VideoTest --file backend/state.py --kind Method --depth 3 --include-tests
```

Expected: no HIGH/CRITICAL result. Treat `_run` as shared even if the graph reports no callers.

- [ ] **Step 2: Write failing video-service tests**

Add these tests using the existing `_pipeline_options` and `_ProcessorDouble` helpers:

```python
def test_external_inference_stores_pipeline_metadata_without_loading_processor():
    factory_calls = []

    def forbidden_factory(options):
        factory_calls.append(options)
        raise AssertionError("external inference must not load DetectionProcessor")

    service = VideoStreamService(
        WhitelistManager(), scene_key="road_abnormal",
        processor_factory=forbidden_factory, external_inference=True,
    )
    service.apply_model_pipeline_options(
        _pipeline_options("road_abnormal", preset="trained")
    )
    service._ensure_processor()
    status = service.status()["detection"]
    assert factory_calls == []
    assert status["enabled"] is True
    assert status["preset"] == "trained"
    assert status["status"] == "由场景分析器处理"


def test_frame_processor_receives_pristine_pixels_before_cached_detection_overlay():
    observed = []

    def frame_processor(_camera_id, frame):
        observed.append(int(frame[0, 0, 0]))
        return np.full_like(frame, 15)

    service = VideoStreamService(
        WhitelistManager(), frame_processor=frame_processor,
        processor_factory=lambda _options: _ProcessorDouble(),
    )
    service.apply_model_pipeline_options(_pipeline_options())
    service._ensure_processor()
    service._cached_detection_results = lambda _snapshot, _revision: []
    drawn_inputs = []
    service._draw_cached_results = lambda frame, _results: (
        drawn_inputs.append(int(frame[0, 0, 0])) or np.full_like(frame, 25)
    )
    source = video_stream_module.StreamSource("id", "camera-a", "camera-a", "test")
    annotated, _snapshot = service._compose_frame(
        source, 1, np.full((4, 4, 3), 5, dtype=np.uint8),
        service._processing_snapshot(),
    )
    assert observed == [5]
    assert drawn_inputs == [15]
    assert int(annotated[0, 0, 0]) == 25


def test_external_inference_status_distinguishes_disabled_and_scene_owned():
    service = VideoStreamService(
        WhitelistManager(), scene_key="road_abnormal", external_inference=True,
    )
    enabled = _pipeline_options("road_abnormal", preset="trained")
    service.apply_model_pipeline_options(enabled)
    assert service.status()["detection"]["status"] == "由场景分析器处理"
    service.apply_model_pipeline_options(replace(enabled, enabled=False, revision=2))
    assert service.status()["detection"]["status"] == "未启用"
```

The first test must inject a processor factory that raises if called. The raw-frame test must use a frame processor that asserts every pixel still equals the capture value before returning its own overlay.

- [ ] **Step 3: Write failing application-state tests**

Update the existing construction test's stream double and assertion:

```python
constructed_streams = []

class StreamDouble:
    def __init__(
        self, *_args, scene_key="realtime", external_inference=False, **_kwargs
    ):
        self.scene_key = scene_key
        constructed_streams.append((scene_key, bool(external_inference)))

    def update_detection_settings(self, **_settings):
        return None

    def status(self):
        return {"scene_key": self.scene_key}

# After ApplicationState(config):
assert constructed_streams == [
    ("realtime", False),
    ("traffic_map", False),
    ("no_parking", False),
    ("road_abnormal", True),
]
```

Add the activation regression:

```python
def test_road_activation_does_not_override_pipeline_enabled_setting():
    selected = []

    class RoadVideoDouble:
        def select_source(self, source_id, name, url):
            selected.append((source_id, name, url))

        def update_detection_settings(self, **settings):
            raise AssertionError(f"road activation overrode pipeline: {settings}")

    state = object.__new__(ApplicationState)
    state.road_abnormal_video = RoadVideoDouble()
    state.road_abnormal = SimpleNamespace(
        get_scene=lambda _scene_id: {"scene_id": "road-1"},
        start=lambda scene_id: {"active_scene_id": scene_id},
    )
    result = state.activate_scene_runtime(
        {
            "scene_id": "road-1",
            "scene_type": "road_abnormal",
            "camera_id": "camera-a",
        },
        "road.mp4",
    )
    assert selected == [("camera-a", "camera-a", "road.mp4")]
    assert result == {"active_scene_id": "road-1"}
```

- [ ] **Step 4: Run tests and confirm failures**

Run:

```powershell
uv run pytest tests/test_video_service.py tests/test_model_pipeline_runtime.py -q
```

Expected: constructor and ownership assertions fail.

- [ ] **Step 5: Implement external ownership and pristine ordering**

Add `external_inference: bool = False` to `VideoStreamService.__init__`. Store it as `_external_inference`. Generic processor work is enabled only when:

```python
processor_enabled = desired.enabled and not self._external_inference
```

When external inference is enabled, `status()["detection"]` still reports the persisted preset, enabled flag, device and revision; the status text is `由场景分析器处理` when enabled and `未启用` otherwise. `_ensure_processor()` and `_submit_inference()` return without constructing a processor.

Make `_processing_snapshot()` set its `enabled` field from `processor_enabled`, while `status()` continues to use `desired.enabled`. Extract the existing per-frame composition block with this complete order:

```python
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
            processing, source_revision
        )
        if cached_results is not None:
            annotated = self._draw_cached_results(annotated, cached_results)
            annotated_snapshot = processing
    return annotated, annotated_snapshot
```

`_run()` continues to schedule generic inference only when `processing.enabled`, then delegates composition to this helper. This passes the original unannotated array to the scene analyzer and draws generic results over the returned frame only for non-external services.

Construct `road_abnormal_video` with `external_inference=True` in `ApplicationState.__init__`. Remove the road branch's `update_detection_settings(enabled=False)` call from `activate_scene_runtime`; the model-pipeline row is now authoritative.

- [ ] **Step 6: Run video, state, API, and scene regressions**

Run:

```powershell
uv run pytest tests/test_video_service.py tests/test_model_pipeline_runtime.py tests/test_api.py tests/test_scene_topology_independence.py -q
```

Expected: all pass.

- [ ] **Step 7: Detect scope and commit**

Run:

```powershell
git add -- backend/video_stream.py backend/state.py tests/test_video_service.py tests/test_model_pipeline_runtime.py
git diff --cached --check
node .gitnexus/run.cjs detect-changes --scope staged --repo VideoTest
git commit -m "fix: give road monitor exclusive inference ownership"
```

## Task 6: Carry MOG Observation Time Into Events

**Files:**
- Modify: `backend/trained_mog.py`
- Modify: `backend/road_abnormal.py`
- Modify: `tests/test_road_abnormal.py`

- [ ] **Step 1: Query impacts for the exact symbols**

Run:

```powershell
node .gitnexus/run.cjs impact TrainedMOGAlert --direction upstream --repo VideoTest --file backend/trained_mog.py --kind Class --depth 3 --include-tests
node .gitnexus/run.cjs impact process --direction upstream --repo VideoTest --file backend/trained_mog.py --kind Method --depth 3 --include-tests
node .gitnexus/run.cjs impact update_candidates --direction upstream --repo VideoTest --file backend/road_abnormal.py --kind Method --depth 3 --include-tests
```

- [ ] **Step 2: Write failing duration tests**

Add assertions to the headless MOG test that confirmed alerts include a positive `observed_duration`. Add this event-layer test:

```python
def test_trained_mog_observation_duration_is_not_counted_twice(tmp_path):
    monitor = _monitor(tmp_path)
    reference = monitor.capture_reference(b"jpeg", "camera", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(reference, camera_id="camera", persistence_seconds=3.0)
    )
    monitor.start(scene["scene_id"])
    events = monitor.update_candidates(
        "camera",
        [_candidate(source="MOG", observed_duration=3.2)],
        (100, 100),
        now=10.0,
    )
    assert len(events) == 1
    assert events[0]["first_seen"] == pytest.approx(6.8)
```

- [ ] **Step 3: Run tests and confirm the duration failure**

Run:

```powershell
uv run pytest tests/test_road_abnormal.py -q
```

Expected: alert has no `observed_duration` and the event still waits from zero.

- [ ] **Step 4: Implement duration propagation**

Add `observed_duration: float = 0.0` to `TrainedMOGAlert` and set it from the confirmed tracked object's duration. In `_trained_mog_candidates_for_runtime()`, copy it into the internal candidate dictionary. In `update_candidates()` initialize and reset MOG candidate state with:

```python
observed_duration = max(0.0, float(candidate.get("observed_duration", 0.0)))
first_seen = observed_at - observed_duration
duration_seconds = observed_duration
```

Do not persist `observed_duration` as a new public event field.

- [ ] **Step 5: Run road tests**

Run:

```powershell
uv run pytest tests/test_road_abnormal.py -q
```

Expected: all tests pass and legacy candidates without the internal field retain existing timing.

- [ ] **Step 6: Detect scope and commit**

Run:

```powershell
git add -- backend/trained_mog.py backend/road_abnormal.py tests/test_road_abnormal.py
git diff --cached --check
node .gitnexus/run.cjs detect-changes --scope staged --repo VideoTest
git commit -m "fix: preserve trained MOG observation time"
```

## Task 7: Add Scheduled And Alert-Triggered Road YOLO Verification

**Files:**
- Modify: `backend/road_abnormal.py`
- Modify: `tests/test_road_abnormal.py`

- [ ] **Step 1: Query exact road-monitor impacts**

Run:

```powershell
node .gitnexus/run.cjs impact apply_model_pipeline_options --direction upstream --repo VideoTest --file backend/road_abnormal.py --kind Method --depth 3 --include-tests
node .gitnexus/run.cjs impact _reset_runtime --direction upstream --repo VideoTest --file backend/road_abnormal.py --kind Method --depth 3 --include-tests
node .gitnexus/run.cjs impact _trained_mog_candidates_for_runtime --direction upstream --repo VideoTest --file backend/road_abnormal.py --kind Method --depth 3 --include-tests
node .gitnexus/run.cjs impact process_frame --direction upstream --repo VideoTest --file backend/road_abnormal.py --kind Method --depth 3 --include-tests
```

- [ ] **Step 2: Write failing scheduling and verification tests**

Add `from collections import deque`, then add these sequenced doubles and tests:

```python
class SequencedDetector:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []

    def prepare(self):
        return self

    def detect(self, frame, threshold):
        self.calls.append((frame.copy(), threshold))
        response = self.responses.popleft() if self.responses else []
        if isinstance(response, Exception):
            raise response
        return [dict(item) for item in response]


class SequencedMog:
    def __init__(self, alerts):
        self.alerts = list(alerts)
        self.process_calls = []
        self.reset_calls = 0

    def reset(self):
        self.reset_calls += 1

    def set_rois(self, _polygons):
        return None

    def process(self, frame, yolo_boxes, frame_id=0, timestamp=None):
        self.process_calls.append((frame.copy(), list(yolo_boxes), frame_id))
        return list(self.alerts)


def trained_monitor(tmp_path, detector, mog, *, options=None, scene_overrides=None):
    monitor = RoadAbnormalMonitor(
        tmp_path / "road-abnormal",
        tmp_path / "legacy.pt",
        detector=FakeDetector(),
        detector_factory=lambda _path, _device, _size: detector,
        mog_factory=lambda **_kwargs: mog,
    )
    monitor.apply_model_pipeline_options(options or _trained_options(tmp_path))
    reference = monitor.capture_reference(b"jpeg", "camera", 100, 100)
    payload = _scene_payload(reference, camera_id="camera")
    payload.update(scene_overrides or {})
    scene = monitor.upsert_scene(payload)
    monitor.start(scene["scene_id"])
    return monitor


def mog_alert(x=30):
    return SimpleNamespace(
        anomaly_type="medium_object", position=(x, 30, 20, 20),
        lane="middle", alert_time=0.0, confidence=0.88,
        frame_id=0, observed_duration=0.2,
    )


def test_trained_pipeline_uses_model_interval_and_threshold_not_legacy_scene_values(tmp_path):
    detector = SequencedDetector([[], []])
    mog = SequencedMog([])
    options = replace(_trained_options(tmp_path), frame_interval=3, yolo_threshold=0.37)
    monitor = trained_monitor(
        tmp_path, detector, mog, options=options,
        scene_overrides={"inference_interval": 1, "yolo_threshold": 0.91},
    )
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    for frame_index in range(4):
        monitor.process_frame("camera", frame, now=float(frame_index))
    assert [threshold for _frame, threshold in detector.calls] == [0.37, 0.37]


def test_stale_yolo_result_triggers_one_shared_current_frame_verification(tmp_path):
    detector = SequencedDetector([[], []])
    mog = SequencedMog([mog_alert(25), mog_alert(60)])
    options = replace(_trained_options(tmp_path), frame_interval=10)
    monitor = trained_monitor(tmp_path, detector, mog, options=options)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    for frame_index in range(4):
        monitor.process_frame("camera", frame, now=float(frame_index))
    assert len(detector.calls) == 2


def test_current_normal_vehicle_filters_mog_candidate_by_coverage_or_center(tmp_path):
    normal = {
        "bbox": (25, 25, 55, 55), "class_name": "car",
        "class_name_cn": "小汽车", "confidence": 0.95, "track_id": -1,
    }
    monitor = trained_monitor(
        tmp_path, SequencedDetector([[normal]]), SequencedMog([mog_alert()])
    )
    monitor.process_frame("camera", np.zeros((100, 100, 3), dtype=np.uint8), now=1.0)
    assert monitor.status()["candidates"] == []


def test_current_known_anomaly_replaces_overlapping_unclassified_mog_candidate(tmp_path):
    person = {
        "bbox": (25, 25, 55, 55), "class_name": "person",
        "class_name_cn": "行人", "confidence": 0.91, "track_id": 7,
    }
    monitor = trained_monitor(
        tmp_path, SequencedDetector([[person]]), SequencedMog([mog_alert()])
    )
    monitor.process_frame("camera", np.zeros((100, 100, 3), dtype=np.uint8), now=1.0)
    assert {item["source"] for item in monitor.status()["candidates"]} == {"YOLO"}


def test_verification_failure_keeps_mog_candidate_and_reports_degraded_status(tmp_path):
    detector = SequencedDetector([[], RuntimeError("secret detector detail")])
    options = replace(_trained_options(tmp_path), frame_interval=10)
    monitor = trained_monitor(tmp_path, detector, SequencedMog([mog_alert()]), options=options)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    for frame_index in range(4):
        monitor.process_frame("camera", frame, now=float(frame_index))
    status = monitor.status()
    assert any(item["source"] == "MOG" for item in status["candidates"])
    assert status["last_error"] == "道路目标检测降级: RuntimeError"
    assert "secret" not in status["last_error"]


def test_disabled_pipeline_publishes_frame_without_yolo_or_mog_calls(tmp_path):
    detector = SequencedDetector([[]])
    mog = SequencedMog([mog_alert()])
    options = replace(_trained_options(tmp_path), enabled=False)
    monitor = trained_monitor(tmp_path, detector, mog, options=options)
    before_detector = len(detector.calls)
    before_mog = len(mog.process_calls)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    returned = monitor.process_frame("camera", frame, now=1.0)
    assert returned is frame
    assert len(detector.calls) == before_detector
    assert len(mog.process_calls) == before_mog
```

The shared-verification test must return multiple MOG candidates and assert the detector call count increases by one, not by candidate count.

- [ ] **Step 3: Run tests and confirm current coupled-error and stale-box failures**

Run:

```powershell
uv run pytest tests/test_road_abnormal.py -q
```

Expected: failures show scene interval usage, missing on-demand verification, detector errors dropping MOG, and disabled options being ignored.

- [ ] **Step 4: Add committed runtime state**

Track `_known_objects_frame = -1` and reset it with `_frame_count`, `_known_objects`, background, MOG, and generation changes. Snapshot the active `ModelPipelineOptions` inside `process_frame()` and derive:

```python
pipeline_enabled = options.enabled if options is not None else True
detector_interval = options.frame_interval if options is not None else scene.inference_interval
detector_threshold = options.yolo_threshold if options is not None else scene.yolo_threshold
```

Return the original frame before entering inference when `pipeline_enabled` is false.

- [ ] **Step 5: Separate detector degradation from MOG execution**

Refactor the inference portion of `process_frame()` so the two failures are independent:

```python
detector_error = ""
mog_error = ""
detected_this_frame = False
if frame_count % detector_interval == 0:
    try:
        known_objects = detector.detect(frame, detector_threshold)
        known_objects_frame = frame_count
        detected_this_frame = True
    except Exception as exc:
        detector_error = "道路目标检测降级: " + type(exc).__name__

try:
    if road_abnormal_mode == "mog":
        foreground, next_frame_count = self._trained_mog_candidates_for_runtime(
            frame, scene, known_objects, observed_at,
            mog_engine, frame_count,
        )
    else:
        foreground, next_frame_count = self._legacy_foreground_candidates(
            frame, scene, known_objects, background, frame_count,
        )
except Exception as exc:
    foreground = []
    next_frame_count = frame_count + 1
    mog_error = "道路异常检测失败: " + type(exc).__name__
```

Scheduled detector failure therefore still invokes MOG with the last committed normal boxes. Commit `mog_error or detector_error` as `last_error`; clear it only after both paths succeed.

- [ ] **Step 6: Add current-frame verification and overlap filtering**

After trained MOG candidates are produced, run one detector verification when candidates exist and `frame_count - known_objects_frame > 2`. Reuse that result for every candidate and update `known_objects_frame`.

```python
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
        detector_error = "道路目标检测降级: " + type(exc).__name__

if road_abnormal_mode == "mog" and detected_this_frame:
    foreground = self._mog_candidates_not_covered_by_known_objects(
        foreground, known_objects
    )
known = self._known_anomaly_candidates(known_objects, scene)
combined_candidates = [*known, *foreground]
```

Implement a helper whose normal-object rule matches the imported algorithm:

```python
def _candidate_is_covered(candidate_bbox: BBox, object_bbox: BBox) -> bool:
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
def _mog_candidates_not_covered_by_known_objects(cls, candidates, objects):
    object_boxes = [tuple(item["bbox"]) for item in objects]
    return [
        candidate
        for candidate in candidates
        if not any(
            cls._candidate_is_covered(tuple(candidate["bbox"]), object_bbox)
            for object_bbox in object_boxes
        )
    ]
```

Suppress MOG candidates overlapping any freshly classified object. Normal classes produce no candidate; configured anomaly classes enter through `_known_anomaly_candidates()`. If verification raises, retain MOG candidates and commit only the sanitized degradation status.

- [ ] **Step 7: Run road, pipeline, API, and concurrency regressions**

Run:

```powershell
uv run pytest tests/test_road_abnormal.py tests/test_model_pipeline_runtime.py tests/test_api.py tests/test_configuration_api.py -q
```

Expected: all pass.

- [ ] **Step 8: Detect scope and commit**

Run:

```powershell
git add -- backend/road_abnormal.py tests/test_road_abnormal.py
git diff --cached --check
node .gitnexus/run.cjs detect-changes --scope staged --repo VideoTest
git commit -m "feat: verify trained road anomalies on current frames"
```

## Task 8: Full Regression, Real-Model Comparison, And Visual Acceptance

**Files:**
- Verify all implementation files.
- Generated and ignored: `runtime/benchmarks/trained-plate-after.json`

- [ ] **Step 1: Run the complete automated suite**

Run:

```powershell
uv run pytest -q
```

Expected: every test passes with no new warning class.

- [ ] **Step 2: Run static and diff checks**

Run:

```powershell
uv run python -m py_compile plate_temporal_fusion.py trained_plate_recognizer.py detection_processor.py backend/video_stream.py backend/trained_mog.py backend/road_abnormal.py scripts/benchmark_trained_pipeline.py
node --check frontend/js/system-management.js
git diff --check a2d521c..HEAD
```

Expected: all commands exit zero.

- [ ] **Step 3: Capture after metrics using the identical video and sample budget**

Run:

```powershell
uv run python scripts/benchmark_trained_pipeline.py "runtime/uploads/601da8ae6ff24d0f9843e4876fb782a4_车牌识别.mp4" --frames 300 --interval 5 --device cuda:0 --output runtime/benchmarks/trained-plate-after.json
Get-Content -Raw runtime/benchmarks/trained-plate-before.json
Get-Content -Raw runtime/benchmarks/trained-plate-after.json
```

Expected: no CUDA OOM; after metrics show non-decreasing `plate_observations`/`plate_continuity`, no increase in `plate_text_switches`, and report P50/P95 latency plus peak memory. If a metric regresses, inspect rendered frames and explain or fix the cause before completion.

- [ ] **Step 4: Exercise the normal-vehicle road path with real weights**

Use the local `车辆追踪.mp4` through an active full-frame road test scene with the trained preset. Let the entire 36.5-second video finish, then query road status and event history.

Expected: the monitor loads one `yolo26x` owner, does not load `license_plate_best` or OCR for the road service, remains free of CUDA OOM, and normal vehicles do not create new MOG obstacle events.

- [ ] **Step 5: Start the application on an unused local port**

Run:

```powershell
$env:VIDEOTEST_HOST='127.0.0.1'
$env:VIDEOTEST_PORT='8015'
uv run python main.py
```

Expected: the service starts at `http://127.0.0.1:8015` and all four stream workers remain alive.

- [ ] **Step 6: Inspect the running UI and scene status**

Open `http://127.0.0.1:8015`, select the local plate video in realtime monitoring, and verify vehicle boxes, stable plate text, status metrics, and no incoherent overlay. Activate a road scene with the local tracking video and verify raw-frame MOG behavior, candidate status, and device status at desktop and mobile widths.

Expected: no overlapping controls, no stale plate text after switching source, road status says the scene analyzer owns inference, and the browser console has no new errors.

- [ ] **Step 7: Run final GitNexus regression detection**

Run:

```powershell
node .gitnexus/run.cjs analyze --force --name VideoTest --skip-agents-md
node .gitnexus/run.cjs detect-changes --scope compare --base-ref a2d521c --repo VideoTest
git status --short
```

Expected: affected scope is limited to trained plate recognition, detection processing, video frame ownership, road MOG/event processing, their tests, and the benchmark utility. `.gitignore` remains the user's only unrelated worktree change.

- [ ] **Step 8: Summarize measured behavior**

Report before/after plate continuity, text switches, latency P50/P95, sampled FPS, peak CUDA memory, road event outcome, full test count, and GitNexus risk. Do not claim precision or recall because the local videos have no complete ground-truth annotations.
