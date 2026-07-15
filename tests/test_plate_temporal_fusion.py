from lpr_recognizer import PlateRecognition
from plate_temporal_fusion import (
    PlateTemporalFusion,
    PlateTrackObservation,
    TrackedVehicle,
)


def vehicle(track_id: int, bbox=(10, 10, 110, 70)) -> TrackedVehicle:
    return TrackedVehicle(bbox=bbox, track_id=track_id, confidence=0.9)


def observation(
    text: str,
    detector_confidence: float,
    bbox=(30, 40, 80, 55),
) -> PlateTrackObservation:
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
    results = fusion.resolve(
        "camera-a",
        [target],
        {7: observation("京A12345", 0.8)},
    )
    assert [item.plate_text for item in results] == ["京A12345"]


def test_missing_observation_is_held_then_expires_and_bbox_is_projected():
    fusion = PlateTemporalFusion(window_size=3, hold_frames=2, max_tracks=16)
    fusion.resolve(
        "camera-a",
        [vehicle(3)],
        {3: observation("京A00003", 0.9)},
    )
    moved = vehicle(3, bbox=(110, 110, 210, 170))
    held = fusion.resolve("camera-a", [moved], {})
    assert held[0].plate_text == "京A00003"
    assert 110 <= held[0].bbox[0] < held[0].bbox[2] <= 210
    fusion.resolve("camera-a", [moved], {})
    assert fusion.resolve("camera-a", [moved], {}) == []


def test_camera_reset_and_lru_bound_are_isolated():
    fusion = PlateTemporalFusion(window_size=2, hold_frames=2, max_tracks=2)
    for track_id in (1, 2, 3):
        fusion.resolve(
            "camera-a",
            [vehicle(track_id)],
            {track_id: observation(f"京A0000{track_id}", 0.9)},
        )
    assert fusion.cache_size == 2
    fusion.resolve(
        "camera-b",
        [vehicle(8)],
        {8: observation("京B00008", 0.9)},
    )
    fusion.reset("camera-a")
    assert [
        item.plate_text
        for item in fusion.resolve("camera-b", [vehicle(8)], {})
    ] == ["京B00008"]
