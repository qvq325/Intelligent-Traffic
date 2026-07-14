"""Portable configuration ZIP export, preflight, backup, and replacement."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import stat
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from uuid import uuid4

from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from .errors import ConfigurationError
from .models import (
    DetectionConfiguration,
    MODEL_PIPELINE_SCENE_KEYS,
    ModelPipelineBatchUpdate,
    StreamCreate,
    TopologyCamera,
    TopologyNode,
    TopologySegment,
)
from backend.schemas import NoParkingScenePayload, RoadAbnormalScenePayload, WhitelistInput


FORMAT_NAME = "videotest.configuration"
SCHEMA_VERSION = 1
MAX_COMPRESSED_BYTES = 256 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_FILE_ENTRIES = 1000
TOKEN_TTL_SECONDS = 15 * 60

CONFIG_PATHS = {
    "fixed-cameras": "config/fixed-cameras.json",
    "stream-sources": "config/stream-sources.json",
    "stream-binding-profiles": "config/stream-binding-profiles.json",
    "topology-profiles": "config/topology-profiles.json",
    "scene-archives": "config/scene-archives.json",
    "detection-settings": "config/detection-settings.json",
    "model-pipelines": "config/model-pipelines.json",
    "whitelist": "config/whitelist.json",
    "activation-state": "config/activation-state.json",
}

OPTIONAL_DOCUMENT_KEYS = {
    CONFIG_PATHS["model-pipelines"]: {"schema_version", "settings"},
}

MODEL_PIPELINE_FIELDS = (
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

DOCUMENT_KEYS = {
    CONFIG_PATHS["fixed-cameras"]: {"schema_version", "fingerprint", "cameras"},
    CONFIG_PATHS["stream-sources"]: {"schema_version", "streams"},
    CONFIG_PATHS["stream-binding-profiles"]: {"schema_version", "profiles"},
    CONFIG_PATHS["topology-profiles"]: {"schema_version", "topologies"},
    CONFIG_PATHS["scene-archives"]: {"schema_version", "scenes"},
    CONFIG_PATHS["detection-settings"]: {"schema_version", "settings"},
    CONFIG_PATHS["whitelist"]: {"schema_version", "enabled", "entries"},
    CONFIG_PATHS["activation-state"]: {"schema_version", "activation"},
}

MANIFEST_KEYS = {
    "format",
    "schema_version",
    "export_id",
    "exported_at",
    "app_version",
    "contains_plaintext_credentials",
    "camera_catalog_fingerprint",
    "builtin_baseline_fingerprint",
    "counts",
    "files",
    "assets",
}


@dataclass(slots=True)
class _PendingImport:
    token: str
    package_path: Path
    package_sha256: str
    expires_at: float
    preview: dict[str, Any]


class ImportExportService:
    def __init__(
        self,
        configuration_service,
        config_root: Path,
        runtime_reload: Callable[[], None],
        *,
        app_version: str = "1.0.0",
        preflight_validator: Callable[[dict[str, dict]], dict[str, Any]] | None = None,
    ) -> None:
        self.service = configuration_service
        self.repository = configuration_service.repository
        self.asset_store = configuration_service.asset_store
        self.config_root = Path(config_root)
        self.staging_dir = self.config_root / "staging"
        self.backup_dir = self.config_root / "backups"
        self.runtime_reload = runtime_reload
        self.app_version = app_version
        self.preflight_validator = preflight_validator
        self._tokens: dict[str, _PendingImport] = {}
        self._token_lock = threading.Lock()
        self._apply_lock = threading.Lock()

    def export_response(self) -> StreamingResponse:
        payload = self.export_bytes()
        filename = f"videotest-config-{datetime.now():%Y%m%d-%H%M%S}.zip"
        return StreamingResponse(
            io.BytesIO(payload),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def export_bytes(self) -> bytes:
        snapshot = self.service.export_snapshot()
        documents = self._documents(snapshot)
        entries: dict[str, bytes] = {
            path: _json_bytes(document) for path, document in documents.items()
        }
        asset_manifest: list[dict[str, Any]] = []
        for asset in snapshot["asset"]:
            path = self.asset_store.resolve(asset["relative_path"])
            self.asset_store.verify(asset)
            package_path = f"assets/{asset['relative_path']}"
            entries[package_path] = path.read_bytes()
            asset_manifest.append(
                {
                    key: asset[key]
                    for key in (
                        "asset_id",
                        "kind",
                        "relative_path",
                        "sha256",
                        "size_bytes",
                        "media_type",
                        "width",
                        "height",
                    )
                }
            )
        metadata = self._metadata(snapshot)
        file_manifest = [
            {
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "media_type": _media_type(path),
            }
            for path, content in sorted(entries.items())
        ]
        manifest = {
            "format": FORMAT_NAME,
            "schema_version": SCHEMA_VERSION,
            "export_id": f"export_{uuid4().hex}",
            "exported_at": _utc_now(),
            "app_version": self.app_version,
            "contains_plaintext_credentials": True,
            "camera_catalog_fingerprint": metadata["camera_catalog_fingerprint"],
            "builtin_baseline_fingerprint": self._baseline_fingerprint(snapshot),
            "counts": {
                "cameras": len(snapshot["camera"]),
                "streams": len(snapshot["stream_source"]),
                "stream_profiles": len(documents[CONFIG_PATHS["stream-binding-profiles"]]["profiles"]),
                "topologies": len(documents[CONFIG_PATHS["topology-profiles"]]["topologies"]),
                "scenes": len(snapshot["scene_archive"]),
                "assets": len(asset_manifest),
                "whitelist_entries": len(snapshot["whitelist_entry"]),
            },
            "files": file_manifest,
            "assets": sorted(asset_manifest, key=lambda item: item["asset_id"]),
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("manifest.json", _json_bytes(manifest))
            for path, content in sorted(entries.items()):
                archive.writestr(path, content)
        return output.getvalue()

    async def preflight_upload(self, upload) -> dict:
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.staging_dir / f"upload-{uuid4().hex}.zip"
        written = 0
        digest = hashlib.sha256()
        try:
            with temporary.open("wb") as target:
                while chunk := await upload.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_COMPRESSED_BYTES:
                        raise ConfigurationError(
                            "CONFIG_PACKAGE_TOO_LARGE",
                            "配置包压缩后超过 256 MiB",
                            status_code=413,
                        )
                    digest.update(chunk)
                    target.write(chunk)
        finally:
            await upload.close()
        try:
            parsed = self._read_package(temporary)
            preview = self._preview(parsed)
            if self.preflight_validator is not None:
                preview["runtime_preflight"] = self.preflight_validator(parsed["documents"])
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        token = f"import_{uuid4().hex}"
        pending = _PendingImport(
            token=token,
            package_path=temporary,
            package_sha256=digest.hexdigest(),
            expires_at=time.monotonic() + TOKEN_TTL_SECONDS,
            preview=preview,
        )
        with self._token_lock:
            self._purge_expired_locked()
            self._tokens[token] = pending
        return {
            **preview,
            "token": token,
            "expires_in_seconds": TOKEN_TTL_SECONDS,
            "package_sha256": pending.package_sha256,
        }

    def apply(self, token: str, *, confirmed: bool) -> dict:
        if not confirmed:
            raise ConfigurationError("IMPORT_CONFIRMATION_REQUIRED", "必须明确确认全量替换")
        if not self._apply_lock.acquire(blocking=False):
            raise ConfigurationError(
                "CONFIG_MUTATION_IN_PROGRESS",
                "另一个配置写操作正在执行",
                status_code=409,
            )
        with self._token_lock:
            self._purge_expired_locked()
            pending = self._tokens.pop(token, None)
        if pending is None:
            self._apply_lock.release()
            raise ConfigurationError(
                "IMPORT_TOKEN_INVALID",
                "导入确认令牌不存在、已使用或已过期",
                status_code=404,
            )
        operation_id = None
        backup_database = None
        try:
            if _sha256_file(pending.package_path) != pending.package_sha256:
                raise ConfigurationError("CONFIG_PACKAGE_CHANGED", "暂存配置包摘要已变化")
            package = self._read_package(pending.package_path)
            operation_id = self.service.start_operation(
                "import_configuration",
                {"database": str(self.repository.database_path)},
                {"package_sha256": pending.package_sha256},
            )
            self.service.update_operation(operation_id, status="applying")
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup_database = self.repository.backup_to(self.backup_dir / f"{stamp}.sqlite3")
            (self.backup_dir / f"{stamp}.zip").write_bytes(self.export_bytes())
            self._prune_backups()
            self._install_assets(package)
            self._replace_user_configuration(package)
            self.runtime_reload()
            self.service.finish_operation(
                operation_id,
                status="succeeded",
                audit_summary={"package_sha256": pending.package_sha256},
            )
            return {
                "operation_id": operation_id,
                "status": "succeeded",
                "rollback": "not_required",
                "preview": pending.preview,
            }
        except Exception as exc:
            rollback = "not_required"
            if backup_database is not None:
                rollback = "succeeded"
                try:
                    self._restore_database(backup_database)
                    self.runtime_reload()
                except Exception:
                    rollback = "failed"
            if operation_id is not None:
                try:
                    self.service.finish_operation(
                        operation_id,
                        status="rolled_back" if rollback == "succeeded" else "failed",
                        error_summary=str(exc),
                        audit_summary={"rollback": rollback},
                    )
                except Exception:
                    pass
            if isinstance(exc, ConfigurationError):
                exc.operation_id = operation_id
                exc.rollback = rollback
                raise
            raise ConfigurationError(
                "CONFIG_IMPORT_FAILED",
                "配置导入失败",
                status_code=500,
                operation_id=operation_id,
                rollback=rollback,
                details=[{"reason": str(exc)[:500]}],
            ) from exc
        finally:
            pending.package_path.unlink(missing_ok=True)
            self._apply_lock.release()

    def _documents(self, snapshot: dict[str, list[dict]]) -> dict[str, dict]:
        user_profile_ids = {
            item["profile_id"]
            for item in snapshot["stream_binding_profile"]
            if not item["is_builtin"]
        }
        profiles = []
        for profile in snapshot["stream_binding_profile"]:
            if profile["profile_id"] not in user_profile_ids:
                continue
            profiles.append(
                {
                    "profile_id": profile["profile_id"],
                    "name": profile["name"],
                    "description": profile["description"],
                    "bindings": [
                        {"camera_id": binding["camera_id"], "stream_id": binding["stream_id"]}
                        for binding in snapshot["stream_binding"]
                        if binding["profile_id"] == profile["profile_id"]
                    ],
                }
            )
        topologies = [
            self._export_topology(item["topology_id"])
            for item in snapshot["topology_profile"]
            if not item["is_builtin"]
        ]
        scenes = [self._export_scene(item["scene_id"]) for item in snapshot["scene_archive"]]
        detection = dict(snapshot["detection_settings"][0])
        detection["enabled"] = bool(detection["enabled"])
        detection["interval"] = detection.pop("frame_interval")
        model_pipeline_rows = {
            item["scene_key"]: item for item in snapshot["model_pipeline_setting"]
        }
        model_pipeline_settings = []
        for scene_key in MODEL_PIPELINE_SCENE_KEYS:
            row = model_pipeline_rows[scene_key]
            item = {key: row[key] for key in MODEL_PIPELINE_FIELDS}
            item["enabled"] = bool(item["enabled"])
            model_pipeline_settings.append(item)
        whitelist_setting = snapshot["whitelist_setting"][0]
        activation = dict(snapshot["activation_state"][0])
        return {
            CONFIG_PATHS["fixed-cameras"]: {
                "schema_version": SCHEMA_VERSION,
                "fingerprint": self._metadata(snapshot)["camera_catalog_fingerprint"],
                "cameras": [
                    {key: item[key] for key in ("camera_id", "display_name", "ordinal", "builtin_fingerprint")}
                    for item in sorted(snapshot["camera"], key=lambda value: value["ordinal"])
                ],
            },
            CONFIG_PATHS["stream-sources"]: {
                "schema_version": SCHEMA_VERSION,
                "streams": [
                    {
                        **{key: item[key] for key in ("stream_id", "name", "rtsp_url")},
                        "enabled": bool(item["enabled"]),
                    }
                    for item in snapshot["stream_source"]
                ],
            },
            CONFIG_PATHS["stream-binding-profiles"]: {
                "schema_version": SCHEMA_VERSION,
                "profiles": profiles,
            },
            CONFIG_PATHS["topology-profiles"]: {
                "schema_version": SCHEMA_VERSION,
                "topologies": topologies,
            },
            CONFIG_PATHS["scene-archives"]: {
                "schema_version": SCHEMA_VERSION,
                "scenes": scenes,
            },
            CONFIG_PATHS["detection-settings"]: {
                "schema_version": SCHEMA_VERSION,
                "settings": {
                    key: detection[key]
                    for key in (
                        "enabled",
                        "yolo_threshold",
                        "lpr_threshold",
                        "interval",
                        "device_preference",
                    )
                },
            },
            CONFIG_PATHS["model-pipelines"]: {
                "schema_version": SCHEMA_VERSION,
                "settings": model_pipeline_settings,
            },
            CONFIG_PATHS["whitelist"]: {
                "schema_version": SCHEMA_VERSION,
                "enabled": bool(whitelist_setting["enabled"]),
                "entries": [
                    {key: item[key] for key in ("plate", "note", "added_at")}
                    for item in snapshot["whitelist_entry"]
                ],
            },
            CONFIG_PATHS["activation-state"]: {
                "schema_version": SCHEMA_VERSION,
                "activation": {
                    key: activation[key]
                    for key in (
                        "stream_profile_id",
                        "topology_id",
                        "topology_revision",
                        "no_parking_scene_id",
                        "road_abnormal_scene_id",
                    )
                },
            },
        }

    def _export_topology(self, topology_id: str) -> dict:
        topology = self.service.get_topology(topology_id)
        payload = {
            key: topology[key]
            for key in (
                "topology_id",
                "name",
                "revision",
                "map_asset_id",
                "map_width",
                "map_height",
                "nodes",
                "segments",
                "cameras",
            )
        }
        payload["cameras"] = [
            {
                key: camera[key]
                for key in ("camera_id", "x", "y", "heading", "view_range", "segment_id")
            }
            for camera in topology["cameras"]
        ]
        return payload

    def _export_scene(self, scene_id: str) -> dict:
        scene = self.service.get_scene(scene_id)
        return {
            key: scene[key]
            for key in (
                "scene_id",
                "scene_type",
                "name",
                "topology_id",
                "topology_revision",
                "camera_id",
                "reference_asset_id",
                "config",
                "review_status",
            )
        }

    def _metadata(self, snapshot: dict) -> dict:
        row = self.repository.fetch_one(
            "SELECT camera_catalog_fingerprint, builtin_baseline_version FROM schema_metadata WHERE singleton_id = 1"
        )
        return dict(row)

    @staticmethod
    def _baseline_fingerprint(snapshot: dict) -> str:
        builtin_profile_ids = {
            item["profile_id"]
            for item in snapshot["stream_binding_profile"]
            if item["is_builtin"]
        }
        builtin_bindings = [
            {
                key: item[key]
                for key in ("profile_id", "camera_id", "stream_id")
            }
            for item in snapshot["stream_binding"]
            if item["profile_id"] in builtin_profile_ids
        ]
        builtin_stream_ids = {item["stream_id"] for item in builtin_bindings}
        baseline = {
            "stream_profiles": [
                {key: item[key] for key in ("profile_id", "name")}
                for item in snapshot["stream_binding_profile"]
                if item["is_builtin"]
            ],
            "topologies": [
                {key: item[key] for key in ("topology_id", "name", "revision", "map_asset_id")}
                for item in snapshot["topology_profile"]
                if item["is_builtin"]
            ],
            "stream_bindings": sorted(
                builtin_bindings,
                key=lambda item: (item["profile_id"], item["camera_id"]),
            ),
            "streams": sorted(
                [
                    {
                        key: item[key]
                        for key in ("stream_id", "name", "rtsp_url", "enabled")
                    }
                    for item in snapshot["stream_source"]
                    if item["stream_id"] in builtin_stream_ids
                ],
                key=lambda item: item["stream_id"],
            ),
        }
        return hashlib.sha256(_json_bytes(baseline)).hexdigest()

    def _read_package(self, path: Path) -> dict[str, Any]:
        if not zipfile.is_zipfile(path):
            raise ConfigurationError("CONFIG_PACKAGE_INVALID", "上传文件不是有效 ZIP")
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILE_ENTRIES:
                raise ConfigurationError("CONFIG_PACKAGE_TOO_MANY_FILES", "配置包文件数超过 1000")
            names: set[str] = set()
            total = 0
            for info in infos:
                self._validate_entry(info, names)
                total += info.file_size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ConfigurationError("CONFIG_PACKAGE_EXPANDED_TOO_LARGE", "配置包解压后超过 512 MiB")
            if "manifest.json" not in names:
                raise ConfigurationError("CONFIG_MANIFEST_MISSING", "配置包缺少 manifest.json")
            manifest = _load_json(archive.read("manifest.json"), "manifest.json")
            _require_keys(manifest, MANIFEST_KEYS, "manifest.json")
            if manifest["format"] != FORMAT_NAME:
                raise ConfigurationError("CONFIG_FORMAT_UNSUPPORTED", "配置包格式标识不受支持")
            if not isinstance(manifest["schema_version"], int) or manifest["schema_version"] > SCHEMA_VERSION:
                raise ConfigurationError("CONFIG_SCHEMA_TOO_NEW", "配置包版本高于当前支持版本")
            if manifest["contains_plaintext_credentials"] is not True:
                raise ConfigurationError("CONFIG_MANIFEST_INVALID", "manifest 必须明确标注包含明文凭据")
            declared = manifest["files"]
            if not isinstance(declared, list):
                raise ConfigurationError("CONFIG_MANIFEST_INVALID", "manifest.files 必须是数组")
            declared_paths: set[str] = set()
            for item in declared:
                _require_keys(item, {"path", "sha256", "size_bytes", "media_type"}, "manifest.files[]")
                package_path = item["path"]
                if package_path in declared_paths:
                    raise ConfigurationError("CONFIG_DUPLICATE_ENTRY", "manifest 中存在重复路径")
                declared_paths.add(package_path)
                if package_path not in names:
                    raise ConfigurationError("CONFIG_DECLARED_FILE_MISSING", f"声明文件不存在: {package_path}")
                content = archive.read(package_path)
                if len(content) != item["size_bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                    raise ConfigurationError("CONFIG_DIGEST_MISMATCH", f"文件完整性校验失败: {package_path}")
            actual = names - {"manifest.json"}
            if actual != declared_paths:
                extra = sorted(actual - declared_paths)
                raise ConfigurationError(
                    "CONFIG_UNDECLARED_FILE",
                    "配置包包含未在 manifest 声明的文件",
                    details=[{"paths": extra}],
                )
            documents = {}
            for config_path, required_keys in DOCUMENT_KEYS.items():
                if config_path not in declared_paths:
                    raise ConfigurationError("CONFIG_DOMAIN_MISSING", f"配置包缺少 {config_path}")
                document = _load_json(archive.read(config_path), config_path)
                _require_keys(document, required_keys, config_path)
                if document["schema_version"] != SCHEMA_VERSION:
                    raise ConfigurationError("CONFIG_SCHEMA_UNSUPPORTED", f"{config_path} 版本不受支持")
                documents[config_path] = document
            for config_path, required_keys in OPTIONAL_DOCUMENT_KEYS.items():
                if config_path not in declared_paths:
                    continue
                document = _load_json(archive.read(config_path), config_path)
                _require_keys(document, required_keys, config_path)
                if document["schema_version"] != SCHEMA_VERSION:
                    raise ConfigurationError(
                        "CONFIG_SCHEMA_UNSUPPORTED",
                        f"{config_path} 版本不受支持",
                    )
                documents[config_path] = document
            self._validate_camera_compatibility(manifest, documents)
            assets = manifest["assets"]
            if not isinstance(assets, list):
                raise ConfigurationError("CONFIG_MANIFEST_INVALID", "manifest.assets 必须是数组")
            seen_asset_ids: set[str] = set()
            seen_asset_paths: set[str] = set()
            for asset in assets:
                _require_keys(
                    asset,
                    {"asset_id", "kind", "relative_path", "sha256", "size_bytes", "media_type", "width", "height"},
                    "manifest.assets[]",
                )
                package_path = f"assets/{asset['relative_path']}"
                if asset["asset_id"] in seen_asset_ids or asset["relative_path"] in seen_asset_paths:
                    raise ConfigurationError("CONFIG_DUPLICATE_ASSET", "manifest 中存在重复资源")
                seen_asset_ids.add(asset["asset_id"])
                seen_asset_paths.add(asset["relative_path"])
                self.asset_store.resolve(asset["relative_path"])
                if package_path not in declared_paths:
                    raise ConfigurationError("CONFIG_ASSET_MISSING", f"资源文件不存在: {package_path}")
                if asset["size_bytes"] > 50 * 1024 * 1024 or asset["width"] > 10000 or asset["height"] > 10000:
                    raise ConfigurationError("CONFIG_ASSET_LIMIT_EXCEEDED", f"资源超出限制: {package_path}")
                content = archive.read(package_path)
                if len(content) != asset["size_bytes"] or hashlib.sha256(content).hexdigest() != asset["sha256"]:
                    raise ConfigurationError("CONFIG_DIGEST_MISMATCH", f"资源完整性校验失败: {package_path}")
                media_type, _ = self.asset_store._detect_format(content)
                width, height = self.asset_store._decode_dimensions(content)
                if media_type != asset["media_type"] or width != asset["width"] or height != asset["height"]:
                    raise ConfigurationError("CONFIG_ASSET_METADATA_INVALID", f"资源元数据不匹配: {package_path}")
            self._validate_documents(manifest, documents, assets)
            return {"manifest": manifest, "documents": documents, "path": path}

    def _validate_documents(self, manifest: dict, documents: dict, assets: list[dict]) -> None:
        cameras_document = documents[CONFIG_PATHS["fixed-cameras"]]
        for item in cameras_document["cameras"]:
            _require_keys(item, {"camera_id", "display_name", "ordinal", "builtin_fingerprint"}, "fixed-cameras.cameras[]")
        camera_ids = {item["camera_id"] for item in cameras_document["cameras"]}
        asset_ids = {item["asset_id"] for item in assets}

        streams = documents[CONFIG_PATHS["stream-sources"]]["streams"]
        stream_ids: set[str] = set()
        stream_names: set[str] = set()
        for item in streams:
            _require_keys(item, {"stream_id", "name", "rtsp_url", "enabled"}, "stream-sources.streams[]")
            if item["stream_id"] in stream_ids or item["name"] in stream_names:
                raise ConfigurationError("CONFIG_DUPLICATE_ENTITY", "配置包流 ID 或名称重复")
            stream_ids.add(item["stream_id"])
            stream_names.add(item["name"])
            try:
                StreamCreate.model_validate({key: item[key] for key in ("name", "rtsp_url", "enabled")})
            except ValidationError as exc:
                raise ConfigurationError("CONFIG_STREAM_INVALID", "配置包包含无效 RTSP 流", details=exc.errors()) from exc

        profiles = documents[CONFIG_PATHS["stream-binding-profiles"]]["profiles"]
        profile_ids: set[str] = set()
        for profile in profiles:
            _require_keys(profile, {"profile_id", "name", "description", "bindings"}, "stream-binding-profiles.profiles[]")
            if profile["profile_id"] in profile_ids:
                raise ConfigurationError("CONFIG_DUPLICATE_ENTITY", "配置包关联方案 ID 重复")
            profile_ids.add(profile["profile_id"])
            seen_cameras: set[str] = set()
            seen_streams: set[str] = set()
            for binding in profile["bindings"]:
                _require_keys(binding, {"camera_id", "stream_id"}, "stream-binding-profiles.bindings[]")
                if binding["camera_id"] not in camera_ids or binding["stream_id"] not in stream_ids:
                    raise ConfigurationError("CONFIG_REFERENCE_INVALID", "关联方案引用未知摄像头或流")
                if binding["camera_id"] in seen_cameras or binding["stream_id"] in seen_streams:
                    raise ConfigurationError("CONFIG_DUPLICATE_ENTITY", "关联方案不是一对一映射")
                seen_cameras.add(binding["camera_id"])
                seen_streams.add(binding["stream_id"])

        topologies = documents[CONFIG_PATHS["topology-profiles"]]["topologies"]
        topology_ids: set[str] = set()
        for topology in topologies:
            _require_keys(
                topology,
                {"topology_id", "name", "revision", "map_asset_id", "map_width", "map_height", "nodes", "segments", "cameras"},
                "topology-profiles.topologies[]",
            )
            if topology["topology_id"] in topology_ids or topology["map_asset_id"] not in asset_ids:
                raise ConfigurationError("CONFIG_REFERENCE_INVALID", "拓扑 ID 重复或底图资源不存在")
            topology_ids.add(topology["topology_id"])
            try:
                nodes = [TopologyNode.model_validate(item) for item in topology["nodes"]]
                segments = [TopologySegment.model_validate(item) for item in topology["segments"]]
                cameras = [TopologyCamera.model_validate(item) for item in topology["cameras"]]
            except ValidationError as exc:
                raise ConfigurationError("CONFIG_TOPOLOGY_INVALID", "配置包包含无效拓扑", details=exc.errors()) from exc
            node_ids = {item.node_id for item in nodes}
            segment_ids = {item.segment_id for item in segments}
            if len(node_ids) != len(nodes) or len(segment_ids) != len(segments):
                raise ConfigurationError("CONFIG_DUPLICATE_ENTITY", "拓扑节点或道路 ID 重复")
            if any(item.start_node_id not in node_ids or item.end_node_id not in node_ids for item in segments):
                raise ConfigurationError("CONFIG_REFERENCE_INVALID", "拓扑道路引用未知节点")
            if {item.camera_id for item in cameras} != camera_ids:
                raise ConfigurationError("CAMERA_CATALOG_MISMATCH", "拓扑未完整覆盖固定摄像头目录")
            if any(item.segment_id not in segment_ids for item in cameras):
                raise ConfigurationError("CONFIG_REFERENCE_INVALID", "拓扑摄像头引用未知道路")

        scenes = documents[CONFIG_PATHS["scene-archives"]]["scenes"]
        scene_ids: set[str] = set()
        assets_by_id = {item["asset_id"]: item for item in assets}
        target_topologies = topology_ids | {
            item["topology_id"]
            for item in self.service.list_topologies()
            if item["is_builtin"]
        }
        for scene in scenes:
            _require_keys(
                scene,
                {"scene_id", "scene_type", "name", "topology_id", "topology_revision", "camera_id", "reference_asset_id", "config", "review_status"},
                "scene-archives.scenes[]",
            )
            if scene["scene_id"] in scene_ids or scene["camera_id"] not in camera_ids:
                raise ConfigurationError("CONFIG_REFERENCE_INVALID", "场景 ID 重复或引用无效")
            scene_ids.add(scene["scene_id"])
            if scene["scene_type"] == "no_parking":
                scene["topology_id"] = None
                scene["topology_revision"] = None
                scene["review_status"] = "ready"
            elif (
                scene["scene_type"] == "road_abnormal"
                and scene["topology_id"] not in target_topologies
            ):
                raise ConfigurationError(
                    "CONFIG_REFERENCE_INVALID",
                    "道路异常场景引用未知拓扑",
                    details=[{"scene_id": scene["scene_id"]}],
                )
            asset = assets_by_id.get(scene["reference_asset_id"]) if scene["reference_asset_id"] else None
            reference_image = f"reference_{scene['scene_id']}.jpg" if asset else ""
            payload = {
                "scene_id": scene["scene_id"],
                "name": scene["name"],
                "camera_id": scene["camera_id"],
                "reference_image": reference_image,
                "reference_width": asset["width"] if asset else 0,
                "reference_height": asset["height"] if asset else 0,
                **scene["config"],
            }
            try:
                model = NoParkingScenePayload if scene["scene_type"] == "no_parking" else RoadAbnormalScenePayload
                if scene["scene_type"] not in {"no_parking", "road_abnormal"}:
                    raise ValueError("unknown scene type")
                model.model_validate(payload)
            except (ValidationError, ValueError) as exc:
                details = exc.errors() if isinstance(exc, ValidationError) else [{"message": str(exc)}]
                raise ConfigurationError("CONFIG_SCENE_INVALID", "配置包包含无效场景", details=details) from exc

        settings = documents[CONFIG_PATHS["detection-settings"]]["settings"]
        _require_keys(settings, {"enabled", "yolo_threshold", "lpr_threshold", "interval", "device_preference"}, "detection-settings.settings")
        try:
            DetectionConfiguration.model_validate(settings)
        except ValidationError as exc:
            raise ConfigurationError("CONFIG_DETECTION_SETTINGS_INVALID", "检测参数无效", details=exc.errors()) from exc

        model_pipeline_document = documents.get(CONFIG_PATHS["model-pipelines"])
        if model_pipeline_document is not None:
            try:
                batch = ModelPipelineBatchUpdate.model_validate(
                    {"settings": model_pipeline_document["settings"]}
                )
            except ValidationError as exc:
                raise ConfigurationError(
                    "CONFIG_MODEL_PIPELINES_INVALID",
                    "模型流水线参数无效",
                    details=exc.errors(),
                ) from exc
            for model_pipeline in batch.settings:
                self.service.model_pipeline_registry.resolve(
                    model_pipeline.model_dump()
                )

        whitelist = documents[CONFIG_PATHS["whitelist"]]
        seen_plates: set[str] = set()
        for entry in whitelist["entries"]:
            _require_keys(entry, {"plate", "note", "added_at"}, "whitelist.entries[]")
            try:
                validated = WhitelistInput.model_validate({"plate": entry["plate"], "note": entry["note"]})
            except ValidationError as exc:
                raise ConfigurationError("CONFIG_WHITELIST_INVALID", "白名单条目无效", details=exc.errors()) from exc
            if validated.plate.upper() in seen_plates or not isinstance(entry["added_at"], str):
                raise ConfigurationError("CONFIG_DUPLICATE_ENTITY", "白名单车牌重复或时间无效")
            seen_plates.add(validated.plate.upper())

        activation = documents[CONFIG_PATHS["activation-state"]]["activation"]
        _require_keys(
            activation,
            {"stream_profile_id", "topology_id", "topology_revision", "no_parking_scene_id", "road_abnormal_scene_id"},
            "activation-state.activation",
        )
        target_profiles = profile_ids | {
            item["profile_id"]
            for item in self.service.list_stream_profiles()
            if item["is_builtin"]
        }
        if activation["stream_profile_id"] not in target_profiles or activation["topology_id"] not in target_topologies:
            raise ConfigurationError("CONFIG_ACTIVATION_INVALID", "激活状态引用未知方案")
        for field in ("no_parking_scene_id", "road_abnormal_scene_id"):
            if activation[field] is not None and activation[field] not in scene_ids:
                raise ConfigurationError("CONFIG_ACTIVATION_INVALID", "激活状态引用未知场景")

        expected_counts = {
            "cameras": len(camera_ids),
            "streams": len(streams),
            "stream_profiles": len(profiles),
            "topologies": len(topologies),
            "scenes": len(scenes),
            "assets": len(assets),
            "whitelist_entries": len(whitelist["entries"]),
        }
        _require_keys(manifest["counts"], set(expected_counts), "manifest.counts")
        if manifest["counts"] != expected_counts:
            raise ConfigurationError("CONFIG_COUNT_MISMATCH", "manifest 实体计数与配置内容不一致")

    @staticmethod
    def _validate_entry(info: zipfile.ZipInfo, names: set[str]) -> None:
        name = info.filename
        if name in names:
            raise ConfigurationError("CONFIG_DUPLICATE_ENTRY", f"ZIP 存在重复条目: {name}")
        if "\\" in name or not name or name.startswith("/"):
            raise ConfigurationError("CONFIG_PATH_INVALID", f"ZIP 路径不安全: {name}")
        portable = PurePosixPath(name)
        if portable.is_absolute() or ".." in portable.parts or "." in portable.parts:
            raise ConfigurationError("CONFIG_PATH_TRAVERSAL", f"ZIP 路径不安全: {name}")
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise ConfigurationError("CONFIG_SYMLINK_FORBIDDEN", f"ZIP 不允许符号链接: {name}")
        if info.is_dir():
            raise ConfigurationError("CONFIG_DIRECTORY_ENTRY_FORBIDDEN", f"ZIP 不允许目录条目: {name}")
        names.add(name)

    def _validate_camera_compatibility(self, manifest: dict, documents: dict) -> None:
        metadata = self._metadata(self.service.export_snapshot())
        cameras = documents[CONFIG_PATHS["fixed-cameras"]]
        target_ids = [item["camera_id"] for item in self.service.list_cameras()]
        package_ids = [item.get("camera_id") for item in cameras["cameras"]]
        if package_ids != target_ids or cameras["fingerprint"] != metadata["camera_catalog_fingerprint"]:
            raise ConfigurationError("CAMERA_CATALOG_MISMATCH", "固定摄像头目录与目标系统不一致")
        if manifest["camera_catalog_fingerprint"] != cameras["fingerprint"]:
            raise ConfigurationError("CAMERA_CATALOG_MISMATCH", "manifest 摄像头指纹不一致")

    def _preview(self, package: dict) -> dict:
        documents = package["documents"]
        incoming = {
            "streams": len(documents[CONFIG_PATHS["stream-sources"]]["streams"]),
            "stream_profiles": len(documents[CONFIG_PATHS["stream-binding-profiles"]]["profiles"]),
            "topologies": len(documents[CONFIG_PATHS["topology-profiles"]]["topologies"]),
            "scenes": len(documents[CONFIG_PATHS["scene-archives"]]["scenes"]),
            "whitelist_entries": len(documents[CONFIG_PATHS["whitelist"]]["entries"]),
        }
        current = self.service.summary()["counts"]
        baseline_warning = package["manifest"]["builtin_baseline_fingerprint"] != self._baseline_fingerprint(self.service.export_snapshot())
        return {
            "ok": True,
            "mode": "replace_all_user_configuration",
            "incoming": incoming,
            "current": current,
            "deleted_or_replaced": {
                "streams": current["streams"],
                "stream_profiles": max(0, current["stream_profiles"] - 1),
                "topologies": max(0, current["topologies"] - 1),
                "scenes": current["scenes"],
            },
            "target_activation": documents[CONFIG_PATHS["activation-state"]]["activation"],
            "warnings": ["目标系统内置基线不同，将保留目标基线"] if baseline_warning else [],
        }

    def _install_assets(self, package: dict) -> None:
        with zipfile.ZipFile(package["path"]) as archive:
            for asset in package["manifest"]["assets"]:
                package_path = f"assets/{asset['relative_path']}"
                content = archive.read(package_path)
                destination = self.asset_store.resolve(asset["relative_path"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    if hashlib.sha256(destination.read_bytes()).hexdigest() != asset["sha256"]:
                        raise ConfigurationError("CONFIG_ASSET_CONFLICT", f"本地资源摘要冲突: {asset['asset_id']}")
                    continue
                temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
                try:
                    temporary.write_bytes(content)
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
                self.asset_store.verify(asset)

    def _replace_user_configuration(self, package: dict) -> None:
        docs = package["documents"]
        streams = docs[CONFIG_PATHS["stream-sources"]]["streams"]
        profiles = docs[CONFIG_PATHS["stream-binding-profiles"]]["profiles"]
        topologies = docs[CONFIG_PATHS["topology-profiles"]]["topologies"]
        scenes = docs[CONFIG_PATHS["scene-archives"]]["scenes"]
        whitelist = docs[CONFIG_PATHS["whitelist"]]
        settings = docs[CONFIG_PATHS["detection-settings"]]["settings"]
        model_pipeline_document = docs.get(CONFIG_PATHS["model-pipelines"])
        activation = docs[CONFIG_PATHS["activation-state"]]["activation"]
        assets = package["manifest"]["assets"]
        with self.repository.transaction() as connection:
            builtin_profile = self.repository.fetch_one(
                "SELECT profile_id FROM stream_binding_profile WHERE is_builtin = 1 ORDER BY profile_id LIMIT 1",
                connection=connection,
            )
            builtin_topology = self.repository.fetch_one(
                "SELECT topology_id, revision FROM topology_profile WHERE is_builtin = 1 ORDER BY topology_id LIMIT 1",
                connection=connection,
            )
            if builtin_profile is None or builtin_topology is None:
                raise ConfigurationError("BUILTIN_BASELINE_MISSING", "目标系统内置基线不存在")
            self.repository.execute(
                connection,
                """
                UPDATE activation_state SET stream_profile_id = ?, topology_id = ?, topology_revision = ?,
                    no_parking_scene_id = NULL, road_abnormal_scene_id = NULL
                WHERE singleton_id = 1
                """,
                (builtin_profile["profile_id"], builtin_topology["topology_id"], builtin_topology["revision"]),
            )
            self.repository.execute(connection, "DELETE FROM scene_archive")
            user_topologies = [row["topology_id"] for row in self.repository.fetch_all(
                "SELECT topology_id FROM topology_profile WHERE is_builtin = 0", connection=connection
            )]
            for topology_id in user_topologies:
                for table in ("topology_camera", "road_segment", "topology_node"):
                    self.repository.execute(connection, f"DELETE FROM {table} WHERE topology_id = ?", (topology_id,))
                self.repository.execute(connection, "DELETE FROM topology_profile WHERE topology_id = ?", (topology_id,))
            user_profiles = [row["profile_id"] for row in self.repository.fetch_all(
                "SELECT profile_id FROM stream_binding_profile WHERE is_builtin = 0", connection=connection
            )]
            for profile_id in user_profiles:
                self.repository.execute(connection, "DELETE FROM stream_binding WHERE profile_id = ?", (profile_id,))
                self.repository.execute(connection, "DELETE FROM stream_binding_profile WHERE profile_id = ?", (profile_id,))
            self.repository.execute(
                connection,
                "DELETE FROM stream_source WHERE stream_id NOT IN (SELECT stream_id FROM stream_binding)",
            )
            for asset in assets:
                existing = self.repository.fetch_one("SELECT * FROM asset WHERE asset_id = ?", (asset["asset_id"],), connection=connection)
                if existing is None:
                    self.repository.execute(
                        connection,
                        """
                        INSERT INTO asset
                            (asset_id, kind, relative_path, sha256, size_bytes, media_type, width, height)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        tuple(asset[key] for key in ("asset_id", "kind", "relative_path", "sha256", "size_bytes", "media_type", "width", "height")),
                    )
                elif any(existing[key] != asset[key] for key in ("kind", "relative_path", "sha256", "size_bytes", "media_type", "width", "height")):
                    raise ConfigurationError("CONFIG_ASSET_CONFLICT", f"资源 ID 冲突: {asset['asset_id']}")
            builtin_stream_ids = {row["stream_id"] for row in self.repository.fetch_all(
                """
                SELECT DISTINCT b.stream_id FROM stream_binding b
                JOIN stream_binding_profile p ON p.profile_id = b.profile_id WHERE p.is_builtin = 1
                """, connection=connection
            )}
            for stream in streams:
                if stream["stream_id"] in builtin_stream_ids:
                    continue
                self.repository.execute(
                    connection,
                    "INSERT INTO stream_source (stream_id, name, rtsp_url, enabled) VALUES (?, ?, ?, ?)",
                    (stream["stream_id"], stream["name"], stream["rtsp_url"], int(stream["enabled"])),
                )
            for profile in profiles:
                self.repository.execute(
                    connection,
                    "INSERT INTO stream_binding_profile (profile_id, name, description, is_builtin) VALUES (?, ?, ?, 0)",
                    (profile["profile_id"], profile["name"], profile.get("description", "")),
                )
                self.repository.executemany(
                    connection,
                    "INSERT INTO stream_binding (profile_id, camera_id, stream_id) VALUES (?, ?, ?)",
                    ((profile["profile_id"], item["camera_id"], item["stream_id"]) for item in profile["bindings"]),
                )
            for topology in topologies:
                self._insert_topology(connection, topology)
            for scene in scenes:
                self.repository.execute(
                    connection,
                    """
                    INSERT INTO scene_archive
                        (scene_id, scene_type, name, topology_id, topology_revision, camera_id,
                         reference_asset_id, validated_config_json, review_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (scene["scene_id"], scene["scene_type"], scene["name"], scene["topology_id"], scene["topology_revision"], scene["camera_id"], scene.get("reference_asset_id"), json.dumps(scene["config"], ensure_ascii=False, separators=(",", ":")), scene["review_status"]),
                )
            self.repository.execute(connection, "DELETE FROM whitelist_entry")
            self.repository.executemany(
                connection,
                "INSERT INTO whitelist_entry (plate, note, added_at) VALUES (?, ?, ?)",
                ((item["plate"], item.get("note", ""), item["added_at"]) for item in whitelist["entries"]),
            )
            self.repository.execute(connection, "UPDATE whitelist_setting SET enabled = ? WHERE singleton_id = 1", (int(whitelist["enabled"]),))
            self.repository.execute(
                connection,
                """
                UPDATE detection_settings SET enabled = ?, yolo_threshold = ?, lpr_threshold = ?,
                    frame_interval = ?, device_preference = ? WHERE singleton_id = 1
                """,
                (int(settings["enabled"]), settings["yolo_threshold"], settings["lpr_threshold"], settings["interval"], settings["device_preference"]),
            )
            if model_pipeline_document is not None:
                self.repository.executemany(
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
                        (
                            item["preset"],
                            int(item["enabled"]),
                            item["device_preference"],
                            item["yolo_threshold"],
                            item["lpr_threshold"],
                            item["frame_interval"],
                            item["inference_size"],
                            item["parking_move_threshold"],
                            item["mog_history"],
                            item["mog_variance_threshold"],
                            item["mog_min_area"],
                            item["mog_min_duration"],
                            item["mog_max_duration"],
                            item["mog_warmup_frames"],
                            _utc_now(),
                            item["scene_key"],
                        )
                        for item in model_pipeline_document["settings"]
                    ),
                )
            self.repository.execute(
                connection,
                """
                UPDATE activation_state SET stream_profile_id = ?, topology_id = ?, topology_revision = ?,
                    no_parking_scene_id = ?, road_abnormal_scene_id = ? WHERE singleton_id = 1
                """,
                tuple(activation[key] for key in ("stream_profile_id", "topology_id", "topology_revision", "no_parking_scene_id", "road_abnormal_scene_id")),
            )

    def _insert_topology(self, connection, topology: dict) -> None:
        self.repository.execute(
            connection,
            """
            INSERT INTO topology_profile
                (topology_id, name, revision, map_asset_id, map_width, map_height, is_builtin)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            tuple(topology[key] for key in ("topology_id", "name", "revision", "map_asset_id", "map_width", "map_height")),
        )
        self.repository.executemany(
            connection,
            "INSERT INTO topology_node (topology_id, node_id, x, y, node_type) VALUES (?, ?, ?, ?, ?)",
            ((topology["topology_id"], item["node_id"], item["x"], item["y"], item["node_type"]) for item in topology["nodes"]),
        )
        self.repository.executemany(
            connection,
            """
            INSERT INTO road_segment
                (topology_id, segment_id, name, points_json, geometry_type, start_node_id,
                 end_node_id, direction, level, capacity, road_width)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ((topology["topology_id"], item["segment_id"], item["name"], json.dumps(item["points"], separators=(",", ":")), item["geometry_type"], item["start_node_id"], item["end_node_id"], item["direction"], item["level"], item["capacity"], item["road_width"]) for item in topology["segments"]),
        )
        self.repository.executemany(
            connection,
            """
            INSERT INTO topology_camera
                (topology_id, camera_id, x, y, heading, view_range, segment_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ((topology["topology_id"], item["camera_id"], item["x"], item["y"], item["heading"], item["view_range"], item["segment_id"]) for item in topology["cameras"]),
        )

    def _restore_database(self, backup: Path) -> None:
        source = sqlite3.connect(backup)
        destination = sqlite3.connect(self.repository.database_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

    def _prune_backups(self) -> None:
        for pattern in ("*.sqlite3", "*.zip"):
            files = sorted(self.backup_dir.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
            for stale in files[5:]:
                stale.unlink(missing_ok=True)

    def _purge_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [token for token, pending in self._tokens.items() if pending.expires_at <= now]
        for token in expired:
            self._tokens.pop(token).package_path.unlink(missing_ok=True)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_json(payload: bytes, label: str) -> dict:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError("CONFIG_JSON_INVALID", f"{label} 不是有效 UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ConfigurationError("CONFIG_JSON_INVALID", f"{label} 顶层必须是对象")
    return value


def _require_keys(value: dict, expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ConfigurationError(
            "CONFIG_UNKNOWN_FIELD",
            f"{label} 字段不符合协议",
            details=[{"missing": sorted(expected - actual), "unknown": sorted(actual - expected)}],
        )


def _media_type(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    return {
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(suffix, "application/octet-stream")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
