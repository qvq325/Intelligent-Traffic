from dataclasses import replace
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import numpy as np

from backend.model_pipelines import ModelPipelineOptions
from backend.road_abnormal import RoadAbnormalMonitor, RoadObjectDetector


class FakeDetector:
    def __init__(self, objects=None):
        self.objects = objects or []

    def detect(self, frame, threshold):
        return list(self.objects)


class FakeMogEngine:
    def __init__(self):
        self.calls = []
        self.reset_calls = 0
        self.roi = None

    def set_roi(self, polygon):
        self.roi = list(polygon)

    def reset(self):
        self.reset_calls += 1

    def process(self, frame, yolo_boxes, frame_id=0, timestamp=None):
        self.calls.append(
            {
                "frame": frame,
                "yolo_boxes": list(yolo_boxes),
                "frame_id": frame_id,
                "timestamp": timestamp,
            }
        )
        return [
            SimpleNamespace(
                anomaly_type="medium_object",
                position=(30, 30, 20, 20),
                lane="middle",
                alert_time=timestamp,
                confidence=0.88,
                frame_id=frame_id,
            )
        ]


def _scene_payload(reference, **overrides):
    payload = {
        "scene_id": "tunnel-scene",
        "name": "隧道路面异常",
        "camera_id": "隧道(事故识别)",
        "reference_image": reference["filename"],
        "reference_width": reference["width"],
        "reference_height": reference["height"],
        "zones": [
            {
                "zone_id": "lane-1",
                "name": "隧道检测区",
                "lane_name": "一号车道",
                "points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
            }
        ],
        "persistence_seconds": 2.0,
        "lost_tolerance_seconds": 0.5,
        "min_area_ratio": 0.01,
        "warmup_frames": 0,
        "inference_interval": 1,
    }
    payload.update(overrides)
    return payload


def _monitor(tmp_path, detector=None):
    return RoadAbnormalMonitor(
        tmp_path / "road-abnormal",
        tmp_path / "unused.pt",
        detector=detector or FakeDetector(),
    )


def _candidate(bbox=(30, 30, 50, 50), **overrides):
    candidate = {
        "source": "MOG2",
        "anomaly_type": "unknown_obstacle",
        "class_name": "unknown",
        "class_name_cn": "未知障碍物",
        "confidence": 0.7,
        "bbox": bbox,
        "track_id": -1,
    }
    candidate.update(overrides)
    return candidate


def _trained_options(tmp_path):
    return ModelPipelineOptions(
        scene_key="road_abnormal",
        preset="trained",
        enabled=True,
        device_preference="cpu",
        yolo_threshold=0.5,
        lpr_threshold=0.7,
        frame_interval=1,
        inference_size=768,
        parking_move_threshold=0.03,
        mog_history=321,
        mog_variance_threshold=19.0,
        mog_min_area=90,
        mog_min_duration=0.1,
        mog_max_duration=2.0,
        mog_warmup_frames=0,
        revision=7,
        vehicle_model_path=tmp_path / "trained.pt",
        plate_model_path=tmp_path / "plate.pt",
        plate_mode="box",
        no_parking_mode="stationary",
        road_abnormal_mode="mog",
    )


def test_candidate_requires_roi_and_persistence_before_alarm(tmp_path):
    monitor = _monitor(tmp_path)
    reference = monitor.capture_reference(b"jpeg", "隧道(事故识别)", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(reference, lost_tolerance_seconds=2.0)
    )
    monitor.start(scene["scene_id"])

    assert monitor.update_candidates(
        "隧道(事故识别)", [_candidate()], (100, 100), now=10.0
    ) == []
    assert monitor.update_candidates(
        "隧道(事故识别)", [_candidate()], (100, 100), now=11.0
    ) == []
    events = monitor.update_candidates(
        "隧道(事故识别)", [_candidate()], (100, 100), now=12.1
    )
    outside = monitor.update_candidates(
        "隧道(事故识别)", [_candidate((0, 0, 5, 5))], (100, 100), now=12.2
    )

    assert len(events) == 1
    assert events[0]["lane_name"] == "一号车道"
    assert events[0]["source"] == "MOG2"
    assert outside == []
    assert monitor.status()["metrics"]["active_alarms"] == 1


def test_lost_candidate_closes_event_and_persists_history(tmp_path):
    root = tmp_path / "road-abnormal"
    monitor = RoadAbnormalMonitor(root, tmp_path / "unused.pt", detector=FakeDetector())
    reference = monitor.capture_reference(b"jpeg", "隧道(事故识别)", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(
            reference,
            persistence_seconds=1.0,
            lost_tolerance_seconds=2.0,
        )
    )
    monitor.start(scene["scene_id"])
    monitor.update_candidates("隧道(事故识别)", [_candidate()], (100, 100), now=1.0)
    monitor.update_candidates("隧道(事故识别)", [_candidate()], (100, 100), now=2.1)
    monitor.update_candidates("隧道(事故识别)", [], (100, 100), now=4.2)

    status = monitor.status()
    reloaded = RoadAbnormalMonitor(root, tmp_path / "unused.pt", detector=FakeDetector())

    assert status["candidates"] == []
    assert status["events"][0]["ended_at"] == 2.1
    assert reloaded.catalog()["scenes"][0]["scene_id"] == "tunnel-scene"
    assert reloaded.status()["metrics"]["total_events"] == 1


def test_mog2_candidates_are_filtered_when_they_overlap_normal_vehicles(tmp_path):
    monitor = _monitor(tmp_path)
    reference = monitor.capture_reference(b"jpeg", "隧道(事故识别)", 100, 100)
    scene_payload = _scene_payload(reference, min_area_ratio=0.001)
    scene = monitor.upsert_scene(scene_payload)
    monitor.start(scene["scene_id"])
    runtime_scene = monitor._scenes[scene["scene_id"]]
    monitor._background = SimpleNamespace(
        apply=lambda frame, learningRate: np.pad(
            np.full((20, 20), 255, dtype=np.uint8), ((30, 50), (30, 50))
        )
    )
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    vehicle = {"bbox": (25, 25, 55, 55), "class_name": "car"}

    candidates = monitor._foreground_candidates(frame, runtime_scene, [vehicle])

    assert candidates == []


def test_known_road_user_is_reported_as_yolo_anomaly(tmp_path):
    detector = FakeDetector(
        [
            {
                "bbox": (30, 20, 45, 55),
                "class_name": "person",
                "class_name_cn": "行人",
                "confidence": 0.91,
                "track_id": 7,
            }
        ]
    )
    monitor = _monitor(tmp_path, detector)
    reference = monitor.capture_reference(b"jpeg", "隧道(事故识别)", 100, 100)
    scene = monitor.upsert_scene(_scene_payload(reference, persistence_seconds=0.1))
    monitor.start(scene["scene_id"])
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    monitor.process_frame("隧道(事故识别)", frame, now=1.0)
    monitor.process_frame("隧道(事故识别)", frame, now=1.2)
    status = monitor.status()

    assert status["metrics"]["active_alarms"] == 1
    assert status["events"][0]["source"] == "YOLO"
    assert status["events"][0]["class_name_cn"] == "行人"
    assert status["events"][0]["snapshot_url"].startswith(
        "/api/road-abnormal/snapshots/"
    )


def test_stationary_unknown_obstacle_is_not_absorbed_after_warmup(tmp_path):
    monitor = _monitor(tmp_path)
    reference = monitor.capture_reference(b"jpeg", "隧道(事故识别)", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(
            reference,
            persistence_seconds=0.3,
            lost_tolerance_seconds=0.5,
            min_area_ratio=0.001,
            warmup_frames=5,
            learning_rate=0.5,
            detect_shadows=False,
        )
    )
    monitor.start(scene["scene_id"])
    background = np.zeros((100, 100, 3), dtype=np.uint8)
    for index in range(5):
        monitor.process_frame("隧道(事故识别)", background, now=index * 0.1)

    obstacle = background.copy()
    obstacle[30:60, 30:60] = 255
    for index in range(80):
        monitor.process_frame(
            "隧道(事故识别)", obstacle, now=1.0 + index * 0.1
        )

    status = monitor.status()

    assert status["metrics"]["active_candidates"] == 1
    assert status["metrics"]["active_alarms"] == 1
    assert status["metrics"]["total_events"] == 1
    assert status["candidates"][0]["duration_seconds"] >= 7.5
    assert status["events"][0]["ended_at"] is None


def test_stationary_normal_vehicle_does_not_become_unknown_obstacle(tmp_path):
    detector = FakeDetector(
        [
            {
                "bbox": (30, 30, 60, 60),
                "class_name": "car",
                "class_name_cn": "小汽车",
                "confidence": 0.95,
                "track_id": 9,
            }
        ]
    )
    monitor = _monitor(tmp_path, detector)
    reference = monitor.capture_reference(b"jpeg", "隧道(事故识别)", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(
            reference,
            persistence_seconds=0.3,
            min_area_ratio=0.001,
            warmup_frames=5,
            learning_rate=0.5,
            detect_shadows=False,
        )
    )
    monitor.start(scene["scene_id"])
    background = np.zeros((100, 100, 3), dtype=np.uint8)
    for index in range(5):
        monitor.process_frame("隧道(事故识别)", background, now=index * 0.1)

    vehicle = background.copy()
    vehicle[30:60, 30:60] = 255
    for index in range(80):
        monitor.process_frame(
            "隧道(事故识别)", vehicle, now=1.0 + index * 0.1
        )

    status = monitor.status()

    assert status["metrics"]["active_candidates"] == 0
    assert status["metrics"]["active_alarms"] == 0
    assert status["metrics"]["total_events"] == 0


def test_scene_validation_rejects_invalid_roi(tmp_path):
    monitor = _monitor(tmp_path)
    reference = monitor.capture_reference(b"jpeg", "camera", 100, 100)
    payload = _scene_payload(reference)
    payload["zones"][0]["points"] = [[0.1, 0.1], [0.5, 0.5]]

    try:
        monitor.upsert_scene(payload)
    except ValueError as exc:
        assert str(exc) == "道路检测区域至少需要三个点"
    else:
        raise AssertionError("invalid ROI should be rejected")


def test_trained_mode_delegates_to_injected_mog_and_keeps_event_payload(tmp_path):
    normal_vehicle = {
        "bbox": (25, 25, 55, 55),
        "class_name": "car",
        "class_name_cn": "小汽车",
        "confidence": 0.95,
        "track_id": 9,
    }
    known_anomaly = {
        "bbox": (60, 20, 70, 55),
        "class_name": "person",
        "class_name_cn": "行人",
        "confidence": 0.91,
        "track_id": 7,
    }
    detector = FakeDetector([normal_vehicle, known_anomaly])
    mog = FakeMogEngine()
    detector_factory_calls = []
    mog_factory_calls = []

    def detector_factory(model_path, device, inference_size):
        detector_factory_calls.append((model_path, device, inference_size))
        return detector

    def mog_factory(**kwargs):
        mog_factory_calls.append(kwargs)
        return mog

    monitor = RoadAbnormalMonitor(
        tmp_path / "road-abnormal",
        tmp_path / "unused.pt",
        detector=FakeDetector(),
        detector_factory=detector_factory,
        mog_factory=mog_factory,
    )
    options = _trained_options(tmp_path)
    monitor.apply_model_pipeline_options(options)
    monitor.apply_model_pipeline_options(options)
    reference = monitor.capture_reference(b"jpeg", "隧道(事故识别)", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(reference, persistence_seconds=0.1, inference_interval=1)
    )
    monitor.start(scene["scene_id"])
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    monitor.process_frame("隧道(事故识别)", frame, now=1.0)
    monitor.process_frame("隧道(事故识别)", frame, now=1.2)

    assert detector_factory_calls == [(options.vehicle_model_path, "cpu", 768)]
    assert mog_factory_calls == [
        {
            "history": 321,
            "var_threshold": 19.0,
            "min_area": 90,
            "min_duration": 0.1,
            "max_duration": 2.0,
            "warmup_frames": 0,
        }
    ]
    assert len(mog.calls) == 2
    assert [tuple(box[:4]) for box in mog.calls[-1]["yolo_boxes"]] == [
        normal_vehicle["bbox"]
    ]
    events = monitor.status()["events"]
    assert {event["source"] for event in events} == {"MOG", "YOLO"}
    mog_event = next(event for event in events if event["source"] == "MOG")
    assert mog_event["anomaly_type"] == "medium_object"
    assert mog_event["bbox"] == (0.3, 0.3, 0.5, 0.5)
    assert mog_event["lane_name"] == "一号车道"


def test_road_object_detector_passes_inference_size_to_yolo(monkeypatch, tmp_path):
    model_loads = []
    device_moves = []
    prediction_calls = []

    class FakeModel:
        def to(self, device):
            device_moves.append(device)
            return self

        def predict(self, frame, **kwargs):
            prediction_calls.append(kwargs)
            return []

    def load_model(path):
        model_loads.append(path)
        return FakeModel()

    monkeypatch.setitem(
        sys.modules,
        "ultralytics",
        SimpleNamespace(YOLO=load_model),
    )
    detector = RoadObjectDetector(
        tmp_path / "vehicle.pt", device="cpu", inference_size=736
    )

    detector.prepare()
    detector.prepare()
    assert detector.detect(np.zeros((20, 20, 3), dtype=np.uint8), 0.42) == []
    assert model_loads == [str(tmp_path / "vehicle.pt")]
    assert device_moves == ["cpu"]
    assert prediction_calls == [
        {
            "classes": [0, 1, 2, 3, 5, 7],
            "conf": 0.42,
            "device": "cpu",
            "imgsz": 736,
            "verbose": False,
        }
    ]


def test_default_trained_mog_is_configured_per_monitor(tmp_path):
    first = RoadAbnormalMonitor(
        tmp_path / "first-road-abnormal",
        tmp_path / "legacy.pt",
        detector=FakeDetector(),
        detector_factory=lambda *_args: FakeDetector(),
    )
    second = RoadAbnormalMonitor(
        tmp_path / "second-road-abnormal",
        tmp_path / "legacy.pt",
        detector=FakeDetector(),
        detector_factory=lambda *_args: FakeDetector(),
    )
    options = _trained_options(tmp_path)

    assert first.apply_model_pipeline_options(options) is True
    assert second.apply_model_pipeline_options(options) is True
    assert first._mog_engine is not second._mog_engine
    assert first._mog_engine.history == 321
    assert first._mog_engine.var_threshold == 19.0
    assert first._mog_engine.min_area == 90
    assert first._mog_engine.min_duration == 0.1
    assert first._mog_engine.max_duration == 2.0
    assert first._mog_engine.warmup_frames == 0


def test_revision_change_rebuilds_pipeline_and_failed_update_keeps_old_strategy(
    tmp_path,
):
    detector_calls = []
    engines = []

    def detector_factory(model_path, device, inference_size):
        detector_calls.append((model_path, device, inference_size))
        if model_path.name == "secret-broken-model.pt":
            raise RuntimeError(f"could not load {model_path}")
        return FakeDetector()

    def mog_factory(**_kwargs):
        engine = FakeMogEngine()
        engines.append(engine)
        return engine

    monitor = RoadAbnormalMonitor(
        tmp_path / "road-abnormal",
        tmp_path / "legacy.pt",
        detector=FakeDetector(),
        detector_factory=detector_factory,
        mog_factory=mog_factory,
    )
    initial = _trained_options(tmp_path)
    revised = replace(initial, revision=initial.revision + 1)

    assert monitor.apply_model_pipeline_options(initial) is True
    assert monitor.apply_model_pipeline_options(initial) is False
    assert monitor.apply_model_pipeline_options(revised) is True
    revised_detector = monitor._detector
    revised_engine = monitor._mog_engine
    assert len(detector_calls) == 2
    assert engines == [engines[0], revised_engine]

    failed = replace(
        revised,
        revision=revised.revision + 1,
        vehicle_model_path=Path("C:/private/secret-broken-model.pt"),
    )
    assert monitor.apply_model_pipeline_options(failed) is False
    assert monitor._detector is revised_detector
    assert monitor._mog_engine is revised_engine
    assert monitor._pipeline_options == revised
    assert monitor._road_abnormal_mode == "mog"
    assert "RuntimeError" in monitor.status()["last_error"]
    assert "secret-broken-model.pt" not in monitor.status()["last_error"]


def test_lazy_detector_prepare_failure_preserves_complete_active_strategy(tmp_path):
    prepared = []
    initial_engine = FakeMogEngine()

    class LazyFailureDetector(FakeDetector):
        def __init__(self, model_path):
            super().__init__()
            self.model_path = model_path

        def prepare(self):
            prepared.append(self)
            raise RuntimeError(f"could not load {self.model_path}")

    def detector_factory(model_path, _device, _inference_size):
        if model_path.name == "secret-lazy-model.pt":
            return LazyFailureDetector(model_path)
        return FakeDetector()

    engines = [initial_engine, FakeMogEngine()]
    monitor = RoadAbnormalMonitor(
        tmp_path / "road-abnormal",
        tmp_path / "legacy.pt",
        detector=FakeDetector(),
        detector_factory=detector_factory,
        mog_factory=lambda **_kwargs: engines.pop(0),
    )
    reference = monitor.capture_reference(b"jpeg", "camera", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(
            reference,
            camera_id="camera",
            persistence_seconds=0.1,
        )
    )
    current = replace(_trained_options(tmp_path), revision=5)
    assert monitor.apply_model_pipeline_options(current) is True
    monitor.start(scene["scene_id"])
    monitor.update_candidates("camera", [_candidate()], (100, 100), now=1.0)
    monitor.update_candidates("camera", [_candidate()], (100, 100), now=1.2)
    old_detector = monitor._detector
    old_engine = monitor._mog_engine
    old_background = monitor._background
    old_candidates = monitor.status()["candidates"]
    old_events = monitor.status()["events"]
    replacement = replace(
        current,
        revision=current.revision + 1,
        vehicle_model_path=tmp_path / "private" / "secret-lazy-model.pt",
    )

    assert monitor.apply_model_pipeline_options(replacement) is False
    status = monitor.status()
    assert len(prepared) == 1
    assert monitor._detector is old_detector
    assert monitor._mog_engine is old_engine
    assert monitor._background is old_background
    assert monitor._pipeline_options == current
    assert monitor._road_abnormal_mode == "mog"
    assert status["candidates"] == old_candidates
    assert status["events"] == old_events
    assert status["last_error"] == "道路异常模型管线更新失败: RuntimeError"
    assert "secret-lazy-model.pt" not in status["last_error"]


def test_pipeline_rejects_active_detector_reuse_before_preparing_it(tmp_path):
    class MutableDetector(FakeDetector):
        def __init__(self):
            super().__init__()
            self.prepare_calls = 0

        def prepare(self):
            self.prepare_calls += 1

    active_detector = MutableDetector()

    def failing_mog_factory(**_kwargs):
        raise RuntimeError(f"failed to build {tmp_path / 'secret-engine.bin'}")

    monitor = RoadAbnormalMonitor(
        tmp_path / "road-abnormal",
        tmp_path / "legacy.pt",
        detector=active_detector,
        detector_factory=lambda *_args: active_detector,
        mog_factory=failing_mog_factory,
    )

    assert monitor.apply_model_pipeline_options(_trained_options(tmp_path)) is False
    assert active_detector.prepare_calls == 0
    assert monitor._detector is active_detector
    assert monitor._pipeline_options is None
    assert monitor.status()["last_error"] == (
        "道路异常模型管线更新失败: RuntimeError"
    )


def test_active_pipeline_reset_failure_preserves_strategy_candidates_and_events(
    tmp_path,
):
    initial_detector = FakeDetector()
    prepared_detectors = []
    old_engine = FakeMogEngine()

    class ResetFailureEngine(FakeMogEngine):
        def reset(self):
            raise RuntimeError(f"failed to reset {tmp_path / 'secret-model.pt'}")

    reset_failure_engine = ResetFailureEngine()
    engines = [old_engine, reset_failure_engine]

    def detector_factory(_model_path, _device, _inference_size):
        detector = FakeDetector()
        prepared_detectors.append(detector)
        return detector

    def mog_factory(**_kwargs):
        return engines.pop(0)

    monitor = RoadAbnormalMonitor(
        tmp_path / "road-abnormal",
        tmp_path / "legacy.pt",
        detector=initial_detector,
        detector_factory=detector_factory,
        mog_factory=mog_factory,
    )
    reference = monitor.capture_reference(b"jpeg", "隧道(事故识别)", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(reference, persistence_seconds=0.1)
    )
    monitor.start(scene["scene_id"])
    old_options = replace(_trained_options(tmp_path), revision=6)
    assert monitor.apply_model_pipeline_options(old_options) is True
    monitor.update_candidates(
        "隧道(事故识别)", [_candidate()], (100, 100), now=1.0
    )
    monitor.update_candidates(
        "隧道(事故识别)", [_candidate()], (100, 100), now=1.2
    )

    old_detector = monitor._detector
    old_background = monitor._background
    old_candidates = monitor.status()["candidates"]
    old_events = monitor.status()["events"]
    replacement = replace(
        old_options,
        revision=7,
        vehicle_model_path=tmp_path / "secret-model.pt",
    )

    assert monitor.apply_model_pipeline_options(replacement) is False
    assert monitor._detector is old_detector
    assert monitor._mog_engine is old_engine
    assert monitor._pipeline_options == old_options
    assert monitor._road_abnormal_mode == "mog"
    assert monitor._background is old_background
    assert monitor.status()["candidates"] == old_candidates
    assert monitor.status()["events"] == old_events
    assert "RuntimeError" in monitor.status()["last_error"]
    assert "secret-model.pt" not in monitor.status()["last_error"]
    assert prepared_detectors[-1] is not old_detector


def test_prepared_pipeline_is_discarded_when_active_scene_changes(tmp_path):
    preparation_started = threading.Event()
    release_preparation = threading.Event()
    initial_detector = FakeDetector()

    def detector_factory(model_path, _device, _inference_size):
        if model_path.name == "slow-model.pt":
            preparation_started.set()
            assert release_preparation.wait(timeout=2.0)
        return FakeDetector()

    monitor = RoadAbnormalMonitor(
        tmp_path / "road-abnormal",
        tmp_path / "legacy.pt",
        detector=initial_detector,
        detector_factory=detector_factory,
        mog_factory=lambda **_kwargs: FakeMogEngine(),
    )
    first_reference = monitor.capture_reference(b"first", "camera-a", 100, 100)
    second_reference = monitor.capture_reference(b"second", "camera-b", 100, 100)
    first_scene = monitor.upsert_scene(
        _scene_payload(
            first_reference,
            scene_id="scene-a",
            name="scene-a",
            camera_id="camera-a",
        )
    )
    second_scene = monitor.upsert_scene(
        _scene_payload(
            second_reference,
            scene_id="scene-b",
            name="scene-b",
            camera_id="camera-b",
        )
    )
    monitor.start(first_scene["scene_id"])
    options = replace(
        _trained_options(tmp_path),
        vehicle_model_path=tmp_path / "slow-model.pt",
    )
    results = []
    worker = threading.Thread(
        target=lambda: results.append(monitor.apply_model_pipeline_options(options))
    )

    worker.start()
    assert preparation_started.wait(timeout=2.0)
    monitor.start(second_scene["scene_id"])
    release_preparation.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert results == [False]
    assert monitor._detector is initial_detector
    assert monitor._mog_engine is None
    assert monitor._pipeline_options is None
    assert monitor._road_abnormal_mode == "mog2"
    assert monitor.status()["active_scene_id"] == "scene-b"


def test_blocked_detector_does_not_block_status_or_pipeline_swap(tmp_path):
    inference_started = threading.Event()
    release_inference = threading.Event()
    status_done = threading.Event()
    apply_done = threading.Event()
    process_result = []
    apply_result = []

    class BlockingDetector(FakeDetector):
        def detect(self, frame, threshold):
            inference_started.set()
            assert release_inference.wait(timeout=2.0)
            return [
                {
                    "bbox": (30, 20, 45, 55),
                    "class_name": "person",
                    "class_name_cn": "行人",
                    "confidence": 0.91,
                    "track_id": 7,
                }
            ]

    monitor = RoadAbnormalMonitor(
        tmp_path / "road-abnormal",
        tmp_path / "legacy.pt",
        detector=BlockingDetector(),
        detector_factory=lambda *_args: FakeDetector(),
        mog_factory=lambda **_kwargs: FakeMogEngine(),
    )
    reference = monitor.capture_reference(b"jpeg", "camera", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(reference, camera_id="camera", persistence_seconds=0.1)
    )
    monitor.start(scene["scene_id"])
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    process_worker = threading.Thread(
        target=lambda: process_result.append(
            monitor.process_frame("camera", frame, now=1.0)
        )
    )
    status_worker = threading.Thread(
        target=lambda: (monitor.status(), status_done.set())
    )
    options = _trained_options(tmp_path)

    def apply_options():
        apply_result.append(monitor.apply_model_pipeline_options(options))
        apply_done.set()

    apply_worker = threading.Thread(target=apply_options)
    process_worker.start()
    assert inference_started.wait(timeout=2.0)
    status_worker.start()
    apply_worker.start()
    try:
        assert status_done.wait(timeout=1.0)
        assert apply_done.wait(timeout=1.0)
    finally:
        release_inference.set()
        process_worker.join(timeout=2.0)
        status_worker.join(timeout=2.0)
        apply_worker.join(timeout=2.0)

    assert not process_worker.is_alive()
    assert not status_worker.is_alive()
    assert not apply_worker.is_alive()
    assert apply_result == [True]
    assert monitor._pipeline_options == options
    assert monitor.status()["candidates"] == []


def test_blocked_trained_mog_does_not_block_status_or_pipeline_swap(tmp_path):
    inference_started = threading.Event()
    release_inference = threading.Event()
    status_done = threading.Event()
    apply_done = threading.Event()

    class BlockingMogEngine(FakeMogEngine):
        def process(self, frame, yolo_boxes, frame_id=0, timestamp=None):
            inference_started.set()
            assert release_inference.wait(timeout=2.0)
            return super().process(frame, yolo_boxes, frame_id, timestamp)

    blocking_engine = BlockingMogEngine()
    replacement_engine = FakeMogEngine()
    engines = [blocking_engine, replacement_engine]
    monitor = RoadAbnormalMonitor(
        tmp_path / "road-abnormal",
        tmp_path / "legacy.pt",
        detector=FakeDetector(),
        detector_factory=lambda *_args: FakeDetector(),
        mog_factory=lambda **_kwargs: engines.pop(0),
    )
    initial = replace(_trained_options(tmp_path), revision=2)
    assert monitor.apply_model_pipeline_options(initial) is True
    reference = monitor.capture_reference(b"jpeg", "camera", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(reference, camera_id="camera", persistence_seconds=0.1)
    )
    monitor.start(scene["scene_id"])
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    process_worker = threading.Thread(
        target=lambda: monitor.process_frame("camera", frame, now=1.0)
    )
    status_worker = threading.Thread(
        target=lambda: (monitor.status(), status_done.set())
    )
    revised = replace(initial, revision=initial.revision + 1)

    def apply_options():
        monitor.apply_model_pipeline_options(revised)
        apply_done.set()

    apply_worker = threading.Thread(target=apply_options)
    process_worker.start()
    assert inference_started.wait(timeout=2.0)
    status_worker.start()
    apply_worker.start()
    try:
        assert status_done.wait(timeout=1.0)
        assert apply_done.wait(timeout=1.0)
    finally:
        release_inference.set()
        process_worker.join(timeout=2.0)
        status_worker.join(timeout=2.0)
        apply_worker.join(timeout=2.0)

    assert not process_worker.is_alive()
    assert not status_worker.is_alive()
    assert not apply_worker.is_alive()
    assert monitor._pipeline_options == revised
    assert monitor._mog_engine is replacement_engine
    assert monitor.status()["candidates"] == []


def test_trained_runtime_accepts_engine_with_set_rois_only(tmp_path):
    class SetRoisOnlyEngine:
        def __init__(self):
            self.rois = []
            self.process_calls = 0

        def reset(self):
            return None

        def set_rois(self, polygons):
            self.rois = list(polygons)

        def process(self, frame, yolo_boxes, frame_id=0, timestamp=None):
            self.process_calls += 1
            return []

    engine = SetRoisOnlyEngine()
    monitor = RoadAbnormalMonitor(
        tmp_path / "road-abnormal",
        tmp_path / "legacy.pt",
        detector=FakeDetector(),
        detector_factory=lambda *_args: FakeDetector(),
        mog_factory=lambda **_kwargs: engine,
    )
    assert monitor.apply_model_pipeline_options(_trained_options(tmp_path)) is True
    reference = monitor.capture_reference(b"jpeg", "camera", 100, 100)
    scene = monitor.upsert_scene(_scene_payload(reference, camera_id="camera"))
    monitor.start(scene["scene_id"])

    monitor.process_frame("camera", np.zeros((100, 100, 3), dtype=np.uint8))

    assert engine.process_calls == 1
    assert len(engine.rois) == 1
    assert monitor.status()["last_error"] == ""


def test_expiring_candidates_invalidates_in_flight_frame(tmp_path):
    inference_started = threading.Event()
    release_inference = threading.Event()
    person = {
        "bbox": (30, 20, 45, 55),
        "source": "YOLO",
        "anomaly_type": "prohibited_road_user",
        "class_name": "person",
        "class_name_cn": "行人",
        "confidence": 0.91,
        "track_id": 7,
    }

    class BlockingDetector(FakeDetector):
        def detect(self, frame, threshold):
            inference_started.set()
            assert release_inference.wait(timeout=2.0)
            return [person]

    monitor = _monitor(tmp_path, BlockingDetector())
    reference = monitor.capture_reference(b"jpeg", "camera", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(reference, camera_id="camera", lost_tolerance_seconds=0.5)
    )
    monitor.start(scene["scene_id"])
    monitor.update_candidates("camera", [person], (100, 100), now=1.0)
    worker = threading.Thread(
        target=lambda: monitor.process_frame(
            "camera", np.zeros((100, 100, 3), dtype=np.uint8), now=2.0
        )
    )

    worker.start()
    assert inference_started.wait(timeout=2.0)
    try:
        assert monitor.status(expire=True, now=2.0)["candidates"] == []
    finally:
        release_inference.set()
        worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert monitor.status()["candidates"] == []


def _reset_failure_monitor(tmp_path):
    class ControllableResetEngine(FakeMogEngine):
        def __init__(self):
            super().__init__()
            self.fail_reset = False

        def reset(self):
            if self.fail_reset:
                raise RuntimeError(f"failed to reset {tmp_path / 'secret-engine.bin'}")
            super().reset()

    engine = ControllableResetEngine()
    monitor = RoadAbnormalMonitor(
        tmp_path / "road-abnormal",
        tmp_path / "legacy.pt",
        detector=FakeDetector(),
        detector_factory=lambda *_args: FakeDetector(),
        mog_factory=lambda **_kwargs: engine,
    )
    assert monitor.apply_model_pipeline_options(_trained_options(tmp_path)) is True
    first_reference = monitor.capture_reference(b"first", "camera-a", 100, 100)
    second_reference = monitor.capture_reference(b"second", "camera-b", 100, 100)
    first_payload = _scene_payload(
        first_reference,
        scene_id="scene-a",
        camera_id="camera-a",
        persistence_seconds=0.1,
    )
    second_payload = _scene_payload(
        second_reference,
        scene_id="scene-b",
        camera_id="camera-b",
    )
    first_scene = monitor.upsert_scene(first_payload)
    second_scene = monitor.upsert_scene(second_payload)
    monitor.start(first_scene["scene_id"])
    monitor.update_candidates("camera-a", [_candidate()], (100, 100), now=1.0)
    monitor.update_candidates("camera-a", [_candidate()], (100, 100), now=1.2)
    return monitor, engine, first_payload, second_scene


def test_start_reset_failure_preserves_active_runtime_state(tmp_path):
    monitor, engine, _first_payload, second_scene = _reset_failure_monitor(tmp_path)
    old_status = monitor.status()
    engine.fail_reset = True

    try:
        monitor.start(second_scene["scene_id"])
    except RuntimeError:
        pass
    else:
        raise AssertionError("reset failure should abort scene start")

    status = monitor.status()
    assert status["active_scene_id"] == old_status["active_scene_id"]
    assert status["running"] is old_status["running"]
    assert status["candidates"] == old_status["candidates"]
    assert status["events"] == old_status["events"]


def test_active_scene_upsert_reset_failure_preserves_scene_and_runtime(tmp_path):
    monitor, engine, first_payload, _second_scene = _reset_failure_monitor(tmp_path)
    old_scene = monitor.get_scene("scene-a")
    old_status = monitor.status()
    engine.fail_reset = True

    try:
        monitor.upsert_scene({**first_payload, "name": "should-not-commit"})
    except RuntimeError:
        pass
    else:
        raise AssertionError("reset failure should abort active scene update")

    assert monitor.get_scene("scene-a") == old_scene
    status = monitor.status()
    assert status["active_scene_id"] == old_status["active_scene_id"]
    assert status["candidates"] == old_status["candidates"]
    assert status["events"] == old_status["events"]


def test_start_persists_events_closed_by_runtime_reset(tmp_path):
    monitor, _engine, _first_payload, second_scene = _reset_failure_monitor(tmp_path)

    monitor.start(second_scene["scene_id"])

    payload = json.loads(monitor.events_file.read_text(encoding="utf-8"))
    assert payload["events"][0]["ended_at"] == 1.2


def test_active_scene_upsert_persists_events_closed_by_runtime_reset(tmp_path):
    monitor, _engine, first_payload, _second_scene = _reset_failure_monitor(tmp_path)

    monitor.upsert_scene({**first_payload, "name": "updated-scene"})

    payload = json.loads(monitor.events_file.read_text(encoding="utf-8"))
    assert payload["events"][0]["ended_at"] == 1.2


def test_queued_frames_use_the_latest_committed_frame_id(tmp_path):
    first_inference_started = threading.Event()
    release_first_inference = threading.Event()
    second_waiting_for_inference = threading.Event()

    class ObservableLock:
        def __init__(self):
            self._lock = threading.Lock()
            self._attempt_lock = threading.Lock()
            self._attempts = 0

        def __enter__(self):
            with self._attempt_lock:
                self._attempts += 1
                if self._attempts == 2:
                    second_waiting_for_inference.set()
            self._lock.acquire()
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            self._lock.release()

    class RecordingMogEngine(FakeMogEngine):
        def __init__(self):
            super().__init__()
            self.frame_ids = []

        def process(self, frame, yolo_boxes, frame_id=0, timestamp=None):
            self.frame_ids.append(frame_id)
            if len(self.frame_ids) == 1:
                first_inference_started.set()
                assert release_first_inference.wait(timeout=2.0)
            return []

    engine = RecordingMogEngine()
    monitor = RoadAbnormalMonitor(
        tmp_path / "road-abnormal",
        tmp_path / "legacy.pt",
        detector=FakeDetector(),
        detector_factory=lambda *_args: FakeDetector(),
        mog_factory=lambda **_kwargs: engine,
    )
    assert monitor.apply_model_pipeline_options(_trained_options(tmp_path)) is True
    reference = monitor.capture_reference(b"jpeg", "camera", 100, 100)
    scene = monitor.upsert_scene(_scene_payload(reference, camera_id="camera"))
    monitor.start(scene["scene_id"])
    monitor._inference_lock = ObservableLock()
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    first = threading.Thread(target=lambda: monitor.process_frame("camera", frame))
    second = threading.Thread(target=lambda: monitor.process_frame("camera", frame))

    first.start()
    assert first_inference_started.wait(timeout=2.0)
    second.start()
    assert second_waiting_for_inference.wait(timeout=2.0)
    release_first_inference.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert engine.frame_ids == [0, 1]


def test_active_scene_reset_waits_for_inference_without_holding_state_lock(tmp_path):
    inference_started = threading.Event()
    release_inference = threading.Event()
    reset_during_inference = threading.Event()
    upsert_started = threading.Event()
    status_done = threading.Event()
    upsert_errors = []

    class ResetAwareMogEngine(FakeMogEngine):
        def __init__(self):
            super().__init__()
            self.processing = False

        def reset(self):
            if self.processing:
                reset_during_inference.set()
                assert release_inference.wait(timeout=2.0)
            super().reset()

        def process(self, frame, yolo_boxes, frame_id=0, timestamp=None):
            self.processing = True
            inference_started.set()
            try:
                assert release_inference.wait(timeout=2.0)
                return []
            finally:
                self.processing = False

    engine = ResetAwareMogEngine()
    monitor = RoadAbnormalMonitor(
        tmp_path / "road-abnormal",
        tmp_path / "legacy.pt",
        detector=FakeDetector(),
        detector_factory=lambda *_args: FakeDetector(),
        mog_factory=lambda **_kwargs: engine,
    )
    options = replace(_trained_options(tmp_path), revision=2)
    assert monitor.apply_model_pipeline_options(options) is True
    reference = monitor.capture_reference(b"jpeg", "camera", 100, 100)
    payload = _scene_payload(reference, camera_id="camera")
    scene = monitor.upsert_scene(payload)
    monitor.start(scene["scene_id"])
    process_worker = threading.Thread(
        target=lambda: monitor.process_frame(
            "camera", np.zeros((100, 100, 3), dtype=np.uint8), now=1.0
        )
    )

    def update_scene():
        upsert_started.set()
        try:
            monitor.upsert_scene({**payload, "name": "updated-scene"})
        except Exception as exc:
            upsert_errors.append(exc)

    upsert_worker = threading.Thread(target=update_scene)
    status_worker = threading.Thread(
        target=lambda: (monitor.status(), status_done.set())
    )
    process_worker.start()
    assert inference_started.wait(timeout=2.0)
    upsert_worker.start()
    assert upsert_started.wait(timeout=2.0)
    status_worker.start()
    try:
        assert status_done.wait(timeout=1.0)
        assert not reset_during_inference.is_set()
    finally:
        release_inference.set()
        process_worker.join(timeout=2.0)
        upsert_worker.join(timeout=2.0)
        status_worker.join(timeout=2.0)

    assert not process_worker.is_alive()
    assert not upsert_worker.is_alive()
    assert not status_worker.is_alive()
    assert upsert_errors == []
    assert monitor.get_scene(scene["scene_id"])["name"] == "updated-scene"


def test_process_frame_error_status_redacts_exception_details(tmp_path):
    secret_path = tmp_path / "private" / "secret-model.pt"

    class FailingDetector(FakeDetector):
        def detect(self, frame, threshold):
            raise RuntimeError(f"could not load {secret_path}")

    monitor = _monitor(tmp_path, FailingDetector())
    reference = monitor.capture_reference(b"jpeg", "camera", 100, 100)
    scene = monitor.upsert_scene(_scene_payload(reference, camera_id="camera"))
    monitor.start(scene["scene_id"])

    monitor.process_frame("camera", np.zeros((100, 100, 3), dtype=np.uint8))

    error = monitor.status()["last_error"]
    assert error == "道路异常检测失败: RuntimeError"
    assert str(secret_path) not in error


def test_event_snapshot_and_history_io_do_not_hold_state_lock(monkeypatch, tmp_path):
    snapshot_started = threading.Event()
    release_snapshot = threading.Event()
    history_started = threading.Event()
    release_history = threading.Event()
    snapshot_status_done = threading.Event()
    history_status_done = threading.Event()
    update_result = []

    monitor = _monitor(tmp_path)
    reference = monitor.capture_reference(b"jpeg", "camera", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(
            reference,
            camera_id="camera",
            persistence_seconds=0.1,
        )
    )
    monitor.start(scene["scene_id"])
    monitor.update_candidates("camera", [_candidate()], (100, 100), now=1.0)
    original_write_json = monitor._write_json

    def blocking_imwrite(_path, _frame):
        snapshot_started.set()
        assert release_snapshot.wait(timeout=2.0)
        return True

    def blocking_write_json(path, payload):
        if path == monitor.events_file:
            history_started.set()
            assert release_history.wait(timeout=2.0)
        return original_write_json(path, payload)

    monkeypatch.setattr("backend.road_abnormal.cv2.imwrite", blocking_imwrite)
    monkeypatch.setattr(monitor, "_write_json", blocking_write_json)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    update_worker = threading.Thread(
        target=lambda: update_result.extend(
            monitor.update_candidates(
                "camera",
                [_candidate()],
                (100, 100),
                frame=frame,
                now=1.2,
            )
        )
    )
    snapshot_status_worker = threading.Thread(
        target=lambda: (monitor.status(), snapshot_status_done.set())
    )
    history_status_worker = threading.Thread(
        target=lambda: (monitor.status(), history_status_done.set())
    )

    update_worker.start()
    assert snapshot_started.wait(timeout=2.0)
    snapshot_status_worker.start()
    try:
        assert snapshot_status_done.wait(timeout=1.0)
        release_snapshot.set()
        assert history_started.wait(timeout=2.0)
        history_status_worker.start()
        assert history_status_done.wait(timeout=1.0)
    finally:
        release_snapshot.set()
        release_history.set()
        update_worker.join(timeout=2.0)
        snapshot_status_worker.join(timeout=2.0)
        if history_status_worker.ident is not None:
            history_status_worker.join(timeout=2.0)

    assert not update_worker.is_alive()
    assert len(update_result) == 1
    event = update_result[0]
    assert event["snapshot"] == f"{event['event_id']}.jpg"
    assert event["snapshot_url"] == (
        f"/api/road-abnormal/snapshots/{event['event_id']}.jpg"
    )
    assert monitor.status()["events"][0] == event


def test_concurrent_event_writes_persist_the_latest_complete_history(
    monkeypatch, tmp_path
):
    first_write_started = threading.Event()
    release_first_write = threading.Event()
    second_waiting_for_io = threading.Event()

    class ObservableLock:
        def __init__(self):
            self._lock = threading.Lock()
            self._attempt_lock = threading.Lock()
            self._attempts = 0

        def __enter__(self):
            with self._attempt_lock:
                self._attempts += 1
                if self._attempts == 2:
                    second_waiting_for_io.set()
            self._lock.acquire()
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            self._lock.release()

    monitor = _monitor(tmp_path)
    reference = monitor.capture_reference(b"jpeg", "camera", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(reference, camera_id="camera", persistence_seconds=0.1)
    )
    monitor.start(scene["scene_id"])
    first_candidate = _candidate(source="YOLO", class_name="person", track_id=1)
    second_candidate = _candidate(source="YOLO", class_name="person", track_id=2)
    monitor.update_candidates("camera", [first_candidate], (100, 100), now=1.0)
    original_write_json = monitor._write_json
    event_writes = 0

    def blocking_first_write(path, payload):
        nonlocal event_writes
        if path == monitor.events_file:
            event_writes += 1
            if event_writes == 1:
                first_write_started.set()
                assert release_first_write.wait(timeout=2.0)
        return original_write_json(path, payload)

    monitor._event_io_lock = ObservableLock()
    monkeypatch.setattr(monitor, "_write_json", blocking_first_write)
    first_worker = threading.Thread(
        target=lambda: monitor.update_candidates(
            "camera", [first_candidate], (100, 100), now=1.2
        )
    )
    second_worker = threading.Thread(
        target=lambda: (
            monitor.update_candidates(
                "camera", [second_candidate], (100, 100), now=1.0
            ),
            monitor.update_candidates(
                "camera", [second_candidate], (100, 100), now=1.2
            ),
        )
    )

    first_worker.start()
    assert first_write_started.wait(timeout=2.0)
    second_worker.start()
    assert second_waiting_for_io.wait(timeout=2.0)
    release_first_write.set()
    first_worker.join(timeout=2.0)
    second_worker.join(timeout=2.0)

    assert not first_worker.is_alive()
    assert not second_worker.is_alive()
    payload = json.loads(monitor.events_file.read_text(encoding="utf-8"))
    assert len(payload["events"]) == 2
    assert {event["event_id"] for event in payload["events"]} == {
        event["event_id"] for event in monitor.status()["events"]
    }


def test_headless_trained_mog_supports_multiple_rois_overlap_and_reset():
    from backend.trained_mog import MOGAnomalyEngine, TrainedMOGAlert

    engine = MOGAnomalyEngine(
        history=40,
        var_threshold=12.0,
        min_area=40,
        min_duration=0.1,
        max_duration=0.2,
        warmup_frames=2,
    )
    engine.set_rois(
        [
            [(10, 10), (45, 10), (45, 75), (10, 75)],
            [(65, 10), (110, 10), (110, 75), (65, 75)],
        ]
    )
    background = np.zeros((90, 120, 3), dtype=np.uint8)
    obstacle = background.copy()
    obstacle[30:55, 75:98] = 255

    assert engine.process(background, [], frame_id=0, timestamp=0.0) == []
    assert engine.process(background, [], frame_id=1, timestamp=0.1) == []
    alerts = []
    for frame_id in range(2, 7):
        alerts = engine.process(
            obstacle, [], frame_id=frame_id, timestamp=frame_id * 0.1
        )
        if alerts:
            break

    assert alerts
    assert isinstance(alerts[0], TrainedMOGAlert)
    x, y, width, height = alerts[0].position
    assert x >= 65
    assert y >= 10
    assert width > 0
    assert height > 0

    engine.reset()
    assert engine.frame_count == 0
    assert engine.is_warmed_up is False
    assert engine.process(background, [], frame_id=10, timestamp=1.0) == []
    assert engine.process(background, [], frame_id=11, timestamp=1.1) == []
    for frame_id in range(12, 18):
        assert engine.process(
            obstacle,
            [(70, 25, 105, 60, 0.99)],
            frame_id=frame_id,
            timestamp=frame_id * 0.1,
        ) == []
