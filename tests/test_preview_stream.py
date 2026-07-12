import numpy as np

from backend.preview_stream import MultiCameraPreviewService, PreviewChannel


def test_preview_channel_publishes_latest_jpeg_without_starting_decoder(monkeypatch):
    channel = PreviewChannel("camera-1", "rtsp://127.0.0.1/live/1")
    monkeypatch.setattr(channel, "start", lambda: None)

    channel._publish(b"jpeg-frame")
    sequence, frame = channel.wait_for_frame(0, timeout=0.01)

    assert sequence == 1
    assert frame == b"jpeg-frame"


def test_multi_camera_preview_service_tracks_configured_sources():
    service = MultiCameraPreviewService(
        {
            "camera-1": "rtsp://127.0.0.1/live/1",
            "camera-2": "rtsp://127.0.0.1/live/2",
        }
    )

    assert service.has_source("camera-1")
    assert service.has_source("camera-2")
    assert not service.has_source("missing")


def test_preview_subscription_releases_channel(monkeypatch):
    service = MultiCameraPreviewService({"camera-1": "rtsp://127.0.0.1/live/1"})
    channel = service._channels["camera-1"]
    monkeypatch.setattr(channel, "start", lambda: None)

    with service.subscribe("camera-1"):
        assert channel._subscribers == 1
        assert channel._idle_since is None

    assert channel._subscribers == 0
    assert channel._idle_since is not None


def test_preview_channel_stops_after_last_subscriber_is_idle(monkeypatch):
    class FakeCapture:
        def read(self):
            return True, np.zeros((24, 32, 3), dtype=np.uint8)

        def release(self):
            pass

    channel = PreviewChannel(
        "camera-1",
        "rtsp://127.0.0.1/live/1",
        max_fps=100,
        idle_timeout=0.02,
    )
    monkeypatch.setattr(channel, "_open_capture", FakeCapture)

    channel.acquire()
    channel.release()
    channel.join(timeout=1.0)

    assert channel._thread is not None
    assert not channel._thread.is_alive()


def test_preview_channel_prewarm_populates_first_frame_cache(monkeypatch):
    class FakeCapture:
        def read(self):
            frame = np.zeros((24, 32, 3), dtype=np.uint8)
            frame[:, :, 1] = 180
            return True, frame

        def release(self):
            pass

    channel = PreviewChannel(
        "camera-1",
        "rtsp://127.0.0.1/live/1",
        max_fps=100,
        idle_timeout=0.02,
    )
    monkeypatch.setattr(channel, "_open_capture", FakeCapture)

    assert channel.prewarm(timeout=1.0)
    channel._warmup_thread.join(timeout=1.0)

    assert channel._latest_jpeg is not None
    assert channel._latest_jpeg.startswith(b"\xff\xd8")
    assert channel._subscribers == 0
    channel.stop()


def test_preview_service_prewarm_coordinates_all_sources(monkeypatch):
    service = MultiCameraPreviewService(
        {
            "camera-1": "rtsp://127.0.0.1/live/1",
            "camera-2": "rtsp://127.0.0.1/live/2",
        }
    )

    for channel in service._channels.values():
        def cache_frame(*, timeout, target=channel):
            target._publish(b"cached-jpeg")
            return True

        monkeypatch.setattr(channel, "prewarm", cache_frame)

    assert service.prewarm() == ["camera-1", "camera-2"]
    service._prewarm_thread.join(timeout=1.0)

    assert all(channel.has_cached_frame() for channel in service._channels.values())
    service.stop()


def test_cancel_prewarm_stops_other_preview_channels(monkeypatch):
    service = MultiCameraPreviewService(
        {
            "camera-1": "rtsp://127.0.0.1/live/1",
            "camera-2": "rtsp://127.0.0.1/live/2",
        }
    )
    stopped = []
    for source_id, channel in service._channels.items():
        monkeypatch.setattr(
            channel,
            "request_stop",
            lambda target=source_id: stopped.append(target),
        )

    service.cancel_prewarm(keep_source_id="camera-2")

    assert service._prewarm_cancel_event.is_set()
    assert stopped == ["camera-1"]


def test_preview_snapshot_returns_cached_frame_without_reconnecting(monkeypatch):
    service = MultiCameraPreviewService({"camera-1": "rtsp://127.0.0.1/live/1"})
    channel = service._channels["camera-1"]
    channel._publish(b"cached-jpeg")
    monkeypatch.setattr(channel, "start", lambda: (_ for _ in ()).throw(AssertionError()))

    assert service.snapshot("camera-1") == b"cached-jpeg"
    service.stop()


def test_cached_only_snapshot_does_not_start_decoder(monkeypatch):
    service = MultiCameraPreviewService({"camera-1": "rtsp://127.0.0.1/live/1"})
    channel = service._channels["camera-1"]
    monkeypatch.setattr(channel, "start", lambda: (_ for _ in ()).throw(AssertionError()))

    assert service.snapshot("camera-1", cached_only=True) is None
