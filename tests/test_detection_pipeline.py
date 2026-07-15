import torch
import pytest
import numpy as np
from types import SimpleNamespace

from detection_processor import DetectionProcessor
from lpr_recognizer import LPRRecognizer, PLATE_CHARACTERS, PlateRecognition
from traffic_map import TrafficMapModel
from vehicle_detector import VehicleDetection
from whitelist_manager import WhitelistManager


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
                bbox=(10, 10, 110, 80),
                confidence=0.9,
                class_id=2,
                class_name="car",
                class_name_cn="小汽车",
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
        return [
            PlateRecognition(
                "京A12345",
                0.9,
                (30, 50, 80, 65),
                "蓝色",
            )
        ]

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
    processor.process(
        np.zeros((100, 120, 3), dtype=np.uint8),
        camera_id="camera-a",
    )
    assert recognizer.calls == 1


def test_tracking_reset_also_resets_trained_plate_fusion():
    vehicle_detector = VehicleDetectorDouble()
    recognizer = BoxRecognizerDouble()
    processor = ready_processor(vehicle_detector, recognizer, "box")
    processor.reset_tracking()
    assert vehicle_detector.reset_calls == 1
    assert recognizer.reset_calls == 1


def test_ctc_decode_collapses_blanks_and_repeated_characters():
    character_indices = [0, 1, 1, 0, 50, 50, 0]
    logits = torch.full((len(character_indices), len(PLATE_CHARACTERS)), -20.0)
    for position, character_index in enumerate(character_indices):
        logits[position, character_index] = 20.0

    text, confidence = LPRRecognizer._decode(logits)

    assert text == PLATE_CHARACTERS[1] + PLATE_CHARACTERS[50]
    assert confidence > 0.99


def test_plate_is_associated_by_center_point():
    inside = PlateRecognition("京A12345", 0.9, (40, 50, 80, 70))
    outside = PlateRecognition("京A54321", 0.9, (110, 50, 130, 70))
    vehicle_bbox = (10, 10, 100, 100)

    assert DetectionProcessor._plate_belongs_to_vehicle(inside, vehicle_bbox)
    assert not DetectionProcessor._plate_belongs_to_vehicle(outside, vehicle_bbox)


def test_camera_placement_persists(tmp_path):
    path = tmp_path / "traffic_map.json"
    model = TrafficMapModel(path, ["道路1"])
    model.set_camera(
        "道路1", 0.42, 0.37, heading=125, view_range=0.2,
        segment_id="north_eastbound",
    )
    model.save()

    reloaded = TrafficMapModel(path, ["道路1"])
    camera = reloaded.cameras["道路1"]

    assert (camera.x, camera.y) == (0.42, 0.37)
    assert camera.heading == 125
    assert camera.view_range == 0.2
    assert camera.segment_id == "north_eastbound"


def test_tracks_update_segment_heat_and_expire(tmp_path):
    model = TrafficMapModel(tmp_path / "traffic_map.json", ["道路1"])
    detection = SimpleNamespace(
        track_id=7,
        vehicle_bbox=(100, 100, 180, 220),
        vehicle_class="car",
        plate_text="京A12345",
    )

    model.update_detections("道路1", [detection], (640, 480), now=10.0)
    states = model.segment_states(now=10.0)
    segment_id = model.cameras["道路1"].segment_id

    assert len(model.tracks) == 1
    assert states[segment_id].vehicle_count == 1
    assert states[segment_id].flow_per_minute == 1
    assert states[segment_id].heat > 0

    model.prune(now=15.0)
    assert model.tracks == {}


def test_custom_road_crud_and_camera_reassignment(tmp_path):
    model = TrafficMapModel(tmp_path / "traffic_map.json", ["道路1"])
    custom = model.upsert_segment(
        segment_id="custom road 1",
        name="测试曲线",
        points=[(0.1, 0.1), (0.2, 0.15), (0.3, 0.1)],
        capacity=9,
        level="bridge",
        direction="东行",
        road_width=64 / 740,
    )
    model.set_camera("道路1", 0.2, 0.15, segment_id=custom.segment_id)
    model.save()

    assert custom.segment_id == "custom_road_1"
    assert custom.capacity == 9
    assert custom.level == "bridge"
    assert custom.direction == "东行"
    assert custom.road_width == pytest.approx(64 / 740)

    reloaded = TrafficMapModel(model.config_path, ["道路1"])
    assert reloaded.segments[custom.segment_id].name == "测试曲线"
    assert reloaded.segments[custom.segment_id].direction == "东行"
    assert reloaded.segments[custom.segment_id].road_width == pytest.approx(64 / 740)
    assert reloaded.cameras["道路1"].segment_id == custom.segment_id

    assert reloaded.delete_segment(custom.segment_id)
    assert custom.segment_id not in reloaded.segments
    assert reloaded.cameras["道路1"].segment_id in reloaded.segments


def test_default_topology_has_paired_directional_lanes(tmp_path):
    model = TrafficMapModel(tmp_path / "traffic_map.json")

    assert len(model.segments) == 18
    assert model.segments["main_clockwise"].direction == "顺时针"
    assert model.segments["main_counterclockwise"].direction == "逆时针"
    assert (
        model.segments["main_clockwise"].points
        != model.segments["main_counterclockwise"].points
    )


def test_polygon_road_persists_and_keeps_mapped_tracks_inside(tmp_path):
    model = TrafficMapModel(tmp_path / "traffic_map.json", ["道路1"])
    polygon = model.upsert_segment(
        segment_id="drawn-area",
        name="手绘道路区域",
        points=[(0.2, 0.2), (0.6, 0.2), (0.6, 0.6), (0.2, 0.6)],
        capacity=6,
        geometry_type="polygon",
    )
    model.set_camera(
        "道路1",
        0.4,
        0.4,
        heading=90,
        view_range=0.5,
        segment_id=polygon.segment_id,
    )

    segment_id, point = model.map_detection(
        "道路1",
        (100, 20, 180, 100),
        (640, 480),
    )
    model.save()
    reloaded = TrafficMapModel(model.config_path, ["道路1"])

    assert segment_id == polygon.segment_id
    assert model._point_in_polygon(point, polygon.points)
    assert reloaded.segments[polygon.segment_id].geometry_type == "polygon"
    assert reloaded.segments[polygon.segment_id].points == polygon.points


def test_legacy_road_without_geometry_type_remains_a_polyline(tmp_path):
    path = tmp_path / "traffic_map.json"
    path.write_text(
        '{"version":3,"segments":[{"segment_id":"legacy","name":"旧道路",'
        '"points":[[0.1,0.1],[0.9,0.9]],"capacity":4,"level":"ground",'
        '"direction":"双向"}],"cameras":[]}',
        encoding="utf-8",
    )

    model = TrafficMapModel(path)

    assert model.segments["legacy"].geometry_type == "polyline"
    assert model.segments["legacy"].road_width == pytest.approx(36 / 740)


def test_saved_camera_without_a_binding_stays_unbound_after_reload(tmp_path):
    path = tmp_path / "traffic_map.json"
    model = TrafficMapModel(path, ["道路1"])
    model.cameras["道路1"].segment_id = ""
    model.save()

    reloaded = TrafficMapModel(path, ["道路1"])

    assert reloaded.segments
    assert reloaded.cameras["道路1"].segment_id == ""


def test_empty_topology_persists_and_ignores_detection_updates(tmp_path):
    path = tmp_path / "traffic_map.json"
    model = TrafficMapModel(path, ["道路1"])
    for segment_id in list(model.segments):
        assert model.delete_segment(segment_id)
    model.save()

    detection = SimpleNamespace(
        track_id=1,
        vehicle_bbox=(10, 10, 80, 100),
        vehicle_class="car",
        plate_text="",
    )
    model.update_detections("道路1", [detection], (640, 480), now=10.0)
    reloaded = TrafficMapModel(path, ["道路1"])

    assert model.segments == {}
    assert model.tracks == {}
    assert model.cameras["道路1"].segment_id == ""
    assert reloaded.segments == {}
    assert reloaded.cameras["道路1"].segment_id == ""
    with pytest.raises(ValueError, match="尚未绘制道路"):
        model.map_detection("道路1", detection.vehicle_bbox, (640, 480))
