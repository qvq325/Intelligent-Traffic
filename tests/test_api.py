from pathlib import Path

import numpy as np
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
            "road_width": 64 / 740,
        },
    )
    assert created.status_code == 201
    assert created.json()["segment_id"] == "web-road"
    assert created.json()["road_width"] == pytest.approx(64 / 740)

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


def test_map_analysis_camera_control_is_independent_from_monitor(client):
    monitor = client.post("/api/stream/select", json={"source_id": "道路1"})
    enabled = client.put("/api/detection/settings", json={"enabled": True})
    analysis = client.post("/api/map/analysis/select", json={"source_id": "道路2"})

    assert monitor.status_code == 200
    assert enabled.json()["enabled"] is True
    assert analysis.status_code == 200
    assert analysis.json()["active_source"]["id"] == "道路2"
    assert analysis.json()["segment"] is not None
    assert analysis.json()["detection"]["enabled"] is True

    monitor_status = client.get("/api/stream/status").json()
    assert monitor_status["active_source"]["id"] == "道路1"
    assert monitor_status["detection"]["enabled"] is False
    assert client.app.state.runtime.video.on_detections is None
    assert client.app.state.runtime.map_analysis.on_detections is not None

    paused = client.post("/api/map/analysis/pause", json={"paused": True})
    assert paused.json()["paused"] is True
    resumed = client.post("/api/map/analysis/pause", json={"paused": False})
    assert resumed.json()["paused"] is False

    client.put("/api/detection/settings", json={"enabled": True})
    assert client.get("/api/map/analysis/status").json()["detection"]["enabled"] is False

    stopped = client.post("/api/map/analysis/stop")
    assert stopped.json()["active_source"] is None
    assert stopped.json()["detection"]["enabled"] is False


def test_map_analysis_rejects_a_camera_without_a_road_binding(client):
    with client.app.state.runtime.map_lock:
        client.app.state.runtime.traffic_map.cameras["道路2"].segment_id = ""
    analysis = client.post("/api/map/analysis/select", json={"source_id": "道路2"})

    assert analysis.status_code == 422
    assert analysis.json()["detail"] == "摄像头尚未绑定有效道路"


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


def test_polygon_road_api_roundtrip_and_validation(client):
    payload = {
        "segment_id": "drawn-area",
        "name": "手绘道路区域",
        "points": [[0.15, 0.2], [0.65, 0.2], [0.65, 0.55], [0.15, 0.55]],
        "capacity": 8,
        "level": "ground",
        "direction": "双向",
        "geometry_type": "polygon",
    }

    created = client.post("/api/map/segments", json=payload)
    current = client.get("/api/map").json()
    invalid = client.post(
        "/api/map/segments",
        json={**payload, "segment_id": "bad-area", "points": [[0.1, 0.1], [0.5, 0.5]]},
    )

    assert created.status_code == 201
    assert created.json()["geometry_type"] == "polygon"
    assert next(
        segment for segment in current["segments"] if segment["segment_id"] == "drawn-area"
    )["geometry_type"] == "polygon"
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "道路区域至少需要三个点"


def test_frontend_exposes_polygon_road_drawing(client):
    index = client.get("/").text
    app_js = client.get("/static/js/app.js").text
    canvas_js = client.get("/static/js/map-canvas.js").text

    assert 'id="draw-polygon-button"' in index
    assert 'startRoadDrawing("polygon")' in app_js
    assert 'segment.geometry_type === "polygon"' in canvas_js
    assert "context.closePath()" in canvas_js


def test_frontend_exposes_precise_centerline_width(client):
    index = client.get("/").text
    app_js = client.get("/static/js/app.js").text
    canvas_js = client.get("/static/js/map-canvas.js").text

    assert 'id="road-width" type="range" min="4" max="120"' in index
    assert "road_width: Number(elements.roadWidth.value) / MAP_REFERENCE_HEIGHT" in app_js
    assert "mapCanvas.setRoadWidth(widthPixels / MAP_REFERENCE_HEIGHT)" in app_js
    assert "this.roadWidthPixels(segment)" in canvas_js
    assert "this.roadWidthPixels()" in canvas_js


def test_map_page_exposes_independent_heat_analysis_controls(client):
    index = client.get("/").text
    api_js = client.get("/static/js/api.js").text
    app_js = client.get("/static/js/app.js").text

    assert 'id="map-analysis-camera-select"' in index
    assert 'id="map-analysis-start-button"' in index
    assert 'id="map-analysis-pause-button"' in index
    assert 'id="map-analysis-stop-button"' in index
    assert 'request("/api/map/analysis/status")' in api_js
    assert '"/api/map/analysis/select"' in api_js
    assert "function populateMapAnalysisSources()" in app_js
    assert "selectSegment(analysis.segment.id, true)" in app_js
    assert "未绑定道路" in app_js


def test_all_map_segments_can_be_deleted(client):
    segment_ids = [segment["segment_id"] for segment in client.get("/api/map").json()["segments"]]

    for segment_id in segment_ids:
        response = client.delete(f"/api/map/segments/{segment_id}")
        assert response.status_code == 200

    current = client.get("/api/map").json()
    assert current["segments"] == []
    assert all(camera["segment_id"] == "" for camera in current["cameras"])


def test_frontend_shell_prevents_stale_asset_initialization_failure(client):
    index = client.get("/")
    app_js = client.get("/static/js/app.js").text

    assert index.headers["cache-control"] == "no-store"
    assert "/static/js/app.js?v=20260713-9" in index.text
    assert 'id="road-video-preview"' in index.text
    assert 'id="road-video-preview-status">实时</span>' in index.text
    assert "/api/video/preview?source_id=" in app_js
    assert "mapCanvas.nearestCamera(point, 20)" in app_js
    assert 'return state.activeView === "map";' in app_js
    assert "drawPolygonButton?.addEventListener" in app_js


def test_map_poll_does_not_replace_an_explicit_new_road_form(client):
    app_js = client.get("/static/js/app.js").text

    assert "creatingRoad: false" in app_js
    assert "state.creatingRoad = true" in app_js
    assert "!state.creatingRoad && !state.selectedSegment" in app_js


def test_no_parking_scene_workflow_and_runtime_controls(client):
    selected = client.post("/api/stream/select", json={"source_id": "道路1"})
    assert selected.status_code == 200
    client.app.state.runtime.video._publish_frame(np.zeros((100, 160, 3), dtype=np.uint8))

    reference = client.post(
        "/api/no-parking/reference",
        json={"camera_id": "道路1"},
    )
    assert reference.status_code == 201
    assert reference.json()["width"] == 160
    assert client.get(reference.json()["url"]).status_code == 200

    scene = client.post(
        "/api/no-parking/scenes",
        json={
            "name": "道路1禁停场景",
            "camera_id": "道路1",
            "reference_image": reference.json()["filename"],
            "reference_width": 160,
            "reference_height": 100,
            "zones": [
                {
                    "name": "入口禁停区",
                    "points": [[0.1, 0.2], [0.8, 0.2], [0.8, 0.8], [0.1, 0.8]],
                    "dwell_seconds": 3,
                    "lost_tolerance_seconds": 1,
                }
            ],
        },
    )
    assert scene.status_code == 201
    scene_id = scene.json()["scene_id"]
    assert client.get("/api/no-parking").json()["scenes"][0]["zones"][0]["name"] == "入口禁停区"

    started = client.post("/api/no-parking/start", json={"scene_id": scene_id})
    assert started.status_code == 200
    assert started.json()["running"] is True
    assert client.app.state.runtime.video.on_detections is None
    assert client.app.state.runtime.video._detection_listeners

    stopped = client.post("/api/no-parking/stop")
    assert stopped.json()["running"] is False
    assert client.delete(f"/api/no-parking/scenes/{scene_id}").status_code == 200


def test_no_parking_page_is_available_from_the_main_navigation(client):
    index = client.get("/").text
    api_js = client.get("/static/js/api.js").text
    app_js = client.get("/static/js/app.js").text
    canvas_js = client.get("/static/js/no-parking-canvas.js").text

    assert 'data-view="no-parking"' in index
    assert 'id="view-no-parking"' in index
    assert 'id="no-parking-canvas"' in index
    assert 'id="no-parking-reference-button"' in index
    assert 'id="no-parking-start-button"' in index
    assert 'data-no-parking-view-mode="topology"' in index
    assert 'id="no-parking-topology-canvas"' in index
    assert 'id="no-parking-topology-zones"' in index
    assert 'id="no-parking-topology-markers"' in index
    assert 'request("/api/no-parking/status")' in api_js
    assert "function captureNoParkingReference()" in app_js
    assert "function loadNoParkingTopology()" in app_js
    assert "function openNoParkingTopologyScene(sceneId)" in app_js
    assert "function projectNoParkingZonePoint(camera, point)" in app_js
    assert "function syncNoParkingSceneToSource()" in app_js
    assert 'addEventListener("change", syncNoParkingSceneToSource)' in app_js
    assert "class VideoRegionEditor" in canvas_js
    assert "mediaRect()" in canvas_js
