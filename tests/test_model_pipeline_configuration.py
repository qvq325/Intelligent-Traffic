from __future__ import annotations

import sqlite3
from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.configuration.camera_catalog import build_camera_catalog
from backend.configuration.repository import ConfigurationRepository, SchemaVersionError
from backend.configuration.schema import SCHEMA_VERSION


SCENE_KEYS = ("realtime", "traffic_map", "no_parking", "road_abnormal")


def _catalog():
    return build_camera_catalog({"camera-1": "Camera 1"})


def _repository(tmp_path) -> ConfigurationRepository:
    repository = ConfigurationRepository(tmp_path / "configuration.sqlite3")
    repository.initialize(_catalog(), builtin_baseline_version="test-v1")
    return repository


def _complete_setting(scene_key: str) -> dict[str, object]:
    return {
        "scene_key": scene_key,
        "preset": "legacy",
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
    }


def _complete_batch() -> dict[str, object]:
    return {"settings": [_complete_setting(scene_key) for scene_key in SCENE_KEYS]}


def _model_types():
    from backend.configuration import models

    return models.ModelPipelineConfiguration, models.ModelPipelineBatchUpdate


def _downgrade_to_version_1(repository: ConfigurationRepository) -> None:
    with repository.transaction() as connection:
        connection.execute("DROP TABLE IF EXISTS model_pipeline_setting")
        connection.execute(
            "UPDATE schema_metadata SET schema_version = 1 WHERE singleton_id = 1"
        )
        connection.execute("PRAGMA user_version = 1")


def _install_legacy_metadata_version(
    repository: ConfigurationRepository, schema_version: object
) -> None:
    metadata = repository.fetch_one(
        "SELECT * FROM schema_metadata WHERE singleton_id = 1"
    )
    with repository.transaction() as connection:
        connection.execute("DROP TABLE model_pipeline_setting")
        connection.execute("DROP TABLE schema_metadata")
        connection.execute(
            """
            CREATE TABLE schema_metadata (
                singleton_id INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton_id = 1),
                schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
                legacy_migration_completed INTEGER NOT NULL DEFAULT 0
                    CHECK (legacy_migration_completed IN (0, 1)),
                builtin_baseline_version TEXT NOT NULL,
                camera_catalog_fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO schema_metadata (
                singleton_id,
                schema_version,
                legacy_migration_completed,
                builtin_baseline_version,
                camera_catalog_fingerprint,
                created_at,
                updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?)
            """,
            (
                schema_version,
                metadata["legacy_migration_completed"],
                metadata["builtin_baseline_version"],
                metadata["camera_catalog_fingerprint"],
                metadata["created_at"],
                metadata["updated_at"],
            ),
        )
        connection.execute("PRAGMA user_version = 2")


def test_new_repository_seeds_exactly_four_legacy_model_pipeline_rows(tmp_path):
    repository = _repository(tmp_path)

    assert SCHEMA_VERSION == 2
    rows = repository.fetch_all(
        "SELECT * FROM model_pipeline_setting ORDER BY scene_key"
    )
    assert {row["scene_key"] for row in rows} == set(SCENE_KEYS)
    assert len(rows) == 4
    for row in rows:
        assert row["preset"] == "legacy"
        assert row["enabled"] == 0
        assert row["device_preference"] == "cpu"
        assert row["yolo_threshold"] == pytest.approx(0.5)
        assert row["lpr_threshold"] == pytest.approx(0.7)
        assert row["frame_interval"] == 5
        assert row["inference_size"] == 640
        assert row["parking_move_threshold"] == pytest.approx(0.03)
        assert row["mog_history"] == 500
        assert row["mog_variance_threshold"] == pytest.approx(25.0)
        assert row["mog_min_area"] == 150
        assert row["mog_min_duration"] == pytest.approx(2.0)
        assert row["mog_max_duration"] == pytest.approx(5.0)
        assert row["mog_warmup_frames"] == 50
        assert row["revision"] == 1
        assert row["updated_at"]


@pytest.mark.parametrize(
    ("column", "valid_value"),
    [
        ("frame_interval", 7),
        ("inference_size", 896),
        ("mog_history", 600),
        ("mog_min_area", 200),
        ("mog_warmup_frames", 75),
        ("revision", 2),
    ],
)
def test_model_pipeline_integer_columns_reject_real_storage(
    tmp_path, column, valid_value
):
    repository = _repository(tmp_path)
    statement = (
        f"UPDATE model_pipeline_setting SET {column} = ? "
        "WHERE scene_key = 'realtime'"
    )
    with repository.transaction() as connection:
        connection.execute(statement, (valid_value,))

    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        with repository.transaction() as connection:
            connection.execute(statement, (valid_value + 0.5,))

    stored = repository.fetch_one(
        f"SELECT {column}, typeof({column}) FROM model_pipeline_setting "
        "WHERE scene_key = 'realtime'"
    )
    assert tuple(stored) == (valid_value, "integer")


def test_reinitialize_preserves_existing_model_pipeline_rows(tmp_path):
    repository = _repository(tmp_path)
    with repository.transaction() as connection:
        connection.execute(
            """
            UPDATE model_pipeline_setting
            SET preset = 'trained', inference_size = 896, revision = 7
            WHERE scene_key = 'realtime'
            """
        )

    repository.initialize(_catalog(), builtin_baseline_version="ignored")

    rows = repository.fetch_all("SELECT * FROM model_pipeline_setting")
    assert len(rows) == 4
    realtime = next(row for row in rows if row["scene_key"] == "realtime")
    assert realtime["preset"] == "trained"
    assert realtime["inference_size"] == 896
    assert realtime["revision"] == 7


def test_version_1_repository_migrates_without_losing_rows(tmp_path):
    repository = _repository(tmp_path)
    with repository.transaction() as connection:
        connection.execute(
            """
            INSERT INTO stream_source (stream_id, name, rtsp_url)
            VALUES ('kept-stream', 'Kept stream', 'rtsp://example.test/live')
            """
        )
        connection.execute(
            """
            UPDATE detection_settings
            SET enabled = 1,
                yolo_threshold = 0.42,
                lpr_threshold = 0.83,
                frame_interval = 7,
                device_preference = 'cuda:0'
            WHERE singleton_id = 1
            """
        )
    _downgrade_to_version_1(repository)

    repository.initialize(_catalog(), builtin_baseline_version="ignored")

    assert repository.fetch_one(
        "SELECT stream_id FROM stream_source WHERE stream_id = 'kept-stream'"
    )[0] == "kept-stream"
    assert repository.fetch_one("PRAGMA user_version")[0] == 2
    assert repository.fetch_one(
        "SELECT schema_version FROM schema_metadata WHERE singleton_id = 1"
    )[0] == 2
    rows = repository.fetch_all("SELECT * FROM model_pipeline_setting")
    assert len(rows) == 4
    for row in rows:
        assert row["preset"] == "legacy"
        assert row["enabled"] == 1
        assert row["device_preference"] == "cuda:0"
        assert row["yolo_threshold"] == pytest.approx(0.42)
        assert row["lpr_threshold"] == pytest.approx(0.83)
        assert row["frame_interval"] == 7


def test_version_1_migration_rolls_back_invalid_v2_seed(tmp_path):
    repository = _repository(tmp_path)
    invalid_device_preference = "x" * 81
    with repository.transaction() as connection:
        connection.execute(
            """
            UPDATE detection_settings
            SET device_preference = ?
            WHERE singleton_id = 1
            """,
            (invalid_device_preference,),
        )
    _downgrade_to_version_1(repository)

    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        repository.initialize(_catalog(), builtin_baseline_version="ignored")

    assert repository.fetch_one("PRAGMA user_version")[0] == 1
    assert repository.fetch_one(
        "SELECT schema_version FROM schema_metadata WHERE singleton_id = 1"
    )[0] == 1
    assert repository.fetch_one(
        "SELECT device_preference FROM detection_settings WHERE singleton_id = 1"
    )[0] == invalid_device_preference
    assert repository.fetch_one(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'model_pipeline_setting'
        """
    ) is None


def test_repository_rejects_future_schema_before_applying_changes(tmp_path):
    repository = _repository(tmp_path)
    with repository.transaction() as connection:
        connection.execute("DROP TABLE IF EXISTS model_pipeline_setting")
        connection.execute(
            "UPDATE schema_metadata SET schema_version = 3 WHERE singleton_id = 1"
        )
        connection.execute("PRAGMA user_version = 3")

    with pytest.raises(SchemaVersionError, match="schema version"):
        repository.initialize(_catalog())

    assert repository.fetch_one(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'model_pipeline_setting'
        """
    ) is None


@pytest.mark.parametrize("invalid_version", [2.5, "2oops"])
def test_new_schema_metadata_rejects_non_integer_versions(
    tmp_path, invalid_version
):
    repository = _repository(tmp_path)

    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        with repository.transaction() as connection:
            connection.execute(
                """
                UPDATE schema_metadata SET schema_version = ?
                WHERE singleton_id = 1
                """,
                (invalid_version,),
            )

    assert repository.fetch_one(
        "SELECT schema_version FROM schema_metadata WHERE singleton_id = 1"
    )[0] == 2


@pytest.mark.parametrize(
    ("invalid_version", "storage_type"),
    [(2.5, "real"), ("2oops", "text")],
)
def test_repository_rejects_non_integer_metadata_before_writes(
    tmp_path, invalid_version, storage_type
):
    repository = _repository(tmp_path)
    _install_legacy_metadata_version(repository, invalid_version)

    with pytest.raises(SchemaVersionError, match="schema version"):
        repository.initialize(_catalog(), builtin_baseline_version="ignored")

    metadata = repository.fetch_one(
        """
        SELECT schema_version, typeof(schema_version)
        FROM schema_metadata WHERE singleton_id = 1
        """
    )
    assert metadata[0] == invalid_version
    assert metadata[1] == storage_type
    assert repository.fetch_one("PRAGMA user_version")[0] == 2
    assert repository.fetch_one(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'model_pipeline_setting'
        """
    ) is None


def test_complete_four_scene_model_pipeline_payload_is_valid():
    configuration_type, batch_type = _model_types()

    batch = batch_type.model_validate(_complete_batch())

    assert all(isinstance(setting, configuration_type) for setting in batch.settings)
    assert {setting.scene_key for setting in batch.settings} == set(SCENE_KEYS)


@pytest.mark.parametrize("location", ["batch", "setting"])
def test_model_pipeline_payload_rejects_unknown_fields(location):
    _, batch_type = _model_types()
    payload = _complete_batch()
    if location == "batch":
        payload["unknown"] = True
    else:
        payload["settings"][0]["unknown"] = True

    with pytest.raises(ValidationError):
        batch_type.model_validate(payload)


def test_model_pipeline_batch_rejects_duplicate_and_missing_scenes():
    _, batch_type = _model_types()
    duplicate = _complete_batch()
    duplicate["settings"][-1]["scene_key"] = "realtime"
    missing = _complete_batch()
    missing["settings"].pop()

    with pytest.raises(ValidationError, match="scene"):
        batch_type.model_validate(duplicate)
    with pytest.raises(ValidationError):
        batch_type.model_validate(missing)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preset", "custom"),
        ("device_preference", ""),
        ("yolo_threshold", 0.049),
        ("yolo_threshold", 1.001),
        ("lpr_threshold", 0.049),
        ("lpr_threshold", 1.001),
        ("frame_interval", 0),
        ("frame_interval", 61),
        ("inference_size", 159),
        ("inference_size", 2049),
        ("parking_move_threshold", 0.0),
        ("parking_move_threshold", 1.001),
        ("mog_history", 9),
        ("mog_history", 5001),
        ("mog_variance_threshold", 0.99),
        ("mog_variance_threshold", 255.01),
        ("mog_min_area", 0),
        ("mog_min_area", 1_000_001),
        ("mog_min_duration", 0.09),
        ("mog_min_duration", 300.01),
        ("mog_max_duration", 0.09),
        ("mog_max_duration", 3600.01),
        ("mog_warmup_frames", -1),
        ("mog_warmup_frames", 5001),
    ],
)
def test_model_pipeline_configuration_rejects_invalid_values(field, value):
    configuration_type, _ = _model_types()
    payload = _complete_setting("realtime")
    payload[field] = value

    with pytest.raises(ValidationError):
        configuration_type.model_validate(payload)


def test_model_pipeline_configuration_rejects_inverted_mog_duration_range():
    configuration_type, _ = _model_types()
    payload = _complete_setting("road_abnormal")
    payload["mog_min_duration"] = 10.0
    payload["mog_max_duration"] = 5.0

    with pytest.raises(ValidationError, match="mog_max_duration"):
        configuration_type.model_validate(payload)


def test_model_pipeline_configuration_applies_advanced_defaults():
    configuration_type, _ = _model_types()
    payload = deepcopy(_complete_setting("no_parking"))
    for field in (
        "preset",
        "inference_size",
        "parking_move_threshold",
        "mog_history",
        "mog_variance_threshold",
        "mog_min_area",
        "mog_min_duration",
        "mog_max_duration",
        "mog_warmup_frames",
    ):
        payload.pop(field)

    setting = configuration_type.model_validate(payload)

    assert setting.preset == "legacy"
    assert setting.inference_size == 640
    assert setting.parking_move_threshold == pytest.approx(0.03)
    assert setting.mog_history == 500
    assert setting.mog_variance_threshold == pytest.approx(25.0)
    assert setting.mog_min_area == 150
    assert setting.mog_min_duration == pytest.approx(2.0)
    assert setting.mog_max_duration == pytest.approx(5.0)
    assert setting.mog_warmup_frames == 50
