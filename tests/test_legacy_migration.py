import json
import sqlite3

import cv2
import numpy as np
import pytest

from backend.configuration import ConfigurationRepository, build_camera_catalog
from backend.configuration.assets import AssetStore
from backend.configuration.legacy_migration import (
    BUILTIN_STREAM_PROFILE_ID,
    BUILTIN_TOPOLOGY_ID,
    LegacyMigrationError,
    LegacyMigrator,
)


CAMERA_IDS = tuple(f"camera-{index:02d}" for index in range(1, 13))
STREAMS = {
    camera_id: f"rtsp://127.0.0.1:8554/live/{index}"
    for index, camera_id in enumerate(CAMERA_IDS, start=1)
}


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_image(path, *, width, height, color):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :, :] = color
    success, encoded = cv2.imencode(path.suffix, image)
    assert success
    path.write_bytes(encoded.tobytes())


def _legacy_project(tmp_path):
    project = tmp_path / "legacy-project"
    map_image = project / "sandpan" / "map.png"
    _write_image(map_image, width=40, height=30, color=(20, 80, 140))
    segments = [
        {
            "segment_id": "road-a",
            "name": "Road A",
            "points": [[0.1, 0.1], [0.5, 0.5]],
            "capacity": 4,
            "level": "ground",
            "direction": "two-way",
            "geometry_type": "polyline",
            "road_width": 0.04,
        },
        {
            "segment_id": "road-b",
            "name": "Road B",
            "points": [[0.5, 0.5], [0.9, 0.8]],
            "capacity": 6,
            "level": "bridge",
            "direction": "two-way",
            "geometry_type": "polyline",
            "road_width": 0.05,
        },
    ]
    cameras = [
        {
            "camera_id": camera_id,
            "x": 0.1 + index * 0.05,
            "y": 0.2 + index * 0.02,
            "heading": float(index * 10),
            "view_range": 0.12,
            "segment_id": "road-a" if index < 6 else "road-b",
        }
        for index, camera_id in enumerate(CAMERA_IDS)
    ]
    _write_json(
        project / "traffic_map.json",
        {
            "version": 3,
            "map_image": "sandpan\\map.png",
            "segments": segments,
            "cameras": cameras,
        },
    )
    _write_json(
        project / "whitelist.json",
        [
            {
                "plate": "abc-123",
                "note": "legacy entry",
                "added_at": "2026-07-01 12:00:00",
            }
        ],
    )

    no_parking_root = project / "runtime" / "no_parking"
    no_parking_reference = no_parking_root / "references" / "no-parking.jpg"
    _write_image(
        no_parking_reference,
        width=32,
        height=20,
        color=(10, 120, 210),
    )
    _write_json(
        no_parking_root / "scenes.json",
        {
            "version": 1,
            "scenes": [
                {
                    "scene_id": "no-parking-scene",
                    "name": "No parking",
                    "camera_id": CAMERA_IDS[0],
                    "reference_image": no_parking_reference.name,
                    "reference_width": 32,
                    "reference_height": 20,
                    "zones": [
                        {
                            "zone_id": "no-parking-zone",
                            "name": "Restricted",
                            "points": [[0.1, 0.1], [0.8, 0.1], [0.8, 0.8]],
                            "dwell_seconds": 3.0,
                            "lost_tolerance_seconds": 1.0,
                            "enabled": True,
                            "vehicle_classes": ["car", "truck"],
                        }
                    ],
                    "created_at": 1_700_000_000.0,
                    "updated_at": 1_700_000_100.0,
                }
            ],
        },
    )
    _write_json(
        no_parking_root / "events.json",
        {"version": 1, "events": [{"event_id": "must-not-migrate"}]},
    )

    road_abnormal_root = project / "runtime" / "road_abnormal"
    road_reference = road_abnormal_root / "references" / "road-abnormal.jpg"
    orphan_reference = road_abnormal_root / "references" / "orphan.jpg"
    _write_image(
        road_reference,
        width=32,
        height=20,
        color=(200, 70, 30),
    )
    _write_image(
        orphan_reference,
        width=18,
        height=12,
        color=(40, 220, 60),
    )
    _write_json(
        road_abnormal_root / "scenes.json",
        {
            "version": 1,
            "scenes": [
                {
                    "scene_id": "road-abnormal-scene",
                    "name": "Road abnormal",
                    "camera_id": CAMERA_IDS[1],
                    "reference_image": road_reference.name,
                    "reference_width": 32,
                    "reference_height": 20,
                    "zones": [
                        {
                            "zone_id": "road-zone",
                            "name": "Road area",
                            "lane_name": "Lane 1",
                            "points": [[0.2, 0.2], [0.9, 0.2], [0.7, 0.9]],
                            "enabled": True,
                        }
                    ],
                    "persistence_seconds": 3.0,
                    "lost_tolerance_seconds": 1.0,
                    "min_area_ratio": 0.001,
                    "history": 500,
                    "variance_threshold": 25.0,
                    "detect_shadows": True,
                    "warmup_frames": 30,
                    "learning_rate": 0.002,
                    "inference_interval": 5,
                    "yolo_threshold": 0.45,
                    "anomaly_classes": ["person", "bicycle", "motorcycle"],
                    "normal_classes": ["car", "bus", "truck"],
                    "created_at": 1_700_000_200.0,
                    "updated_at": 1_700_000_300.0,
                }
            ],
        },
    )
    _write_json(
        road_abnormal_root / "events.json",
        {"version": 1, "events": [{"event_id": "also-must-not-migrate"}]},
    )
    snapshot = road_abnormal_root / "snapshots" / "event.jpg"
    _write_image(snapshot, width=12, height=8, color=(90, 90, 90))
    return project, orphan_reference


def _repository(tmp_path, name="config.sqlite3"):
    repository = ConfigurationRepository(tmp_path / name)
    repository.initialize(build_camera_catalog({camera_id: camera_id for camera_id in CAMERA_IDS}))
    return repository


def _snapshot_files(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_migrates_complete_legacy_snapshot_without_changing_source_files(tmp_path):
    project, orphan_reference = _legacy_project(tmp_path)
    before = _snapshot_files(project)
    repository = _repository(tmp_path)
    store = AssetStore(tmp_path / "config-assets")

    result = LegacyMigrator(repository, store, project, STREAMS).migrate()

    assert result.as_dict() == {
        "migrated": True,
        "streams": 12,
        "segments": 2,
        "cameras": 12,
        "whitelist_entries": 1,
        "scenes": 2,
        "assets": 3,
    }
    assert _snapshot_files(project) == before
    assert repository.integrity_check() == ["ok"]

    profile = repository.fetch_one("SELECT * FROM stream_binding_profile")
    topology = repository.fetch_one("SELECT * FROM topology_profile")
    assert profile["profile_id"] == BUILTIN_STREAM_PROFILE_ID
    assert profile["is_builtin"] == 1
    assert topology["topology_id"] == BUILTIN_TOPOLOGY_ID
    assert topology["revision"] == 1
    assert (topology["map_width"], topology["map_height"]) == (40, 30)

    nodes = repository.fetch_all(
        "SELECT node_id, x, y FROM topology_node ORDER BY node_id"
    )
    assert len(nodes) == 4
    assert len({row["node_id"] for row in nodes}) == 4
    road_rows = repository.fetch_all(
        "SELECT segment_id, start_node_id, end_node_id FROM road_segment ORDER BY segment_id"
    )
    assert road_rows[0]["end_node_id"] != road_rows[1]["start_node_id"]
    coordinates = {
        row["node_id"]: (row["x"], row["y"])
        for row in nodes
    }
    assert coordinates[road_rows[0]["end_node_id"]] == (0.5, 0.5)
    assert coordinates[road_rows[1]["start_node_id"]] == (0.5, 0.5)

    scenes = repository.fetch_all("SELECT * FROM scene_archive ORDER BY scene_type")
    assert len(scenes) == 2
    assert all(row["topology_id"] == BUILTIN_TOPOLOGY_ID for row in scenes)
    assert all(row["topology_revision"] == 1 for row in scenes)
    assert all(row["review_status"] == "ready" for row in scenes)
    no_parking_config = json.loads(scenes[0]["validated_config_json"])
    assert set(no_parking_config) == {"zones"}

    activation = repository.fetch_one("SELECT * FROM activation_state WHERE singleton_id = 1")
    assert activation["stream_profile_id"] == BUILTIN_STREAM_PROFILE_ID
    assert activation["topology_id"] == BUILTIN_TOPOLOGY_ID
    assert activation["no_parking_scene_id"] is None
    assert activation["road_abnormal_scene_id"] is None
    detection = dict(
        repository.fetch_one("SELECT * FROM detection_settings WHERE singleton_id = 1")
    )
    assert detection["enabled"] == 0
    assert detection["yolo_threshold"] == 0.5
    assert detection["lpr_threshold"] == 0.7
    assert detection["frame_interval"] == 5
    assert detection["device_preference"] == "cpu"
    assert repository.fetch_one("SELECT enabled FROM whitelist_setting")["enabled"] == 1
    assert dict(repository.fetch_one("SELECT * FROM whitelist_entry")) == {
        "plate": "ABC-123",
        "note": "legacy entry",
        "added_at": "2026-07-01 12:00:00",
    }
    assert repository.fetch_one("SELECT COUNT(*) AS count FROM asset")["count"] == 3
    assert orphan_reference.read_bytes() not in [
        path.read_bytes()
        for path in (tmp_path / "config-assets").rglob("*")
        if path.is_file()
    ]
    assert repository.fetch_one(
        "SELECT legacy_migration_completed FROM schema_metadata"
    )["legacy_migration_completed"] == 1


def test_completed_migration_is_a_noop_without_reading_legacy_files(tmp_path):
    project, _ = _legacy_project(tmp_path)
    repository = _repository(tmp_path)
    migrator = LegacyMigrator(
        repository,
        AssetStore(tmp_path / "config-assets"),
        project,
        STREAMS,
    )
    assert migrator.migrate().migrated is True
    (project / "traffic_map.json").write_text("invalid", encoding="utf-8")
    (project / "runtime" / "no_parking" / "scenes.json").unlink()

    result = migrator.migrate()

    assert result.as_dict() == {
        "migrated": False,
        "streams": 0,
        "segments": 0,
        "cameras": 0,
        "whitelist_entries": 0,
        "scenes": 0,
        "assets": 0,
    }
    assert repository.fetch_one("SELECT COUNT(*) AS count FROM stream_source")["count"] == 12


def test_endpoint_node_ids_are_deterministic_across_repositories(tmp_path):
    project, _ = _legacy_project(tmp_path)
    first = _repository(tmp_path, "first.sqlite3")
    second = _repository(tmp_path, "second.sqlite3")

    LegacyMigrator(first, AssetStore(tmp_path / "first-assets"), project, STREAMS).migrate()
    LegacyMigrator(second, AssetStore(tmp_path / "second-assets"), project, STREAMS).migrate()

    query = "SELECT node_id FROM topology_node ORDER BY node_id"
    assert [row["node_id"] for row in first.fetch_all(query)] == [
        row["node_id"] for row in second.fetch_all(query)
    ]


def test_strict_validation_failure_leaves_legacy_and_business_tables_unchanged(tmp_path):
    project, _ = _legacy_project(tmp_path)
    traffic_map_path = project / "traffic_map.json"
    traffic_map = json.loads(traffic_map_path.read_text(encoding="utf-8"))
    traffic_map["unexpected"] = True
    _write_json(traffic_map_path, traffic_map)
    before = _snapshot_files(project)
    repository = _repository(tmp_path)

    with pytest.raises(LegacyMigrationError, match=r"unknown=\['unexpected'\]"):
        LegacyMigrator(
            repository,
            AssetStore(tmp_path / "config-assets"),
            project,
            STREAMS,
        ).migrate()

    assert _snapshot_files(project) == before
    assert repository.fetch_one("SELECT COUNT(*) AS count FROM stream_source")["count"] == 0
    assert repository.fetch_one("SELECT COUNT(*) AS count FROM asset")["count"] == 0
    assert repository.fetch_one(
        "SELECT legacy_migration_completed FROM schema_metadata"
    )["legacy_migration_completed"] == 0
    assert not (tmp_path / "config-assets").exists()


def test_missing_referenced_image_fails_before_database_writes(tmp_path):
    project, _ = _legacy_project(tmp_path)
    missing = project / "runtime" / "no_parking" / "references" / "no-parking.jpg"
    missing.unlink()
    before = _snapshot_files(project)
    repository = _repository(tmp_path)

    with pytest.raises(LegacyMigrationError, match="file does not exist"):
        LegacyMigrator(
            repository,
            AssetStore(tmp_path / "config-assets"),
            project,
            STREAMS,
        ).migrate()

    assert _snapshot_files(project) == before
    assert repository.fetch_one("SELECT COUNT(*) AS count FROM stream_source")["count"] == 0
    assert repository.fetch_one(
        "SELECT legacy_migration_completed FROM schema_metadata"
    )["legacy_migration_completed"] == 0


def test_database_failure_rolls_back_every_migration_row_and_preserves_legacy(tmp_path):
    project, _ = _legacy_project(tmp_path)
    before = _snapshot_files(project)
    repository = _repository(tmp_path)
    with repository.transaction() as connection:
        repository.execute(
            connection,
            """
            INSERT INTO stream_source (stream_id, name, rtsp_url)
            VALUES ('existing-stream', ?, 'rtsp://127.0.0.1/existing')
            """,
            (CAMERA_IDS[0],),
        )

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        LegacyMigrator(
            repository,
            AssetStore(tmp_path / "config-assets"),
            project,
            STREAMS,
        ).migrate()

    assert _snapshot_files(project) == before
    assert repository.fetch_one("SELECT COUNT(*) AS count FROM stream_source")["count"] == 1
    for table in (
        "asset",
        "stream_binding_profile",
        "stream_binding",
        "topology_profile",
        "topology_node",
        "road_segment",
        "topology_camera",
        "scene_archive",
        "whitelist_entry",
        "activation_state",
    ):
        assert repository.fetch_one(f"SELECT COUNT(*) AS count FROM {table}")["count"] == 0
    assert repository.fetch_one(
        "SELECT legacy_migration_completed FROM schema_metadata"
    )["legacy_migration_completed"] == 0
