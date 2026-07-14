from __future__ import annotations

import sqlite3

import pytest

from backend.configuration import (
    CameraCatalogMismatchError,
    ConfigurationRepository,
    SCHEMA_TABLES,
    SCHEMA_VERSION,
    build_camera_catalog,
)


def _catalog():
    return build_camera_catalog(
        {
            "camera-b": "北门",
            "camera-a": "南门",
        }
    )


def _repository(tmp_path):
    repository = ConfigurationRepository(
        tmp_path / "runtime" / "config" / "config.sqlite3",
        busy_timeout_ms=2345,
    )
    repository.initialize(_catalog(), builtin_baseline_version="test-baseline-1")
    return repository


def _create_v2_database(tmp_path):
    database_path = tmp_path / "runtime" / "config" / "config.sqlite3"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    catalog = _catalog()

    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE camera (
                camera_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
                ordinal INTEGER NOT NULL UNIQUE CHECK (ordinal >= 1),
                builtin_fingerprint TEXT NOT NULL UNIQUE
                    CHECK (length(builtin_fingerprint) = 64)
            );

            CREATE TABLE stream_binding_profile (
                profile_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
                description TEXT NOT NULL DEFAULT '',
                is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );

            CREATE TABLE asset (
                asset_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('map', 'scene_reference')),
                relative_path TEXT NOT NULL UNIQUE
                    CHECK (length(trim(relative_path)) > 0),
                sha256 TEXT NOT NULL
                    CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
                size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
                media_type TEXT NOT NULL CHECK (
                    media_type IN ('image/png', 'image/jpeg', 'image/webp', 'image/bmp')
                ),
                width INTEGER NOT NULL CHECK (width BETWEEN 1 AND 10000),
                height INTEGER NOT NULL CHECK (height BETWEEN 1 AND 10000),
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE (kind, sha256)
            );

            CREATE TABLE topology_profile (
                topology_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
                revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
                map_asset_id TEXT NOT NULL,
                map_width INTEGER NOT NULL CHECK (map_width BETWEEN 1 AND 10000),
                map_height INTEGER NOT NULL CHECK (map_height BETWEEN 1 AND 10000),
                is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                FOREIGN KEY (map_asset_id) REFERENCES asset(asset_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT
            );

            CREATE TABLE scene_archive (
                scene_id TEXT PRIMARY KEY,
                scene_type TEXT NOT NULL
                    CHECK (scene_type IN ('no_parking', 'road_abnormal')),
                name TEXT NOT NULL CHECK (length(trim(name)) > 0),
                topology_id TEXT NOT NULL,
                topology_revision INTEGER NOT NULL CHECK (topology_revision >= 1),
                camera_id TEXT NOT NULL,
                reference_asset_id TEXT,
                validated_config_json TEXT NOT NULL
                    CHECK (json_valid(validated_config_json)),
                review_status TEXT NOT NULL DEFAULT 'ready'
                    CHECK (review_status IN ('ready', 'needs_review')),
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                FOREIGN KEY (topology_id) REFERENCES topology_profile(topology_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                FOREIGN KEY (camera_id) REFERENCES camera(camera_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                FOREIGN KEY (reference_asset_id) REFERENCES asset(asset_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT
            );

            CREATE TABLE detection_settings (
                singleton_id INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton_id = 1),
                enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
                yolo_threshold REAL NOT NULL DEFAULT 0.5
                    CHECK (yolo_threshold BETWEEN 0.05 AND 1.0),
                lpr_threshold REAL NOT NULL DEFAULT 0.7
                    CHECK (lpr_threshold BETWEEN 0.05 AND 1.0),
                frame_interval INTEGER NOT NULL DEFAULT 5
                    CHECK (frame_interval BETWEEN 1 AND 60),
                device_preference TEXT NOT NULL DEFAULT 'cpu'
                    CHECK (length(trim(device_preference)) > 0),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );

            CREATE TABLE activation_state (
                singleton_id INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton_id = 1),
                stream_profile_id TEXT NOT NULL,
                topology_id TEXT NOT NULL,
                topology_revision INTEGER NOT NULL CHECK (topology_revision >= 1),
                no_parking_scene_id TEXT,
                road_abnormal_scene_id TEXT,
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                FOREIGN KEY (stream_profile_id)
                    REFERENCES stream_binding_profile(profile_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                FOREIGN KEY (topology_id) REFERENCES topology_profile(topology_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                FOREIGN KEY (no_parking_scene_id) REFERENCES scene_archive(scene_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                FOREIGN KEY (road_abnormal_scene_id) REFERENCES scene_archive(scene_id)
                    ON UPDATE CASCADE ON DELETE RESTRICT
            );

            CREATE TABLE schema_metadata (
                singleton_id INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton_id = 1),
                schema_version INTEGER NOT NULL CHECK (
                    typeof(schema_version) = 'integer' AND schema_version >= 1
                ),
                legacy_migration_completed INTEGER NOT NULL DEFAULT 0
                    CHECK (legacy_migration_completed IN (0, 1)),
                builtin_baseline_version TEXT NOT NULL
                    CHECK (length(trim(builtin_baseline_version)) > 0),
                camera_catalog_fingerprint TEXT NOT NULL
                    CHECK (length(camera_catalog_fingerprint) = 64),
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO camera (
                camera_id, display_name, ordinal, builtin_fingerprint
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (
                    entry.camera_id,
                    entry.display_name,
                    entry.ordinal,
                    entry.builtin_fingerprint,
                )
                for entry in catalog
            ],
        )
        connection.execute(
            """
            INSERT INTO stream_binding_profile (
                profile_id, name, description, is_builtin
            ) VALUES ('profile-1', '默认流方案', '', 1)
            """
        )
        connection.execute(
            """
            INSERT INTO asset (
                asset_id, kind, relative_path, sha256,
                size_bytes, media_type, width, height
            ) VALUES (
                'map-asset', 'map', 'assets/maps/map.png', ?,
                128, 'image/png', 1105, 740
            )
            """,
            ("a" * 64,),
        )
        connection.execute(
            """
            INSERT INTO topology_profile (
                topology_id, name, revision, map_asset_id, map_width, map_height
            ) VALUES ('topology-1', '默认拓扑', 1, 'map-asset', 1105, 740)
            """
        )
        connection.executemany(
            """
            INSERT INTO scene_archive (
                scene_id, scene_type, name, topology_id, topology_revision,
                camera_id, validated_config_json, review_status
            ) VALUES (?, ?, ?, 'topology-1', 1, ?, '{"zones":[]}', ?)
            """,
            [
                (
                    "legacy-no-parking",
                    "no_parking",
                    "旧禁停场景",
                    "camera-b",
                    "needs_review",
                ),
                (
                    "legacy-road-abnormal",
                    "road_abnormal",
                    "旧道路异常场景",
                    "camera-a",
                    "needs_review",
                ),
            ],
        )
        connection.execute(
            """
            INSERT INTO activation_state (
                singleton_id, stream_profile_id, topology_id, topology_revision,
                no_parking_scene_id, road_abnormal_scene_id
            ) VALUES (
                1, 'profile-1', 'topology-1', 1,
                'legacy-no-parking', 'legacy-road-abnormal'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO schema_metadata (
                singleton_id, schema_version, legacy_migration_completed,
                builtin_baseline_version, camera_catalog_fingerprint
            ) VALUES (1, 2, 1, 'test-v2', ?)
            """,
            (catalog.fingerprint,),
        )
        connection.execute("PRAGMA user_version = 2")

    return database_path


def test_camera_catalog_preserves_mapping_order_and_has_stable_fingerprint():
    catalog = _catalog()

    assert catalog.camera_ids == ("camera-b", "camera-a")
    assert [entry.ordinal for entry in catalog] == [1, 2]
    assert [entry.display_name for entry in catalog] == ["北门", "南门"]
    assert len(catalog.fingerprint) == 64
    assert catalog.fingerprint == _catalog().fingerprint
    assert len({entry.builtin_fingerprint for entry in catalog}) == 2

    reversed_catalog = build_camera_catalog(
        {
            "camera-a": "南门",
            "camera-b": "北门",
        }
    )
    assert reversed_catalog.fingerprint != catalog.fingerprint


def test_initialize_creates_all_tables_pragmas_and_singletons(tmp_path):
    repository = _repository(tmp_path)

    table_names = {
        row["name"]
        for row in repository.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert set(SCHEMA_TABLES) <= table_names
    assert repository.fetch_one("PRAGMA foreign_keys")[0] == 1
    assert repository.fetch_one("PRAGMA journal_mode")[0].lower() == "wal"
    assert repository.fetch_one("PRAGMA busy_timeout")[0] == 2345
    assert repository.fetch_one("PRAGMA user_version")[0] == SCHEMA_VERSION

    cameras = repository.fetch_all(
        "SELECT camera_id, display_name, ordinal FROM camera ORDER BY ordinal"
    )
    assert [tuple(row) for row in cameras] == [
        ("camera-b", "北门", 1),
        ("camera-a", "南门", 2),
    ]
    metadata = repository.fetch_one(
        "SELECT * FROM schema_metadata WHERE singleton_id = 1"
    )
    assert metadata["schema_version"] == SCHEMA_VERSION
    assert metadata["builtin_baseline_version"] == "test-baseline-1"
    assert metadata["legacy_migration_completed"] == 0
    assert metadata["camera_catalog_fingerprint"] == _catalog().fingerprint
    assert repository.fetch_one("SELECT enabled FROM detection_settings")[0] == 0
    assert repository.fetch_one("SELECT enabled FROM whitelist_setting")[0] == 1

    repository.initialize(_catalog(), builtin_baseline_version="ignored-on-reopen")
    assert repository.fetch_one(
        "SELECT builtin_baseline_version FROM schema_metadata"
    )[0] == "test-baseline-1"


def test_explicit_transaction_rolls_back_and_catalog_is_immutable(tmp_path):
    repository = _repository(tmp_path)

    with pytest.raises(RuntimeError, match="abort transaction"):
        with repository.transaction() as connection:
            repository.execute(
                connection,
                """
                INSERT INTO stream_source (stream_id, name, rtsp_url)
                VALUES (?, ?, ?)
                """,
                ("stream-rollback", "rollback", "rtsp://127.0.0.1/rollback"),
            )
            raise RuntimeError("abort transaction")
    assert repository.fetch_one(
        "SELECT stream_id FROM stream_source WHERE stream_id = ?",
        ("stream-rollback",),
    ) is None

    with pytest.raises(sqlite3.IntegrityError, match="fixed camera catalog"):
        with repository.transaction() as connection:
            repository.execute(
                connection,
                "UPDATE camera SET display_name = ? WHERE camera_id = ?",
                ("changed", "camera-b"),
            )

    mismatched = build_camera_catalog(
        {
            "camera-b": "北门",
            "camera-a": "different",
        }
    )
    with pytest.raises(CameraCatalogMismatchError):
        repository.initialize(mismatched)


def test_core_relations_enforce_graph_scene_and_stream_constraints(tmp_path):
    repository = _repository(tmp_path)

    with repository.transaction() as connection:
        repository.executemany(
            connection,
            """
            INSERT INTO stream_source (stream_id, name, rtsp_url)
            VALUES (?, ?, ?)
            """,
            [
                ("stream-1", "流 1", "rtsp://127.0.0.1/live/1"),
                ("stream-2", "流 2", "rtsp://127.0.0.1/live/2"),
            ],
        )
        repository.execute(
            connection,
            """
            INSERT INTO stream_binding_profile (profile_id, name, is_builtin)
            VALUES ('profile-1', '默认流方案', 1)
            """,
        )
        repository.executemany(
            connection,
            """
            INSERT INTO stream_binding (profile_id, camera_id, stream_id)
            VALUES (?, ?, ?)
            """,
            [
                ("profile-1", "camera-b", "stream-1"),
                ("profile-1", "camera-a", "stream-2"),
            ],
        )
        repository.executemany(
            connection,
            """
            INSERT INTO asset (
                asset_id, kind, relative_path, sha256,
                size_bytes, media_type, width, height
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "map-asset",
                    "map",
                    "assets/maps/map.png",
                    "a" * 64,
                    128,
                    "image/png",
                    1105,
                    740,
                ),
                (
                    "scene-asset",
                    "scene_reference",
                    "assets/scene-references/reference.jpg",
                    "b" * 64,
                    256,
                    "image/jpeg",
                    1920,
                    1080,
                ),
            ],
        )
        repository.execute(
            connection,
            """
            INSERT INTO topology_profile (
                topology_id, name, revision, map_asset_id,
                map_width, map_height, is_builtin
            ) VALUES ('topology-1', '默认拓扑', 1, 'map-asset', 1105, 740, 1)
            """,
        )
        repository.executemany(
            connection,
            """
            INSERT INTO topology_node (topology_id, node_id, x, y, node_type)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("topology-1", "node-start", 0.1, 0.2, "endpoint"),
                ("topology-1", "node-end", 0.8, 0.2, "endpoint"),
            ],
        )
        repository.execute(
            connection,
            """
            INSERT INTO road_segment (
                topology_id, segment_id, name, points_json, geometry_type,
                start_node_id, end_node_id, direction, level, capacity, road_width
            ) VALUES (
                'topology-1', 'road-1', '测试道路', '[[0.1,0.2],[0.8,0.2]]',
                'polyline', 'node-start', 'node-end', '双向', 'ground', 4, 0.05
            )
            """,
        )
        repository.executemany(
            connection,
            """
            INSERT INTO topology_camera (
                topology_id, camera_id, x, y, heading, view_range, segment_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("topology-1", "camera-b", 0.2, 0.2, 90.0, 0.12, "road-1"),
                ("topology-1", "camera-a", 0.7, 0.2, 270.0, 0.12, "road-1"),
            ],
        )
        repository.execute(
            connection,
            """
            INSERT INTO scene_archive (
                scene_id, scene_type, name, topology_id, topology_revision,
                camera_id, reference_asset_id, validated_config_json
            ) VALUES (
                'scene-1', 'no_parking', '禁停场景', NULL, NULL,
                'camera-b', 'scene-asset', '{"zones":[]}'
            )
            """,
        )
        repository.execute(
            connection,
            """
            INSERT INTO activation_state (
                singleton_id, stream_profile_id, topology_id, topology_revision,
                no_parking_scene_id, road_abnormal_scene_id
            ) VALUES (1, 'profile-1', 'topology-1', 1, 'scene-1', NULL)
            """,
        )

    assert repository.fetch_one("SELECT scene_id FROM scene_archive")[0] == "scene-1"
    assert repository.fetch_one("SELECT topology_id FROM activation_state")[0] == "topology-1"

    with pytest.raises(sqlite3.IntegrityError):
        with repository.transaction() as connection:
            repository.execute(
                connection,
                """
                INSERT INTO scene_archive (
                    scene_id, scene_type, name, topology_id, topology_revision,
                    camera_id, validated_config_json
                ) VALUES (
                    'bad-no-parking', 'no_parking', '错误绑定',
                    'topology-1', 1, 'camera-b', '{"zones":[]}'
                )
                """,
            )

    with pytest.raises(sqlite3.IntegrityError):
        with repository.transaction() as connection:
            repository.execute(
                connection,
                """
                UPDATE stream_binding SET stream_id = 'stream-1'
                WHERE profile_id = 'profile-1' AND camera_id = 'camera-a'
                """,
            )


def test_topology_revision_can_advance_while_old_scenes_are_retained(tmp_path):
    repository = _repository(tmp_path)

    with repository.transaction() as connection:
        repository.execute(
            connection,
            """
            INSERT INTO asset (
                asset_id, kind, relative_path, sha256,
                size_bytes, media_type, width, height
            ) VALUES (
                'map-asset', 'map', 'assets/maps/map.png', ?,
                128, 'image/png', 1105, 740
            )
            """,
            ("c" * 64,),
        )
        repository.execute(
            connection,
            """
            INSERT INTO topology_profile (
                topology_id, name, revision, map_asset_id, map_width, map_height
            ) VALUES ('topology-1', '拓扑', 1, 'map-asset', 1105, 740)
            """,
        )
        repository.executemany(
            connection,
            """
            INSERT INTO topology_node (topology_id, node_id, x, y, node_type)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("topology-1", "start", 0.1, 0.1, "endpoint"),
                ("topology-1", "end", 0.9, 0.1, "endpoint"),
            ],
        )
        repository.execute(
            connection,
            """
            INSERT INTO road_segment (
                topology_id, segment_id, name, points_json, geometry_type,
                start_node_id, end_node_id, direction, level, capacity, road_width
            ) VALUES (
                'topology-1', 'road-1', '道路', '[[0.1,0.1],[0.9,0.1]]',
                'polyline', 'start', 'end', '双向', 'ground', 4, 0.05
            )
            """,
        )
        repository.execute(
            connection,
            """
            INSERT INTO topology_camera (
                topology_id, camera_id, x, y, heading, view_range, segment_id
            ) VALUES ('topology-1', 'camera-b', 0.2, 0.1, 90, 0.12, 'road-1')
            """,
        )
        repository.execute(
            connection,
            """
            INSERT INTO scene_archive (
                scene_id, scene_type, name, topology_id, topology_revision,
                camera_id, validated_config_json
            ) VALUES (
                'scene-v1', 'road_abnormal', '旧修订场景', 'topology-1', 1,
                'camera-b', '{"zones":[]}'
            )
            """,
        )

    with repository.transaction() as connection:
        repository.execute(
            connection,
            "DELETE FROM topology_camera WHERE topology_id = 'topology-1'",
        )
        repository.execute(
            connection,
            """
            INSERT INTO topology_camera (
                topology_id, camera_id, x, y, heading, view_range, segment_id
            ) VALUES ('topology-1', 'camera-b', 0.3, 0.1, 100, 0.14, 'road-1')
            """,
        )
        repository.execute(
            connection,
            "UPDATE topology_profile SET revision = 2 WHERE topology_id = 'topology-1'",
        )
        repository.execute(
            connection,
            """
            UPDATE scene_archive SET review_status = 'needs_review'
            WHERE topology_id = 'topology-1' AND topology_revision < 2
            """,
        )

    scene = repository.fetch_one(
        "SELECT topology_revision, review_status FROM scene_archive WHERE scene_id = 'scene-v1'"
    )
    assert tuple(scene) == (1, "needs_review")
    assert repository.fetch_one(
        "SELECT x FROM topology_camera WHERE topology_id = 'topology-1'"
    )[0] == pytest.approx(0.3)
    assert repository.fetch_one(
        "SELECT revision FROM topology_profile WHERE topology_id = 'topology-1'"
    )[0] == 2

    with pytest.raises(sqlite3.IntegrityError):
        with repository.transaction() as connection:
            repository.execute(
                connection,
                """
                INSERT INTO scene_archive (
                    scene_id, scene_type, name, topology_id, topology_revision,
                    camera_id, validated_config_json
                ) VALUES (
                    'bad-scene', 'no_parking', '坏场景', NULL, NULL,
                    'camera-b', 'not-json'
                )
                """,
            )


def test_v2_migration_decouples_no_parking_scenes_from_topology(tmp_path):
    database_path = _create_v2_database(tmp_path)
    repository = ConfigurationRepository(database_path)

    repository.initialize(_catalog(), builtin_baseline_version="ignored")

    assert repository.fetch_one("PRAGMA user_version")[0] == 3
    no_parking = repository.fetch_one(
        """
        SELECT topology_id, topology_revision, review_status
        FROM scene_archive
        WHERE scene_id = 'legacy-no-parking'
        """
    )
    assert tuple(no_parking) == (None, None, "ready")

    road_abnormal = repository.fetch_one(
        """
        SELECT topology_id, topology_revision, review_status
        FROM scene_archive
        WHERE scene_id = 'legacy-road-abnormal'
        """
    )
    assert tuple(road_abnormal) == ("topology-1", 1, "needs_review")

    activation = repository.fetch_one(
        """
        SELECT no_parking_scene_id, road_abnormal_scene_id
        FROM activation_state
        WHERE singleton_id = 1
        """
    )
    assert tuple(activation) == (
        "legacy-no-parking",
        "legacy-road-abnormal",
    )
    assert repository.fetch_all("PRAGMA foreign_key_check") == []


def test_integrity_check_and_backup_produce_a_readable_snapshot(tmp_path):
    repository = _repository(tmp_path)
    with repository.transaction() as connection:
        repository.execute(
            connection,
            "INSERT INTO whitelist_entry (plate, note) VALUES (?, ?)",
            ("京A12345", "测试车辆"),
        )

    assert repository.integrity_check() == ["ok"]

    destination = tmp_path / "backups" / "configuration.sqlite3"
    assert repository.backup_to(destination) == destination
    backup = ConfigurationRepository(destination)
    entry = backup.fetch_one("SELECT plate, note FROM whitelist_entry")
    assert tuple(entry) == ("京A12345", "测试车辆")
    assert backup.integrity_check() == ["ok"]
