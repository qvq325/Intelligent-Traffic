import io
import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import AppConfig, PROJECT_DIR, STREAM_SOURCES


def _configuration(root: Path) -> AppConfig:
    return AppConfig(
        project_dir=PROJECT_DIR,
        frontend_dir=PROJECT_DIR / "frontend",
        whitelist_file=PROJECT_DIR / "whitelist.json",
        traffic_map_file=PROJECT_DIR / "traffic_map.json",
        fallback_map_image=PROJECT_DIR / "sandpan" / "沙盘平面图2.png",
        upload_dir=root / "uploads",
        map_upload_dir=root / "maps",
        stream_sources=dict(STREAM_SOURCES),
        configuration_dir=root / "config",
    )


@pytest.fixture(scope="module")
def configuration_client(tmp_path_factory):
    root = tmp_path_factory.mktemp("configuration-api")
    config = _configuration(root)
    with TestClient(create_app(config, start_video=False)) as client:
        yield client


@pytest.fixture(scope="module")
def exported_package(configuration_client):
    response = configuration_client.post("/api/config/exports")
    assert response.status_code == 200
    return response.content


def _rewrite_package(payload, mutation):
    with zipfile.ZipFile(io.BytesIO(payload)) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(entries["manifest.json"])
    mutation(entries, manifest)
    entries["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, content in entries.items():
            target.writestr(name, content)
    return output.getvalue()


def _refresh_manifest_file(manifest, path, content):
    descriptor = next(item for item in manifest["files"] if item["path"] == path)
    descriptor["size_bytes"] = len(content)
    descriptor["sha256"] = hashlib.sha256(content).hexdigest()


def test_configuration_summary_and_stream_credentials_are_safe_by_default(configuration_client):
    summary = configuration_client.get("/api/config/summary")
    streams = configuration_client.get("/api/config/streams")

    assert summary.status_code == 200
    assert summary.json()["counts"] == {
        "cameras": 12,
        "streams": 12,
        "stream_profiles": 1,
        "topologies": 1,
        "scenes": 15,
        "assets": 16,
    }
    assert summary.json()["integrity"] == {"ok": True, "messages": ["ok"]}
    assert len(streams.json()) == 12
    assert all("@" not in item["rtsp_url"] or "***:***@" in item["rtsp_url"] for item in streams.json())
    assert configuration_client.get("/api/system").json()["configuration"]["activation"]["topology_id"] == "builtin-default-topology"


def test_device_snapshot_api_uses_runtime_monitor(configuration_client, monkeypatch):
    runtime = configuration_client.app.state.runtime
    expected = {
        "collected_at": "2026-07-14T08:00:00+00:00",
        "cpu": {"available": True, "utilization_percent": 10.0},
        "memory": {"available": True, "utilization_percent": 20.0},
        "process": {"available": True, "cpu_percent": 3.0},
        "gpu": {"available": False, "devices": [], "error": "NVML_UNAVAILABLE"},
        "streams": [
            {"scene_key": scene_key, "available": True}
            for scene_key in ("realtime", "traffic_map", "no_parking", "road_abnormal")
        ],
    }
    monkeypatch.setattr(runtime.device_monitor, "snapshot", lambda: expected)

    response = configuration_client.get("/api/config/devices")

    assert response.status_code == 200
    assert response.json() == expected


def test_stream_crud_and_builtin_protection_use_stable_errors(configuration_client):
    created = configuration_client.post(
        "/api/config/streams",
        json={"name": "备用离线流", "rtsp_url": "rtsp://127.0.0.1:8554/offline", "enabled": False},
    )
    assert created.status_code == 201
    stream_id = created.json()["stream_id"]
    updated = configuration_client.put(
        f"/api/config/streams/{stream_id}", json={"name": "备用流", "enabled": True}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "备用流"
    assert configuration_client.delete(f"/api/config/streams/{stream_id}").json()["deleted"] is True

    protected = configuration_client.delete(
        "/api/config/stream-profiles/builtin-default-stream-profile"
    )
    assert protected.status_code == 409
    assert protected.json()["error"]["code"] == "BUILTIN_PROFILE_PROTECTED"
    assert protected.json()["error"]["rollback"] == "not_required"

    invalid = configuration_client.post(
        "/api/config/streams",
        json={"name": "错误协议", "rtsp_url": "http://example.test/live", "enabled": True},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "CONFIG_VALIDATION_ERROR"


def test_stream_batch_crud_probe_and_redaction_contracts(configuration_client, monkeypatch):
    runtime = configuration_client.app.state.runtime
    created = configuration_client.post(
        "/api/config/streams/batch",
        json={
            "streams": [
                {
                    "name": "批量 API 流 A",
                    "rtsp_url": "rtsp://batch-user:batch-secret@example.test/a",
                    "enabled": True,
                },
                {
                    "name": "批量 API 流 B",
                    "rtsp_url": "rtsp://batch-user:batch-secret@example.test/b",
                    "enabled": False,
                },
            ]
        },
    )
    assert created.status_code == 201
    assert created.json()["created"] == 2
    assert "batch-secret" not in created.text
    stream_ids = [item["stream_id"] for item in created.json()["streams"]]

    monkeypatch.setattr(
        runtime.probe_service,
        "probe_many",
        lambda _streams: pytest.fail("non-active batch updates must not probe"),
    )
    monkeypatch.setattr(
        runtime,
        "apply_stream_mapping",
        lambda _mapping: pytest.fail("non-active batch updates must not reconnect runtime"),
    )
    updated = configuration_client.put(
        "/api/config/streams/batch",
        json={
            "streams": [
                {
                    "stream_id": stream_ids[0],
                    "name": "批量 API 流 A2",
                    "rtsp_url": "rtsp://batch-user:new-secret@example.test/a2",
                    "enabled": True,
                },
                {
                    "stream_id": stream_ids[1],
                    "name": "批量 API 流 B2",
                    "rtsp_url": "rtsp://batch-user:new-secret@example.test/b2",
                    "enabled": False,
                },
            ]
        },
    )
    assert updated.status_code == 200
    assert updated.json()["updated"] == 2
    assert updated.json()["runtime"] is None
    assert "new-secret" not in updated.text

    def fake_probe_many(streams):
        assert [item["stream_id"] for item in streams] == stream_ids
        assert [item["enabled"] for item in streams] == [True, False]
        return [
            {
                "stream_id": stream_ids[0],
                "ok": True,
                "code": "OK",
                "message": "ok",
                "elapsed_ms": 2,
                "width": 20,
                "height": 10,
            },
            {
                "stream_id": stream_ids[1],
                "ok": False,
                "code": "STREAM_CONNECT_FAILED",
                "message": "offline",
                "elapsed_ms": 3,
                "width": 0,
                "height": 0,
            },
        ]

    monkeypatch.setattr(runtime.probe_service, "probe_many", fake_probe_many)
    probed = configuration_client.post(
        "/api/config/streams/probe",
        json={"stream_ids": stream_ids},
    )
    assert probed.status_code == 200
    assert probed.json()["total"] == 2
    assert probed.json()["succeeded"] == 1
    assert probed.json()["failed"] == 1
    assert [item["stream_id"] for item in probed.json()["results"]] == stream_ids
    refreshed = {
        item["stream_id"]: item
        for item in configuration_client.get("/api/config/streams").json()
    }
    assert refreshed[stream_ids[0]]["last_probe"]["code"] == "OK"
    assert refreshed[stream_ids[1]]["last_probe"]["code"] == "STREAM_CONNECT_FAILED"

    deleted = configuration_client.request(
        "DELETE",
        "/api/config/streams/batch",
        json={"stream_ids": stream_ids},
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": 2, "stream_ids": stream_ids}


def test_stream_batch_conflicts_are_atomic_and_use_stable_errors(configuration_client):
    existing = configuration_client.get("/api/config/streams").json()[0]
    conflict = configuration_client.post(
        "/api/config/streams/batch",
        json={
            "streams": [
                {
                    "name": "批量冲突时不得保留",
                    "rtsp_url": "rtsp://example.test/transient",
                    "enabled": True,
                },
                {
                    "name": existing["name"],
                    "rtsp_url": "rtsp://example.test/conflict",
                    "enabled": True,
                },
            ]
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "STREAM_BATCH_CONFLICT"
    assert conflict.json()["error"]["details"] == [
        {
            "field": "name",
            "name": existing["name"],
            "stream_id": existing["stream_id"],
            "reason": "already_exists",
        }
    ]
    assert all(
        item["name"] != "批量冲突时不得保留"
        for item in configuration_client.get("/api/config/streams").json()
    )

    invalid = configuration_client.put(
        "/api/config/streams/batch",
        json={"streams": []},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "CONFIG_VALIDATION_ERROR"


def test_stream_batch_delete_reference_conflict_keeps_every_selected_stream(configuration_client):
    referenced = configuration_client.get("/api/config/streams").json()[0]
    created = configuration_client.post(
        "/api/config/streams/batch",
        json={
            "streams": [
                {
                    "name": "批量删除原子性临时流",
                    "rtsp_url": "rtsp://example.test/delete-atomicity",
                    "enabled": False,
                }
            ]
        },
    ).json()["streams"][0]
    selected_ids = [referenced["stream_id"], created["stream_id"]]

    blocked = configuration_client.request(
        "DELETE",
        "/api/config/streams/batch",
        json={"stream_ids": selected_ids},
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "STREAM_BATCH_IN_USE"
    assert blocked.json()["error"]["details"][0]["stream_id"] == referenced["stream_id"]
    remaining_ids = {
        item["stream_id"]
        for item in configuration_client.get("/api/config/streams").json()
    }
    assert set(selected_ids) <= remaining_ids
    cleanup = configuration_client.request(
        "DELETE",
        "/api/config/streams/batch",
        json={"stream_ids": [created["stream_id"]]},
    )
    assert cleanup.status_code == 200


def test_active_stream_batch_update_uses_stable_probe_and_rollback_errors(
    configuration_client,
    monkeypatch,
):
    runtime = configuration_client.app.state.runtime
    activation = configuration_client.get("/api/config/summary").json()["activation"]
    profile = configuration_client.get(
        f"/api/config/stream-profiles/{activation['stream_profile_id']}"
    ).json()
    stream_id = profile["bindings"][0]["stream_id"]
    current = next(
        item
        for item in configuration_client.get(
            "/api/config/streams?reveal_credentials=true"
        ).json()
        if item["stream_id"] == stream_id
    )
    payload = {
        "streams": [
            {
                "stream_id": stream_id,
                "name": current["name"],
                "rtsp_url": "rtsp://new-user:new-secret@example.test/active",
                "enabled": True,
            }
        ]
    }

    monkeypatch.setattr(
        runtime.probe_service,
        "probe_many",
        lambda targets: [
            {
                "stream_id": targets[0]["stream_id"],
                "ok": False,
                "code": "STREAM_CONNECT_FAILED",
                "message": "offline",
                "elapsed_ms": 1,
            }
        ],
    )
    probe_failed = configuration_client.put(
        "/api/config/streams/batch",
        json=payload,
    )
    assert probe_failed.status_code == 422
    assert probe_failed.json()["error"]["code"] == "STREAM_PROBE_FAILED"
    assert probe_failed.json()["error"]["rollback"] == "not_required"

    monkeypatch.setattr(
        runtime.probe_service,
        "probe_many",
        lambda targets: [
            {
                "stream_id": targets[0]["stream_id"],
                "ok": True,
                "code": "OK",
                "message": "ok",
                "elapsed_ms": 1,
            }
        ],
    )
    applied = []

    def fail_once(mapping):
        applied.append(dict(mapping))
        if len(applied) == 1:
            raise RuntimeError("runtime apply failed")
        return {"reconnected_camera_ids": list(mapping)}

    monkeypatch.setattr(runtime, "apply_stream_mapping", fail_once)
    apply_failed = configuration_client.put(
        "/api/config/streams/batch",
        json=payload,
    )
    assert apply_failed.status_code == 502
    assert apply_failed.json()["error"]["code"] == "STREAM_BATCH_UPDATE_APPLY_FAILED"
    assert apply_failed.json()["error"]["rollback"] == "succeeded"
    assert "new-secret" not in apply_failed.text
    assert len(applied) == 2
    persisted = next(
        item
        for item in configuration_client.get(
            "/api/config/streams?reveal_credentials=true"
        ).json()
        if item["stream_id"] == stream_id
    )
    assert persisted["rtsp_url"] == current["rtsp_url"]


def test_incomplete_stream_profile_preflight_never_touches_network(configuration_client):
    stream_id = configuration_client.get("/api/config/streams?reveal_credentials=true").json()[0]["stream_id"]
    created = configuration_client.post(
        "/api/config/stream-profiles",
        json={
            "name": "不完整草稿",
            "description": "允许保存但不能激活",
            "bindings": [{"camera_id": "桥面", "stream_id": stream_id}],
        },
    )
    profile_id = created.json()["profile_id"]
    preflight = configuration_client.post(
        f"/api/config/stream-profiles/{profile_id}/preflight"
    )
    assert preflight.status_code == 200
    assert preflight.json()["ok"] is False
    assert len(preflight.json()["structural"]["missing_camera_ids"]) == 11
    assert preflight.json()["streams"] == []
    assert configuration_client.delete(f"/api/config/stream-profiles/{profile_id}").status_code == 200


def test_stream_profile_preflight_token_is_forwarded_and_consumed(tmp_path, monkeypatch):
    app = create_app(_configuration(tmp_path), start_video=False)
    with TestClient(app) as client:
        runtime = client.app.state.runtime
        probe_calls = []

        def fake_probe_many(bindings):
            probe_calls.append([item["stream_id"] for item in bindings])
            return [
                {
                    "stream_id": item["stream_id"],
                    "ok": True,
                    "code": "OK",
                    "message": "ok",
                    "elapsed_ms": 1,
                    "width": 1920,
                    "height": 1080,
                }
                for item in bindings
            ]

        monkeypatch.setattr(runtime.probe_service, "probe_many", fake_probe_many)
        monkeypatch.setattr(
            runtime,
            "apply_stream_mapping",
            lambda mapping: {"reconnected_camera_ids": list(mapping)},
        )

        cloned = client.post(
            "/api/config/stream-profiles/builtin-default-stream-profile/clone"
        ).json()
        profile_id = cloned["profile_id"]
        preflight = client.post(
            f"/api/config/stream-profiles/{profile_id}/preflight"
        )
        assert preflight.status_code == 200
        token = preflight.json()["preflight_token"]

        activated = client.post(
            f"/api/config/stream-profiles/{profile_id}/activate",
            json={"preflight_token": token},
        )
        assert activated.status_code == 200
        assert activated.json()["probe_results"] == preflight.json()["streams"]
        assert len(probe_calls) == 1

        second = client.post(
            f"/api/config/stream-profiles/{profile_id}/activate",
            json={"preflight_token": token},
        )
        assert second.status_code == 200
        assert second.json()["noop"] is True

        direct_profile = client.post(
            "/api/config/stream-profiles/builtin-default-stream-profile/clone"
        ).json()["profile_id"]
        direct = client.post(
            f"/api/config/stream-profiles/{direct_profile}/activate"
        )
        assert direct.status_code == 200
        assert len(probe_calls) == 2

        reused = client.post(
            f"/api/config/stream-profiles/{profile_id}/activate",
            json={"preflight_token": token},
        )
        assert reused.status_code == 409
        assert reused.json()["error"]["code"] == "STREAM_PREFLIGHT_EXPIRED"


def test_cloned_topology_can_activate_and_legacy_editor_increments_revision(configuration_client):
    cloned = configuration_client.post(
        "/api/config/topologies/builtin-default-topology/clone"
    )
    assert cloned.status_code == 201
    topology_id = cloned.json()["topology_id"]
    activated = configuration_client.post(
        f"/api/config/topologies/{topology_id}/activate"
    )
    assert activated.status_code == 200

    camera = next(
        item for item in configuration_client.get("/api/map").json()["cameras"]
        if item["camera_id"] == "桥面"
    )
    camera["heading"] = 123
    updated = configuration_client.put(
        "/api/map/cameras/%E6%A1%A5%E9%9D%A2",
        json={key: camera[key] for key in ("x", "y", "heading", "view_range", "segment_id")},
    )
    assert updated.status_code == 200
    topology = configuration_client.get(
        f"/api/config/topologies/{topology_id}"
    ).json()
    assert topology["revision"] == 2
    assert next(item for item in topology["cameras"] if item["camera_id"] == "桥面")["heading"] == 123

    map_image = PROJECT_DIR / "sandpan" / "沙盘平面图3.png"
    uploaded = configuration_client.post(
        f"/api/config/topologies/{topology_id}/map-image",
        files={"file": (map_image.name, map_image.read_bytes(), "image/png")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["revision"] == 3

    assert configuration_client.post(
        "/api/config/topologies/builtin-default-topology/activate"
    ).status_code == 200
    assert configuration_client.delete(
        f"/api/config/topologies/{topology_id}"
    ).status_code == 200


def test_export_preflight_apply_roundtrip_and_one_time_token(
    configuration_client, exported_package, monkeypatch
):
    monkeypatch.setattr(
        configuration_client.app.state.runtime.import_export_service,
        "preflight_validator",
        lambda _documents: {"device": "cpu", "streams": [], "topology_id": "builtin-default-topology", "topology_revision": 1},
    )
    with zipfile.ZipFile(io.BytesIO(exported_package)) as archive:
        assert "manifest.json" in archive.namelist()
        assert "config/activation-state.json" in archive.namelist()

    preflight = configuration_client.post(
        "/api/config/imports/preflight",
        files={"file": ("config.zip", exported_package, "application/zip")},
    )
    assert preflight.status_code == 200
    token = preflight.json()["token"]
    assert preflight.json()["expires_in_seconds"] == 900
    applied = configuration_client.post(
        f"/api/config/imports/{token}/apply", json={"confirm": True}
    )
    assert applied.status_code == 200
    assert applied.json()["rollback"] == "not_required"
    reused = configuration_client.post(
        f"/api/config/imports/{token}/apply", json={"confirm": True}
    )
    assert reused.status_code == 404
    assert reused.json()["error"]["code"] == "IMPORT_TOKEN_INVALID"


def test_export_includes_complete_path_free_model_pipeline_document(
    configuration_client,
):
    payload = configuration_client.post("/api/config/exports").content

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        path = "config/model-pipelines.json"
        assert path in archive.namelist()
        document = json.loads(archive.read(path))

    assert document["schema_version"] == 1
    assert [item["scene_key"] for item in document["settings"]] == [
        "realtime",
        "traffic_map",
        "no_parking",
        "road_abnormal",
    ]
    assert all("revision" not in item and "updated_at" not in item for item in document["settings"])
    serialized = json.dumps(document, ensure_ascii=False).lower()
    assert "model_path" not in serialized
    assert "vehicle_model" not in serialized
    assert "plate_model" not in serialized


def test_import_applies_all_model_pipeline_rows_atomically(
    configuration_client,
    monkeypatch,
):
    runtime = configuration_client.app.state.runtime
    original = configuration_client.get("/api/config/model-pipelines").json()["settings"]
    package = configuration_client.post("/api/config/exports").content
    path = "config/model-pipelines.json"

    def mutate(entries, manifest):
        document = json.loads(entries[path])
        for index, setting in enumerate(document["settings"]):
            setting["yolo_threshold"] = 0.61 + index * 0.01
        entries[path] = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        _refresh_manifest_file(manifest, path, entries[path])

    monkeypatch.setattr(
        runtime.import_export_service,
        "preflight_validator",
        lambda _documents: {
            "device": "cpu",
            "streams": [],
            "topology_id": "builtin-default-topology",
            "topology_revision": 1,
        },
    )
    try:
        preflight = configuration_client.post(
            "/api/config/imports/preflight",
            files={
                "file": (
                    "model-pipelines.zip",
                    _rewrite_package(package, mutate),
                    "application/zip",
                )
            },
        )
        assert preflight.status_code == 200
        applied = configuration_client.post(
            f"/api/config/imports/{preflight.json()['token']}/apply",
            json={"confirm": True},
        )
        assert applied.status_code == 200
        settings = configuration_client.get(
            "/api/config/model-pipelines"
        ).json()["settings"]
        assert [item["yolo_threshold"] for item in settings] == [
            0.61,
            0.62,
            0.63,
            0.64,
        ]
    finally:
        configuration_client.put(
            "/api/config/model-pipelines",
            json={"settings": [_editable_model_pipeline(item) for item in original]},
        )
        runtime.apply_model_pipeline_settings()


def test_legacy_package_without_model_pipeline_document_retains_current_rows(
    configuration_client,
    monkeypatch,
):
    runtime = configuration_client.app.state.runtime
    original = configuration_client.get("/api/config/model-pipelines").json()["settings"]
    package = configuration_client.post("/api/config/exports").content
    changed = [_editable_model_pipeline(item) for item in original]
    for setting in changed:
        setting["lpr_threshold"] = 0.58
    assert configuration_client.put(
        "/api/config/model-pipelines", json={"settings": changed}
    ).status_code == 200

    def remove_optional_document(entries, manifest):
        path = "config/model-pipelines.json"
        entries.pop(path)
        manifest["files"] = [
            item for item in manifest["files"] if item["path"] != path
        ]

    monkeypatch.setattr(
        runtime.import_export_service,
        "preflight_validator",
        lambda _documents: {
            "device": "cpu",
            "streams": [],
            "topology_id": "builtin-default-topology",
            "topology_revision": 1,
        },
    )
    try:
        preflight = configuration_client.post(
            "/api/config/imports/preflight",
            files={
                "file": (
                    "legacy.zip",
                    _rewrite_package(package, remove_optional_document),
                    "application/zip",
                )
            },
        )
        assert preflight.status_code == 200
        applied = configuration_client.post(
            f"/api/config/imports/{preflight.json()['token']}/apply",
            json={"confirm": True},
        )
        assert applied.status_code == 200
        retained = configuration_client.get(
            "/api/config/model-pipelines"
        ).json()["settings"]
        assert [item["lpr_threshold"] for item in retained] == [0.58] * 4
    finally:
        configuration_client.put(
            "/api/config/model-pipelines",
            json={"settings": [_editable_model_pipeline(item) for item in original]},
        )
        runtime.apply_model_pipeline_settings()


def _editable_model_pipeline(setting):
    excluded = {"revision", "updated_at"}
    return {key: value for key, value in setting.items() if key not in excluded}


def test_import_rejects_zip_path_traversal(configuration_client):
    malicious = io.BytesIO()
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("../outside.json", b"{}")
        archive.writestr("manifest.json", b"{}")
    response = configuration_client.post(
        "/api/config/imports/preflight",
        files={"file": ("malicious.zip", malicious.getvalue(), "application/zip")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CONFIG_PATH_TRAVERSAL"


def test_import_rejects_nested_unknown_json_fields(configuration_client, exported_package):
    path = "config/stream-sources.json"

    def mutate(entries, manifest):
        document = json.loads(entries[path])
        document["streams"][0]["unexpected"] = "must be rejected"
        entries[path] = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        _refresh_manifest_file(manifest, path, entries[path])

    response = configuration_client.post(
        "/api/config/imports/preflight",
        files={"file": ("unknown-field.zip", _rewrite_package(exported_package, mutate), "application/zip")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CONFIG_UNKNOWN_FIELD"


def test_import_rejects_digest_tampering(configuration_client, exported_package):
    def mutate(entries, _manifest):
        entries["config/whitelist.json"] += b" "

    response = configuration_client.post(
        "/api/config/imports/preflight",
        files={"file": ("tampered.zip", _rewrite_package(exported_package, mutate), "application/zip")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CONFIG_DIGEST_MISMATCH"


def test_import_rejects_newer_schema_and_zip_symlinks(configuration_client, exported_package):
    def newer(_entries, manifest):
        manifest["schema_version"] = 2

    newer_response = configuration_client.post(
        "/api/config/imports/preflight",
        files={"file": ("newer.zip", _rewrite_package(exported_package, newer), "application/zip")},
    )
    assert newer_response.status_code == 422
    assert newer_response.json()["error"]["code"] == "CONFIG_SCHEMA_TOO_NEW"

    malicious = io.BytesIO()
    with zipfile.ZipFile(malicious, "w") as archive:
        link = zipfile.ZipInfo("assets/maps/link.png")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../outside")
        archive.writestr("manifest.json", "{}")
    symlink_response = configuration_client.post(
        "/api/config/imports/preflight",
        files={"file": ("symlink.zip", malicious.getvalue(), "application/zip")},
    )
    assert symlink_response.status_code == 422
    assert symlink_response.json()["error"]["code"] == "CONFIG_SYMLINK_FORBIDDEN"


def test_no_parking_and_road_abnormal_scenes_run_on_independent_channels(
    configuration_client, monkeypatch
):
    class Result:
        def __init__(self, stream_id):
            self.stream_id = stream_id

        def as_dict(self):
            return {
                "stream_id": self.stream_id,
                "ok": True,
                "code": "OK",
                "message": "fake first frame",
                "elapsed_ms": 1,
                "width": 1920,
                "height": 1080,
            }

    runtime = configuration_client.app.state.runtime
    monkeypatch.setattr(
        runtime.activation_coordinator.probe_service,
        "probe",
        lambda stream_id, _url: Result(stream_id),
    )
    no_parking = configuration_client.get(
        "/api/config/scenes?scene_type=no_parking"
    ).json()[0]
    road_abnormal = configuration_client.get(
        "/api/config/scenes?scene_type=road_abnormal"
    ).json()[0]

    assert configuration_client.post(
        "/api/no-parking/start", json={"scene_id": no_parking["scene_id"]}
    ).status_code == 200
    assert configuration_client.post(
        "/api/road-abnormal/start", json={"scene_id": road_abnormal["scene_id"]}
    ).status_code == 200

    assert configuration_client.get("/api/no-parking/status").json()["running"] is True
    assert configuration_client.get("/api/road-abnormal/status").json()["running"] is True
    assert runtime.no_parking_video.status()["active_source"]["id"] == no_parking["camera_id"]
    assert runtime.road_abnormal_video.status()["active_source"]["id"] == road_abnormal["camera_id"]


def test_restart_restores_committed_scene_activation(tmp_path, monkeypatch):
    config = _configuration(tmp_path)
    with TestClient(create_app(config, start_video=False)) as first:
        runtime = first.app.state.runtime

        class Result:
            def __init__(self, stream_id):
                self.stream_id = stream_id

            def as_dict(self):
                return {
                    "stream_id": self.stream_id,
                    "ok": True,
                    "code": "OK",
                    "message": "fake first frame",
                    "elapsed_ms": 1,
                }

        monkeypatch.setattr(
            runtime.activation_coordinator.probe_service,
            "probe",
            lambda stream_id, _url: Result(stream_id),
        )
        scene = first.get("/api/config/scenes?scene_type=no_parking").json()[0]
        assert first.post(
            f"/api/config/scenes/{scene['scene_id']}/activate"
        ).status_code == 200
        camera_id = scene["camera_id"]

    with TestClient(create_app(config, start_video=False)) as restarted:
        runtime = restarted.app.state.runtime
        assert restarted.get("/api/no-parking/status").json()["running"] is True
        assert runtime.no_parking_video.status()["active_source"]["id"] == camera_id
