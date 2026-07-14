"""Versioned SQLite schema for all business configuration domains."""

from __future__ import annotations


SCHEMA_VERSION = 2

MODEL_PIPELINE_SCENE_KEYS = (
    "realtime",
    "traffic_map",
    "no_parking",
    "road_abnormal",
)

SCHEMA_TABLES = (
    "camera",
    "stream_source",
    "stream_binding_profile",
    "stream_binding",
    "asset",
    "topology_profile",
    "topology_node",
    "road_segment",
    "topology_camera",
    "scene_archive",
    "detection_settings",
    "model_pipeline_setting",
    "whitelist_entry",
    "whitelist_setting",
    "activation_state",
    "configuration_operation",
    "audit_log",
    "schema_metadata",
)

_TIMESTAMP_DEFAULT = "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"

_MODEL_PIPELINE_TABLE_STATEMENT = f"""
CREATE TABLE IF NOT EXISTS model_pipeline_setting (
    scene_key TEXT PRIMARY KEY CHECK (
        scene_key IN ('realtime', 'traffic_map', 'no_parking', 'road_abnormal')
    ),
    preset TEXT NOT NULL DEFAULT 'legacy'
        CHECK (preset IN ('legacy', 'trained')),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    device_preference TEXT NOT NULL DEFAULT 'cpu' CHECK (
        length(trim(device_preference)) BETWEEN 1 AND 80
    ),
    yolo_threshold REAL NOT NULL DEFAULT 0.5
        CHECK (yolo_threshold BETWEEN 0.05 AND 1.0),
    lpr_threshold REAL NOT NULL DEFAULT 0.7
        CHECK (lpr_threshold BETWEEN 0.05 AND 1.0),
    frame_interval INTEGER NOT NULL DEFAULT 5 CHECK (
        typeof(frame_interval) = 'integer' AND frame_interval BETWEEN 1 AND 60
    ),
    inference_size INTEGER NOT NULL DEFAULT 640 CHECK (
        typeof(inference_size) = 'integer' AND inference_size BETWEEN 160 AND 2048
    ),
    parking_move_threshold REAL NOT NULL DEFAULT 0.03
        CHECK (parking_move_threshold > 0.0 AND parking_move_threshold <= 1.0),
    mog_history INTEGER NOT NULL DEFAULT 500 CHECK (
        typeof(mog_history) = 'integer' AND mog_history BETWEEN 10 AND 5000
    ),
    mog_variance_threshold REAL NOT NULL DEFAULT 25.0
        CHECK (mog_variance_threshold BETWEEN 1.0 AND 255.0),
    mog_min_area INTEGER NOT NULL DEFAULT 150 CHECK (
        typeof(mog_min_area) = 'integer' AND mog_min_area BETWEEN 1 AND 1000000
    ),
    mog_min_duration REAL NOT NULL DEFAULT 2.0
        CHECK (mog_min_duration BETWEEN 0.1 AND 300.0),
    mog_max_duration REAL NOT NULL DEFAULT 5.0
        CHECK (mog_max_duration BETWEEN 0.1 AND 3600.0),
    mog_warmup_frames INTEGER NOT NULL DEFAULT 50 CHECK (
        typeof(mog_warmup_frames) = 'integer'
        AND mog_warmup_frames BETWEEN 0 AND 5000
    ),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (
        typeof(revision) = 'integer' AND revision >= 1
    ),
    updated_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT},
    CHECK (mog_max_duration >= mog_min_duration)
)
"""

MODEL_PIPELINE_SEED_STATEMENT = """
INSERT INTO model_pipeline_setting (
    scene_key,
    preset,
    enabled,
    device_preference,
    yolo_threshold,
    lpr_threshold,
    frame_interval
)
SELECT
    scene.scene_key,
    'legacy',
    detection.enabled,
    detection.device_preference,
    detection.yolo_threshold,
    detection.lpr_threshold,
    detection.frame_interval
FROM detection_settings AS detection
CROSS JOIN (
    SELECT 'realtime' AS scene_key
    UNION ALL SELECT 'traffic_map'
    UNION ALL SELECT 'no_parking'
    UNION ALL SELECT 'road_abnormal'
) AS scene
WHERE detection.singleton_id = 1
ON CONFLICT(scene_key) DO NOTHING
"""

SCHEMA_STATEMENTS = (
    f"""
    CREATE TABLE IF NOT EXISTS camera (
        camera_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
        ordinal INTEGER NOT NULL UNIQUE CHECK (ordinal >= 1),
        builtin_fingerprint TEXT NOT NULL UNIQUE
            CHECK (length(builtin_fingerprint) = 64)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS stream_source (
        stream_id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
        rtsp_url TEXT NOT NULL
            CHECK (lower(substr(rtsp_url, 1, 7)) = 'rtsp://'),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
        last_probe_status TEXT,
        last_probe_at TEXT,
        created_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT},
        updated_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT}
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS stream_binding_profile (
        profile_id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
        description TEXT NOT NULL DEFAULT '',
        is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT},
        updated_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT}
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stream_binding (
        profile_id TEXT NOT NULL,
        camera_id TEXT NOT NULL,
        stream_id TEXT NOT NULL,
        PRIMARY KEY (profile_id, camera_id),
        UNIQUE (profile_id, stream_id),
        FOREIGN KEY (profile_id) REFERENCES stream_binding_profile(profile_id)
            ON UPDATE CASCADE ON DELETE RESTRICT,
        FOREIGN KEY (camera_id) REFERENCES camera(camera_id)
            ON UPDATE CASCADE ON DELETE RESTRICT,
        FOREIGN KEY (stream_id) REFERENCES stream_source(stream_id)
            ON UPDATE CASCADE ON DELETE RESTRICT
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS asset (
        asset_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('map', 'scene_reference')),
        relative_path TEXT NOT NULL UNIQUE CHECK (length(trim(relative_path)) > 0),
        sha256 TEXT NOT NULL
            CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
        size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
        media_type TEXT NOT NULL CHECK (
            media_type IN ('image/png', 'image/jpeg', 'image/webp', 'image/bmp')
        ),
        width INTEGER NOT NULL CHECK (width BETWEEN 1 AND 10000),
        height INTEGER NOT NULL CHECK (height BETWEEN 1 AND 10000),
        created_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT},
        UNIQUE (kind, sha256)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS topology_profile (
        topology_id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
        revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
        map_asset_id TEXT NOT NULL,
        map_width INTEGER NOT NULL CHECK (map_width BETWEEN 1 AND 10000),
        map_height INTEGER NOT NULL CHECK (map_height BETWEEN 1 AND 10000),
        is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT},
        updated_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT},
        FOREIGN KEY (map_asset_id) REFERENCES asset(asset_id)
            ON UPDATE CASCADE ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS topology_node (
        topology_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        x REAL NOT NULL CHECK (x BETWEEN 0.0 AND 1.0),
        y REAL NOT NULL CHECK (y BETWEEN 0.0 AND 1.0),
        node_type TEXT NOT NULL
            CHECK (node_type IN ('endpoint', 'intersection', 'connector')),
        PRIMARY KEY (topology_id, node_id),
        FOREIGN KEY (topology_id) REFERENCES topology_profile(topology_id)
            ON UPDATE CASCADE ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS road_segment (
        topology_id TEXT NOT NULL,
        segment_id TEXT NOT NULL,
        name TEXT NOT NULL CHECK (length(trim(name)) > 0),
        points_json TEXT NOT NULL CHECK (json_valid(points_json)),
        geometry_type TEXT NOT NULL CHECK (geometry_type IN ('polyline', 'polygon')),
        start_node_id TEXT NOT NULL,
        end_node_id TEXT NOT NULL,
        direction TEXT NOT NULL CHECK (length(trim(direction)) > 0),
        level TEXT NOT NULL CHECK (level IN ('ground', 'bridge', 'parking', 'service')),
        capacity INTEGER NOT NULL CHECK (capacity >= 1),
        road_width REAL NOT NULL CHECK (road_width > 0.0 AND road_width <= 1.0),
        PRIMARY KEY (topology_id, segment_id),
        FOREIGN KEY (topology_id) REFERENCES topology_profile(topology_id)
            ON UPDATE CASCADE ON DELETE RESTRICT,
        FOREIGN KEY (topology_id, start_node_id)
            REFERENCES topology_node(topology_id, node_id)
            ON UPDATE CASCADE ON DELETE RESTRICT,
        FOREIGN KEY (topology_id, end_node_id)
            REFERENCES topology_node(topology_id, node_id)
            ON UPDATE CASCADE ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS topology_camera (
        topology_id TEXT NOT NULL,
        camera_id TEXT NOT NULL,
        x REAL NOT NULL CHECK (x BETWEEN 0.0 AND 1.0),
        y REAL NOT NULL CHECK (y BETWEEN 0.0 AND 1.0),
        heading REAL NOT NULL CHECK (heading BETWEEN 0.0 AND 360.0),
        view_range REAL NOT NULL CHECK (view_range > 0.0 AND view_range <= 1.0),
        segment_id TEXT NOT NULL,
        PRIMARY KEY (topology_id, camera_id),
        FOREIGN KEY (topology_id) REFERENCES topology_profile(topology_id)
            ON UPDATE CASCADE ON DELETE RESTRICT,
        FOREIGN KEY (camera_id) REFERENCES camera(camera_id)
            ON UPDATE CASCADE ON DELETE RESTRICT,
        FOREIGN KEY (topology_id, segment_id)
            REFERENCES road_segment(topology_id, segment_id)
            ON UPDATE CASCADE ON DELETE RESTRICT
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS scene_archive (
        scene_id TEXT PRIMARY KEY,
        scene_type TEXT NOT NULL CHECK (scene_type IN ('no_parking', 'road_abnormal')),
        name TEXT NOT NULL CHECK (length(trim(name)) > 0),
        topology_id TEXT NOT NULL,
        topology_revision INTEGER NOT NULL CHECK (topology_revision >= 1),
        camera_id TEXT NOT NULL,
        reference_asset_id TEXT,
        validated_config_json TEXT NOT NULL CHECK (json_valid(validated_config_json)),
        review_status TEXT NOT NULL DEFAULT 'ready'
            CHECK (review_status IN ('ready', 'needs_review')),
        created_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT},
        updated_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT},
        FOREIGN KEY (topology_id)
            REFERENCES topology_profile(topology_id)
            ON UPDATE CASCADE ON DELETE RESTRICT,
        FOREIGN KEY (camera_id)
            REFERENCES camera(camera_id)
            ON UPDATE CASCADE ON DELETE RESTRICT,
        FOREIGN KEY (reference_asset_id) REFERENCES asset(asset_id)
            ON UPDATE CASCADE ON DELETE RESTRICT
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS detection_settings (
        singleton_id INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton_id = 1),
        enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
        yolo_threshold REAL NOT NULL DEFAULT 0.5
            CHECK (yolo_threshold BETWEEN 0.05 AND 1.0),
        lpr_threshold REAL NOT NULL DEFAULT 0.7
            CHECK (lpr_threshold BETWEEN 0.05 AND 1.0),
        frame_interval INTEGER NOT NULL DEFAULT 5 CHECK (frame_interval BETWEEN 1 AND 60),
        device_preference TEXT NOT NULL DEFAULT 'cpu'
            CHECK (length(trim(device_preference)) > 0),
        updated_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT}
    )
    """,
    _MODEL_PIPELINE_TABLE_STATEMENT,
    f"""
    CREATE TABLE IF NOT EXISTS whitelist_entry (
        plate TEXT PRIMARY KEY CHECK (length(trim(plate)) > 0),
        note TEXT NOT NULL DEFAULT '',
        added_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT}
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS whitelist_setting (
        singleton_id INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton_id = 1),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
        updated_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT}
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS activation_state (
        singleton_id INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton_id = 1),
        stream_profile_id TEXT NOT NULL,
        topology_id TEXT NOT NULL,
        topology_revision INTEGER NOT NULL CHECK (topology_revision >= 1),
        no_parking_scene_id TEXT,
        road_abnormal_scene_id TEXT,
        updated_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT},
        FOREIGN KEY (stream_profile_id) REFERENCES stream_binding_profile(profile_id)
            ON UPDATE CASCADE ON DELETE RESTRICT,
        FOREIGN KEY (topology_id)
            REFERENCES topology_profile(topology_id)
            ON UPDATE CASCADE ON DELETE RESTRICT,
        FOREIGN KEY (no_parking_scene_id) REFERENCES scene_archive(scene_id)
            ON UPDATE CASCADE ON DELETE RESTRICT,
        FOREIGN KEY (road_abnormal_scene_id) REFERENCES scene_archive(scene_id)
            ON UPDATE CASCADE ON DELETE RESTRICT
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS configuration_operation (
        operation_id TEXT PRIMARY KEY,
        operation_type TEXT NOT NULL CHECK (length(trim(operation_type)) > 0),
        old_ref_json TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid(old_ref_json)),
        target_ref_json TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid(target_ref_json)),
        status TEXT NOT NULL CHECK (
            status IN (
                'pending', 'preflighting', 'applying', 'succeeded',
                'failed', 'rolled_back', 'interrupted'
            )
        ),
        error_summary TEXT,
        started_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT},
        finished_at TEXT
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS audit_log (
        audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_type TEXT NOT NULL CHECK (length(trim(operation_type)) > 0),
        target_type TEXT NOT NULL CHECK (length(trim(target_type)) > 0),
        target_id TEXT,
        result TEXT NOT NULL CHECK (length(trim(result)) > 0),
        summary TEXT NOT NULL DEFAULT '',
        occurred_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT},
        operation_id TEXT,
        FOREIGN KEY (operation_id) REFERENCES configuration_operation(operation_id)
            ON UPDATE CASCADE ON DELETE SET NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS schema_metadata (
        singleton_id INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton_id = 1),
        schema_version INTEGER NOT NULL CHECK (
            typeof(schema_version) = 'integer' AND schema_version >= 1
        ),
        legacy_migration_completed INTEGER NOT NULL DEFAULT 0
            CHECK (legacy_migration_completed IN (0, 1)),
        builtin_baseline_version TEXT NOT NULL CHECK (
            length(trim(builtin_baseline_version)) > 0
        ),
        camera_catalog_fingerprint TEXT NOT NULL
            CHECK (length(camera_catalog_fingerprint) = 64),
        created_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT},
        updated_at TEXT NOT NULL DEFAULT {_TIMESTAMP_DEFAULT}
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_stream_binding_stream ON stream_binding(stream_id)",
    "CREATE INDEX IF NOT EXISTS idx_asset_sha256 ON asset(sha256)",
    "CREATE INDEX IF NOT EXISTS idx_topology_node_profile ON topology_node(topology_id)",
    "CREATE INDEX IF NOT EXISTS idx_road_segment_profile ON road_segment(topology_id)",
    "CREATE INDEX IF NOT EXISTS idx_topology_camera_profile ON topology_camera(topology_id)",
    "CREATE INDEX IF NOT EXISTS idx_scene_type ON scene_archive(scene_type)",
    "CREATE INDEX IF NOT EXISTS idx_scene_topology ON scene_archive(topology_id, topology_revision)",
    "CREATE INDEX IF NOT EXISTS idx_operation_status ON configuration_operation(status)",
    "CREATE INDEX IF NOT EXISTS idx_audit_occurred_at ON audit_log(occurred_at DESC)",
    """
    CREATE TRIGGER IF NOT EXISTS camera_prevent_update
    BEFORE UPDATE ON camera
    BEGIN
        SELECT RAISE(ABORT, 'fixed camera catalog cannot be updated');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS camera_prevent_delete
    BEFORE DELETE ON camera
    BEGIN
        SELECT RAISE(ABORT, 'fixed camera catalog cannot be deleted');
    END
    """,
)

SCHEMA_MIGRATIONS = {
    1: (
        _MODEL_PIPELINE_TABLE_STATEMENT,
        MODEL_PIPELINE_SEED_STATEMENT,
    ),
}
