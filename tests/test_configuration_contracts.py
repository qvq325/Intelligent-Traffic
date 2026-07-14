import numpy as np
import pytest
from pydantic import ValidationError

from backend.configuration.models import (
    StreamCreate,
    StreamProfileActivationRequest,
    StreamProfileUpdate,
)
from backend.configuration.probe import StreamProbeService
from backend.configuration.package import CONFIG_PATHS, DOCUMENT_KEYS
from backend.configuration.security import redact_mapping, redact_text, redact_url_userinfo


def test_configuration_models_reject_unknown_fields_and_invalid_stream_urls():
    with pytest.raises(ValidationError):
        StreamCreate(name="camera", rtsp_url="http://example.test/live", secret="x")

    with pytest.raises(ValidationError):
        StreamProfileUpdate(
            name="重复绑定",
            bindings=[{"camera_id": "camera-1", "stream_id": "stream-1", "extra": True}],
        )

    with pytest.raises(ValidationError):
        StreamProfileActivationRequest(preflight_token="token", run_probe=False)


def test_rtsp_credentials_are_redacted_recursively():
    url = "rtsp://admin:secret@[2001:db8::1]:8554/live?token=visible&channel=1"
    assert redact_url_userinfo(url) == "rtsp://***:***@[2001:db8::1]:8554/live?token=%2A%2A%2A&channel=1"
    payload = redact_mapping({"items": [{"rtsp_url": url}], "other": url})
    assert "admin" not in payload["items"][0]["rtsp_url"]
    assert payload["other"] == url
    message = f"decoder failed for {url} after timeout"
    redacted = redact_text(message)
    assert "admin" not in redacted and "secret" not in redacted and "visible" not in redacted


def test_parallel_probe_preserves_request_order(monkeypatch):
    service = StreamProbeService(max_workers=2)

    class FakeCapture:
        def set(self, *_args):
            return True

        def open(self, url, _backend):
            self.url = url
            return "offline" not in url

        def read(self):
            return True, np.zeros((12, 24, 3), dtype=np.uint8)

        def release(self):
            pass

    monkeypatch.setattr("backend.configuration.probe.cv2.VideoCapture", FakeCapture)
    results = service.probe_many(
        [
            {"stream_id": "second", "rtsp_url": "rtsp://example.test/2"},
            {"stream_id": "first", "rtsp_url": "rtsp://example.test/offline"},
        ]
    )

    assert [item["stream_id"] for item in results] == ["second", "first"]
    assert results[0]["ok"] is True
    assert results[0]["width"] == 24
    assert results[1]["code"] == "STREAM_CONNECT_FAILED"


def test_model_pipeline_package_document_is_optional_for_legacy_packages():
    path = CONFIG_PATHS["model-pipelines"]

    assert path == "config/model-pipelines.json"
    assert path not in DOCUMENT_KEYS
