from types import SimpleNamespace

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
