"""Strict one-time migration from the legacy JSON configuration files."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from backend.schemas import (
    NoParkingScenePayload,
    RoadAbnormalScenePayload,
    WhitelistInput,
)

from .assets import AssetStore
from .models import StreamCreate, TopologyCamera, TopologySegment


BUILTIN_STREAM_PROFILE_ID = "builtin-default-stream-profile"
BUILTIN_TOPOLOGY_ID = "builtin-default-topology"
BUILTIN_TOPOLOGY_REVISION = 1

DEFAULT_DETECTION_SETTINGS = {
    "enabled": False,
    "yolo_threshold": 0.5,
    "lpr_threshold": 0.7,
    "frame_interval": 5,
    "device_preference": "cpu",
}

_MAX_LEGACY_JSON_BYTES = 16 * 1024 * 1024

_TRAFFIC_MAP_KEYS = {"version", "map_image", "segments", "cameras"}
_SEGMENT_KEYS = {
    "segment_id",
    "name",
    "points",
    "capacity",
    "level",
    "direction",
    "geometry_type",
    "road_width",
}
_CAMERA_KEYS = {"camera_id", "x", "y", "heading", "view_range", "segment_id"}
_WHITELIST_KEYS = {"plate", "note", "added_at"}
_SCENE_DOCUMENT_KEYS = {"version", "scenes"}
_NO_PARKING_SCENE_KEYS = {
    "scene_id",
    "name",
    "camera_id",
    "reference_image",
    "reference_width",
    "reference_height",
    "zones",
    "created_at",
    "updated_at",
}
_NO_PARKING_ZONE_KEYS = {
    "zone_id",
    "name",
    "points",
    "dwell_seconds",
    "lost_tolerance_seconds",
    "enabled",
    "vehicle_classes",
}
_ROAD_ABNORMAL_SCENE_KEYS = {
    "scene_id",
    "name",
    "camera_id",
    "reference_image",
    "reference_width",
    "reference_height",
    "zones",
    "persistence_seconds",
    "lost_tolerance_seconds",
    "min_area_ratio",
    "history",
    "variance_threshold",
    "detect_shadows",
    "warmup_frames",
    "learning_rate",
    "inference_interval",
    "yolo_threshold",
    "anomaly_classes",
    "normal_classes",
    "created_at",
    "updated_at",
}
_ROAD_ABNORMAL_ZONE_KEYS = {"zone_id", "name", "lane_name", "points", "enabled"}


class LegacyMigrationError(ValueError):
    """Raised when legacy configuration cannot be migrated without guessing."""


@dataclass(frozen=True, slots=True)
class LegacyPaths:
    traffic_map: Path
    whitelist: Path
    no_parking_scenes: Path
    road_abnormal_scenes: Path

    @classmethod
    def from_project(cls, project_dir: Path) -> "LegacyPaths":
        project_dir = Path(project_dir)
        return cls(
            traffic_map=project_dir / "traffic_map.json",
            whitelist=project_dir / "whitelist.json",
            no_parking_scenes=project_dir / "runtime" / "no_parking" / "scenes.json",
            road_abnormal_scenes=(
                project_dir / "runtime" / "road_abnormal" / "scenes.json"
            ),
        )


@dataclass(frozen=True, slots=True)
class MigrationResult:
    migrated: bool
    streams: int = 0
    segments: int = 0
    cameras: int = 0
    whitelist_entries: int = 0
    scenes: int = 0
    assets: int = 0

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "migrated": self.migrated,
            "streams": self.streams,
            "segments": self.segments,
            "cameras": self.cameras,
            "whitelist_entries": self.whitelist_entries,
            "scenes": self.scenes,
            "assets": self.assets,
        }


@dataclass(slots=True)
class _LegacyTopology:
    map_path: Path
    nodes: list[dict[str, Any]]
    segments: list[dict[str, Any]]
    cameras: list[dict[str, Any]]


@dataclass(slots=True)
class _LegacyScene:
    scene_id: str
    scene_type: str
    name: str
    camera_id: str
    reference_path: Path | None
    reference_width: int
    reference_height: int
    validated_config: dict[str, Any]
    created_at: str
    updated_at: str


class LegacyMigrator:
    """Import one strict legacy snapshot into an initialized repository."""

    def __init__(
        self,
        repository: Any,
        asset_store: AssetStore,
        project_dir: Path,
        stream_sources: Mapping[str, str],
        *,
        paths: LegacyPaths | None = None,
    ) -> None:
        self.repository = repository
        self.asset_store = asset_store
        self.project_dir = Path(project_dir).resolve()
        self.stream_sources = dict(stream_sources)
        self.paths = paths or LegacyPaths.from_project(self.project_dir)

    def migrate(self) -> MigrationResult:
        """Migrate once; an existing completion marker is a successful no-op."""
        if self._migration_completed():
            return MigrationResult(migrated=False)

        streams = self._validate_streams()
        topology = self._load_topology(set(streams))
        whitelist = self._load_whitelist()
        scenes = self._load_scenes(set(streams))
        self._validate_scene_ids(scenes)

        map_asset = self.asset_store.ingest(topology.map_path, "map")
        assets = {map_asset["asset_id"]: map_asset}
        scene_assets: dict[Path, dict[str, Any]] = {}
        for scene in scenes:
            if scene.reference_path is None:
                continue
            resolved_reference = scene.reference_path.resolve()
            asset = scene_assets.get(resolved_reference)
            if asset is None:
                asset = self.asset_store.ingest(
                    resolved_reference,
                    "scene_reference",
                )
                scene_assets[resolved_reference] = asset
                assets[asset["asset_id"]] = asset
            if (
                asset["width"] != scene.reference_width
                or asset["height"] != scene.reference_height
            ):
                raise LegacyMigrationError(
                    f"scene {scene.scene_id} reference dimensions do not match the image"
                )

        timestamp = _utc_now()
        stream_rows = [
            {
                "stream_id": _stream_id(camera_id),
                "name": camera_id,
                "rtsp_url": rtsp_url,
            }
            for camera_id, rtsp_url in streams.items()
        ]

        with self.repository.transaction(immediate=True) as connection:
            marker = self.repository.execute(
                connection,
                "SELECT legacy_migration_completed FROM schema_metadata WHERE singleton_id = 1",
            ).fetchone()
            if marker is None:
                raise LegacyMigrationError("configuration repository is not initialized")
            if bool(marker["legacy_migration_completed"]):
                return MigrationResult(migrated=False)
            self._verify_repository_cameras(connection, list(streams))
            self._write_migration(
                connection=connection,
                timestamp=timestamp,
                stream_rows=stream_rows,
                topology=topology,
                map_asset=map_asset,
                assets=list(assets.values()),
                scenes=scenes,
                scene_assets=scene_assets,
                whitelist=whitelist,
            )

        return MigrationResult(
            migrated=True,
            streams=len(stream_rows),
            segments=len(topology.segments),
            cameras=len(topology.cameras),
            whitelist_entries=len(whitelist),
            scenes=len(scenes),
            assets=len(assets),
        )

    def _migration_completed(self) -> bool:
        marker = self.repository.fetch_one(
            "SELECT legacy_migration_completed FROM schema_metadata WHERE singleton_id = 1"
        )
        if marker is None:
            raise LegacyMigrationError("configuration repository is not initialized")
        return bool(marker["legacy_migration_completed"])

    def _validate_streams(self) -> dict[str, str]:
        if len(self.stream_sources) != 12:
            raise LegacyMigrationError("legacy stream catalog must contain exactly 12 cameras")
        validated: dict[str, str] = {}
        seen_urls: set[str] = set()
        for camera_id, rtsp_url in self.stream_sources.items():
            if not isinstance(camera_id, str) or not camera_id.strip():
                raise LegacyMigrationError("legacy camera IDs must be non-empty strings")
            if camera_id != camera_id.strip():
                raise LegacyMigrationError(f"legacy camera ID has surrounding whitespace: {camera_id!r}")
            try:
                model = StreamCreate(name=camera_id, rtsp_url=rtsp_url, enabled=True)
            except ValidationError as exc:
                raise LegacyMigrationError(
                    f"invalid legacy stream for camera {camera_id}: {exc.errors()}"
                ) from exc
            parsed = urlsplit(model.rtsp_url)
            if parsed.scheme.lower() != "rtsp" or not parsed.hostname:
                raise LegacyMigrationError(f"legacy stream has no host: {camera_id}")
            if model.rtsp_url in seen_urls:
                raise LegacyMigrationError("legacy streams must be unique")
            seen_urls.add(model.rtsp_url)
            validated[camera_id] = model.rtsp_url
        return validated

    def _load_topology(self, camera_ids: set[str]) -> _LegacyTopology:
        document = _read_json(self.paths.traffic_map, "traffic map")
        _require_object(document, "traffic map")
        _require_exact_keys(document, _TRAFFIC_MAP_KEYS, "traffic map")
        if document["version"] != 3:
            raise LegacyMigrationError("legacy traffic map version must be 3")

        map_path = _resolve_controlled_file(
            self.project_dir,
            document["map_image"],
            "traffic map image",
        )
        raw_segments = _require_list(document["segments"], "traffic map segments")
        if not raw_segments:
            raise LegacyMigrationError("legacy traffic map must contain at least one segment")

        segments: list[dict[str, Any]] = []
        nodes: list[dict[str, Any]] = []
        segment_ids: set[str] = set()
        for index, raw_segment in enumerate(raw_segments):
            label = f"traffic map segment[{index}]"
            _require_object(raw_segment, label)
            _require_exact_keys(raw_segment, _SEGMENT_KEYS, label)
            segment_id = _required_string(raw_segment["segment_id"], f"{label}.segment_id")
            if segment_id in segment_ids:
                raise LegacyMigrationError(f"duplicate legacy segment ID: {segment_id}")
            segment_ids.add(segment_id)
            points = _validate_points(raw_segment["points"], f"{label}.points", minimum=2)
            start_node_id = _node_id(segment_id, "start")
            end_node_id = _node_id(segment_id, "end")
            candidate = {
                **raw_segment,
                "segment_id": segment_id,
                "points": points,
                "start_node_id": start_node_id,
                "end_node_id": end_node_id,
            }
            try:
                segment = TopologySegment.model_validate(candidate)
            except ValidationError as exc:
                raise LegacyMigrationError(f"invalid {label}: {exc.errors()}") from exc
            if segment.geometry_type == "polygon":
                if len(segment.points) < 3 or _polygon_area(segment.points) < 1e-6:
                    raise LegacyMigrationError(f"invalid polygon geometry in {label}")
            elif all(
                math.dist(segment.points[0], point) < 1e-8
                for point in segment.points[1:]
            ):
                raise LegacyMigrationError(f"degenerate polyline geometry in {label}")
            segments.append(segment.model_dump(mode="json"))
            nodes.extend(
                [
                    {
                        "node_id": start_node_id,
                        "x": points[0][0],
                        "y": points[0][1],
                        "node_type": "endpoint",
                    },
                    {
                        "node_id": end_node_id,
                        "x": points[-1][0],
                        "y": points[-1][1],
                        "node_type": "endpoint",
                    },
                ]
            )

        raw_cameras = _require_list(document["cameras"], "traffic map cameras")
        cameras: list[dict[str, Any]] = []
        seen_cameras: set[str] = set()
        for index, raw_camera in enumerate(raw_cameras):
            label = f"traffic map camera[{index}]"
            _require_object(raw_camera, label)
            _require_exact_keys(raw_camera, _CAMERA_KEYS, label)
            try:
                camera = TopologyCamera.model_validate(raw_camera)
            except ValidationError as exc:
                raise LegacyMigrationError(f"invalid {label}: {exc.errors()}") from exc
            if camera.camera_id in seen_cameras:
                raise LegacyMigrationError(f"duplicate legacy camera ID: {camera.camera_id}")
            if camera.segment_id not in segment_ids:
                raise LegacyMigrationError(
                    f"legacy camera {camera.camera_id} references an unknown segment"
                )
            seen_cameras.add(camera.camera_id)
            cameras.append(camera.model_dump(mode="json"))
        if seen_cameras != camera_ids:
            missing = sorted(camera_ids - seen_cameras)
            extra = sorted(seen_cameras - camera_ids)
            raise LegacyMigrationError(
                f"legacy topology camera catalog mismatch; missing={missing}, extra={extra}"
            )
        return _LegacyTopology(
            map_path=map_path,
            nodes=nodes,
            segments=segments,
            cameras=cameras,
        )

    def _load_whitelist(self) -> list[dict[str, str]]:
        document = _read_json(self.paths.whitelist, "whitelist")
        entries = _require_list(document, "whitelist")
        result: list[dict[str, str]] = []
        seen_plates: set[str] = set()
        for index, raw_entry in enumerate(entries):
            label = f"whitelist[{index}]"
            _require_object(raw_entry, label)
            _require_exact_keys(raw_entry, _WHITELIST_KEYS, label)
            added_at = _required_string(raw_entry["added_at"], f"{label}.added_at")
            try:
                entry = WhitelistInput.model_validate(
                    {"plate": raw_entry["plate"], "note": raw_entry["note"]}
                )
            except ValidationError as exc:
                raise LegacyMigrationError(f"invalid {label}: {exc.errors()}") from exc
            plate = entry.plate.upper()
            if plate in seen_plates:
                raise LegacyMigrationError(f"duplicate legacy whitelist plate: {plate}")
            seen_plates.add(plate)
            result.append({"plate": plate, "note": entry.note, "added_at": added_at})
        return result

    def _load_scenes(self, camera_ids: set[str]) -> list[_LegacyScene]:
        return [
            *self._load_scene_document(
                self.paths.no_parking_scenes,
                "no_parking",
                camera_ids,
            ),
            *self._load_scene_document(
                self.paths.road_abnormal_scenes,
                "road_abnormal",
                camera_ids,
            ),
        ]

    def _load_scene_document(
        self,
        path: Path,
        scene_type: str,
        camera_ids: set[str],
    ) -> list[_LegacyScene]:
        document = _read_json(path, f"{scene_type} scenes")
        _require_object(document, f"{scene_type} scenes")
        _require_exact_keys(document, _SCENE_DOCUMENT_KEYS, f"{scene_type} scenes")
        if document["version"] != 1:
            raise LegacyMigrationError(f"legacy {scene_type} scene version must be 1")
        raw_scenes = _require_list(document["scenes"], f"{scene_type} scenes")
        result: list[_LegacyScene] = []
        for index, raw_scene in enumerate(raw_scenes):
            label = f"{scene_type} scene[{index}]"
            _require_object(raw_scene, label)
            if scene_type == "no_parking":
                _require_exact_keys(raw_scene, _NO_PARKING_SCENE_KEYS, label)
                for zone_index, zone in enumerate(
                    _require_list(raw_scene["zones"], f"{label}.zones")
                ):
                    _require_object(zone, f"{label}.zones[{zone_index}]")
                    _require_exact_keys(
                        zone,
                        _NO_PARKING_ZONE_KEYS,
                        f"{label}.zones[{zone_index}]",
                    )
                model_type = NoParkingScenePayload
            else:
                _require_exact_keys(raw_scene, _ROAD_ABNORMAL_SCENE_KEYS, label)
                for zone_index, zone in enumerate(
                    _require_list(raw_scene["zones"], f"{label}.zones")
                ):
                    _require_object(zone, f"{label}.zones[{zone_index}]")
                    _require_exact_keys(
                        zone,
                        _ROAD_ABNORMAL_ZONE_KEYS,
                        f"{label}.zones[{zone_index}]",
                    )
                model_type = RoadAbnormalScenePayload
            payload = {
                key: value
                for key, value in raw_scene.items()
                if key not in {"created_at", "updated_at"}
            }
            try:
                scene = model_type.model_validate(payload)
            except ValidationError as exc:
                raise LegacyMigrationError(f"invalid {label}: {exc.errors()}") from exc
            if not scene.scene_id:
                raise LegacyMigrationError(f"{label}.scene_id must not be empty")
            if scene.camera_id not in camera_ids:
                raise LegacyMigrationError(
                    f"legacy scene {scene.scene_id} references an unknown camera"
                )
            scene_dump = scene.model_dump(mode="json")
            _validate_scene_polygons(scene_dump["zones"], label)
            reference_path = None
            if scene.reference_image:
                reference_path = _resolve_reference_file(
                    path.parent / "references",
                    scene.reference_image,
                    f"{label}.reference_image",
                )
            elif scene_type == "no_parking":
                raise LegacyMigrationError(f"legacy scene {scene.scene_id} has no reference")
            if reference_path is None and (
                scene_dump["reference_width"] or scene_dump["reference_height"]
            ):
                raise LegacyMigrationError(
                    f"legacy scene {scene.scene_id} has dimensions without a reference"
                )
            config_exclusions = {
                "scene_id",
                "name",
                "camera_id",
                "reference_image",
                "reference_width",
                "reference_height",
            }
            result.append(
                _LegacyScene(
                    scene_id=scene.scene_id,
                    scene_type=scene_type,
                    name=scene.name,
                    camera_id=scene.camera_id,
                    reference_path=reference_path,
                    reference_width=scene_dump["reference_width"],
                    reference_height=scene_dump["reference_height"],
                    validated_config={
                        key: value
                        for key, value in scene_dump.items()
                        if key not in config_exclusions
                    },
                    created_at=_legacy_timestamp(raw_scene["created_at"], label),
                    updated_at=_legacy_timestamp(raw_scene["updated_at"], label),
                )
            )
        return result

    @staticmethod
    def _validate_scene_ids(scenes: Sequence[_LegacyScene]) -> None:
        seen: set[str] = set()
        for scene in scenes:
            if scene.scene_id in seen:
                raise LegacyMigrationError(f"duplicate legacy scene ID: {scene.scene_id}")
            seen.add(scene.scene_id)

    def _verify_repository_cameras(self, connection: Any, camera_ids: list[str]) -> None:
        rows = self.repository.execute(
            connection,
            "SELECT camera_id FROM camera ORDER BY ordinal",
        ).fetchall()
        repository_ids = [row["camera_id"] for row in rows]
        if repository_ids != camera_ids:
            raise LegacyMigrationError(
                "repository camera catalog does not match legacy stream order"
            )

    def _write_migration(
        self,
        *,
        connection: Any,
        timestamp: str,
        stream_rows: list[dict[str, str]],
        topology: _LegacyTopology,
        map_asset: dict[str, Any],
        assets: list[dict[str, Any]],
        scenes: list[_LegacyScene],
        scene_assets: dict[Path, dict[str, Any]],
        whitelist: list[dict[str, str]],
    ) -> None:
        execute = self.repository.execute
        executemany = self.repository.executemany

        executemany(
            connection,
            """
            INSERT INTO asset (
                asset_id, kind, relative_path, sha256, size_bytes, media_type,
                width, height, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    asset["asset_id"],
                    asset["kind"],
                    asset["relative_path"],
                    asset["sha256"],
                    asset["size_bytes"],
                    asset["media_type"],
                    asset["width"],
                    asset["height"],
                    timestamp,
                )
                for asset in assets
            ],
        )
        executemany(
            connection,
            """
            INSERT INTO stream_source (
                stream_id, name, rtsp_url, enabled, last_probe_status,
                last_probe_at, created_at, updated_at
            ) VALUES (?, ?, ?, 1, NULL, NULL, ?, ?)
            """,
            [
                (
                    row["stream_id"],
                    row["name"],
                    row["rtsp_url"],
                    timestamp,
                    timestamp,
                )
                for row in stream_rows
            ],
        )
        execute(
            connection,
            """
            INSERT INTO stream_binding_profile (
                profile_id, name, description, is_builtin, created_at, updated_at
            ) VALUES (?, ?, ?, 1, ?, ?)
            """,
            (
                BUILTIN_STREAM_PROFILE_ID,
                "内置默认流关联方案",
                "由升级前固定 12 路 RTSP 配置生成",
                timestamp,
                timestamp,
            ),
        )
        executemany(
            connection,
            "INSERT INTO stream_binding (profile_id, camera_id, stream_id) VALUES (?, ?, ?)",
            [
                (BUILTIN_STREAM_PROFILE_ID, row["name"], row["stream_id"])
                for row in stream_rows
            ],
        )
        execute(
            connection,
            """
            INSERT INTO topology_profile (
                topology_id, name, revision, map_asset_id, map_width, map_height,
                is_builtin, created_at, updated_at
            ) VALUES (?, ?, 1, ?, ?, ?, 1, ?, ?)
            """,
            (
                BUILTIN_TOPOLOGY_ID,
                "内置默认道路拓扑",
                map_asset["asset_id"],
                map_asset["width"],
                map_asset["height"],
                timestamp,
                timestamp,
            ),
        )
        executemany(
            connection,
            """
            INSERT INTO topology_node (topology_id, node_id, x, y, node_type)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    BUILTIN_TOPOLOGY_ID,
                    node["node_id"],
                    node["x"],
                    node["y"],
                    node["node_type"],
                )
                for node in topology.nodes
            ],
        )
        executemany(
            connection,
            """
            INSERT INTO road_segment (
                topology_id, segment_id, name, points_json, geometry_type,
                start_node_id, end_node_id, direction, level, capacity, road_width
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    BUILTIN_TOPOLOGY_ID,
                    segment["segment_id"],
                    segment["name"],
                    _canonical_json(segment["points"]),
                    segment["geometry_type"],
                    segment["start_node_id"],
                    segment["end_node_id"],
                    segment["direction"],
                    segment["level"],
                    segment["capacity"],
                    segment["road_width"],
                )
                for segment in topology.segments
            ],
        )
        executemany(
            connection,
            """
            INSERT INTO topology_camera (
                topology_id, camera_id, x, y, heading, view_range, segment_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    BUILTIN_TOPOLOGY_ID,
                    camera["camera_id"],
                    camera["x"],
                    camera["y"],
                    camera["heading"],
                    camera["view_range"],
                    camera["segment_id"],
                )
                for camera in topology.cameras
            ],
        )
        executemany(
            connection,
            """
            INSERT INTO scene_archive (
                scene_id, scene_type, name, topology_id, topology_revision,
                camera_id, reference_asset_id, validated_config_json,
                review_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, 'ready', ?, ?)
            """,
            [
                (
                    scene.scene_id,
                    scene.scene_type,
                    scene.name,
                    BUILTIN_TOPOLOGY_ID,
                    scene.camera_id,
                    (
                        scene_assets[scene.reference_path.resolve()]["asset_id"]
                        if scene.reference_path is not None
                        else None
                    ),
                    _canonical_json(scene.validated_config),
                    scene.created_at,
                    scene.updated_at,
                )
                for scene in scenes
            ],
        )
        executemany(
            connection,
            "INSERT INTO whitelist_entry (plate, note, added_at) VALUES (?, ?, ?)",
            [(item["plate"], item["note"], item["added_at"]) for item in whitelist],
        )
        execute(
            connection,
            """
            INSERT INTO detection_settings (
                singleton_id, enabled, yolo_threshold, lpr_threshold,
                frame_interval, device_preference, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(singleton_id) DO UPDATE SET
                enabled = excluded.enabled,
                yolo_threshold = excluded.yolo_threshold,
                lpr_threshold = excluded.lpr_threshold,
                frame_interval = excluded.frame_interval,
                device_preference = excluded.device_preference,
                updated_at = excluded.updated_at
            """,
            (
                int(DEFAULT_DETECTION_SETTINGS["enabled"]),
                DEFAULT_DETECTION_SETTINGS["yolo_threshold"],
                DEFAULT_DETECTION_SETTINGS["lpr_threshold"],
                DEFAULT_DETECTION_SETTINGS["frame_interval"],
                DEFAULT_DETECTION_SETTINGS["device_preference"],
                timestamp,
            ),
        )
        execute(
            connection,
            """
            INSERT INTO whitelist_setting (singleton_id, enabled, updated_at)
            VALUES (1, 1, ?)
            ON CONFLICT(singleton_id) DO UPDATE SET
                enabled = 1,
                updated_at = excluded.updated_at
            """,
            (timestamp,),
        )
        execute(
            connection,
            """
            INSERT INTO activation_state (
                singleton_id, stream_profile_id, topology_id, topology_revision,
                no_parking_scene_id, road_abnormal_scene_id, updated_at
            ) VALUES (1, ?, ?, 1, NULL, NULL, ?)
            ON CONFLICT(singleton_id) DO UPDATE SET
                stream_profile_id = excluded.stream_profile_id,
                topology_id = excluded.topology_id,
                topology_revision = excluded.topology_revision,
                no_parking_scene_id = NULL,
                road_abnormal_scene_id = NULL,
                updated_at = excluded.updated_at
            """,
            (BUILTIN_STREAM_PROFILE_ID, BUILTIN_TOPOLOGY_ID, timestamp),
        )
        marker = execute(
            connection,
            """
            UPDATE schema_metadata
            SET legacy_migration_completed = 1, updated_at = ?
            WHERE singleton_id = 1 AND legacy_migration_completed = 0
            """,
            (timestamp,),
        )
        if marker.rowcount != 1:
            raise LegacyMigrationError("legacy migration marker changed concurrently")


def migrate_legacy_configuration(
    repository: Any,
    asset_store: AssetStore,
    project_dir: Path,
    stream_sources: Mapping[str, str],
    *,
    paths: LegacyPaths | None = None,
) -> MigrationResult:
    """Convenience wrapper for the one-time legacy migration."""
    return LegacyMigrator(
        repository,
        asset_store,
        project_dir,
        stream_sources,
        paths=paths,
    ).migrate()


def _read_json(path: Path, label: str) -> Any:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise LegacyMigrationError(f"legacy {label} file does not exist: {path}")
    size_bytes = path.stat().st_size
    if size_bytes < 1 or size_bytes > _MAX_LEGACY_JSON_BYTES:
        raise LegacyMigrationError(f"legacy {label} file has an invalid size")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LegacyMigrationError(f"legacy {label} contains duplicate key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise LegacyMigrationError(f"legacy {label} contains invalid number: {value}")

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except UnicodeError as exc:
        raise LegacyMigrationError(f"legacy {label} is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise LegacyMigrationError(f"legacy {label} is invalid JSON: {exc}") from exc


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LegacyMigrationError(f"legacy {label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise LegacyMigrationError(f"legacy {label} must be an array")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        raise LegacyMigrationError(
            f"legacy {label} fields mismatch; missing={missing}, unknown={unknown}"
        )


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LegacyMigrationError(f"legacy {label} must be a non-empty string")
    if value != value.strip():
        raise LegacyMigrationError(f"legacy {label} has surrounding whitespace")
    return value


def _resolve_controlled_file(root: Path, value: Any, label: str) -> Path:
    raw_path = _required_string(value, label)
    if PureWindowsPath(raw_path).is_absolute() or PurePosixPath(raw_path).is_absolute():
        raise LegacyMigrationError(f"legacy {label} must be relative")
    portable = PurePosixPath(raw_path.replace("\\", "/"))
    if ".." in portable.parts or "." in portable.parts:
        raise LegacyMigrationError(f"legacy {label} path is not controlled")
    resolved_root = Path(root).resolve()
    candidate = resolved_root
    for part in portable.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise LegacyMigrationError(f"legacy {label} must not use symbolic links")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise LegacyMigrationError(f"legacy {label} escapes the project directory")
    if resolved.is_symlink() or not resolved.is_file():
        raise LegacyMigrationError(f"legacy {label} file does not exist: {raw_path}")
    return resolved


def _resolve_reference_file(root: Path, filename: str, label: str) -> Path:
    filename = _required_string(filename, label)
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise LegacyMigrationError(f"legacy {label} must be a filename")
    return _resolve_controlled_file(Path(root), filename, label)


def _validate_points(value: Any, label: str, *, minimum: int) -> list[tuple[float, float]]:
    raw_points = _require_list(value, label)
    if len(raw_points) < minimum or len(raw_points) > 200:
        raise LegacyMigrationError(
            f"legacy {label} must contain between {minimum} and 200 points"
        )
    points: list[tuple[float, float]] = []
    for index, raw_point in enumerate(raw_points):
        if not isinstance(raw_point, list) or len(raw_point) != 2:
            raise LegacyMigrationError(f"legacy {label}[{index}] must be [x, y]")
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in raw_point):
            raise LegacyMigrationError(f"legacy {label}[{index}] must be numeric")
        x, y = (float(item) for item in raw_point)
        if not math.isfinite(x) or not math.isfinite(y) or not (0 <= x <= 1 and 0 <= y <= 1):
            raise LegacyMigrationError(f"legacy {label}[{index}] is outside [0, 1]")
        points.append((x, y))
    return points


def _validate_scene_polygons(zones: Sequence[Mapping[str, Any]], label: str) -> None:
    for index, zone in enumerate(zones):
        points = _validate_points(zone["points"], f"{label}.zones[{index}].points", minimum=3)
        if _polygon_area(points) < 1e-5:
            raise LegacyMigrationError(f"legacy {label}.zones[{index}] is degenerate")


def _polygon_area(points: Sequence[tuple[float, float]]) -> float:
    return abs(
        sum(
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
            for index in range(len(points))
        )
    ) / 2.0


def _legacy_timestamp(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LegacyMigrationError(f"legacy {label} timestamps must be numeric")
    timestamp = float(value)
    if not math.isfinite(timestamp) or timestamp < 0:
        raise LegacyMigrationError(f"legacy {label} timestamp is invalid")
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError) as exc:
        raise LegacyMigrationError(f"legacy {label} timestamp is out of range") from exc


def _stream_id(camera_id: str) -> str:
    return f"stream-{uuid5(NAMESPACE_URL, f'videotest:legacy-stream:{camera_id}').hex}"


def _node_id(segment_id: str, endpoint: str) -> str:
    return f"node-{uuid5(NAMESPACE_URL, f'videotest:legacy-node:{segment_id}:{endpoint}').hex}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
