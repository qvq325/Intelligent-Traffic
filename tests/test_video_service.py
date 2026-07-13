import numpy as np

from backend.video_stream import VideoStreamService
from whitelist_manager import WhitelistManager


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
