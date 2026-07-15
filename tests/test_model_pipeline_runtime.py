from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

import detection_processor as detection_processor_module
import lpr_recognizer as lpr_recognizer_module
import vehicle_detector as vehicle_detector_module
import backend.state as state_module
from backend.configuration.errors import ConfigurationError
from backend.model_pipelines import ModelPipelineOptions
from backend.state import ApplicationState
from backend.video_stream import VideoStreamService
from detection_processor import DetectionProcessor
from lpr_recognizer import LPRRecognizer, PlateRecognition
from vehicle_detector import VehicleDetector
from whitelist_manager import WhitelistManager


class _EmptyVehicleModel:
    def __init__(self, model_path: str, calls: dict) -> None:
        calls["model_path"] = model_path
        self._calls = calls

    def predict(self, frame: np.ndarray, **kwargs):
        self._calls["predict_frame"] = frame
        self._calls["predict_kwargs"] = kwargs
        return [SimpleNamespace(boxes=[])]


def _runtime_options(scene_key: str, *, revision: int = 1) -> ModelPipelineOptions:
    return ModelPipelineOptions(
        scene_key=scene_key,
        preset="legacy",
        enabled=True,
        device_preference="cpu",
        yolo_threshold=0.5,
        lpr_threshold=0.7,
        frame_interval=5,
        inference_size=640,
        parking_move_threshold=0.03,
        mog_history=500,
        mog_variance_threshold=25.0,
        mog_min_area=150,
        mog_min_duration=2.0,
        mog_max_duration=5.0,
        mog_warmup_frames=50,
        revision=revision,
        vehicle_model_path=Path(f"{scene_key}.pt"),
        plate_model_path=None,
        plate_mode="pose",
        no_parking_mode="dwell",
        road_abnormal_mode="mog2",
    )


def _patch_vehicle_runtime(monkeypatch, calls: dict) -> None:
    monkeypatch.setattr(vehicle_detector_module, "HAS_YOLO", True)
    monkeypatch.setattr(
        vehicle_detector_module,
        "YOLO",
        lambda model_path: _EmptyVehicleModel(model_path, calls),
    )
    monkeypatch.setattr(
        vehicle_detector_module,
        "YAML",
        SimpleNamespace(load=lambda _path: {}),
    )
    monkeypatch.setattr(vehicle_detector_module, "check_yaml", lambda _name: "tracker")


def test_vehicle_detector_passes_model_path_and_inference_size_to_ultralytics(
    monkeypatch,
    tmp_path,
):
    calls = {}
    _patch_vehicle_runtime(monkeypatch, calls)
    model_path = tmp_path / "trained-vehicle.pt"

    detector = VehicleDetector(
        model_path=model_path,
        inference_size=896,
        conf_threshold=0.42,
        device="cpu",
    )
    frame = np.zeros((12, 20, 3), dtype=np.uint8)
    assert detector.detect(frame) == []

    assert calls["model_path"] == str(model_path)
    assert calls["predict_frame"] is frame
    assert calls["predict_kwargs"]["imgsz"] == 896
    assert calls["predict_kwargs"]["conf"] == 0.42
    assert calls["predict_kwargs"]["device"] == "cpu"


def test_vehicle_detector_keeps_legacy_model_and_size_keywords(monkeypatch):
    calls = {}
    _patch_vehicle_runtime(monkeypatch, calls)

    detector = VehicleDetector(model_name="legacy.pt", imgsz=512)
    detector.detect(np.zeros((4, 4, 3), dtype=np.uint8))

    assert calls["model_path"] == "legacy.pt"
    assert calls["predict_kwargs"]["imgsz"] == 512


@pytest.mark.parametrize(
    ("legacy_options", "expected_model", "expected_size"),
    [
        ({"model_name": "legacy.pt"}, "legacy.pt", 640),
        ({"imgsz": 512}, "yolo11m.pt", 512),
    ],
)
def test_vehicle_detector_accepts_each_legacy_alias_without_canonical_values(
    monkeypatch,
    legacy_options,
    expected_model,
    expected_size,
):
    calls = {}
    _patch_vehicle_runtime(monkeypatch, calls)

    detector = VehicleDetector(**legacy_options)
    detector.detect(np.zeros((4, 4, 3), dtype=np.uint8))

    assert calls["model_path"] == expected_model
    assert calls["predict_kwargs"]["imgsz"] == expected_size


def test_vehicle_detector_rejects_conflicting_model_aliases_before_loading(
    monkeypatch,
):
    calls = {}
    _patch_vehicle_runtime(monkeypatch, calls)

    with pytest.raises(ValueError, match="model_path.*model_name"):
        VehicleDetector(model_path=Path("canonical.pt"), model_name="legacy.pt")

    assert "model_path" not in calls


def test_vehicle_detector_rejects_conflicting_size_aliases_before_loading(
    monkeypatch,
):
    calls = {}
    _patch_vehicle_runtime(monkeypatch, calls)

    with pytest.raises(ValueError, match="inference_size.*imgsz"):
        VehicleDetector(inference_size=896, imgsz=640)

    assert "model_path" not in calls


def test_vehicle_detector_allows_equivalent_canonical_and_legacy_values(
    monkeypatch,
):
    calls = {}
    _patch_vehicle_runtime(monkeypatch, calls)
    model_path = Path("same-model.pt")

    detector = VehicleDetector(
        model_path=model_path,
        model_name=str(model_path),
        inference_size=768,
        imgsz=768,
    )
    detector.detect(np.zeros((4, 4, 3), dtype=np.uint8))

    assert calls["model_path"] == str(model_path)
    assert calls["predict_kwargs"]["imgsz"] == 768


def test_lpr_recognizer_supports_ocr_only_initialization(monkeypatch, tmp_path):
    recognizer_path = tmp_path / "ocr.pth"
    recognizer_path.write_bytes(b"test-double")
    loaded_ocr = object()

    monkeypatch.setattr(lpr_recognizer_module, "HAS_PLATE_RECOGNIZER", True)
    monkeypatch.setattr(
        lpr_recognizer_module,
        "YOLO",
        lambda _path: (_ for _ in ()).throw(AssertionError("pose detector loaded")),
    )
    monkeypatch.setattr(
        LPRRecognizer,
        "_load_recognizer",
        lambda self, model_path: loaded_ocr,
    )

    recognizer = LPRRecognizer(
        detector_model=None,
        recognizer_model=recognizer_path,
    )

    assert recognizer.detector is None
    assert recognizer.recognizer is loaded_ocr


def test_lpr_recognize_crops_returns_existing_plate_contract():
    recognizer = object.__new__(LPRRecognizer)
    recognizer.conf_threshold = 0.5
    recognizer.use_half = False
    recognizer.device = torch.device("cpu")
    recognizer._prepare_batch = lambda _images: torch.empty((2, 3, 48, 168))

    first = torch.full((3, len(lpr_recognizer_module.PLATE_CHARACTERS)), -20.0)
    first[:, 1] = 20.0
    second = torch.full((3, len(lpr_recognizer_module.PLATE_CHARACTERS)), -5.0)
    second[:, 2] = 5.0
    sequences = torch.stack((first, second))
    color_logits = torch.tensor(((0.0, 5.0, 0.0, 0.0, 0.0), (0.0, 0.0, 5.0, 0.0, 0.0)))
    recognizer.recognizer = lambda _batch: (sequences, color_logits)

    bboxes = [(1, 2, 5, 6), (7, 8, 11, 12)]
    results = recognizer.recognize_crops(
        [np.ones((4, 4, 3), dtype=np.uint8)] * 2,
        bboxes,
    )

    assert all(isinstance(result, PlateRecognition) for result in results)
    assert [result.bbox for result in results] == bboxes
    assert [result.plate_color for result in results] == ["蓝色", "绿色"]
    assert results[0].confidence > results[1].confidence


def test_detection_processor_wires_vehicle_options_and_defaults_to_pose_lpr(
    monkeypatch,
    tmp_path,
):
    calls = {}
    vehicle_path = tmp_path / "vehicle.pt"

    class FakeVehicleDetector:
        def __init__(self, **kwargs) -> None:
            calls["vehicle"] = kwargs

    class FakePoseRecognizer:
        def __init__(self, **kwargs) -> None:
            calls["pose"] = kwargs

    class UnexpectedBoxRecognizer:
        def __init__(self, **kwargs) -> None:
            raise AssertionError(f"box recognizer selected: {kwargs}")

    monkeypatch.setattr(detection_processor_module, "HAS_YOLO", True)
    monkeypatch.setattr(detection_processor_module, "HAS_PLATE_RECOGNIZER", True)
    monkeypatch.setattr(detection_processor_module, "VehicleDetector", FakeVehicleDetector)
    monkeypatch.setattr(detection_processor_module, "LPRRecognizer", FakePoseRecognizer)
    monkeypatch.setattr(
        detection_processor_module,
        "BoxPlateRecognizer",
        UnexpectedBoxRecognizer,
        raising=False,
    )

    processor = DetectionProcessor(
        yolo_conf=0.41,
        lpr_conf=0.73,
        device="cpu",
        vehicle_model_path=vehicle_path,
        inference_size=960,
    )

    assert processor.initialize()
    assert calls["vehicle"] == {
        "conf_threshold": 0.41,
        "device": "cpu",
        "model_path": vehicle_path,
        "inference_size": 960,
    }
    assert calls["pose"] == {
        "conf_threshold": 0.73,
        "device": "cpu",
        "image_size": 960,
    }


def test_detection_processor_selects_box_plate_recognizer(monkeypatch, tmp_path):
    calls = {}
    plate_path = tmp_path / "plate.pt"

    class FakeVehicleDetector:
        def __init__(self, **kwargs) -> None:
            calls["vehicle"] = kwargs

    class UnexpectedPoseRecognizer:
        def __init__(self, **kwargs) -> None:
            raise AssertionError(f"pose recognizer selected: {kwargs}")

    class FakeBoxRecognizer:
        def __init__(self, **kwargs) -> None:
            calls["box"] = kwargs

    monkeypatch.setattr(detection_processor_module, "HAS_YOLO", True)
    monkeypatch.setattr(detection_processor_module, "HAS_PLATE_RECOGNIZER", True)
    monkeypatch.setattr(detection_processor_module, "VehicleDetector", FakeVehicleDetector)
    monkeypatch.setattr(detection_processor_module, "LPRRecognizer", UnexpectedPoseRecognizer)
    monkeypatch.setattr(
        detection_processor_module,
        "BoxPlateRecognizer",
        FakeBoxRecognizer,
        raising=False,
    )

    processor = DetectionProcessor(
        lpr_conf=0.68,
        device="cpu",
        plate_model_path=plate_path,
        inference_size=832,
        lpr_mode="box",
    )

    assert processor.initialize()
    assert calls["box"] == {
        "model_path": plate_path,
        "conf_threshold": 0.68,
        "device": "cpu",
        "inference_size": 832,
    }


@pytest.mark.parametrize("has_plate_recognizer", [True, False])
@pytest.mark.parametrize(
    ("options", "error_match"),
    [
        ({"lpr_mode": "unsupported"}, "lpr_mode"),
        ({"lpr_mode": "box"}, "plate_model_path"),
    ],
)
def test_detection_processor_rejects_invalid_lpr_configuration_before_models_load(
    monkeypatch,
    has_plate_recognizer,
    options,
    error_match,
):
    vehicle_calls = []

    class UnexpectedVehicleDetector:
        def __init__(self, **kwargs) -> None:
            vehicle_calls.append(kwargs)

    monkeypatch.setattr(
        detection_processor_module,
        "HAS_PLATE_RECOGNIZER",
        has_plate_recognizer,
    )
    monkeypatch.setattr(
        detection_processor_module,
        "VehicleDetector",
        UnexpectedVehicleDetector,
    )

    with pytest.raises(ValueError, match=error_match):
        DetectionProcessor(**options)

    assert vehicle_calls == []


def test_box_plate_recognizer_clamps_crops_and_returns_plate_contract():
    from trained_plate_recognizer import BoxPlateRecognizer

    frame = np.arange(8 * 10 * 3, dtype=np.uint8).reshape((8, 10, 3))

    class DetectorDouble:
        def __init__(self) -> None:
            self.frame = None

        def detect(self, image):
            self.frame = image
            return [
                SimpleNamespace(bbox=(-3, 1, 5, 6), confidence=0.8),
                SimpleNamespace(bbox=(6, -2, 15, 4), confidence=0.9),
                SimpleNamespace(bbox=(4, 4, 4, 7), confidence=0.99),
            ]

    class OCRDouble:
        def __init__(self) -> None:
            self.crops = None
            self.bboxes = None

        def recognize_crops(self, crops, bboxes):
            self.crops = [crop.copy() for crop in crops]
            self.bboxes = list(bboxes)
            return [
                PlateRecognition("京A00001", 0.61, bboxes[0], "蓝色"),
                PlateRecognition("京A00002", 0.94, bboxes[1], "绿色"),
            ]

    detector = DetectorDouble()
    ocr = OCRDouble()
    recognizer = BoxPlateRecognizer(
        model_path=Path("trusted-plate.pt"),
        detector=detector,
        ocr=ocr,
    )

    results = recognizer.recognize(frame)

    assert detector.frame is frame
    assert ocr.bboxes == [(0, 1, 5, 6), (6, 0, 10, 4)]
    assert np.array_equal(ocr.crops[0], frame[1:6, 0:5])
    assert np.array_equal(ocr.crops[1], frame[0:4, 6:10])
    assert all(isinstance(result, PlateRecognition) for result in results)
    assert [result.plate_text for result in results] == ["京A00002", "京A00001"]
    assert [result.bbox for result in results] == [(6, 0, 10, 4), (0, 1, 5, 6)]


def test_box_plate_recognizer_default_detector_uses_runtime_options(
    monkeypatch,
    tmp_path,
):
    import trained_plate_recognizer as trained_module

    calls = {"predict": []}
    model_path = tmp_path / "plate.pt"
    model_path.write_bytes(b"trusted")

    class FakeModel:
        def __init__(self, path):
            calls["model_path"] = path

        def predict(self, frames, **kwargs):
            calls["predict"].append((frames, kwargs))
            return [SimpleNamespace(boxes=[]) for _frame in frames]

    class OCRDouble:
        def recognize_crops(self, crops, bboxes):
            raise AssertionError((crops, bboxes))

    monkeypatch.setattr(trained_module, "YOLO", FakeModel)
    recognizer = trained_module.BoxPlateRecognizer(
        model_path=model_path,
        conf_threshold=0.37,
        device="cpu",
        inference_size=768,
        ocr=OCRDouble(),
    )
    frame = np.zeros((4, 6, 3), dtype=np.uint8)

    assert recognizer.recognize(frame) == []
    assert recognizer.detector.detect_batch(
        [frame, frame], conf_threshold=0.12
    ) == [[], []]
    assert calls["model_path"] == str(model_path)
    assert len(calls["predict"][0][0]) == 1
    assert calls["predict"][0][0][0] is frame
    assert calls["predict"][0][1] == {
        "conf": 0.37,
        "device": "cpu",
        "imgsz": 768,
        "verbose": False,
    }
    assert len(calls["predict"][1][0]) == 2
    assert all(item is frame for item in calls["predict"][1][0])
    assert calls["predict"][1][1] == {
        "conf": 0.12,
        "device": "cpu",
        "imgsz": 768,
        "verbose": False,
    }


def test_application_state_constructs_all_streams_with_fixed_scene_keys(
    monkeypatch,
    tmp_path,
):
    constructed_scene_keys = []

    class TrafficMapDouble:
        def __init__(self, *_args):
            pass

        def save(self):
            pass

    class RoadAbnormalDouble:
        def __init__(self, *_args, **_kwargs):
            pass

        def process_frame(self, _camera_id, frame):
            return frame

    class StreamDouble:
        def __init__(self, *_args, scene_key="realtime", **_kwargs):
            self.scene_key = scene_key
            constructed_scene_keys.append(scene_key)

        def update_detection_settings(self, **_settings):
            pass

        def status(self):
            return {"scene_key": self.scene_key}

    monkeypatch.setattr(state_module, "TrafficMapModel", TrafficMapDouble)
    monkeypatch.setattr(state_module, "NoParkingMonitor", lambda *_args: object())
    monkeypatch.setattr(state_module, "RoadAbnormalMonitor", RoadAbnormalDouble)
    monkeypatch.setattr(state_module, "VideoStreamService", StreamDouble)
    monkeypatch.setattr(
        state_module.DetectionProcessor,
        "get_available_devices",
        lambda: [("cpu", "CPU")],
    )
    config = SimpleNamespace(
        whitelist_file=tmp_path / "missing-whitelist.json",
        traffic_map_file=tmp_path / "missing-map.json",
        stream_sources={"camera-1": "test.mp4"},
        configuration_dir=None,
        upload_dir=tmp_path / "uploads",
        project_dir=tmp_path,
    )

    ApplicationState(config)

    assert constructed_scene_keys == [
        "realtime",
        "traffic_map",
        "no_parking",
        "road_abnormal",
    ]


def test_application_state_resolves_all_pipeline_rows_before_matching_services():
    scene_keys = ("realtime", "traffic_map", "no_parking", "road_abnormal")
    rows = [{"scene_key": scene_key, "revision": 4} for scene_key in scene_keys]
    resolved = []

    class RegistryDouble:
        def resolve(self, row):
            resolved.append(row["scene_key"])
            return _runtime_options(row["scene_key"], revision=row["revision"])

    class ConfigurationServiceDouble:
        model_pipeline_registry = RegistryDouble()

        def model_pipeline_settings(self):
            return {"settings": rows}

    class StreamDouble:
        def __init__(self, scene_key):
            self.scene_key = scene_key
            self.applied = []

        def apply_model_pipeline_options(self, options):
            self.applied.append(options)

    state = object.__new__(ApplicationState)
    state.configuration_service = ConfigurationServiceDouble()
    state.video = StreamDouble("realtime")
    state.map_analysis = StreamDouble("traffic_map")
    state.no_parking_video = StreamDouble("no_parking")
    state.road_abnormal_video = StreamDouble("road_abnormal")
    state.no_parking = StreamDouble("no_parking")
    state.road_abnormal = StreamDouble("road_abnormal")

    state.apply_model_pipeline_settings()

    assert resolved == list(scene_keys)
    for scene_key, stream in (
        ("realtime", state.video),
        ("traffic_map", state.map_analysis),
        ("no_parking", state.no_parking_video),
        ("road_abnormal", state.road_abnormal_video),
    ):
        assert [item.scene_key for item in stream.applied] == [scene_key]
        assert stream.applied[0].revision == 4
    assert [item.scene_key for item in state.no_parking.applied] == ["no_parking"]
    assert [item.scene_key for item in state.road_abnormal.applied] == [
        "road_abnormal"
    ]


@pytest.mark.parametrize(
    "scene_keys",
    [
        ("realtime", "traffic_map", "no_parking"),
        ("realtime", "realtime", "no_parking", "road_abnormal"),
        ("realtime", "traffic_map", "no_parking", "unknown"),
    ],
)
def test_application_state_rejects_incomplete_or_invalid_pipeline_scene_sets(
    scene_keys,
):
    resolver_calls = []

    class RegistryDouble:
        def resolve(self, row):
            resolver_calls.append(row)
            return _runtime_options(row["scene_key"])

    service = SimpleNamespace(
        model_pipeline_registry=RegistryDouble(),
        model_pipeline_settings=lambda: {
            "settings": [{"scene_key": scene_key} for scene_key in scene_keys]
        },
    )
    streams = [SimpleNamespace(applied=[]) for _scene_key in range(4)]
    for stream in streams:
        stream.apply_model_pipeline_options = stream.applied.append
    state = object.__new__(ApplicationState)
    state.configuration_service = service
    (
        state.video,
        state.map_analysis,
        state.no_parking_video,
        state.road_abnormal_video,
    ) = streams

    with pytest.raises(ConfigurationError) as raised:
        state.apply_model_pipeline_settings()

    assert raised.value.code == "MODEL_PIPELINE_RUNTIME_INVALID"
    assert resolver_calls == []
    assert all(stream.applied == [] for stream in streams)


def test_application_state_does_not_partially_apply_when_pipeline_resolution_fails():
    scene_keys = ("realtime", "traffic_map", "no_parking", "road_abnormal")

    class RegistryDouble:
        def resolve(self, row):
            if row["scene_key"] == "no_parking":
                raise RuntimeError("unavailable test preset")
            return _runtime_options(row["scene_key"])

    state = object.__new__(ApplicationState)
    state.configuration_service = SimpleNamespace(
        model_pipeline_registry=RegistryDouble(),
        model_pipeline_settings=lambda: {
            "settings": [{"scene_key": scene_key} for scene_key in scene_keys]
        },
    )
    streams = []
    for attribute in (
        "video",
        "map_analysis",
        "no_parking_video",
        "road_abnormal_video",
        "no_parking",
        "road_abnormal",
    ):
        stream = SimpleNamespace(applied=[])
        stream.apply_model_pipeline_options = stream.applied.append
        setattr(state, attribute, stream)
        streams.append(stream)

    with pytest.raises(RuntimeError, match="unavailable test preset"):
        state.apply_model_pipeline_settings()

    assert all(stream.applied == [] for stream in streams)


def test_bootstrap_loader_and_reload_apply_model_pipeline_settings():
    class ConfigurationServiceDouble:
        def get_activation_state(self):
            return {
                "stream_profile_id": "profile-1",
                "topology_id": "topology-1",
                "no_parking_scene_id": None,
                "road_abnormal_scene_id": None,
            }

        def get_stream_profile(self, _profile_id):
            return {"bindings": [{"camera_id": "camera-1", "rtsp_url": "test.mp4"}]}

        def get_topology(self, _topology_id):
            return {"topology_id": "topology-1"}

        def detection_settings(self):
            return {
                "enabled": True,
                "yolo_threshold": 0.5,
                "lpr_threshold": 0.7,
                "interval": 5,
                "device_preference": "cpu",
            }

    state = object.__new__(ApplicationState)
    state.configuration_service = ConfigurationServiceDouble()
    state.preview_stream = None
    state.devices = [{"id": "cpu"}]
    state.video = SimpleNamespace(update_detection_settings=lambda **_kwargs: None)
    state._active_stream_sources = {}
    state._install_topology_runtime = lambda _topology: None
    state.apply_stream_mapping = lambda _mapping: None
    state.apply_topology = lambda _topology: None
    state._load_whitelist_runtime = lambda: None
    state._load_scenes_runtime = lambda: None
    state._restore_active_scenes = lambda _activation: None
    pipeline_loads = []
    state.apply_model_pipeline_settings = lambda: pipeline_loads.append("applied")

    ApplicationState._load_configuration_runtime(state)
    ApplicationState.reload_configuration_runtime(state)

    assert pipeline_loads == ["applied", "applied"]


def test_apply_stream_mapping_uses_private_mapping_without_exposing_source_url():
    old_url = "rtsp://user:secret@example.test/old"
    new_url = "rtsp://user:secret@example.test/new"
    video = VideoStreamService(WhitelistManager())
    video.select_source("camera-1", "camera-1", old_url)

    class IdleStream:
        def status(self):
            return {"active_source": None, "running": False}

    state = object.__new__(ApplicationState)
    state._active_stream_sources = {"camera-1": old_url}
    state.video = video
    state.map_analysis = IdleStream()
    state.no_parking_video = IdleStream()
    state.road_abnormal_video = IdleStream()
    state.preview_stream = None

    assert "url" not in video.status()["active_source"]
    unchanged = state.apply_stream_mapping({"camera-1": old_url})
    changed = state.apply_stream_mapping({"camera-1": new_url})

    assert unchanged == {"reconnected_camera_ids": []}
    assert changed == {"reconnected_camera_ids": ["camera-1"]}
    assert state.current_stream_mapping() == {"camera-1": new_url}
    assert "url" not in video.status()["active_source"]
