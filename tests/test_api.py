from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import AppConfig, PROJECT_DIR


@pytest.fixture()
def client(tmp_path):
    config = AppConfig(
        project_dir=PROJECT_DIR,
        frontend_dir=PROJECT_DIR / "frontend",
        whitelist_file=tmp_path / "whitelist.json",
        traffic_map_file=tmp_path / "traffic_map.json",
        fallback_map_image=PROJECT_DIR / "sandpan" / "沙盘平面图2.png",
        upload_dir=tmp_path / "uploads",
        map_upload_dir=tmp_path / "maps",
        stream_sources={"道路1": "rtsp://127.0.0.1/live/1", "道路2": "rtsp://127.0.0.1/live/2"},
    )
    app = create_app(config, start_video=False)
    with TestClient(app) as test_client:
        yield test_client


def test_frontend_and_system_catalog_are_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "沙盘交通智控台" in response.text

    system = client.get("/api/system").json()
    assert system["sources"] == [
        {"id": "道路1", "name": "道路1"},
        {"id": "道路2", "name": "道路2"},
    ]
    assert system["devices"][0]["id"] == "cpu"
    assert client.get("/api/health").json()["status"] == "ok"


def test_stream_selection_pause_and_detection_settings(client):
    selected = client.post("/api/stream/select", json={"source_id": "道路1"})
    assert selected.status_code == 200
    assert selected.json()["active_source"]["id"] == "道路1"

    paused = client.post("/api/stream/pause", json={"paused": True})
    assert paused.json()["paused"] is True

    settings = client.put(
        "/api/detection/settings",
        json={"enabled": True, "yolo_threshold": 0.6, "lpr_threshold": 0.8, "interval": 3},
    )
    assert settings.status_code == 200
    assert settings.json()["enabled"] is True
    assert settings.json()["interval"] == 3

    stopped = client.post("/api/stream/stop")
    assert stopped.json()["active_source"] is None


def test_whitelist_lifecycle_is_persisted(client):
    created = client.post(
        "/api/whitelist",
        json={"plate": "京A12345", "note": "测试车辆"},
    )
    assert created.status_code == 201
    assert created.json()["created"] is True

    listing = client.get("/api/whitelist").json()
    assert listing["count"] == 1
    assert listing["entries"][0]["note"] == "测试车辆"

    disabled = client.patch("/api/whitelist/enabled", json={"enabled": False})
    assert disabled.json() == {"enabled": False}

    deleted = client.delete("/api/whitelist/%E4%BA%ACA12345")
    assert deleted.status_code == 200
    assert client.get("/api/whitelist").json()["count"] == 0


def test_map_camera_and_segment_crud(client):
    initial = client.get("/api/map").json()
    assert len(initial["segments"]) == 18
    assert len(initial["cameras"]) == 2
    assert initial["image_url"].startswith("/api/map/image?v=")

    created = client.post(
        "/api/map/segments",
        json={
            "segment_id": "web-road",
            "name": "网页道路",
            "points": [[0.1, 0.2], [0.5, 0.4], [0.8, 0.3]],
            "capacity": 7,
            "level": "bridge",
            "direction": "东行",
        },
    )
    assert created.status_code == 201
    assert created.json()["segment_id"] == "web-road"

    camera = client.put(
        "/api/map/cameras/%E9%81%93%E8%B7%AF1",
        json={
            "x": 0.4,
            "y": 0.3,
            "heading": 90,
            "view_range": 0.2,
            "segment_id": "web-road",
        },
    )
    assert camera.status_code == 200
    assert camera.json()["segment_id"] == "web-road"

    removed = client.delete("/api/map/segments/web-road")
    assert removed.status_code == 200
    current = client.get("/api/map").json()
    assert all(segment["segment_id"] != "web-road" for segment in current["segments"])
    assert all(camera["segment_id"] != "web-road" for camera in current["cameras"])


def test_map_image_and_validation_errors(client):
    image = client.get("/api/map/image")
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/")

    missing_source = client.post("/api/stream/select", json={"source_id": "missing"})
    assert missing_source.status_code == 404

    invalid_segment = client.post(
        "/api/map/segments",
        json={"name": "坏道路", "points": [[0.1, 0.1]]},
    )
    assert invalid_segment.status_code == 422


def test_multi_camera_preview_route_and_frontend_mode(client):
    missing = client.get("/api/video/preview", params={"source_id": "missing"})
    assert missing.status_code == 404
    assert missing.json()["detail"] == "视频源不存在"

    index = client.get("/").text
    assert 'data-monitor-mode="multi"' in index
    assert "多摄像头" in index
    assert "六画面" not in index
    assert 'id="multi-camera-grid"' in index
    assert 'id="multi-camera-previous"' in index
    assert 'id="multi-camera-next"' in index
    assert 'id="video-switch-preview"' in index
    assert "/api/video/preview" in client.get("/openapi.json").text


def test_multi_camera_snapshot_uses_cached_preview_frame(client):
    cached = b"\xff\xd8cached-preview\xff\xd9"
    client.app.state.preview_stream._channels["道路1"]._publish(cached)

    response = client.get("/api/video/preview/snapshot", params={"source_id": "道路1"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == cached


def test_single_camera_feed_reconnects_for_source_changes(client):
    app_js = client.get("/static/js/app.js").text

    assert "videoFeed.dataset.sourceId !== sourceId" in app_js
    assert 'videoFeed.addEventListener("error"' in app_js


def test_frontend_waits_for_rendered_main_frame_and_skips_blocking_previews(client):
    app_js = client.get("/static/js/app.js").text

    assert 'videoFeed.addEventListener("load"' in app_js
    assert "videoFeed.dataset.readySourceId === sourceId" in app_js
    assert "cached_only=true" in app_js


def test_hidden_switch_preview_cannot_cover_the_main_video(client):
    styles = client.get("/static/styles.css").text

    assert ".video-stage .video-switch-preview[hidden]" in styles


def test_multi_camera_tile_opens_its_source_in_single_mode(client):
    app_js = client.get("/static/js/app.js").text

    assert 'tile.setAttribute("role", "button")' in app_js
    assert 'setMonitorMode("single", { sourceId: source.id })' in app_js
    assert 'event.key !== "Enter" && event.key !== " "' in app_js
