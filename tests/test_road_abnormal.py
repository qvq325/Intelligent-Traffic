from types import SimpleNamespace

import numpy as np

from backend.road_abnormal import RoadAbnormalMonitor


class FakeDetector:
    def __init__(self, objects=None):
        self.objects = objects or []

    def detect(self, frame, threshold):
        return list(self.objects)


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
