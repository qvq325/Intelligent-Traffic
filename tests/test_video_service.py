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
