from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.model_pipelines import ModelPipelineOptions
from backend.no_parking import NoParkingMonitor


def _scene_payload(reference, *, dwell_seconds=2.0, lost_seconds=1.5):
    return {
        "scene_id": "test-scene",
        "name": "测试禁停场景",
        "camera_id": "道路1",
        "reference_image": reference["filename"],
        "reference_width": reference["width"],
        "reference_height": reference["height"],
        "zones": [
            {
                "zone_id": "main-zone",
                "name": "主禁停区",
                "points": [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)],
                "dwell_seconds": dwell_seconds,
                "lost_tolerance_seconds": lost_seconds,
                "enabled": True,
                "vehicle_classes": ["car"],
            }
        ],
    }


def _vehicle(track_id=7, bbox=(30, 20, 70, 60)):
    return SimpleNamespace(
        track_id=track_id,
        vehicle_bbox=bbox,
        vehicle_class="car",
        plate_text="京A12345",
    )


def _trained_options(tmp_path, *, move_threshold=0.03):
    return ModelPipelineOptions(
        scene_key="no_parking",
        preset="trained",
        enabled=True,
        device_preference="cpu",
        yolo_threshold=0.5,
        lpr_threshold=0.7,
        frame_interval=1,
        inference_size=640,
        parking_move_threshold=move_threshold,
        mog_history=500,
        mog_variance_threshold=25.0,
        mog_min_area=150,
        mog_min_duration=2.0,
        mog_max_duration=5.0,
        mog_warmup_frames=50,
        revision=1,
        vehicle_model_path=tmp_path / "vehicle.pt",
        plate_model_path=tmp_path / "plate.pt",
        plate_mode="box",
        no_parking_mode="stationary",
        road_abnormal_mode="mog",
    )


def test_no_parking_monitor_triggers_once_and_closes_after_track_loss(tmp_path):
    monitor = NoParkingMonitor(tmp_path / "no-parking")
    reference = monitor.capture_reference(b"jpeg", "道路1", 100, 100)
    scene = monitor.upsert_scene(_scene_payload(reference))
    monitor.start(scene["scene_id"])

    assert monitor.update_detections("道路1", [_vehicle()], (100, 100), now=10.0) == []
    assert monitor.update_detections("道路1", [_vehicle()], (100, 100), now=11.0) == []
    events = monitor.update_detections("道路1", [_vehicle()], (100, 100), now=12.1)
    duplicate = monitor.update_detections("道路1", [_vehicle()], (100, 100), now=12.5)

    assert len(events) == 1
    assert duplicate == []
    assert monitor.status()["metrics"]["active_alarms"] == 1

    monitor.update_detections("道路1", [], (100, 100), now=14.1)
    status = monitor.status()

    assert status["tracks"] == []
    assert status["events"][0]["ended_at"] == 12.5
    assert status["events"][0]["duration_seconds"] == 2.5


def test_no_parking_monitor_ignores_outside_tracks_and_resets_long_gaps(tmp_path):
    monitor = NoParkingMonitor(tmp_path / "no-parking")
    reference = monitor.capture_reference(b"jpeg", "道路1", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(reference, dwell_seconds=1.0, lost_seconds=0.5)
    )
    monitor.start(scene["scene_id"])

    outside = _vehicle(bbox=(0, 0, 10, 10))
    assert monitor.update_detections("道路1", [outside], (100, 100), now=1.0) == []
    assert monitor.status()["tracks"] == []

    monitor.update_detections("道路1", [_vehicle()], (100, 100), now=2.0)
    assert monitor.update_detections("道路1", [_vehicle()], (100, 100), now=4.0) == []
    assert monitor.status()["tracks"][0]["dwell_seconds"] == 0.0


def test_no_parking_scene_and_events_persist(tmp_path):
    root = tmp_path / "no-parking"
    monitor = NoParkingMonitor(root)
    reference = monitor.capture_reference(b"jpeg", "道路1", 100, 100)
    scene = monitor.upsert_scene(_scene_payload(reference, dwell_seconds=1.0))
    monitor.start(scene["scene_id"])
    monitor.update_detections("道路1", [_vehicle()], (100, 100), now=1.0)
    monitor.update_detections("道路1", [_vehicle()], (100, 100), now=2.1)

    reloaded = NoParkingMonitor(root)

    assert reloaded.catalog()["scenes"][0]["scene_id"] == "test-scene"
    assert reloaded.status()["metrics"]["total_events"] == 1


def test_status_expires_tracks_when_the_video_stops_producing_results(tmp_path):
    monitor = NoParkingMonitor(tmp_path / "no-parking")
    reference = monitor.capture_reference(b"jpeg", "道路1", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(reference, dwell_seconds=1.0, lost_seconds=0.5)
    )
    monitor.start(scene["scene_id"])
    monitor.update_detections("道路1", [_vehicle()], (100, 100), now=10.0)
    monitor.update_detections("道路1", [_vehicle()], (100, 100), now=11.0)

    status = monitor.status(expire=True, now=12.0)

    assert status["tracks"] == []


def test_trained_no_parking_rejects_gradually_moving_track(tmp_path):
    monitor = NoParkingMonitor(tmp_path / "no-parking")
    monitor.apply_model_pipeline_options(_trained_options(tmp_path))
    reference = monitor.capture_reference(b"jpeg", "道路1", 100, 100)
    scene = monitor.upsert_scene(_scene_payload(reference, dwell_seconds=2.0))
    monitor.start(scene["scene_id"])

    emitted = []
    for second, offset in enumerate((0, 2, 4, 6, 8, 10)):
        emitted.extend(
            monitor.update_detections(
                "道路1",
                [_vehicle(bbox=(20 + offset, 20, 40 + offset, 60))],
                (100, 100),
                now=float(second),
            )
        )

    assert emitted == []
    assert monitor.status()["metrics"]["active_alarms"] == 0


def test_trained_no_parking_rejects_slow_drift_beyond_bounded_window(tmp_path):
    monitor = NoParkingMonitor(tmp_path / "no-parking")
    monitor.apply_model_pipeline_options(
        _trained_options(tmp_path, move_threshold=0.03)
    )
    reference = monitor.capture_reference(b"jpeg", "道路1", 10_000, 10_000)
    scene = monitor.upsert_scene(_scene_payload(reference, dwell_seconds=60.0))
    monitor.start(scene["scene_id"])

    emitted = []
    for second in range(61):
        offset = second * 9
        emitted.extend(
            monitor.update_detections(
                "道路1",
                [_vehicle(bbox=(2_000 + offset, 2_000, 4_000 + offset, 6_000))],
                (10_000, 10_000),
                now=float(second),
            )
        )

    assert emitted == []
    assert monitor.status()["metrics"]["active_alarms"] == 0


def test_trained_no_parking_accepts_stationary_track_after_dwell(tmp_path):
    monitor = NoParkingMonitor(tmp_path / "no-parking")
    monitor.apply_model_pipeline_options(_trained_options(tmp_path))
    reference = monitor.capture_reference(b"jpeg", "道路1", 100, 100)
    scene = monitor.upsert_scene(_scene_payload(reference, dwell_seconds=2.0))
    monitor.start(scene["scene_id"])

    assert monitor.update_detections("道路1", [_vehicle()], (100, 100), now=1.0) == []
    assert monitor.update_detections("道路1", [_vehicle()], (100, 100), now=2.0) == []
    events = monitor.update_detections(
        "道路1", [_vehicle()], (100, 100), now=3.1
    )

    assert len(events) == 1
    assert events[0]["duration_seconds"] == 2.1


def test_trained_no_parking_treats_subthreshold_jitter_as_stationary(tmp_path):
    monitor = NoParkingMonitor(tmp_path / "no-parking")
    monitor.apply_model_pipeline_options(
        _trained_options(tmp_path, move_threshold=0.03)
    )
    reference = monitor.capture_reference(b"jpeg", "道路1", 100, 100)
    scene = monitor.upsert_scene(_scene_payload(reference, dwell_seconds=2.0))
    monitor.start(scene["scene_id"])

    assert monitor.update_detections(
        "道路1", [_vehicle(bbox=(20, 20, 40, 60))], (100, 100), now=1.0
    ) == []
    assert monitor.update_detections(
        "道路1", [_vehicle(bbox=(22, 20, 42, 60))], (100, 100), now=2.0
    ) == []
    events = monitor.update_detections(
        "道路1", [_vehicle(bbox=(20, 20, 40, 60))], (100, 100), now=3.0
    )

    assert len(events) == 1
    assert events[0]["duration_seconds"] == 2.0


def test_trained_no_parking_anchor_history_is_bounded_and_normalized(tmp_path):
    monitor = NoParkingMonitor(tmp_path / "no-parking")
    monitor.apply_model_pipeline_options(
        _trained_options(tmp_path, move_threshold=1.0)
    )
    reference = monitor.capture_reference(b"jpeg", "道路1", 200, 100)
    scene = monitor.upsert_scene(_scene_payload(reference, dwell_seconds=60.0))
    monitor.start(scene["scene_id"])

    for frame in range(100):
        monitor.update_detections(
            "道路1",
            [_vehicle(bbox=(60, 20, 140, 60))],
            (200, 100),
            now=float(frame),
        )

    state = next(iter(monitor._tracks.values()))
    assert state.anchor_history.maxlen == 30
    assert len(state.anchor_history) == 30
    assert set(state.anchor_history) == {(0.5, 0.6)}


def test_trained_no_parking_restarts_dwell_after_movement(tmp_path):
    monitor = NoParkingMonitor(tmp_path / "no-parking")
    monitor.apply_model_pipeline_options(
        _trained_options(tmp_path, move_threshold=0.03)
    )
    reference = monitor.capture_reference(b"jpeg", "道路1", 100, 100)
    scene = monitor.upsert_scene(_scene_payload(reference, dwell_seconds=2.0))
    monitor.start(scene["scene_id"])

    monitor.update_detections(
        "道路1", [_vehicle(bbox=(20, 20, 40, 60))], (100, 100), now=0.0
    )
    assert monitor.update_detections(
        "道路1", [_vehicle(bbox=(25, 20, 45, 60))], (100, 100), now=1.0
    ) == []
    assert monitor.update_detections(
        "道路1", [_vehicle(bbox=(25, 20, 45, 60))], (100, 100), now=2.0
    ) == []
    assert monitor.update_detections(
        "道路1", [_vehicle(bbox=(25, 20, 45, 60))], (100, 100), now=2.9
    ) == []

    events = monitor.update_detections(
        "道路1", [_vehicle(bbox=(25, 20, 45, 60))], (100, 100), now=3.1
    )

    assert len(events) == 1
    assert events[0]["entered_at"] == 1.0
    assert events[0]["triggered_at"] == 3.1
    assert events[0]["duration_seconds"] == 2.1


def test_trained_no_parking_gap_starts_a_new_stationary_period(tmp_path):
    monitor = NoParkingMonitor(tmp_path / "no-parking")
    monitor.apply_model_pipeline_options(_trained_options(tmp_path))
    reference = monitor.capture_reference(b"jpeg", "道路1", 100, 100)
    scene = monitor.upsert_scene(
        _scene_payload(reference, dwell_seconds=1.0, lost_seconds=1.5)
    )
    monitor.start(scene["scene_id"])

    monitor.update_detections(
        "道路1", [_vehicle(bbox=(20, 20, 40, 60))], (100, 100), now=0.0
    )
    assert monitor.update_detections(
        "道路1", [_vehicle(bbox=(30, 20, 50, 60))], (100, 100), now=2.0
    ) == []
    events = monitor.update_detections(
        "道路1", [_vehicle(bbox=(30, 20, 50, 60))], (100, 100), now=3.1
    )

    assert len(events) == 1
    assert events[0]["entered_at"] == 2.0
    assert events[0]["duration_seconds"] == 1.1


def test_no_parking_pipeline_revision_closes_events_and_clears_tracks(tmp_path):
    monitor = NoParkingMonitor(tmp_path / "no-parking")
    options = _trained_options(tmp_path)
    assert monitor.apply_model_pipeline_options(options) is True
    reference = monitor.capture_reference(b"jpeg", "道路1", 100, 100)
    scene = monitor.upsert_scene(_scene_payload(reference, dwell_seconds=1.0))
    monitor.start(scene["scene_id"])
    monitor.update_detections("道路1", [_vehicle()], (100, 100), now=1.0)
    monitor.update_detections("道路1", [_vehicle()], (100, 100), now=2.1)

    assert monitor.apply_model_pipeline_options(options) is False
    assert monitor.status()["metrics"]["active_alarms"] == 1
    assert monitor.apply_model_pipeline_options(
        replace(options, revision=options.revision + 1)
    ) is True

    status = monitor.status()
    assert status["tracks"] == []
    assert status["events"][0]["ended_at"] == 2.1
    assert status["events"][0]["duration_seconds"] == 1.1

    monitor.update_detections(
        "道路1", [_vehicle(bbox=(20, 20, 40, 60))], (100, 100), now=10.0
    )
    events = monitor.update_detections(
        "道路1", [_vehicle(bbox=(20, 20, 40, 60))], (100, 100), now=11.1
    )

    assert len(events) == 1
    assert events[0]["entered_at"] == 10.0


def test_no_parking_pipeline_options_reject_another_scene(tmp_path):
    monitor = NoParkingMonitor(tmp_path / "no-parking")

    with pytest.raises(ValueError, match="scene must be no_parking"):
        monitor.apply_model_pipeline_options(
            replace(_trained_options(tmp_path), scene_key="road_abnormal")
        )
