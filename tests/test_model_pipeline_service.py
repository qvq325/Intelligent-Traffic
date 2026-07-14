from __future__ import annotations

import importlib
import json
import os
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Event, Lock, Thread
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import AppConfig, PROJECT_DIR, STREAM_SOURCES
from backend.configuration import ConfigurationRepository, build_camera_catalog
from backend.configuration.errors import ConfigurationError
from backend.configuration.service import ConfigurationService


SCENE_KEYS = ("realtime", "traffic_map", "no_parking", "road_abnormal")
MODEL_FIELDS = (
    "scene_key",
    "preset",
    "enabled",
    "device_preference",
    "yolo_threshold",
    "lpr_threshold",
    "frame_interval",
    "inference_size",
    "parking_move_threshold",
    "mog_history",
    "mog_variance_threshold",
    "mog_min_area",
    "mog_min_duration",
    "mog_max_duration",
    "mog_warmup_frames",
)


class StubRegistry:
    def __init__(self, *, rejected_scene: str | None = None) -> None:
        self.rejected_scene = rejected_scene

    def list_presets(self) -> list[dict]:
        return [
            {
                "id": "legacy",
                "label": "Existing models",
                "available": True,
                "capabilities": {"plate_recognition": "pose"},
            },
            {
                "id": "trained",
                "label": "Trained models",
                "available": True,
                "capabilities": {"plate_recognition": "box"},
            },
        ]

    def list_devices(self) -> list[dict]:
        return [
            {"id": "cpu", "label": "CPU", "available": True},
            {"id": "cuda:0", "label": "Test GPU", "available": True},
        ]

    def resolve(self, setting: dict) -> SimpleNamespace:
        if setting["scene_key"] == self.rejected_scene:
            raise ConfigurationError(
                "MODEL_PIPELINE_UNAVAILABLE",
                "The requested model pipeline is unavailable",
                details=[{"scene_key": setting["scene_key"], "reason": "task_mismatch"}],
            )
        return SimpleNamespace(
            vehicle_model_path=Path("C:/private/trained-vehicle.pt"),
            plate_model_path=Path("C:/private/trained-plate.pt"),
            inspector={"sha256": "binary-secret", "size_bytes": 999},
        )


def _model_pipeline_module():
    return importlib.import_module("backend.model_pipelines")


def _trusted_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    trained = root / "\u8bad\u7ec3\u540e\u6a21\u578b"
    trained.mkdir(parents=True)
    (root / "yolo11m.pt").write_bytes(b"legacy")
    (trained / "yolo26x.pt").write_bytes(b"trained-vehicle")
    (trained / "license_plate_best.pt").write_bytes(b"trained-plate")
    return root


def _replace_weight_atomically(
    path: Path,
    content: bytes,
) -> tuple[os.stat_result, os.stat_result]:
    before = path.stat()
    replacement = path.with_name(f".{path.name}.replacement")
    replacement.write_bytes(content)
    os.utime(
        replacement,
        ns=(before.st_atime_ns, before.st_mtime_ns),
    )
    os.replace(replacement, path)
    return before, path.stat()


def _repository(tmp_path: Path) -> ConfigurationRepository:
    repository = ConfigurationRepository(tmp_path / "config.sqlite3")
    repository.initialize(build_camera_catalog({"camera-1": "Camera 1"}))
    return repository


def _service(tmp_path: Path, registry: StubRegistry | None = None):
    repository = _repository(tmp_path)
    service = ConfigurationService(
        repository,
        object(),
        model_pipeline_registry=registry or StubRegistry(),
    )
    return service, repository


def _editable(settings: list[dict]) -> list[dict]:
    return [{field: row[field] for field in MODEL_FIELDS} for row in settings]


def _app_config(root: Path) -> AppConfig:
    return AppConfig(
        project_dir=PROJECT_DIR,
        frontend_dir=PROJECT_DIR / "frontend",
        whitelist_file=PROJECT_DIR / "whitelist.json",
        traffic_map_file=PROJECT_DIR / "traffic_map.json",
        fallback_map_image=PROJECT_DIR / "sandpan" / "\u6c99\u76d8\u5e73\u9762\u56fe2.png",
        upload_dir=root / "uploads",
        map_upload_dir=root / "maps",
        stream_sources=dict(STREAM_SOURCES),
        configuration_dir=root / "configuration",
    )


def test_registry_exposes_fixed_path_free_preset_metadata(tmp_path):
    model_pipelines = _model_pipeline_module()
    root = _trusted_root(tmp_path)
    inspected: list[Path] = []

    def inspect(path: Path) -> dict:
        inspected.append(path)
        return {"task": "detect", "sha256": "must-not-leak", "size_bytes": 123}

    registry = model_pipelines.ModelPipelineRegistry(
        root,
        inspector=inspect,
        device_provider=lambda: [("cpu", "CPU"), ("cuda:0", "Test GPU")],
    )

    presets = registry.list_presets()

    assert [item["id"] for item in presets] == ["legacy", "trained"]
    assert all(item["available"] is True for item in presets)
    assert all(item["label"] and item["capabilities"] for item in presets)
    serialized = json.dumps(presets, ensure_ascii=False)
    assert str(root.resolve()) not in serialized
    assert "yolo11m.pt" not in serialized
    assert "yolo26x.pt" not in serialized
    assert "license_plate_best.pt" not in serialized
    assert "sha256" not in serialized
    assert {path.resolve() for path in inspected} == {
        (root / "yolo11m.pt").resolve(),
        (root / "\u8bad\u7ec3\u540e\u6a21\u578b" / "yolo26x.pt").resolve(),
        (root / "\u8bad\u7ec3\u540e\u6a21\u578b" / "license_plate_best.pt").resolve(),
    }


def test_registry_resolves_only_trusted_absolute_paths_and_frozen_options(tmp_path):
    model_pipelines = _model_pipeline_module()
    root = _trusted_root(tmp_path)
    registry = model_pipelines.ModelPipelineRegistry(
        root,
        inspector=lambda _path: {"task": "detect"},
        device_provider=lambda: [("cpu", "CPU")],
    )
    setting = {
        "scene_key": "realtime",
        "preset": "trained",
        "enabled": True,
        "device_preference": "cpu",
        "yolo_threshold": 0.45,
        "lpr_threshold": 0.7,
        "frame_interval": 5,
        "inference_size": 640,
        "parking_move_threshold": 0.03,
        "mog_history": 500,
        "mog_variance_threshold": 25.0,
        "mog_min_area": 150,
        "mog_min_duration": 2.0,
        "mog_max_duration": 5.0,
        "mog_warmup_frames": 50,
        "revision": 3,
    }

    options = registry.resolve(setting)

    assert options.vehicle_model_path == (
        root / "\u8bad\u7ec3\u540e\u6a21\u578b" / "yolo26x.pt"
    ).resolve()
    assert options.plate_model_path == (
        root / "\u8bad\u7ec3\u540e\u6a21\u578b" / "license_plate_best.pt"
    ).resolve()
    assert options.vehicle_model_path.is_absolute()
    assert options.plate_model_path.is_absolute()
    with pytest.raises(FrozenInstanceError):
        options.preset = "legacy"


@pytest.mark.parametrize("failure", ["missing", "wrong_task"])
def test_registry_rejects_missing_or_wrong_task_weights(tmp_path, failure):
    model_pipelines = _model_pipeline_module()
    root = _trusted_root(tmp_path)
    plate_path = root / "\u8bad\u7ec3\u540e\u6a21\u578b" / "license_plate_best.pt"
    if failure == "missing":
        plate_path.unlink()

    def inspect(path: Path) -> dict:
        task = "segment" if failure == "wrong_task" and path == plate_path.resolve() else "detect"
        return {"task": task, "raw": {"filename": path.name, "sha256": "secret"}}

    registry = model_pipelines.ModelPipelineRegistry(
        root,
        inspector=inspect,
        device_provider=lambda: [("cpu", "CPU")],
    )

    with pytest.raises(ConfigurationError) as caught:
        registry.resolve({"scene_key": "realtime", "preset": "trained", "device_preference": "cpu"})

    assert caught.value.code == "MODEL_PIPELINE_UNAVAILABLE"
    serialized = json.dumps(caught.value.payload(), ensure_ascii=False)
    assert str(root.resolve()) not in serialized
    assert "license_plate_best.pt" not in serialized
    assert "sha256" not in serialized


def test_registry_reinspects_weight_replaced_at_same_path_before_resolve(tmp_path):
    model_pipelines = _model_pipeline_module()
    root = _trusted_root(tmp_path)
    vehicle_path = root / "\u8bad\u7ec3\u540e\u6a21\u578b" / "yolo26x.pt"
    inspected: list[Path] = []

    def inspect(path: Path) -> dict:
        inspected.append(path)
        task = "segment" if path.read_bytes().startswith(b"replacement") else "detect"
        return {"task": task}

    registry = model_pipelines.ModelPipelineRegistry(
        root,
        inspector=inspect,
        device_provider=lambda: [("cpu", "CPU")],
    )
    setting = {
        "scene_key": "realtime",
        "preset": "trained",
        "device_preference": "cpu",
    }
    registry.resolve(setting)

    vehicle_path.write_bytes(b"replacement-with-segment-task")

    with pytest.raises(ConfigurationError) as caught:
        registry.resolve(setting)

    assert caught.value.code == "MODEL_PIPELINE_UNAVAILABLE"
    assert caught.value.details[0]["reason"] == "task_mismatch"
    assert inspected.count(vehicle_path.resolve()) == 2


def test_registry_refreshes_preset_availability_after_weight_replacement(tmp_path):
    model_pipelines = _model_pipeline_module()
    root = _trusted_root(tmp_path)
    plate_path = root / "\u8bad\u7ec3\u540e\u6a21\u578b" / "license_plate_best.pt"
    inspected: list[Path] = []

    def inspect(path: Path) -> dict:
        inspected.append(path)
        task = "segment" if path.read_bytes().startswith(b"replacement") else "detect"
        return {"task": task}

    registry = model_pipelines.ModelPipelineRegistry(
        root,
        inspector=inspect,
        device_provider=lambda: [("cpu", "CPU")],
    )
    assert {item["id"]: item["available"] for item in registry.list_presets()} == {
        "legacy": True,
        "trained": True,
    }

    plate_path.write_bytes(b"replacement-with-segment-task")

    assert {item["id"]: item["available"] for item in registry.list_presets()} == {
        "legacy": True,
        "trained": False,
    }
    assert inspected.count(plate_path.resolve()) == 2


@pytest.mark.parametrize("consumer", ["resolve", "list_presets"])
def test_registry_reinspects_same_size_atomic_replacement_with_preserved_mtime(
    tmp_path,
    consumer,
):
    model_pipelines = _model_pipeline_module()
    root = _trusted_root(tmp_path)
    vehicle_path = root / "\u8bad\u7ec3\u540e\u6a21\u578b" / "yolo26x.pt"
    inspected: list[Path] = []

    def inspect(path: Path) -> dict:
        inspected.append(path)
        task = "segment" if path.read_bytes() == b"wrong-task-data" else "detect"
        return {"task": task}

    registry = model_pipelines.ModelPipelineRegistry(
        root,
        inspector=inspect,
        device_provider=lambda: [("cpu", "CPU")],
    )
    setting = {
        "scene_key": "realtime",
        "preset": "trained",
        "device_preference": "cpu",
    }
    if consumer == "resolve":
        registry.resolve(setting)
    else:
        assert next(
            item for item in registry.list_presets() if item["id"] == "trained"
        )["available"] is True

    before, after = _replace_weight_atomically(vehicle_path, b"wrong-task-data")

    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    if before.st_ino and after.st_ino:
        assert (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
    if consumer == "resolve":
        with pytest.raises(ConfigurationError) as caught:
            registry.resolve(setting)
        assert caught.value.code == "MODEL_PIPELINE_UNAVAILABLE"
    else:
        assert next(
            item for item in registry.list_presets() if item["id"] == "trained"
        )["available"] is False
    assert inspected.count(vehicle_path.resolve()) == 2


def test_registry_retries_when_weight_is_atomically_replaced_during_inspection(
    tmp_path,
):
    model_pipelines = _model_pipeline_module()
    root = _trusted_root(tmp_path)
    vehicle_path = (root / "\u8bad\u7ec3\u540e\u6a21\u578b" / "yolo26x.pt").resolve()
    inspected_contents: list[bytes] = []
    replaced = False

    def inspect(path: Path) -> dict:
        nonlocal replaced
        content = path.read_bytes()
        if path == vehicle_path:
            inspected_contents.append(content)
            if not replaced:
                _replace_weight_atomically(path, b"wrong-task-data")
                replaced = True
        task = "segment" if content == b"wrong-task-data" else "detect"
        return {"task": task}

    registry = model_pipelines.ModelPipelineRegistry(
        root,
        inspector=inspect,
        device_provider=lambda: [("cpu", "CPU")],
    )

    with pytest.raises(ConfigurationError) as caught:
        registry.resolve(
            {
                "scene_key": "realtime",
                "preset": "trained",
                "device_preference": "cpu",
            }
        )

    assert caught.value.code == "MODEL_PIPELINE_UNAVAILABLE"
    assert inspected_contents == [b"trained-vehicle", b"wrong-task-data"]


def test_registry_rejects_weight_that_changes_during_every_inspection(tmp_path):
    model_pipelines = _model_pipeline_module()
    root = _trusted_root(tmp_path)
    vehicle_path = (root / "\u8bad\u7ec3\u540e\u6a21\u578b" / "yolo26x.pt").resolve()
    replacements = iter((b"replacement-one", b"replacement-two"))

    def inspect(path: Path) -> dict:
        if path == vehicle_path:
            _replace_weight_atomically(path, next(replacements))
        return {"task": "detect"}

    registry = model_pipelines.ModelPipelineRegistry(
        root,
        inspector=inspect,
        device_provider=lambda: [("cpu", "CPU")],
    )

    with pytest.raises(ConfigurationError) as caught:
        registry.resolve(
            {
                "scene_key": "realtime",
                "preset": "trained",
                "device_preference": "cpu",
            }
        )

    assert caught.value.code == "MODEL_PIPELINE_UNAVAILABLE"
    assert caught.value.details[0]["reason"] == "inspection_unstable"
    serialized = json.dumps(caught.value.payload(), ensure_ascii=False)
    assert str(root.resolve()) not in serialized
    assert "yolo26x.pt" not in serialized


def test_registry_can_explicitly_invalidate_observably_unchanged_weight(tmp_path):
    model_pipelines = _model_pipeline_module()
    root = _trusted_root(tmp_path)
    vehicle_path = (root / "\u8bad\u7ec3\u540e\u6a21\u578b" / "yolo26x.pt").resolve()
    vehicle_task = "detect"

    def inspect(path: Path) -> dict:
        return {"task": vehicle_task if path == vehicle_path else "detect"}

    registry = model_pipelines.ModelPipelineRegistry(
        root,
        inspector=inspect,
        device_provider=lambda: [("cpu", "CPU")],
    )
    setting = {
        "scene_key": "realtime",
        "preset": "trained",
        "device_preference": "cpu",
    }
    registry.resolve(setting)
    vehicle_task = "segment"
    registry.resolve(setting)

    registry.invalidate_cache(vehicle_path)

    with pytest.raises(ConfigurationError) as caught:
        registry.resolve(setting)
    assert caught.value.code == "MODEL_PIPELINE_UNAVAILABLE"


def test_registry_invalidation_cannot_clear_cache_between_lookup_and_read(tmp_path):
    model_pipelines = _model_pipeline_module()
    root = _trusted_root(tmp_path)
    registry = model_pipelines.ModelPipelineRegistry(
        root,
        inspector=lambda _path: {"task": "detect"},
        device_provider=lambda: [("cpu", "CPU")],
    )
    setting = {
        "scene_key": "realtime",
        "preset": "trained",
        "device_preference": "cpu",
    }
    registry.resolve(setting)
    membership_checked = Event()
    resume_lookup = Event()
    cache_cleared = Event()
    invalidation_started = Event()

    class PausingCache(dict):
        paused = False

        def __contains__(self, key):
            present = super().__contains__(key)
            if not self.paused:
                self.paused = True
                membership_checked.set()
                if not resume_lookup.wait(timeout=3):
                    raise TimeoutError("cache lookup was not resumed")
            return present

        def clear(self):
            super().clear()
            cache_cleared.set()

    registry._task_cache = PausingCache(registry._task_cache)
    results = []
    errors: list[Exception] = []

    def resolve_cached() -> None:
        try:
            results.append(registry.resolve(setting))
        except Exception as exc:
            errors.append(exc)

    def invalidate() -> None:
        invalidation_started.set()
        registry.invalidate_cache()

    resolver = Thread(target=resolve_cached, daemon=True)
    invalidator = Thread(target=invalidate, daemon=True)
    resolver.start()
    assert membership_checked.wait(timeout=3)
    invalidator.start()
    assert invalidation_started.wait(timeout=3)
    cache_cleared.wait(timeout=1)
    resume_lookup.set()
    resolver.join(timeout=3)
    invalidator.join(timeout=3)

    assert not resolver.is_alive()
    assert not invalidator.is_alive()
    assert errors == []
    assert len(results) == 1
    assert registry.resolve(setting).vehicle_model_path.is_file()


def test_registry_concurrent_first_resolves_inspect_each_weight_once(tmp_path):
    model_pipelines = _model_pipeline_module()
    root = _trusted_root(tmp_path)
    vehicle_path = (root / "\u8bad\u7ec3\u540e\u6a21\u578b" / "yolo26x.pt").resolve()
    plate_path = (
        root / "\u8bad\u7ec3\u540e\u6a21\u578b" / "license_plate_best.pt"
    ).resolve()
    first_inspection_started = Event()
    second_resolve_attempted = Event()
    second_vehicle_inspection = Event()
    release_first_inspection = Event()
    calls_lock = Lock()
    inspected: list[Path] = []

    def inspect(path: Path) -> dict:
        with calls_lock:
            inspected.append(path)
            vehicle_calls = inspected.count(vehicle_path)
        if path == vehicle_path and vehicle_calls == 1:
            first_inspection_started.set()
            if not release_first_inspection.wait(timeout=3):
                raise TimeoutError("first inspection was not released")
        elif path == vehicle_path:
            second_vehicle_inspection.set()
        return {"task": "detect"}

    registry = model_pipelines.ModelPipelineRegistry(
        root,
        inspector=inspect,
        device_provider=lambda: [("cpu", "CPU")],
    )
    setting = {
        "scene_key": "realtime",
        "preset": "trained",
        "device_preference": "cpu",
    }
    results = []
    errors: list[Exception] = []

    def resolve(first: bool) -> None:
        if not first:
            second_resolve_attempted.set()
        try:
            results.append(registry.resolve(setting))
        except Exception as exc:
            errors.append(exc)

    first = Thread(target=resolve, args=(True,), daemon=True)
    second = Thread(target=resolve, args=(False,), daemon=True)
    first.start()
    assert first_inspection_started.wait(timeout=3)
    second.start()
    assert second_resolve_attempted.wait(timeout=3)
    second_vehicle_inspection.wait(timeout=1)
    release_first_inspection.set()
    first.join(timeout=3)
    second.join(timeout=3)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert len(results) == 2
    assert inspected.count(vehicle_path) == 1
    assert inspected.count(plate_path) == 1


def test_registry_list_devices_falls_back_to_cpu_when_provider_raises(tmp_path):
    model_pipelines = _model_pipeline_module()

    def unavailable_devices():
        raise RuntimeError("private device discovery failure")

    registry = model_pipelines.ModelPipelineRegistry(
        tmp_path,
        inspector=lambda _path: {"task": "detect"},
        device_provider=unavailable_devices,
    )

    assert registry.list_devices() == [
        {"id": "cpu", "label": "CPU", "available": True}
    ]


def test_registry_resolve_maps_device_provider_failure_without_leaking_it(tmp_path):
    model_pipelines = _model_pipeline_module()

    def unavailable_devices():
        raise RuntimeError("private device discovery failure")

    registry = model_pipelines.ModelPipelineRegistry(
        tmp_path,
        inspector=lambda _path: {"task": "detect"},
        device_provider=unavailable_devices,
    )

    with pytest.raises(ConfigurationError) as caught:
        registry.resolve(
            {
                "scene_key": "realtime",
                "preset": "trained",
                "device_preference": "cuda:0",
            }
        )

    assert caught.value.code == "MODEL_PIPELINE_UNAVAILABLE"
    serialized = json.dumps(caught.value.payload(), ensure_ascii=False)
    assert "private device discovery failure" not in serialized


def test_registry_list_devices_deduplicates_and_ignores_empty_values(tmp_path):
    model_pipelines = _model_pipeline_module()
    registry = model_pipelines.ModelPipelineRegistry(
        tmp_path,
        inspector=lambda _path: {"task": "detect"},
        device_provider=lambda: [
            ("", "Empty"),
            {"id": None, "label": "Missing"},
            (None, "Also missing"),
            ("cpu", "Duplicate CPU"),
            {"id": "cuda:0", "label": "Test GPU"},
            ("cuda:0", "Duplicate GPU"),
            " ",
            None,
        ],
    )

    assert registry.list_devices() == [
        {"id": "cpu", "label": "CPU", "available": True},
        {"id": "cuda:0", "label": "Test GPU", "available": True},
    ]


def test_service_reads_rows_in_fixed_scene_order(tmp_path):
    service, repository = _service(tmp_path)
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE model_pipeline_setting SET revision = 9 WHERE scene_key = 'road_abnormal'"
        )

    payload = service.model_pipeline_settings()

    assert set(payload) == {"presets", "devices", "settings"}
    assert [row["scene_key"] for row in payload["settings"]] == list(SCENE_KEYS)
    assert all(type(row["enabled"]) is bool for row in payload["settings"])
    assert payload["settings"][-1]["revision"] == 9


def test_service_validates_all_rows_before_opening_a_transaction(tmp_path, monkeypatch):
    service, repository = _service(
        tmp_path,
        StubRegistry(rejected_scene="road_abnormal"),
    )
    settings = _editable(service.model_pipeline_settings()["settings"])
    entered_transactions = 0
    original_transaction = repository.transaction

    @contextmanager
    def counted_transaction(*args, **kwargs):
        nonlocal entered_transactions
        entered_transactions += 1
        with original_transaction(*args, **kwargs) as connection:
            yield connection

    monkeypatch.setattr(repository, "transaction", counted_transaction)

    with pytest.raises(ConfigurationError) as caught:
        service.update_model_pipeline_settings(settings)

    assert caught.value.code == "MODEL_PIPELINE_UNAVAILABLE"
    assert entered_transactions == 0


def test_service_updates_changed_rows_once_and_redacts_audit_details(tmp_path):
    service, repository = _service(tmp_path)
    before = service.model_pipeline_settings()["settings"]
    settings = _editable(before)
    settings[0]["preset"] = "trained"
    settings[0]["device_preference"] = "cuda:0"
    settings[0]["inference_size"] = 960
    settings[2]["enabled"] = not settings[2]["enabled"]

    result = service.update_model_pipeline_settings(settings)

    after = result["settings"]
    assert [row["scene_key"] for row in after] == list(SCENE_KEYS)
    assert after[0]["revision"] == before[0]["revision"] + 1
    assert after[2]["revision"] == before[2]["revision"] + 1
    assert after[1]["revision"] == before[1]["revision"]
    assert after[3]["revision"] == before[3]["revision"]
    audits = repository.fetch_all(
        "SELECT target_id, summary FROM audit_log "
        "WHERE operation_type = 'update_model_pipeline_setting' ORDER BY target_id"
    )
    changed_scene_keys = {"realtime", "no_parking"}
    assert len(audits) == len(changed_scene_keys)
    assert [row["target_id"] for row in audits] == sorted(changed_scene_keys)
    for row in audits:
        summary = json.loads(row["summary"])
        assert set(summary) == set(MODEL_FIELDS)
        serialized = json.dumps(summary, ensure_ascii=False)
        assert "private" not in serialized
        assert ".pt" not in serialized
        assert "sha256" not in serialized
        assert "size_bytes" not in serialized
        assert "inspector" not in serialized

    service.update_model_pipeline_settings(_editable(after))

    assert repository.fetch_one(
        "SELECT COUNT(*) AS count FROM audit_log "
        "WHERE operation_type = 'update_model_pipeline_setting'"
    )["count"] == len(changed_scene_keys)


def test_service_rolls_back_all_rows_and_audits_when_one_update_fails(tmp_path):
    service, repository = _service(tmp_path)
    before = service.model_pipeline_settings()["settings"]
    settings = _editable(before)
    settings[0]["inference_size"] = 768
    settings[1]["inference_size"] = 896
    with repository.transaction() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_traffic_map_pipeline_update
            BEFORE UPDATE ON model_pipeline_setting
            WHEN NEW.scene_key = 'traffic_map'
            BEGIN
                SELECT RAISE(ABORT, 'injected update failure');
            END
            """
        )

    with pytest.raises(Exception, match="injected update failure"):
        service.update_model_pipeline_settings(settings)

    after = service.model_pipeline_settings()["settings"]
    assert after == before
    assert repository.fetch_one(
        "SELECT COUNT(*) AS count FROM audit_log "
        "WHERE operation_type = 'update_model_pipeline_setting'"
    )["count"] == 0


def test_model_pipeline_get_and_put_api_contracts(tmp_path, monkeypatch):
    app = create_app(_app_config(tmp_path), start_video=False)
    with TestClient(app) as client:
        runtime = client.app.state.runtime
        service = runtime.configuration_service
        service.model_pipeline_registry = StubRegistry()
        runtime_applications = []
        monkeypatch.setattr(
            runtime,
            "apply_model_pipeline_settings",
            lambda: runtime_applications.append("applied"),
        )

        fetched = client.get("/api/config/model-pipelines")

        assert fetched.status_code == 200
        body = fetched.json()
        assert set(body) == {"presets", "devices", "settings"}
        assert [row["scene_key"] for row in body["settings"]] == list(SCENE_KEYS)
        assert all("revision" in row and "updated_at" in row for row in body["settings"])
        update = _editable(body["settings"])
        update[1]["preset"] = "trained"
        update[1]["inference_size"] = 832

        saved = client.put(
            "/api/config/model-pipelines",
            json={"settings": update},
        )

        assert saved.status_code == 200
        assert runtime_applications == ["applied"]
        saved_body = saved.json()
        assert set(saved_body) == {"presets", "devices", "settings"}
        assert saved_body["settings"][1]["preset"] == "trained"
        assert saved_body["settings"][1]["inference_size"] == 832
        assert saved_body["settings"][1]["revision"] == body["settings"][1]["revision"] + 1


def test_model_pipeline_put_maps_unavailable_registry_errors(tmp_path):
    app = create_app(_app_config(tmp_path), start_video=False)
    with TestClient(app) as client:
        service = client.app.state.runtime.configuration_service
        service.model_pipeline_registry = StubRegistry(rejected_scene="road_abnormal")
        body = client.get("/api/config/model-pipelines").json()

        response = client.put(
            "/api/config/model-pipelines",
            json={"settings": _editable(body["settings"])},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "MODEL_PIPELINE_UNAVAILABLE"
        assert response.json()["error"]["rollback"] == "not_required"
