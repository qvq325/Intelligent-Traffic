from dataclasses import replace
from pathlib import Path
import threading
import time

import numpy as np
import pytest

import backend.video_stream as video_stream_module
from backend.model_pipelines import ModelPipelineOptions
from backend.video_stream import VideoStreamService
from detection_processor import DetectionResult
from whitelist_manager import WhitelistManager


def _pipeline_options(
    scene_key="realtime",
    *,
    revision=1,
    enabled=True,
    preset="legacy",
):
    return ModelPipelineOptions(
        scene_key=scene_key,
        preset=preset,
        enabled=enabled,
        device_preference="cpu",
        yolo_threshold=0.42,
        lpr_threshold=0.73,
        frame_interval=3,
        inference_size=768,
        parking_move_threshold=0.03,
        mog_history=500,
        mog_variance_threshold=25.0,
        mog_min_area=150,
        mog_min_duration=2.0,
        mog_max_duration=5.0,
        mog_warmup_frames=50,
        revision=revision,
        vehicle_model_path=Path(f"{scene_key}-vehicle.pt"),
        plate_model_path=(
            Path(f"{scene_key}-plate.pt") if preset == "trained" else None
        ),
        plate_mode="box" if preset == "trained" else "pose",
        no_parking_mode="stationary" if preset == "trained" else "dwell",
        road_abnormal_mode="mog" if preset == "trained" else "mog2",
    )


class _ProcessorDouble:
    def __init__(self, *, initialize_result=True, init_error="load failed"):
        self.initialize_result = initialize_result
        self.init_error = init_error
        self.is_initialized = False
        self.has_lpr = True
        self.yolo_threshold = None
        self.lpr_threshold = None
        self.process_calls = []
        self.reset_tracking_calls = 0
        self.on_process = None

    def initialize(self):
        self.is_initialized = self.initialize_result
        return self.initialize_result

    def process(self, frame, camera_id=""):
        self.process_calls.append(
            {
                "camera_id": camera_id,
                "yolo_threshold": self.yolo_threshold,
                "lpr_threshold": self.lpr_threshold,
            }
        )
        if self.on_process is not None:
            return self.on_process(frame, camera_id)
        return frame, []

    def reset_tracking(self):
        self.reset_tracking_calls += 1


class _CaptureDouble:
    def __init__(self, frames, *, on_exhausted=None):
        self.frames = [frame.copy() for frame in frames]
        self.on_exhausted = on_exhausted
        self.released = False

    def read(self):
        if self.frames:
            return True, self.frames.pop(0)
        if self.on_exhausted is not None:
            callback, self.on_exhausted = self.on_exhausted, None
            callback()
        return False, None

    def release(self):
        self.released = True

    def get(self, _property):
        return 30.0


class _ContinuousCapture:
    def __init__(self, *, delay=0.005):
        self.delay = delay
        self.read_count = 0
        self.released = False

    def read(self):
        time.sleep(self.delay)
        self.read_count += 1
        frame = np.full((8, 8, 3), self.read_count % 255, dtype=np.uint8)
        return True, frame

    def release(self):
        self.released = True

    def get(self, _property):
        return 30.0


def _result(camera_id="camera-1"):
    return DetectionResult(
        vehicle_bbox=(0, 0, 4, 4),
        vehicle_class="car",
        vehicle_class_cn="汽车",
        yolo_confidence=0.9,
        camera_id=camera_id,
    )


def test_cached_detection_panel_matches_inference_panel_labels(monkeypatch):
    class WhitelistSummary:
        enabled = True
        count = 2

    panel_lines = []
    monkeypatch.setattr(
        "draw_utils.draw_vehicle_box",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "draw_utils.draw_info_panel",
        lambda _frame, lines, **_kwargs: panel_lines.append(list(lines)),
    )
    service = VideoStreamService(WhitelistSummary())

    service._draw_cached_results(
        np.zeros((8, 8, 3), dtype=np.uint8),
        [_result()],
    )

    assert panel_lines == [[
        "车辆检测: 1 辆  |  车牌识别: 0 个",
        "白名单匹配: 0/1  |  白名单总数: 2",
    ]]


def test_empty_detection_results_are_cached_between_inference_frames(monkeypatch):
    processor = _ProcessorDouble()
    processor.on_process = lambda frame, _camera_id: (
        np.full_like(frame, 90),
        [],
    )
    service = VideoStreamService(
        WhitelistManager(),
        processor_factory=lambda _options: processor,
    )
    service.apply_model_pipeline_options(
        replace(_pipeline_options(), frame_interval=2)
    )
    service._ensure_processor()
    cached_draws = []
    monkeypatch.setattr(
        service,
        "_draw_cached_results",
        lambda frame, results: (
            cached_draws.append(list(results)) or np.full_like(frame, 200)
        ),
    )
    service.select_source("camera-1", "camera-1", "rtsp://example.test/live")
    work = video_stream_module._InferenceWork(
        source=service._requested_source,
        source_revision=service._source_revision,
        frame=np.zeros((8, 8, 3), dtype=np.uint8),
    )

    assert service._process_inference_work(work)
    processing = service._processing_snapshot()
    cached = service._cached_detection_results(
        processing,
        service._source_revision,
    )
    annotated = service._draw_cached_results(
        np.full((8, 8, 3), 20, dtype=np.uint8),
        cached,
    )

    assert len(processor.process_calls) == 1
    assert cached_draws == [[]]
    assert int(annotated[0, 0, 0]) == 200


def test_inference_slot_keeps_only_the_latest_frame():
    service = VideoStreamService(WhitelistManager())
    service.apply_model_pipeline_options(_pipeline_options())
    service.select_source("camera-1", "camera-1", "rtsp://example.test/live")
    source = service._requested_source

    assert service._submit_inference(
        source,
        service._source_revision,
        np.zeros((8, 8, 3), dtype=np.uint8),
    )
    assert service._submit_inference(
        source,
        service._source_revision,
        np.full((8, 8, 3), 2, dtype=np.uint8),
    )

    with service._condition:
        pending = service._pending_inference
    assert pending is not None
    assert np.all(pending.frame == 2)


def test_blocked_inference_does_not_block_capture_publication(monkeypatch):
    inference_started = threading.Event()
    release_inference = threading.Event()
    continued_publication = threading.Event()
    published_frames = []
    publication_baseline = [None]
    processor = _ProcessorDouble()
    capture = _ContinuousCapture()

    def process(frame, _camera_id):
        inference_started.set()
        assert release_inference.wait(timeout=5.0)
        return frame, []

    def publish(frame, _snapshot=None):
        published_frames.append(frame.copy())
        baseline = publication_baseline[0]
        if baseline is not None and len(published_frames) >= baseline + 3:
            continued_publication.set()
        return True

    processor.on_process = process
    service = VideoStreamService(
        WhitelistManager(),
        processor_factory=lambda _options: processor,
    )
    service.apply_model_pipeline_options(
        replace(_pipeline_options(), frame_interval=1)
    )
    monkeypatch.setattr(service, "_open_capture", lambda _source: capture)
    monkeypatch.setattr(service, "_publish_frame", publish)
    service.select_source("camera-1", "camera-1", "rtsp://example.test/live")

    service.start()
    try:
        assert inference_started.wait(timeout=2.0)
        publication_baseline[0] = len(published_frames)
        assert continued_publication.wait(timeout=1.0)
    finally:
        service.update_detection_settings(enabled=False)
        release_inference.set()
        service.stop(timeout=2.0)

    assert capture.read_count > publication_baseline[0]


def test_slow_model_loading_does_not_block_capture_publication(monkeypatch):
    loading_started = threading.Event()
    release_loading = threading.Event()
    continued_publication = threading.Event()
    published_count = [0]
    publication_baseline = [None]
    capture = _ContinuousCapture()

    class LoadingProcessor(_ProcessorDouble):
        def initialize(self):
            loading_started.set()
            assert release_loading.wait(timeout=5.0)
            return super().initialize()

    def publish(_frame, _snapshot=None):
        published_count[0] += 1
        baseline = publication_baseline[0]
        if baseline is not None and published_count[0] >= baseline + 3:
            continued_publication.set()
        return True

    service = VideoStreamService(
        WhitelistManager(),
        processor_factory=lambda _options: LoadingProcessor(),
    )
    service.apply_model_pipeline_options(
        replace(_pipeline_options(), frame_interval=1)
    )
    monkeypatch.setattr(service, "_open_capture", lambda _source: capture)
    monkeypatch.setattr(service, "_publish_frame", publish)
    service.select_source("camera-1", "camera-1", "rtsp://example.test/live")

    service.start()
    try:
        assert loading_started.wait(timeout=2.0)
        publication_baseline[0] = published_count[0]
        assert continued_publication.wait(timeout=1.0)
    finally:
        service.update_detection_settings(enabled=False)
        release_loading.set()
        service.stop(timeout=2.0)

    assert capture.read_count > publication_baseline[0]


def test_model_loading_uses_the_latest_queued_frame():
    loading_started = threading.Event()
    release_loading = threading.Event()
    processed_values = []

    class LoadingProcessor(_ProcessorDouble):
        def initialize(self):
            loading_started.set()
            assert release_loading.wait(timeout=5.0)
            return super().initialize()

    processor = LoadingProcessor()
    processor.on_process = lambda frame, _camera_id: (
        processed_values.append(int(frame[0, 0, 0])) or frame,
        [],
    )
    service = VideoStreamService(
        WhitelistManager(),
        processor_factory=lambda _options: processor,
    )
    service.apply_model_pipeline_options(_pipeline_options())
    service.select_source("camera-1", "camera-1", "rtsp://example.test/live")
    source = service._requested_source
    first_work = video_stream_module._InferenceWork(
        source=source,
        source_revision=service._source_revision,
        frame=np.zeros((8, 8, 3), dtype=np.uint8),
    )
    worker = threading.Thread(
        target=service._process_inference_work,
        args=(first_work,),
    )
    worker.start()
    assert loading_started.wait(timeout=2.0)

    service._submit_inference(
        source,
        service._source_revision,
        np.full((8, 8, 3), 2, dtype=np.uint8),
    )
    service._submit_inference(
        source,
        service._source_revision,
        np.full((8, 8, 3), 3, dtype=np.uint8),
    )
    release_loading.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert processed_values == [3]
    assert service._pending_inference is None


def test_video_service_pause_state_and_source_switch():
    service = VideoStreamService(WhitelistManager())

    assert not service.is_paused()

    service.set_paused(True)
    assert service.is_paused()

    service.select_source("camera-1", "道路1", "example.mp4")
    assert not service.is_paused()
    assert service.status()["active_source"]["id"] == "camera-1"

    service.set_paused(True)
    assert service.status()["paused"]

    service.stop_stream()
    assert service.status()["active_source"] is None
    assert not service.is_paused()


def test_video_service_publishes_jpeg_frames():
    service = VideoStreamService(WhitelistManager())
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:, :, 1] = 180

    service._publish_frame(frame)
    sequence, jpeg = service.wait_for_frame(-1, timeout=0.01)

    assert sequence > 0
    assert jpeg is not None
    assert jpeg.startswith(b"\xff\xd8")
    assert service.status()["resolution"] == {"width": 64, "height": 48}


def test_video_service_can_restart_the_selected_local_source():
    service = VideoStreamService(WhitelistManager())
    service.select_source("camera-1", "道路1", "example.mp4")
    initial_revision = service._source_revision
    service.set_paused(True)

    assert service.restart_source() is True
    assert service._source_revision == initial_revision + 1
    assert service.status()["paused"] is False
    assert service.status()["active_source"]["id"] == "camera-1"

    service.stop_stream()
    assert service.restart_source() is False


def test_video_service_applies_optional_frame_processor():
    calls = []

    def processor(camera_id, frame):
        calls.append(camera_id)
        processed = frame.copy()
        processed[:, :, 2] = 255
        return processed

    service = VideoStreamService(WhitelistManager(), frame_processor=processor)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    result = service.frame_processor("camera-1", frame)

    assert calls == ["camera-1"]
    assert result[0, 0, 2] == 255
    assert frame[0, 0, 2] == 0


def test_video_service_defaults_to_realtime_and_exposes_explicit_scene_key():
    legacy = VideoStreamService(WhitelistManager())
    traffic_map = VideoStreamService(
        WhitelistManager(),
        scene_key="traffic_map",
    )

    assert legacy.scene_key == "realtime"
    assert legacy.status()["scene_key"] == "realtime"
    assert traffic_map.scene_key == "traffic_map"
    assert traffic_map.status()["scene_key"] == "traffic_map"


def test_pipeline_revision_rebuilds_only_the_matching_scene_processor():
    factory_calls = []

    def factory(options):
        factory_calls.append((options.scene_key, options.revision))
        return _ProcessorDouble()

    realtime = VideoStreamService(
        WhitelistManager(),
        scene_key="realtime",
        processor_factory=factory,
    )
    traffic_map = VideoStreamService(
        WhitelistManager(),
        scene_key="traffic_map",
        processor_factory=factory,
    )
    realtime_options = _pipeline_options("realtime")
    traffic_options = _pipeline_options("traffic_map")

    realtime.apply_model_pipeline_options(realtime_options)
    traffic_map.apply_model_pipeline_options(traffic_options)
    realtime._ensure_processor()
    traffic_map._ensure_processor()

    realtime_revision = replace(realtime_options, revision=2, inference_size=896)
    realtime.apply_model_pipeline_options(realtime_revision)
    traffic_map.apply_model_pipeline_options(traffic_options)
    realtime._ensure_processor()
    traffic_map._ensure_processor()

    assert factory_calls == [
        ("realtime", 1),
        ("traffic_map", 1),
        ("realtime", 2),
    ]
    assert realtime.status()["detection"]["active_revision"] == 2
    assert traffic_map.status()["detection"]["active_revision"] == 1


def test_pipeline_options_must_match_the_service_scene():
    service = VideoStreamService(
        WhitelistManager(),
        scene_key="traffic_map",
        processor_factory=lambda _options: _ProcessorDouble(),
    )

    try:
        service.apply_model_pipeline_options(_pipeline_options("realtime"))
    except ValueError as exc:
        assert "traffic_map" in str(exc)
        assert "realtime" in str(exc)
    else:
        raise AssertionError("mismatched scene options were accepted")


def test_default_processor_factory_maps_all_detection_options(monkeypatch):
    calls = {}

    class ProcessorDouble(_ProcessorDouble):
        def __init__(self, **kwargs):
            super().__init__()
            calls["kwargs"] = kwargs

    monkeypatch.setattr(video_stream_module, "DetectionProcessor", ProcessorDouble)
    service = VideoStreamService(WhitelistManager(), scene_key="realtime")
    options = _pipeline_options("realtime", preset="trained")

    service.apply_model_pipeline_options(options)
    service._ensure_processor()

    assert calls["kwargs"] == {
        "yolo_conf": 0.42,
        "lpr_conf": 0.73,
        "device": "cpu",
        "vehicle_model_path": Path("realtime-vehicle.pt"),
        "plate_model_path": Path("realtime-plate.pt"),
        "inference_size": 768,
        "lpr_mode": "box",
    }


def test_processor_initialization_runs_outside_the_condition_lock():
    service = None
    lock_was_available = threading.Event()

    class LockProbeProcessor(_ProcessorDouble):
        def initialize(self):
            def acquire_condition():
                with service._condition:
                    lock_was_available.set()

            probe = threading.Thread(target=acquire_condition)
            probe.start()
            probe.join(timeout=0.5)
            self.is_initialized = lock_was_available.is_set()
            return self.is_initialized

    service = VideoStreamService(
        WhitelistManager(),
        processor_factory=lambda _options: LockProbeProcessor(),
    )
    service.apply_model_pipeline_options(_pipeline_options())

    service._ensure_processor()

    assert lock_was_available.is_set()
    assert service.status()["detection"]["active_revision"] == 1


def test_failed_replacement_preserves_initialized_processor_and_is_not_retried():
    created = []

    def factory(options):
        processor = _ProcessorDouble(
            initialize_result=options.revision == 1,
            init_error=f"replacement failed: {options.vehicle_model_path}",
        )
        created.append(processor)
        return processor

    service = VideoStreamService(
        WhitelistManager(),
        processor_factory=factory,
    )
    initial = _pipeline_options(revision=1)
    replacement = replace(initial, revision=2, inference_size=896)

    service.apply_model_pipeline_options(initial)
    service._ensure_processor()
    active_processor = service._processor
    service.apply_model_pipeline_options(replacement)
    service._ensure_processor()

    status = service.status()["detection"]
    assert service._processor is active_processor
    assert active_processor.is_initialized
    assert status["enabled"] is True
    assert status["active_revision"] == 1
    assert status["desired_revision"] == 2
    assert "replacement failed" in status["status"]

    service._ensure_processor()
    assert len(created) == 2
    assert str(replacement.vehicle_model_path) not in repr(service.status())


@pytest.mark.parametrize("separator_style", ("backslash", "slash", "mixed"))
def test_failed_processor_redacts_windows_model_paths_case_insensitively(
    separator_style,
):
    vehicle_path = Path(r"C:\Trusted\Models\Vehicle.pt")
    plate_path = Path(r"C:\Trusted\Models\Plate.pt")
    options = replace(
        _pipeline_options(preset="trained"),
        vehicle_model_path=vehicle_path,
        plate_model_path=plate_path,
    )

    def error_path(path):
        components = str(path).swapcase().replace("\\", "/").split("/")
        if separator_style == "backslash":
            return "\\".join(components)
        if separator_style == "slash":
            return "/".join(components)
        return "".join(
            component + ("\\" if index % 2 == 0 else "/")
            for index, component in enumerate(components[:-1])
        ) + components[-1]

    load_error = (
        f"processor unavailable: vehicle={error_path(vehicle_path)}; "
        f"plate={error_path(plate_path)}"
    )
    service = VideoStreamService(
        WhitelistManager(),
        processor_factory=lambda _options: _ProcessorDouble(
            initialize_result=False,
            init_error=load_error,
        ),
    )

    service.apply_model_pipeline_options(options)
    service._ensure_processor()

    status = service.status()["detection"]["status"]
    normalized_status = status.casefold().replace("\\", "/")
    assert "processor unavailable" in status
    assert status.count("[model]") == 2
    for path in (vehicle_path, plate_path):
        assert str(path).casefold().replace("\\", "/") not in normalized_status
        rendered = error_path(path)
        assert rendered.casefold() not in status.casefold()
        assert rendered.casefold().replace("\\", "/") not in normalized_status


def test_failed_replacement_keeps_active_thresholds_and_interval():
    processors = {}
    initial = replace(
        _pipeline_options(revision=1),
        yolo_threshold=0.21,
        lpr_threshold=0.31,
        frame_interval=1,
        vehicle_model_path=Path("revision-1.pt"),
    )
    replacement = replace(
        initial,
        revision=2,
        yolo_threshold=0.91,
        lpr_threshold=0.81,
        frame_interval=4,
        vehicle_model_path=Path("revision-2.pt"),
    )

    def factory(options):
        processor = _ProcessorDouble(
            initialize_result=options.revision == 1,
            init_error="replacement unavailable",
        )
        processors[options.revision] = processor
        return processor

    service = VideoStreamService(WhitelistManager(), processor_factory=factory)
    service.apply_model_pipeline_options(initial)
    service._ensure_processor()
    active = processors[1]
    service.apply_model_pipeline_options(replacement)
    service._ensure_processor()

    service.select_source("camera-1", "camera-1", "rtsp://example.test/live")
    work = video_stream_module._InferenceWork(
        source=service._requested_source,
        source_revision=service._source_revision,
        frame=np.zeros((8, 8, 3), dtype=np.uint8),
    )

    assert service._process_inference_work(work)

    runtime = service._settings_snapshot()
    assert runtime.yolo_threshold == 0.21
    assert runtime.lpr_threshold == 0.31
    assert runtime.interval == 1
    assert active.process_calls == [
        {
            "camera_id": "camera-1",
            "yolo_threshold": 0.21,
            "lpr_threshold": 0.31,
        }
    ]
    assert active.yolo_threshold == 0.21
    assert active.lpr_threshold == 0.31


def test_disable_discards_inflight_processor_results_and_annotation():
    started = threading.Event()
    release = threading.Event()
    callbacks = []
    processor = _ProcessorDouble()
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    def process(blocked_frame, _camera_id):
        started.set()
        release.wait(timeout=5.0)
        return np.full_like(blocked_frame, 255), [_result()]

    processor.on_process = process
    service = VideoStreamService(
        WhitelistManager(),
        processor_factory=lambda _options: processor,
    )
    service.add_detection_listener(
        lambda camera_id, results, _resolution: callbacks.append(
            (camera_id, list(results))
        )
    )
    service.apply_model_pipeline_options(
        replace(_pipeline_options(), frame_interval=1)
    )
    service._ensure_processor()
    service.select_source("camera-1", "camera-1", "rtsp://example.test/live")
    work = video_stream_module._InferenceWork(
        source=service._requested_source,
        source_revision=service._source_revision,
        frame=frame,
    )
    worker = threading.Thread(
        target=service._process_inference_work,
        args=(work,),
    )
    worker.start()
    assert started.wait(timeout=2.0)

    service.update_detection_settings(enabled=False)
    release.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert service.status()["results"] == []
    assert callbacks == []
    assert service._detection_overlay is None


def test_disable_during_encoding_does_not_commit_stale_annotation(monkeypatch):
    encoding_started = threading.Event()
    release_encoding = threading.Event()
    processor = _ProcessorDouble()
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    annotated_jpeg = b"annotated-jpeg"

    service = VideoStreamService(
        WhitelistManager(),
        processor_factory=lambda _options: processor,
    )
    service.apply_model_pipeline_options(
        replace(_pipeline_options(), frame_interval=1)
    )
    service._ensure_processor()

    def encode(_extension, encoded_frame, _parameters):
        if np.all(encoded_frame == 255):
            encoding_started.set()
            assert release_encoding.wait(timeout=5.0)
            return True, np.frombuffer(annotated_jpeg, dtype=np.uint8)
        return True, np.frombuffer(b"raw-jpeg", dtype=np.uint8)

    monkeypatch.setattr(video_stream_module.cv2, "imencode", encode)
    service.select_source("camera-1", "camera-1", "rtsp://example.test/live")
    snapshot = service._processing_snapshot()
    worker = threading.Thread(
        target=service._publish_frame,
        args=(np.full_like(frame, 255), snapshot),
    )
    worker.start()
    assert encoding_started.wait(timeout=2.0)

    service.update_detection_settings(enabled=False)
    release_encoding.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert service.latest_frame() != annotated_jpeg


def test_disable_during_first_listener_prevents_later_listener_callback():
    first_started = threading.Event()
    release_first = threading.Event()
    callbacks = []
    processor = _ProcessorDouble()
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    service = VideoStreamService(
        WhitelistManager(),
        processor_factory=lambda _options: processor,
    )

    def first_listener(_camera_id, _results, _resolution):
        callbacks.append("first")
        first_started.set()
        assert release_first.wait(timeout=5.0)

    def second_listener(_camera_id, _results, _resolution):
        callbacks.append("second")

    service.add_detection_listener(first_listener)
    service.add_detection_listener(second_listener)
    service.apply_model_pipeline_options(_pipeline_options())
    service._ensure_processor()
    service.select_source("camera-1", "camera-1", "rtsp://example.test/live")
    snapshot = service._processing_snapshot()
    worker = threading.Thread(
        target=service._publish_results,
        args=(service._requested_source, [_result()], frame, snapshot),
    )
    worker.start()
    assert first_started.wait(timeout=2.0)

    service.update_detection_settings(enabled=False)
    release_first.set()
    worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert callbacks == ["first"]


def test_successful_swap_clears_results_and_does_not_draw_old_cache():
    processors = {}
    initial = replace(_pipeline_options(revision=1), frame_interval=1)
    replacement = replace(
        initial,
        revision=2,
        frame_interval=100,
        vehicle_model_path=Path("revision-2.pt"),
    )

    def factory(options):
        processor = _ProcessorDouble()
        if options.revision == 1:
            processor.on_process = lambda frame, _camera_id: (
                np.full_like(frame, 90),
                [_result()],
            )
        processors[options.revision] = processor
        return processor

    service = VideoStreamService(WhitelistManager(), processor_factory=factory)
    service.apply_model_pipeline_options(initial)
    service._ensure_processor()
    service.select_source("camera-1", "camera-1", "rtsp://example.test/live")
    work = video_stream_module._InferenceWork(
        source=service._requested_source,
        source_revision=service._source_revision,
        frame=np.zeros((8, 8, 3), dtype=np.uint8),
    )
    assert service._process_inference_work(work)
    assert service.status()["results"]

    service.apply_model_pipeline_options(replacement)
    service._ensure_processor()
    results_immediately_after_swap = service.status()["results"]
    cached_after_swap = service._cached_detection_results(
        service._processing_snapshot(),
        service._source_revision,
    )

    assert results_immediately_after_swap == []
    assert service.status()["results"] == []
    assert cached_after_swap is None
    assert len(processors[2].process_calls) == 0


def test_malformed_replacement_is_rejected_before_swap_and_not_retried():
    factory_calls = []
    initial = _pipeline_options(revision=1)
    replacement = replace(
        initial,
        revision=2,
        vehicle_model_path=Path(r"C:\Trusted\Models\Revision2.pt"),
    )
    active = _ProcessorDouble()

    class MalformedProcessor:
        is_initialized = True

        def initialize(self):
            return True

    def factory(options):
        factory_calls.append(options.revision)
        return active if options.revision == 1 else MalformedProcessor()

    service = VideoStreamService(WhitelistManager(), processor_factory=factory)
    service.apply_model_pipeline_options(initial)
    service._ensure_processor()
    service.apply_model_pipeline_options(replacement)

    service._ensure_processor()

    status = service.status()["detection"]
    assert service._processor is active
    assert status["active_revision"] == 1
    assert status["desired_revision"] == 2
    assert "processor protocol invalid" in status["status"]
    assert str(replacement.vehicle_model_path).casefold() not in repr(status).casefold()
    service._ensure_processor()
    assert factory_calls == [1, 2]


def test_external_inference_stores_pipeline_metadata_without_loading_processor():
    factory_calls = []

    def forbidden_factory(options):
        factory_calls.append(options)
        raise AssertionError("external inference must not load DetectionProcessor")

    service = VideoStreamService(
        WhitelistManager(),
        scene_key="road_abnormal",
        processor_factory=forbidden_factory,
        external_inference=True,
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
        assert np.all(frame == 5)
        observed.append(int(frame[0, 0, 0]))
        return np.full_like(frame, 15)

    service = VideoStreamService(
        WhitelistManager(),
        frame_processor=frame_processor,
        processor_factory=lambda _options: _ProcessorDouble(),
    )
    service.apply_model_pipeline_options(_pipeline_options())
    service._ensure_processor()
    service._cached_detection_results = lambda _snapshot, _revision: []
    drawn_inputs = []
    service._draw_cached_results = lambda frame, _results: (
        drawn_inputs.append(int(frame[0, 0, 0]))
        or np.full_like(frame, 25)
    )
    source = video_stream_module.StreamSource(
        "id",
        "camera-a",
        "camera-a",
        "test",
    )
    annotated, _snapshot = service._compose_frame(
        source,
        1,
        np.full((4, 4, 3), 5, dtype=np.uint8),
        service._processing_snapshot(),
    )
    assert observed == [5]
    assert drawn_inputs == [15]
    assert int(annotated[0, 0, 0]) == 25


def test_external_inference_status_distinguishes_disabled_and_scene_owned():
    service = VideoStreamService(
        WhitelistManager(),
        scene_key="road_abnormal",
        external_inference=True,
    )
    enabled = _pipeline_options("road_abnormal", preset="trained")
    service.apply_model_pipeline_options(enabled)
    assert service.status()["detection"]["status"] == "由场景分析器处理"
    service.apply_model_pipeline_options(
        replace(enabled, enabled=False, revision=2)
    )
    assert service.status()["detection"]["status"] == "未启用"
