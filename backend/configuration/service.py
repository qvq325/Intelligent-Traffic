"""Transactional business services for configuration management."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from backend.model_pipelines import ModelPipelineRegistry

from .errors import ConfigurationError, not_found
from .models import MODEL_PIPELINE_SCENE_KEYS, ModelPipelineBatchUpdate
from .security import (
    redact_mapping,
    redact_text,
    redact_url_credentials,
    redact_url_userinfo,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _row(row) -> dict | None:
    return dict(row) if row is not None else None


_MODEL_PIPELINE_FIELDS = (
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


class ConfigurationService:
    def __init__(
        self,
        repository,
        asset_store,
        *,
        model_pipeline_registry=None,
    ) -> None:
        self.repository = repository
        self.asset_store = asset_store
        self.model_pipeline_registry = (
            model_pipeline_registry
            if model_pipeline_registry is not None
            else ModelPipelineRegistry()
        )

    def list_cameras(self) -> list[dict]:
        return [
            dict(item)
            for item in self.repository.fetch_all(
                "SELECT camera_id, display_name, ordinal FROM camera ORDER BY ordinal"
            )
        ]

    def get_activation_state(self) -> dict:
        row = self.repository.fetch_one(
            """
            SELECT stream_profile_id, topology_id, topology_revision,
                   no_parking_scene_id, road_abnormal_scene_id, updated_at
            FROM activation_state WHERE singleton_id = 1
            """
        )
        if row is None:
            return {
                "stream_profile_id": None,
                "topology_id": None,
                "topology_revision": None,
                "no_parking_scene_id": None,
                "road_abnormal_scene_id": None,
                "updated_at": None,
            }
        return dict(row)

    def summary(self) -> dict:
        state = self.get_activation_state()
        counts = {}
        for table, key in (
            ("camera", "cameras"),
            ("stream_source", "streams"),
            ("stream_binding_profile", "stream_profiles"),
            ("topology_profile", "topologies"),
            ("scene_archive", "scenes"),
            ("asset", "assets"),
        ):
            counts[key] = int(self.repository.fetch_one(f"SELECT COUNT(*) AS count FROM {table}")["count"])
        failed = self.repository.fetch_one(
            """
            SELECT operation_id, operation_type, status, error_summary, started_at, finished_at
            FROM configuration_operation ORDER BY started_at DESC LIMIT 1
            """
        )
        recent_operations = [
            dict(item)
            for item in self.repository.fetch_all(
                """
                SELECT operation_id, operation_type, status, error_summary, started_at, finished_at
                FROM configuration_operation ORDER BY started_at DESC LIMIT 8
                """
            )
        ]
        metadata = self.repository.fetch_one(
            "SELECT schema_version FROM schema_metadata WHERE singleton_id = 1"
        )
        integrity = self.repository.integrity_check()
        return {
            "schema_version": int(metadata["schema_version"]) if metadata else 0,
            "activation": state,
            "counts": counts,
            "integrity": {"ok": integrity == ["ok"], "messages": integrity},
            "recent_operation": _row(failed),
            "recent_operations": recent_operations,
            "camera_catalog": self.list_cameras(),
            "database": str(self.repository.database_path),
            "repository": {
                "status": "ready" if integrity == ["ok"] else "failed",
                "database": str(self.repository.database_path),
                "schema_version": int(metadata["schema_version"]) if metadata else 0,
                "asset_count": counts["assets"],
                "backup_count": len(list((self.repository.database_path.parent / "backups").glob("*.zip"))),
            },
        }

    def list_streams(self, *, reveal_credentials: bool = False) -> list[dict]:
        rows = self.repository.fetch_all(
            """
            SELECT stream_id, name, rtsp_url, enabled, last_probe_status,
                   last_probe_at, created_at, updated_at
            FROM stream_source ORDER BY name COLLATE NOCASE, stream_id
            """
        )
        payloads = [self._stream_payload(row) for row in rows]
        if not reveal_credentials:
            for payload in payloads:
                payload["rtsp_url"] = redact_url_userinfo(payload["rtsp_url"])
        return payloads

    def get_stream(self, stream_id: str, *, reveal_credentials: bool = True) -> dict:
        row = self.repository.fetch_one(
            "SELECT * FROM stream_source WHERE stream_id = ?", (stream_id,)
        )
        if row is None:
            raise not_found("stream", stream_id)
        payload = self._stream_payload(row)
        if not reveal_credentials:
            payload["rtsp_url"] = redact_url_userinfo(payload["rtsp_url"])
        return payload

    def get_streams(
        self,
        stream_ids: list[str],
        *,
        reveal_credentials: bool = True,
    ) -> list[dict]:
        if not stream_ids:
            return []
        placeholders = ",".join("?" for _ in stream_ids)
        rows = self.repository.fetch_all(
            f"SELECT * FROM stream_source WHERE stream_id IN ({placeholders})",
            tuple(stream_ids),
        )
        by_id = {row["stream_id"]: row for row in rows}
        missing = [stream_id for stream_id in stream_ids if stream_id not in by_id]
        if missing:
            raise ConfigurationError(
                "STREAM_NOT_FOUND",
                "部分流不存在",
                status_code=404,
                details=[{"stream_id": stream_id} for stream_id in missing],
            )
        payloads = [self._stream_payload(by_id[stream_id]) for stream_id in stream_ids]
        if not reveal_credentials:
            for payload in payloads:
                payload["rtsp_url"] = redact_url_credentials(payload["rtsp_url"])
        return payloads

    def create_stream(self, values: dict) -> dict:
        stream_id = f"stream_{uuid4().hex}"
        try:
            with self.repository.transaction() as connection:
                self.repository.execute(
                    connection,
                    """
                    INSERT INTO stream_source (stream_id, name, rtsp_url, enabled)
                    VALUES (?, ?, ?, ?)
                    """,
                    (stream_id, values["name"], values["rtsp_url"], int(values.get("enabled", True))),
                )
                self._insert_audit(
                    connection, "create_stream", "stream", stream_id, "succeeded", {"name": values["name"]}
                )
        except sqlite3.IntegrityError as exc:
            raise ConfigurationError("STREAM_CONFLICT", "流名称或地址不符合约束", details=[{"reason": str(exc)}]) from exc
        return self.get_stream(stream_id)

    def create_stream_batch(self, values: list[dict]) -> dict:
        streams = [
            {
                "stream_id": f"stream_{uuid4().hex}",
                "name": item["name"],
                "rtsp_url": item["rtsp_url"],
                "enabled": bool(item.get("enabled", True)),
            }
            for item in values
        ]
        created_rows = []
        try:
            with self.repository.transaction() as connection:
                names = [stream["name"] for stream in streams]
                placeholders = ",".join("?" for _ in names)
                existing_rows = self.repository.fetch_all(
                    f"SELECT stream_id, name FROM stream_source WHERE name IN ({placeholders})",
                    tuple(names),
                    connection=connection,
                )
                existing_by_name = {row["name"]: row for row in existing_rows}
                conflicts = [
                    {
                        "field": "name",
                        "name": name,
                        "stream_id": existing_by_name[name]["stream_id"],
                        "reason": "already_exists",
                    }
                    for name in names
                    if name in existing_by_name
                ]
                if conflicts:
                    raise ConfigurationError(
                        "STREAM_BATCH_CONFLICT",
                        "批量新增包含已存在的流名称",
                        status_code=409,
                        details=conflicts,
                    )
                for stream in streams:
                    self.repository.execute(
                        connection,
                        """
                        INSERT INTO stream_source (stream_id, name, rtsp_url, enabled)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            stream["stream_id"],
                            stream["name"],
                            stream["rtsp_url"],
                            int(stream["enabled"]),
                        ),
                    )
                    self._insert_audit(
                        connection,
                        "create_stream_batch",
                        "stream",
                        stream["stream_id"],
                        "succeeded",
                        {"name": stream["name"], "enabled": stream["enabled"]},
                    )
                stream_ids = [stream["stream_id"] for stream in streams]
                placeholders = ",".join("?" for _ in stream_ids)
                created_rows = self.repository.fetch_all(
                    f"SELECT * FROM stream_source WHERE stream_id IN ({placeholders})",
                    tuple(stream_ids),
                    connection=connection,
                )
        except sqlite3.IntegrityError as exc:
            raise ConfigurationError(
                "STREAM_BATCH_CONFLICT",
                "批量新增与现有流或批次内数据冲突",
                status_code=409,
                details=[{"reason": str(exc)[:500]}],
            ) from exc
        stream_ids = [stream["stream_id"] for stream in streams]
        by_id = {row["stream_id"]: row for row in created_rows}
        payloads = [self._stream_payload(by_id[stream_id]) for stream_id in stream_ids]
        for payload in payloads:
            payload["rtsp_url"] = redact_url_credentials(payload["rtsp_url"])
        return {
            "created": len(stream_ids),
            "streams": payloads,
        }

    def update_stream(self, stream_id: str, values: dict) -> dict:
        current = self.get_stream(stream_id)
        merged = {**current, **{key: value for key, value in values.items() if value is not None}}
        try:
            with self.repository.transaction() as connection:
                self.repository.execute(
                    connection,
                    """
                    UPDATE stream_source
                    SET name = ?, rtsp_url = ?, enabled = ?, updated_at = ?
                    WHERE stream_id = ?
                    """,
                    (merged["name"], merged["rtsp_url"], int(merged["enabled"]), _now(), stream_id),
                )
                self._insert_audit(
                    connection, "update_stream", "stream", stream_id, "succeeded", {"name": merged["name"]}
                )
        except sqlite3.IntegrityError as exc:
            raise ConfigurationError("STREAM_CONFLICT", "流更新违反唯一性或格式约束", details=[{"reason": str(exc)}]) from exc
        return self.get_stream(stream_id)

    def prepare_stream_batch_update(self, values: list[dict]) -> list[dict]:
        stream_ids = [item["stream_id"] for item in values]
        current_streams = self.get_streams(stream_ids, reveal_credentials=True)
        current_by_id = {stream["stream_id"]: stream for stream in current_streams}
        entries = []
        for item in values:
            current = current_by_id[item["stream_id"]]
            target = {
                "stream_id": item["stream_id"],
                "name": item["name"],
                "rtsp_url": item["rtsp_url"],
                "enabled": bool(item["enabled"]),
            }
            changed_fields = [
                key for key in ("name", "rtsp_url", "enabled")
                if target[key] != current[key]
            ]
            entries.append(
                {
                    "current": current,
                    "target": target,
                    "changed_fields": changed_fields,
                }
            )

        target_by_id = {entry["target"]["stream_id"]: entry["target"] for entry in entries}
        names: dict[str, list[str]] = {}
        for stream in self.list_streams(reveal_credentials=True):
            target = target_by_id.get(stream["stream_id"], stream)
            names.setdefault(target["name"], []).append(stream["stream_id"])
        conflicts = [
            {"name": name, "stream_ids": ids}
            for name, ids in names.items()
            if len(ids) > 1
        ]
        if conflicts:
            raise ConfigurationError(
                "STREAM_BATCH_CONFLICT",
                "批量修改后的流名称存在冲突",
                status_code=409,
                details=conflicts,
            )
        return entries

    def update_stream_batch(
        self,
        entries: list[dict],
        *,
        probe_results: list[dict] | None = None,
    ) -> list[dict]:
        timestamp = _now()
        probes = {item["stream_id"]: item for item in (probe_results or [])}
        temporary_names = {
            entry["target"]["stream_id"]: f"__stream_batch_{uuid4().hex}"
            for entry in entries
            if "name" in entry["changed_fields"]
        }
        updated_rows = []
        try:
            with self.repository.transaction() as connection:
                for entry in entries:
                    current = entry["current"]
                    temporary_name = temporary_names.get(current["stream_id"])
                    if temporary_name is None:
                        continue
                    cursor = self.repository.execute(
                        connection,
                        """
                        UPDATE stream_source SET name = ?
                        WHERE stream_id = ? AND name = ? AND rtsp_url = ? AND enabled = ?
                        """,
                        (
                            temporary_name,
                            current["stream_id"],
                            current["name"],
                            current["rtsp_url"],
                            int(current["enabled"]),
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ConfigurationError(
                            "STREAM_BATCH_CONFLICT",
                            "流已被其他配置操作修改",
                            status_code=409,
                            details=[{"stream_id": current["stream_id"]}],
                        )

                for entry in entries:
                    current = entry["current"]
                    target = entry["target"]
                    expected_name = temporary_names.get(current["stream_id"], current["name"])
                    cursor = self.repository.execute(
                        connection,
                        """
                        UPDATE stream_source
                        SET name = ?, rtsp_url = ?, enabled = ?, updated_at = ?
                        WHERE stream_id = ? AND name = ? AND rtsp_url = ? AND enabled = ?
                        """,
                        (
                            target["name"],
                            target["rtsp_url"],
                            int(target["enabled"]),
                            timestamp,
                            target["stream_id"],
                            expected_name,
                            current["rtsp_url"],
                            int(current["enabled"]),
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ConfigurationError(
                            "STREAM_BATCH_CONFLICT",
                            "流已被其他配置操作修改",
                            status_code=409,
                            details=[{"stream_id": target["stream_id"]}],
                        )
                    probe = probes.get(target["stream_id"])
                    if probe is not None:
                        summary = json.dumps(
                            {
                                key: probe.get(key)
                                for key in (
                                    "ok", "code", "message", "elapsed_ms", "width", "height"
                                )
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        self.repository.execute(
                            connection,
                            """
                            UPDATE stream_source
                            SET last_probe_status = ?, last_probe_at = ?
                            WHERE stream_id = ?
                            """,
                            (summary, timestamp, target["stream_id"]),
                        )
                    self._insert_audit(
                        connection,
                        "update_stream_batch",
                        "stream",
                        target["stream_id"],
                        "succeeded",
                        {"name": target["name"], "enabled": target["enabled"]},
                    )
                stream_ids = [entry["target"]["stream_id"] for entry in entries]
                placeholders = ",".join("?" for _ in stream_ids)
                updated_rows = self.repository.fetch_all(
                    f"SELECT * FROM stream_source WHERE stream_id IN ({placeholders})",
                    tuple(stream_ids),
                    connection=connection,
                )
        except sqlite3.IntegrityError as exc:
            raise ConfigurationError(
                "STREAM_BATCH_CONFLICT",
                "批量修改违反流名称唯一性约束",
                status_code=409,
                details=[{"reason": str(exc)[:500]}],
            ) from exc
        stream_ids = [entry["target"]["stream_id"] for entry in entries]
        by_id = {row["stream_id"]: row for row in updated_rows}
        payloads = [self._stream_payload(by_id[stream_id]) for stream_id in stream_ids]
        for payload in payloads:
            payload["rtsp_url"] = redact_url_credentials(payload["rtsp_url"])
        return payloads

    def delete_stream(self, stream_id: str) -> dict:
        stream = self.get_stream(stream_id, reveal_credentials=False)
        dependencies = [
            dict(item)
            for item in self.repository.fetch_all(
                """
                SELECT p.profile_id, p.name FROM stream_binding b
                JOIN stream_binding_profile p ON p.profile_id = b.profile_id
                WHERE b.stream_id = ? ORDER BY p.name
                """,
                (stream_id,),
            )
        ]
        if dependencies:
            raise ConfigurationError(
                "STREAM_IN_USE", "流仍被关联方案引用", status_code=409, details=dependencies
            )
        with self.repository.transaction() as connection:
            self.repository.execute(connection, "DELETE FROM stream_source WHERE stream_id = ?", (stream_id,))
            self._insert_audit(connection, "delete_stream", "stream", stream_id, "succeeded", stream)
        return {"deleted": True, "stream_id": stream_id}

    def delete_stream_batch(self, stream_ids: list[str]) -> dict:
        placeholders = ",".join("?" for _ in stream_ids)
        with self.repository.transaction() as connection:
            rows = self.repository.fetch_all(
                f"SELECT stream_id, name FROM stream_source WHERE stream_id IN ({placeholders})",
                tuple(stream_ids),
                connection=connection,
            )
            by_id = {row["stream_id"]: row for row in rows}
            missing = [stream_id for stream_id in stream_ids if stream_id not in by_id]
            if missing:
                raise ConfigurationError(
                    "STREAM_NOT_FOUND",
                    "部分流不存在",
                    status_code=404,
                    details=[{"stream_id": stream_id} for stream_id in missing],
                )
            dependency_rows = self.repository.fetch_all(
                f"""
                SELECT b.stream_id, p.profile_id, p.name AS profile_name
                FROM stream_binding b
                JOIN stream_binding_profile p ON p.profile_id = b.profile_id
                WHERE b.stream_id IN ({placeholders})
                ORDER BY b.stream_id, p.name
                """,
                tuple(stream_ids),
                connection=connection,
            )
            dependencies: dict[str, dict] = {}
            for row in dependency_rows:
                detail = dependencies.setdefault(
                    row["stream_id"],
                    {
                        "stream_id": row["stream_id"],
                        "name": by_id[row["stream_id"]]["name"],
                        "profiles": [],
                    },
                )
                detail["profiles"].append(
                    {"profile_id": row["profile_id"], "name": row["profile_name"]}
                )
            if dependencies:
                raise ConfigurationError(
                    "STREAM_BATCH_IN_USE",
                    "所选流中存在仍被关联方案引用的项目",
                    status_code=409,
                    details=[dependencies[stream_id] for stream_id in stream_ids if stream_id in dependencies],
                )
            for stream_id in stream_ids:
                stream = by_id[stream_id]
                self.repository.execute(
                    connection,
                    "DELETE FROM stream_source WHERE stream_id = ?",
                    (stream_id,),
                )
                self._insert_audit(
                    connection,
                    "delete_stream_batch",
                    "stream",
                    stream_id,
                    "succeeded",
                    {"name": stream["name"]},
                )
        return {"deleted": len(stream_ids), "stream_ids": list(stream_ids)}

    def record_probe_results(self, results: list[dict]) -> None:
        if not results:
            return
        timestamp = _now()
        with self.repository.transaction() as connection:
            for result in results:
                summary = json.dumps(
                    {key: result.get(key) for key in ("ok", "code", "message", "elapsed_ms", "width", "height")},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                self.repository.execute(
                    connection,
                    """
                    UPDATE stream_source SET last_probe_status = ?, last_probe_at = ?, updated_at = ?
                    WHERE stream_id = ?
                    """,
                    (summary, timestamp, timestamp, result["stream_id"]),
                )

    def register_asset(self, metadata: dict) -> dict:
        try:
            with self.repository.transaction() as connection:
                self.repository.execute(
                    connection,
                    """
                    INSERT OR IGNORE INTO asset
                        (asset_id, kind, relative_path, sha256, size_bytes, media_type, width, height)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        metadata["asset_id"], metadata["kind"], metadata["relative_path"],
                        metadata["sha256"], metadata["size_bytes"], metadata["media_type"],
                        metadata["width"], metadata["height"],
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ConfigurationError("ASSET_CONFLICT", "资源元数据与已有内容冲突", details=[{"reason": str(exc)}]) from exc
        return dict(self.repository.fetch_one("SELECT * FROM asset WHERE kind = ? AND sha256 = ?", (metadata["kind"], metadata["sha256"])))

    def attach_topology_map(self, topology_id: str, asset: dict) -> dict:
        topology = self.get_topology(topology_id)
        if topology["is_builtin"]:
            raise ConfigurationError("BUILTIN_TOPOLOGY_READ_ONLY", "内置拓扑不可修改", status_code=409)
        stored = self.register_asset(asset)
        with self.repository.transaction() as connection:
            self.repository.execute(
                connection,
                """
                UPDATE topology_profile SET map_asset_id = ?, map_width = ?, map_height = ?,
                    updated_at = ? WHERE topology_id = ?
                """,
                (stored["asset_id"], stored["width"], stored["height"], _now(), topology_id),
            )
            self._insert_audit(connection, "update_topology_map", "topology", topology_id, "succeeded", {"asset_id": stored["asset_id"]})
        return self.get_topology(topology_id)

    def list_stream_profiles(self) -> list[dict]:
        state = self.get_activation_state()
        rows = self.repository.fetch_all(
            """
            SELECT p.*, COUNT(b.camera_id) AS binding_count
            FROM stream_binding_profile p
            LEFT JOIN stream_binding b ON b.profile_id = p.profile_id
            GROUP BY p.profile_id ORDER BY p.is_builtin DESC, p.name
            """
        )
        return [
            {
                **dict(item),
                "is_builtin": bool(item["is_builtin"]),
                "is_active": item["profile_id"] == state["stream_profile_id"],
            }
            for item in rows
        ]

    def get_stream_profile(self, profile_id: str) -> dict:
        profile = self.repository.fetch_one(
            "SELECT * FROM stream_binding_profile WHERE profile_id = ?", (profile_id,)
        )
        if profile is None:
            raise not_found("stream_profile", profile_id)
        bindings = self.repository.fetch_all(
            """
            SELECT b.camera_id, c.display_name AS camera_name, b.stream_id,
                   s.name AS stream_name, s.rtsp_url, s.enabled, s.last_probe_status, s.last_probe_at
            FROM stream_binding b
            JOIN camera c ON c.camera_id = b.camera_id
            JOIN stream_source s ON s.stream_id = b.stream_id
            WHERE b.profile_id = ? ORDER BY c.ordinal
            """,
            (profile_id,),
        )
        payload = dict(profile)
        payload["is_builtin"] = bool(payload["is_builtin"])
        payload["bindings"] = [
            {**dict(item), "enabled": bool(item["enabled"])} for item in bindings
        ]
        payload["is_active"] = self.get_activation_state()["stream_profile_id"] == profile_id
        return payload

    def create_stream_profile(self, values: dict) -> dict:
        profile_id = f"profile_{uuid4().hex}"
        self._write_stream_profile(profile_id, values, creating=True)
        return self.get_stream_profile(profile_id)

    def update_stream_profile(self, profile_id: str, values: dict) -> dict:
        current = self.get_stream_profile(profile_id)
        if current["is_builtin"]:
            raise ConfigurationError("BUILTIN_PROFILE_READ_ONLY", "内置关联方案不可修改", status_code=409)
        self._write_stream_profile(profile_id, values, creating=False)
        return self.get_stream_profile(profile_id)

    def clone_stream_profile(self, profile_id: str) -> dict:
        current = self.get_stream_profile(profile_id)
        names = {item["name"] for item in self.list_stream_profiles()}
        base = f"{current['name']} 副本"
        name = base
        suffix = 2
        while name in names:
            name = f"{base} {suffix}"
            suffix += 1
        return self.create_stream_profile(
            {
                "name": name,
                "description": current["description"],
                "bindings": [
                    {"camera_id": item["camera_id"], "stream_id": item["stream_id"]}
                    for item in current["bindings"]
                ],
            }
        )

    def delete_stream_profile(self, profile_id: str) -> dict:
        profile = self.get_stream_profile(profile_id)
        if profile["is_builtin"]:
            raise ConfigurationError("BUILTIN_PROFILE_PROTECTED", "内置关联方案不可删除", status_code=409)
        if profile["is_active"]:
            raise ConfigurationError("ACTIVE_PROFILE_PROTECTED", "当前激活方案不可删除", status_code=409)
        with self.repository.transaction() as connection:
            self.repository.execute(connection, "DELETE FROM stream_binding WHERE profile_id = ?", (profile_id,))
            self.repository.execute(connection, "DELETE FROM stream_binding_profile WHERE profile_id = ?", (profile_id,))
            self._insert_audit(connection, "delete_stream_profile", "stream_profile", profile_id, "succeeded", {"name": profile["name"]})
        return {"deleted": True, "profile_id": profile_id}

    def _write_stream_profile(self, profile_id: str, values: dict, *, creating: bool) -> None:
        bindings = values.get("bindings", [])
        camera_ids = [item["camera_id"] for item in bindings]
        stream_ids = [item["stream_id"] for item in bindings]
        if len(camera_ids) != len(set(camera_ids)) or len(stream_ids) != len(set(stream_ids)):
            raise ConfigurationError("STREAM_PROFILE_DUPLICATE_BINDING", "同一方案内摄像头和流必须一对一")
        timestamp = _now()
        try:
            with self.repository.transaction() as connection:
                if creating:
                    self.repository.execute(
                        connection,
                        "INSERT INTO stream_binding_profile (profile_id, name, description) VALUES (?, ?, ?)",
                        (profile_id, values["name"], values.get("description", "")),
                    )
                else:
                    self.repository.execute(
                        connection,
                        "UPDATE stream_binding_profile SET name = ?, description = ?, updated_at = ? WHERE profile_id = ?",
                        (values["name"], values.get("description", ""), timestamp, profile_id),
                    )
                    self.repository.execute(connection, "DELETE FROM stream_binding WHERE profile_id = ?", (profile_id,))
                self.repository.executemany(
                    connection,
                    "INSERT INTO stream_binding (profile_id, camera_id, stream_id) VALUES (?, ?, ?)",
                    ((profile_id, item["camera_id"], item["stream_id"]) for item in bindings),
                )
                self._insert_audit(
                    connection,
                    "create_stream_profile" if creating else "update_stream_profile",
                    "stream_profile",
                    profile_id,
                    "succeeded",
                    {"name": values["name"], "binding_count": len(bindings)},
                )
        except sqlite3.IntegrityError as exc:
            raise ConfigurationError("STREAM_PROFILE_INVALID", "关联方案包含未知引用或重复名称", details=[{"reason": str(exc)}]) from exc

    def list_topologies(self) -> list[dict]:
        state = self.get_activation_state()
        rows = self.repository.fetch_all(
            """
            SELECT p.*, COUNT(DISTINCT c.camera_id) AS camera_count,
                   COUNT(DISTINCT s.segment_id) AS segment_count
            FROM topology_profile p
            LEFT JOIN topology_camera c ON c.topology_id = p.topology_id
            LEFT JOIN road_segment s ON s.topology_id = p.topology_id
            GROUP BY p.topology_id ORDER BY p.is_builtin DESC, p.name
            """
        )
        return [
            {
                **dict(item),
                "is_builtin": bool(item["is_builtin"]),
                "is_active": item["topology_id"] == state["topology_id"],
            }
            for item in rows
        ]

    def get_topology(self, topology_id: str) -> dict:
        profile = self.repository.fetch_one(
            "SELECT * FROM topology_profile WHERE topology_id = ?", (topology_id,)
        )
        if profile is None:
            raise not_found("topology", topology_id)
        nodes = [dict(item) for item in self.repository.fetch_all(
            "SELECT node_id, x, y, node_type FROM topology_node WHERE topology_id = ? ORDER BY node_id", (topology_id,)
        )]
        segments = []
        for item in self.repository.fetch_all(
            "SELECT * FROM road_segment WHERE topology_id = ? ORDER BY segment_id", (topology_id,)
        ):
            value = dict(item)
            value["points"] = json.loads(value.pop("points_json"))
            value.pop("topology_id", None)
            segments.append(value)
        cameras = [dict(item) for item in self.repository.fetch_all(
            """
            SELECT c.camera_id, f.display_name, c.x, c.y, c.heading, c.view_range, c.segment_id
            FROM topology_camera c JOIN camera f ON f.camera_id = c.camera_id
            WHERE c.topology_id = ? ORDER BY f.ordinal
            """, (topology_id,)
        )]
        payload = dict(profile)
        payload["is_builtin"] = bool(payload["is_builtin"])
        payload["nodes"] = nodes
        payload["segments"] = segments
        payload["cameras"] = cameras
        payload["is_active"] = self.get_activation_state()["topology_id"] == topology_id
        if payload.get("map_asset_id"):
            payload["map_asset"] = _row(self.repository.fetch_one("SELECT * FROM asset WHERE asset_id = ?", (payload["map_asset_id"],)))
        else:
            payload["map_asset"] = None
        return payload

    def create_topology(self, values: dict) -> dict:
        topology_id = f"topology_{uuid4().hex}"
        if not values.get("map_asset_id"):
            state = self.get_activation_state()
            if not state["topology_id"]:
                raise ConfigurationError("TOPOLOGY_MAP_REQUIRED", "拓扑必须关联独立底图")
            source = self.get_topology(state["topology_id"])
            values = {
                **values,
                "map_asset_id": source["map_asset_id"],
                "map_width": source["map_width"],
                "map_height": source["map_height"],
                "nodes": source["nodes"],
                "segments": source["segments"],
                "cameras": source["cameras"],
            }
        self._write_topology(topology_id, values, creating=True)
        return self.get_topology(topology_id)

    def update_topology(self, topology_id: str, values: dict) -> dict:
        current = self.get_topology(topology_id)
        if current["is_builtin"]:
            raise ConfigurationError("BUILTIN_TOPOLOGY_READ_ONLY", "内置拓扑不可修改", status_code=409)
        self._write_topology(topology_id, values, creating=False)
        return self.get_topology(topology_id)

    def _write_topology(self, topology_id: str, values: dict, *, creating: bool) -> None:
        nodes = values.get("nodes", [])
        segments = values.get("segments", [])
        cameras = values.get("cameras", [])
        node_ids = {item["node_id"] for item in nodes}
        segment_ids = {item["segment_id"] for item in segments}
        if len(node_ids) != len(nodes) or len(segment_ids) != len(segments):
            raise ConfigurationError("TOPOLOGY_DUPLICATE_ID", "拓扑节点或道路 ID 重复")
        for segment in segments:
            if segment["start_node_id"] not in node_ids or segment["end_node_id"] not in node_ids:
                raise ConfigurationError("TOPOLOGY_NODE_REFERENCE_INVALID", "道路引用了不存在的起止节点", details=[{"segment_id": segment["segment_id"]}])
        for camera in cameras:
            if camera["segment_id"] not in segment_ids:
                raise ConfigurationError("TOPOLOGY_CAMERA_REFERENCE_INVALID", "摄像头引用了不存在的道路", details=[{"camera_id": camera["camera_id"]}])
        current = None if creating else self.get_topology(topology_id)
        map_asset_id = values.get("map_asset_id") or (current or {}).get("map_asset_id")
        if not map_asset_id:
            raise ConfigurationError("TOPOLOGY_MAP_REQUIRED", "拓扑必须关联独立底图")
        try:
            with self.repository.transaction() as connection:
                if creating:
                    self.repository.execute(
                        connection,
                        """
                        INSERT INTO topology_profile
                            (topology_id, name, revision, map_asset_id, map_width, map_height)
                        VALUES (?, ?, 1, ?, ?, ?)
                        """,
                        (topology_id, values["name"], map_asset_id, values["map_width"], values["map_height"]),
                    )
                    revision = 1
                else:
                    revision = int(current["revision"]) + 1
                    self.repository.execute(connection, "DELETE FROM topology_camera WHERE topology_id = ?", (topology_id,))
                    self.repository.execute(connection, "DELETE FROM road_segment WHERE topology_id = ?", (topology_id,))
                    self.repository.execute(connection, "DELETE FROM topology_node WHERE topology_id = ?", (topology_id,))
                    self.repository.execute(
                        connection,
                        """
                        UPDATE topology_profile SET name = ?, revision = ?, map_asset_id = ?,
                            map_width = ?, map_height = ?, updated_at = ? WHERE topology_id = ?
                        """,
                        (values["name"], revision, map_asset_id, values["map_width"], values["map_height"], _now(), topology_id),
                    )
                self.repository.executemany(
                    connection,
                    "INSERT INTO topology_node (topology_id, node_id, x, y, node_type) VALUES (?, ?, ?, ?, ?)",
                    ((topology_id, item["node_id"], item["x"], item["y"], item["node_type"]) for item in nodes),
                )
                self.repository.executemany(
                    connection,
                    """
                    INSERT INTO road_segment
                        (topology_id, segment_id, name, points_json, geometry_type,
                         start_node_id, end_node_id, direction, level, capacity, road_width)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (topology_id, item["segment_id"], item["name"], json.dumps(item["points"], separators=(",", ":")), item["geometry_type"], item["start_node_id"], item["end_node_id"], item["direction"], item["level"], item["capacity"], item["road_width"])
                        for item in segments
                    ),
                )
                self.repository.executemany(
                    connection,
                    """
                    INSERT INTO topology_camera
                        (topology_id, camera_id, x, y, heading, view_range, segment_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ((topology_id, item["camera_id"], item["x"], item["y"], item["heading"], item["view_range"], item["segment_id"]) for item in cameras),
                )
                if not creating:
                    self.repository.execute(
                        connection,
                        """
                        UPDATE scene_archive
                        SET review_status = 'needs_review', updated_at = ?
                        WHERE scene_type = 'road_abnormal'
                          AND topology_id = ?
                          AND topology_revision < ?
                        """,
                        (_now(), topology_id, revision),
                    )
                    state = self.repository.fetch_one("SELECT * FROM activation_state WHERE singleton_id = 1", connection=connection)
                    if state and state["topology_id"] == topology_id:
                        self.repository.execute(
                            connection,
                            """
                            UPDATE activation_state
                            SET topology_revision = ?, road_abnormal_scene_id = NULL,
                                updated_at = ?
                            WHERE singleton_id = 1
                            """,
                            (revision, _now()),
                        )
                self._insert_audit(
                    connection,
                    "create_topology" if creating else "update_topology",
                    "topology",
                    topology_id,
                    "succeeded",
                    {"name": values["name"], "revision": revision},
                )
        except sqlite3.IntegrityError as exc:
            raise ConfigurationError("TOPOLOGY_INVALID", "拓扑包含无效引用或重复名称", details=[{"reason": str(exc)}]) from exc

    def clone_topology(self, topology_id: str) -> dict:
        current = self.get_topology(topology_id)
        names = {item["name"] for item in self.list_topologies()}
        base = f"{current['name']} 副本"
        name = base
        index = 2
        while name in names:
            name = f"{base} {index}"
            index += 1
        return self.create_topology(
            {
                "name": name,
                "map_asset_id": current["map_asset_id"],
                "map_width": current["map_width"],
                "map_height": current["map_height"],
                "nodes": current["nodes"],
                "segments": current["segments"],
                "cameras": current["cameras"],
            }
        )

    def delete_topology(self, topology_id: str) -> dict:
        topology = self.get_topology(topology_id)
        if topology["is_builtin"]:
            raise ConfigurationError("BUILTIN_TOPOLOGY_PROTECTED", "内置拓扑不可删除", status_code=409)
        if topology["is_active"]:
            raise ConfigurationError("ACTIVE_TOPOLOGY_PROTECTED", "当前激活拓扑不可删除", status_code=409)
        scenes = [
            dict(item)
            for item in self.repository.fetch_all(
                """
                SELECT scene_id, name
                FROM scene_archive
                WHERE scene_type = 'road_abnormal' AND topology_id = ?
                """,
                (topology_id,),
            )
        ]
        if scenes:
            raise ConfigurationError("TOPOLOGY_IN_USE", "拓扑仍被场景引用", status_code=409, details=scenes)
        with self.repository.transaction() as connection:
            for table in ("topology_camera", "road_segment", "topology_node"):
                self.repository.execute(connection, f"DELETE FROM {table} WHERE topology_id = ?", (topology_id,))
            self.repository.execute(connection, "DELETE FROM topology_profile WHERE topology_id = ?", (topology_id,))
            self._insert_audit(connection, "delete_topology", "topology", topology_id, "succeeded", {"name": topology["name"]})
        return {"deleted": True, "topology_id": topology_id}

    def validate_topology(self, topology: dict) -> None:
        expected = {item["camera_id"] for item in self.list_cameras()}
        actual = {item["camera_id"] for item in topology["cameras"]}
        if actual != expected:
            raise ConfigurationError(
                "TOPOLOGY_CAMERA_CATALOG_MISMATCH",
                "拓扑必须完整覆盖固定摄像头目录",
                details=[{"missing_camera_ids": sorted(expected - actual), "extra_camera_ids": sorted(actual - expected)}],
            )
        if not topology["segments"] or not topology["nodes"]:
            raise ConfigurationError("TOPOLOGY_GRAPH_EMPTY", "拓扑必须包含道路和连通节点")
        if not topology.get("map_asset"):
            raise ConfigurationError("TOPOLOGY_MAP_REQUIRED", "拓扑底图不存在")
        try:
            self.asset_store.verify(topology["map_asset"])
        except ValueError as exc:
            raise ConfigurationError("TOPOLOGY_MAP_INVALID", "拓扑底图完整性校验失败", details=[{"reason": str(exc)}]) from exc

    def commit_topology_activation(self, topology_id: str, revision: int, scene_ids: list[str]) -> list[dict]:
        deactivated = [self.get_scene(scene_id) for scene_id in scene_ids if self._scene_exists(scene_id)]
        values = {"topology_id": topology_id, "topology_revision": revision}
        for scene in deactivated:
            values[f"{scene['scene_type']}_scene_id"] = None
        self.update_activation_state(**values)
        return deactivated

    def list_scenes(self, *, scene_type: str | None = None, topology_id: str | None = None, camera_id: str | None = None) -> list[dict]:
        clauses, params = [], []
        for column, value in (("scene_type", scene_type), ("topology_id", topology_id), ("camera_id", camera_id)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.repository.fetch_all(f"SELECT scene_id FROM scene_archive {where} ORDER BY scene_type, name", tuple(params))
        return [self.get_scene(item["scene_id"]) for item in rows]

    def get_scene(self, scene_id: str) -> dict:
        row = self.repository.fetch_one("SELECT * FROM scene_archive WHERE scene_id = ?", (scene_id,))
        if row is None:
            raise not_found("scene", scene_id)
        payload = dict(row)
        payload["config"] = json.loads(payload.pop("validated_config_json"))
        payload["reference_asset"] = _row(self.repository.fetch_one("SELECT * FROM asset WHERE asset_id = ?", (payload["reference_asset_id"],))) if payload["reference_asset_id"] else None
        state = self.get_activation_state()
        payload["is_active"] = state[f"{payload['scene_type']}_scene_id"] == scene_id
        return payload

    def upsert_scene_archive(self, values: dict) -> dict:
        values = dict(values)
        scene_type = values.get("scene_type")
        camera_id = values.get("camera_id")
        camera = self.repository.fetch_one(
            "SELECT camera_id FROM camera WHERE camera_id = ?",
            (camera_id,),
        )
        if camera is None:
            raise ConfigurationError(
                "SCENE_CAMERA_INVALID",
                "场景摄像头不在固定全局摄像头目录中",
                details=[{"camera_id": camera_id}],
            )
        if scene_type == "no_parking":
            values["topology_id"] = None
            values["topology_revision"] = None
        elif not values.get("topology_id") or values.get("topology_revision") is None:
            raise ConfigurationError(
                "SCENE_TOPOLOGY_REQUIRED",
                "道路异常场景必须绑定道路拓扑",
            )

        scene_id = values.get("scene_id") or f"scene_{uuid4().hex}"
        exists = self._scene_exists(scene_id)
        try:
            with self.repository.transaction() as connection:
                if exists:
                    active = self.get_scene(scene_id)
                    self.repository.execute(
                        connection,
                        """
                        UPDATE scene_archive SET name = ?, topology_id = ?, topology_revision = ?,
                            camera_id = ?, reference_asset_id = ?, validated_config_json = ?,
                            review_status = 'ready', updated_at = ? WHERE scene_id = ?
                        """,
                        (values["name"], values["topology_id"], values["topology_revision"], values["camera_id"], values.get("reference_asset_id"), json.dumps(values["config"], ensure_ascii=False, separators=(",", ":")), _now(), scene_id),
                    )
                else:
                    self.repository.execute(
                        connection,
                        """
                        INSERT INTO scene_archive
                            (scene_id, scene_type, name, topology_id, topology_revision,
                             camera_id, reference_asset_id, validated_config_json, review_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready')
                        """,
                        (scene_id, values["scene_type"], values["name"], values["topology_id"], values["topology_revision"], values["camera_id"], values.get("reference_asset_id"), json.dumps(values["config"], ensure_ascii=False, separators=(",", ":"))),
                    )
                self._insert_audit(connection, "update_scene" if exists else "create_scene", "scene", scene_id, "succeeded", {"scene_type": values["scene_type"], "name": values["name"]})
        except sqlite3.IntegrityError as exc:
            raise ConfigurationError("SCENE_INVALID", "场景引用或参数无效", details=[{"reason": str(exc)}]) from exc
        return self.get_scene(scene_id)

    def delete_scene(self, scene_id: str) -> dict:
        scene = self.get_scene(scene_id)
        if scene["is_active"]:
            raise ConfigurationError("ACTIVE_SCENE_PROTECTED", "当前运行场景不可删除", status_code=409)
        with self.repository.transaction() as connection:
            self.repository.execute(connection, "DELETE FROM scene_archive WHERE scene_id = ?", (scene_id,))
            self._insert_audit(connection, "delete_scene", "scene", scene_id, "succeeded", {"name": scene["name"]})
        return {"deleted": True, "scene_id": scene_id}

    def resolve_camera_stream(self, camera_id: str) -> dict | None:
        state = self.get_activation_state()
        if not state["stream_profile_id"]:
            return None
        row = self.repository.fetch_one(
            """
            SELECT s.stream_id, s.name, s.rtsp_url, s.enabled
            FROM stream_binding b JOIN stream_source s ON s.stream_id = b.stream_id
            WHERE b.profile_id = ? AND b.camera_id = ?
            """,
            (state["stream_profile_id"], camera_id),
        )
        return {**dict(row), "enabled": bool(row["enabled"])} if row else None

    def detection_settings(self) -> dict:
        row = self.repository.fetch_one("SELECT * FROM detection_settings WHERE singleton_id = 1")
        payload = dict(row)
        payload["enabled"] = bool(payload["enabled"])
        payload["interval"] = payload.pop("frame_interval")
        return payload

    def model_pipeline_settings(self) -> dict:
        return {
            "presets": self.model_pipeline_registry.list_presets(),
            "devices": self.model_pipeline_registry.list_devices(),
            "settings": self._model_pipeline_rows(),
        }

    def update_model_pipeline_settings(self, settings: list[dict]) -> dict:
        try:
            batch = ModelPipelineBatchUpdate.model_validate({"settings": settings})
        except ValidationError as exc:
            details = [
                {
                    "field": ".".join(str(item) for item in error["loc"]),
                    "message": error["msg"],
                    "type": error["type"],
                }
                for error in exc.errors()
            ]
            raise ConfigurationError(
                "CONFIG_VALIDATION_ERROR",
                "Model pipeline settings failed validation",
                details=details,
            ) from exc

        ordered = {
            item.scene_key: item.model_dump()
            for item in batch.settings
        }
        validated = [ordered[scene_key] for scene_key in MODEL_PIPELINE_SCENE_KEYS]
        for setting in validated:
            self.model_pipeline_registry.resolve(setting)

        with self.repository.transaction() as connection:
            current = {
                row["scene_key"]: row
                for row in self._model_pipeline_rows(connection=connection)
            }
            for setting in validated:
                scene_key = setting["scene_key"]
                existing = current[scene_key]
                if all(existing[field] == setting[field] for field in _MODEL_PIPELINE_FIELDS):
                    continue
                self.repository.execute(
                    connection,
                    """
                    UPDATE model_pipeline_setting
                    SET preset = ?, enabled = ?, device_preference = ?,
                        yolo_threshold = ?, lpr_threshold = ?, frame_interval = ?,
                        inference_size = ?, parking_move_threshold = ?, mog_history = ?,
                        mog_variance_threshold = ?, mog_min_area = ?,
                        mog_min_duration = ?, mog_max_duration = ?, mog_warmup_frames = ?,
                        revision = revision + 1, updated_at = ?
                    WHERE scene_key = ?
                    """,
                    (
                        setting["preset"],
                        int(setting["enabled"]),
                        setting["device_preference"],
                        setting["yolo_threshold"],
                        setting["lpr_threshold"],
                        setting["frame_interval"],
                        setting["inference_size"],
                        setting["parking_move_threshold"],
                        setting["mog_history"],
                        setting["mog_variance_threshold"],
                        setting["mog_min_area"],
                        setting["mog_min_duration"],
                        setting["mog_max_duration"],
                        setting["mog_warmup_frames"],
                        _now(),
                        scene_key,
                    ),
                )
                self._insert_audit(
                    connection,
                    "update_model_pipeline_setting",
                    "model_pipeline",
                    scene_key,
                    "succeeded",
                    setting,
                )
        return self.model_pipeline_settings()

    def update_detection_settings(self, values: dict) -> dict:
        current = self.detection_settings()
        merged = {**current, **{key: value for key, value in values.items() if value is not None}}
        with self.repository.transaction() as connection:
            self.repository.execute(
                connection,
                """
                UPDATE detection_settings SET enabled = ?, yolo_threshold = ?, lpr_threshold = ?,
                    frame_interval = ?, device_preference = ?, updated_at = ? WHERE singleton_id = 1
                """,
                (int(merged["enabled"]), merged["yolo_threshold"], merged["lpr_threshold"], merged["interval"], merged["device_preference"], _now()),
            )
            self._insert_audit(connection, "update_detection_settings", "system", "detection", "succeeded", values)
        return self.detection_settings()

    def _model_pipeline_rows(self, *, connection=None) -> list[dict]:
        rows = self.repository.fetch_all(
            """
            SELECT scene_key, preset, enabled, device_preference,
                   yolo_threshold, lpr_threshold, frame_interval, inference_size,
                   parking_move_threshold, mog_history, mog_variance_threshold,
                   mog_min_area, mog_min_duration, mog_max_duration,
                   mog_warmup_frames, revision, updated_at
            FROM model_pipeline_setting
            ORDER BY CASE scene_key
                WHEN 'realtime' THEN 1
                WHEN 'traffic_map' THEN 2
                WHEN 'no_parking' THEN 3
                WHEN 'road_abnormal' THEN 4
            END
            """,
            connection=connection,
        )
        payload = [dict(row) for row in rows]
        for row in payload:
            row["enabled"] = bool(row["enabled"])
        return payload

    def update_activation_state(self, **values) -> dict:
        allowed = {"stream_profile_id", "topology_id", "topology_revision", "no_parking_scene_id", "road_abnormal_scene_id"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unknown activation fields: {sorted(unknown)}")
        current = self.get_activation_state()
        current.update(values)
        with self.repository.transaction() as connection:
            self.repository.execute(
                connection,
                """
                UPDATE activation_state SET stream_profile_id = ?, topology_id = ?, topology_revision = ?,
                    no_parking_scene_id = ?, road_abnormal_scene_id = ?, updated_at = ?
                WHERE singleton_id = 1
                """,
                (current["stream_profile_id"], current["topology_id"], current["topology_revision"], current["no_parking_scene_id"], current["road_abnormal_scene_id"], _now()),
            )
        return self.get_activation_state()

    def start_operation(self, operation_type: str, old_ref: dict, target_ref: dict) -> str:
        operation_id = f"op_{datetime.now():%Y%m%d}_{uuid4().hex[:12]}"
        with self.repository.transaction() as connection:
            self.repository.execute(
                connection,
                """
                INSERT INTO configuration_operation
                    (operation_id, operation_type, old_ref_json, target_ref_json, status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (operation_id, operation_type, json.dumps(redact_mapping(old_ref), ensure_ascii=False), json.dumps(redact_mapping(target_ref), ensure_ascii=False)),
            )
        return operation_id

    def update_operation(self, operation_id: str, *, status: str, error_summary: str | None = None) -> None:
        with self.repository.transaction() as connection:
            cursor = self.repository.execute(
                connection,
                "UPDATE configuration_operation SET status = ?, error_summary = ? WHERE operation_id = ?",
                (status, error_summary, operation_id),
            )
            if cursor.rowcount == 0:
                raise not_found("operation", operation_id)

    def finish_operation(self, operation_id: str, *, status: str, audit_summary: dict, error_summary: str | None = None) -> None:
        operation = self.get_operation(operation_id)
        safe_error = redact_text(error_summary) if error_summary else None
        with self.repository.transaction() as connection:
            self.repository.execute(
                connection,
                "UPDATE configuration_operation SET status = ?, error_summary = ?, finished_at = ? WHERE operation_id = ?",
                (status, safe_error, _now(), operation_id),
            )
            self._insert_audit(connection, operation["operation_type"], "operation", operation_id, status, audit_summary, operation_id)

    def get_operation(self, operation_id: str) -> dict:
        row = self.repository.fetch_one("SELECT * FROM configuration_operation WHERE operation_id = ?", (operation_id,))
        if row is None:
            raise not_found("operation", operation_id)
        payload = dict(row)
        payload["old_ref"] = json.loads(payload.pop("old_ref_json"))
        payload["target_ref"] = json.loads(payload.pop("target_ref_json"))
        return payload

    def list_audit(self, *, limit: int = 100, offset: int = 0, result: str | None = None) -> dict:
        where = "WHERE result = ?" if result else ""
        params = (result,) if result else ()
        total = int(self.repository.fetch_one(f"SELECT COUNT(*) AS count FROM audit_log {where}", params)["count"])
        rows = self.repository.fetch_all(
            f"SELECT * FROM audit_log {where} ORDER BY occurred_at DESC, audit_id DESC LIMIT ? OFFSET ?",
            (*params, max(1, min(500, limit)), max(0, offset)),
        )
        items = []
        for row in rows:
            payload = dict(row)
            try:
                payload["summary"] = json.loads(payload["summary"])
            except json.JSONDecodeError:
                pass
            items.append(payload)
        return {"total": total, "items": items, "limit": limit, "offset": offset}

    def write_audit(self, operation_type: str, target_id: str, result: str, summary: dict) -> None:
        with self.repository.transaction() as connection:
            self._insert_audit(connection, operation_type, "configuration", target_id, result, summary)

    def mark_interrupted_operations(self) -> int:
        with self.repository.transaction() as connection:
            cursor = self.repository.execute(
                connection,
                """
                UPDATE configuration_operation SET status = 'interrupted', finished_at = ?
                WHERE status IN ('pending', 'preflighting', 'applying')
                """,
                (_now(),),
            )
            return cursor.rowcount

    def export_snapshot(self) -> dict[str, list[dict]]:
        tables = (
            "camera", "stream_source", "stream_binding_profile", "stream_binding", "asset",
            "topology_profile", "topology_node", "road_segment", "topology_camera",
            "scene_archive", "detection_settings", "whitelist_entry", "whitelist_setting",
            "activation_state", "model_pipeline_setting",
        )
        with self.repository.transaction(immediate=False) as connection:
            snapshot = {table: [dict(row) for row in self.repository.fetch_all(f"SELECT * FROM {table}", connection=connection)] for table in tables}
        return snapshot

    def _scene_exists(self, scene_id: str) -> bool:
        return self.repository.fetch_one("SELECT 1 FROM scene_archive WHERE scene_id = ?", (scene_id,)) is not None

    @staticmethod
    def _stream_payload(row) -> dict:
        payload = dict(row)
        payload["enabled"] = bool(payload["enabled"])
        if payload.get("last_probe_status"):
            try:
                payload["last_probe"] = json.loads(payload["last_probe_status"])
            except json.JSONDecodeError:
                payload["last_probe"] = {"message": payload["last_probe_status"]}
        else:
            payload["last_probe"] = None
        return payload

    def _insert_audit(
        self,
        connection,
        operation_type: str,
        target_type: str,
        target_id: str | None,
        result: str,
        summary: Any,
        operation_id: str | None = None,
    ) -> None:
        safe_summary = redact_mapping(summary)
        self.repository.execute(
            connection,
            """
            INSERT INTO audit_log
                (operation_type, target_type, target_id, result, summary, operation_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (operation_type, target_type, target_id, result, json.dumps(safe_summary, ensure_ascii=False, separators=(",", ":")), operation_id),
        )
