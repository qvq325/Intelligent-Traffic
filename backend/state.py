"""Shared application state and persistence boundaries."""

from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from detection_processor import DetectionProcessor, DetectionResult
from traffic_map import TrafficMapModel
from whitelist_manager import WhitelistManager

from .config import AppConfig
from .configuration import ConfigurationRepository, build_camera_catalog
from .configuration.activation import ActivationCoordinator
from .configuration.assets import AssetStore
from .configuration.errors import ConfigurationError
from .configuration.legacy_migration import LegacyMigrator
from .configuration.package import CONFIG_PATHS, ImportExportService
from .configuration.probe import StreamProbeService
from .configuration.service import ConfigurationService
from .device_monitor import DeviceMonitor
from .model_pipelines import ModelPipelineOptions
from .no_parking import NoParkingMonitor
from .road_abnormal import RoadAbnormalMonitor
from .video_stream import VideoStreamService


_MODEL_PIPELINE_STREAMS = (
    ("realtime", "video"),
    ("traffic_map", "map_analysis"),
    ("no_parking", "no_parking_video"),
    ("road_abnormal", "road_abnormal_video"),
)


class ApplicationState:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.map_lock = threading.RLock()
        self.preview_stream = None
        self._active_stream_sources = dict(config.stream_sources)
        self._configuration_bootstrapped = False

        self.whitelist = WhitelistManager()
        if config.whitelist_file.is_file():
            self.whitelist.load(str(config.whitelist_file))

        self.traffic_map = TrafficMapModel(
            config.traffic_map_file,
            self._active_stream_sources.keys(),
        )
        if not config.traffic_map_file.is_file():
            self.traffic_map.save()

        self.devices = [
            {"id": device_id, "name": display_name}
            for device_id, display_name in DetectionProcessor.get_available_devices()
        ]
        default_device = self.devices[1]["id"] if len(self.devices) > 1 else self.devices[0]["id"]

        scene_root = (
            config.configuration_dir / "runtime-scenes"
            if config.configuration_dir is not None
            else config.upload_dir.parent
        )
        self.no_parking = NoParkingMonitor(scene_root / "no_parking")
        self.road_abnormal = RoadAbnormalMonitor(
            scene_root / "road_abnormal",
            config.project_dir / "yolo11m.pt",
            device=default_device,
        )
        self.video = VideoStreamService(
            self.whitelist,
            scene_key="realtime",
        )
        self.map_analysis = VideoStreamService(
            self.whitelist,
            on_detections=self._handle_detections,
            scene_key="traffic_map",
        )
        self.no_parking_video = VideoStreamService(
            self.whitelist,
            on_detections=self._handle_no_parking_detections,
            scene_key="no_parking",
        )
        self.road_abnormal_video = VideoStreamService(
            self.whitelist,
            frame_processor=self.road_abnormal.process_frame,
            scene_key="road_abnormal",
        )
        for stream in (
            self.video,
            self.map_analysis,
            self.no_parking_video,
            self.road_abnormal_video,
        ):
            stream.update_detection_settings(device=default_device)
        self.road_abnormal_video.update_detection_settings(enabled=False)
        self.device_monitor = DeviceMonitor(
            stream_status_providers={
                "realtime": self.video.status,
                "traffic_map": self.map_analysis.status,
                "no_parking": self.no_parking_video.status,
                "road_abnormal": self.road_abnormal_video.status,
            }
        )

        self.configuration_repository = None
        self.asset_store = None
        self.configuration_service = None
        self.probe_service = None
        self.activation_coordinator = None
        self.import_export_service = None
        if config.configuration_dir is not None:
            root = config.configuration_dir
            catalog = build_camera_catalog(
                {camera_id: camera_id for camera_id in config.stream_sources}
            )
            self.configuration_repository = ConfigurationRepository(root / "config.sqlite3")
            self.asset_store = AssetStore(root / "assets")
            self.configuration_service = ConfigurationService(
                self.configuration_repository,
                self.asset_store,
            )
            self.probe_service = StreamProbeService()
            self.activation_coordinator = ActivationCoordinator(
                self.configuration_service,
                self,
                self.probe_service,
            )
            self.import_export_service = ImportExportService(
                self.configuration_service,
                root,
                self.reload_configuration_runtime,
                preflight_validator=self.preflight_configuration_package,
            )
            self._camera_catalog = catalog

    @property
    def configuration_enabled(self) -> bool:
        return self.configuration_service is not None

    def attach_preview_service(self, preview_stream) -> None:
        self.preview_stream = preview_stream

    def apply_model_pipeline_settings(self) -> None:
        """Resolve all fixed scenes before changing any runtime service."""
        assert self.configuration_service is not None
        payload = self.configuration_service.model_pipeline_settings()
        rows = payload.get("settings") if isinstance(payload, dict) else None
        expected = tuple(scene_key for scene_key, _attribute in _MODEL_PIPELINE_STREAMS)

        scene_keys: list[str] = []
        structurally_valid = isinstance(rows, list)
        if structurally_valid:
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("scene_key"), str):
                    structurally_valid = False
                    break
                scene_keys.append(row["scene_key"])

        seen: set[str] = set()
        duplicates: set[str] = set()
        for scene_key in scene_keys:
            if scene_key in seen:
                duplicates.add(scene_key)
            seen.add(scene_key)
        missing = sorted(set(expected) - set(scene_keys))
        unknown = sorted(set(scene_keys) - set(expected))
        if (
            not structurally_valid
            or len(scene_keys) != len(expected)
            or duplicates
            or missing
            or unknown
        ):
            raise ConfigurationError(
                "MODEL_PIPELINE_RUNTIME_INVALID",
                "Model pipeline runtime settings must contain each fixed scene once",
                details=[
                    {
                        "missing_scene_keys": missing,
                        "duplicate_scene_keys": sorted(duplicates),
                        "unknown_scene_keys": unknown,
                    }
                ],
            )

        resolved: dict[str, ModelPipelineOptions] = {}
        registry = self.configuration_service.model_pipeline_registry
        for row in rows:
            scene_key = row["scene_key"]
            options = registry.resolve(row)
            if (
                not isinstance(options, ModelPipelineOptions)
                or options.scene_key != scene_key
            ):
                raise ConfigurationError(
                    "MODEL_PIPELINE_RUNTIME_INVALID",
                    "Resolved model pipeline options do not match their scene",
                    details=[{"scene_key": scene_key}],
                )
            resolved[scene_key] = options

        for scene_key, attribute in _MODEL_PIPELINE_STREAMS:
            getattr(self, attribute).apply_model_pipeline_options(resolved[scene_key])
        self.no_parking.apply_model_pipeline_options(resolved["no_parking"])
        self.road_abnormal.apply_model_pipeline_options(resolved["road_abnormal"])

    def bootstrap_configuration(self) -> dict | None:
        if not self.configuration_enabled or self._configuration_bootstrapped:
            return None
        assert self.configuration_repository is not None
        assert self.asset_store is not None
        assert self.configuration_service is not None
        self.configuration_repository.initialize(self._camera_catalog)
        migration = LegacyMigrator(
            self.configuration_repository,
            self.asset_store,
            self.config.project_dir,
            self.config.stream_sources,
        ).migrate()
        interrupted = self.configuration_service.mark_interrupted_operations()
        self._load_configuration_runtime()
        self._configuration_bootstrapped = True
        return {"migration": migration.as_dict(), "interrupted_operations": interrupted}

    def _load_configuration_runtime(self) -> None:
        assert self.configuration_service is not None
        state = self.configuration_service.get_activation_state()
        if state["stream_profile_id"]:
            profile = self.configuration_service.get_stream_profile(state["stream_profile_id"])
            mapping = {
                item["camera_id"]: item["rtsp_url"]
                for item in profile["bindings"]
            }
            self._active_stream_sources = mapping
            if self.preview_stream is not None:
                self.preview_stream.reconfigure(mapping)
        if state["topology_id"]:
            topology = self.configuration_service.get_topology(state["topology_id"])
            self._install_topology_runtime(topology)
        self._load_whitelist_runtime()
        self._load_scenes_runtime()
        settings = self.configuration_service.detection_settings()
        available = {item["id"] for item in self.devices}
        if settings["device_preference"] not in available:
            raise ConfigurationError(
                "DETECTION_DEVICE_UNAVAILABLE",
                "持久化的推理设备在当前系统不可用",
                details=[{"device": settings["device_preference"]}],
            )
        self.video.update_detection_settings(
            enabled=settings["enabled"],
            yolo_threshold=settings["yolo_threshold"],
            lpr_threshold=settings["lpr_threshold"],
            interval=settings["interval"],
            device=settings["device_preference"],
        )
        self._restore_active_scenes(state)
        self.apply_model_pipeline_settings()

    def reload_configuration_runtime(self) -> None:
        """Converge all runtime adapters to the last committed activation state."""
        assert self.configuration_service is not None
        state = self.configuration_service.get_activation_state()
        profile = self.configuration_service.get_stream_profile(state["stream_profile_id"])
        mapping = {
            item["camera_id"]: item["rtsp_url"]
            for item in profile["bindings"]
        }
        self.apply_stream_mapping(mapping)
        topology = self.configuration_service.get_topology(state["topology_id"])
        self.apply_topology(topology)
        self._load_whitelist_runtime()
        self._load_scenes_runtime()
        settings = self.configuration_service.detection_settings()
        available = {item["id"] for item in self.devices}
        if settings["device_preference"] not in available:
            raise ConfigurationError(
                "DETECTION_DEVICE_UNAVAILABLE",
                "导入配置指定的推理设备在当前系统不可用",
                details=[{"device": settings["device_preference"]}],
            )
        self.video.update_detection_settings(
            enabled=settings["enabled"],
            yolo_threshold=settings["yolo_threshold"],
            lpr_threshold=settings["lpr_threshold"],
            interval=settings["interval"],
            device=settings["device_preference"],
        )
        state = self.configuration_service.get_activation_state()
        self._restore_active_scenes(state)
        self.apply_model_pipeline_settings()

    def _restore_active_scenes(self, state: dict) -> None:
        assert self.configuration_service is not None
        for field in ("no_parking_scene_id", "road_abnormal_scene_id"):
            scene_id = state[field]
            if scene_id:
                scene = self.configuration_service.get_scene(scene_id)
                stream = self.configuration_service.resolve_camera_stream(scene["camera_id"])
                if stream is None:
                    raise RuntimeError(f"导入场景 {scene_id} 缺少流映射")
                self.activate_scene_runtime(scene, stream["rtsp_url"])

    def preflight_configuration_package(self, documents: dict[str, dict]) -> dict:
        """Validate imported activation targets against this host and its network."""
        assert self.configuration_service is not None
        settings = documents[CONFIG_PATHS["detection-settings"]]["settings"]
        available_devices = {item["id"] for item in self.devices}
        if settings["device_preference"] not in available_devices:
            raise ConfigurationError(
                "DETECTION_DEVICE_UNAVAILABLE",
                "配置包指定的推理设备在当前系统不可用",
                details=[{"device": settings["device_preference"]}],
            )

        activation = documents[CONFIG_PATHS["activation-state"]]["activation"]
        package_profiles = {
            item["profile_id"]: item
            for item in documents[CONFIG_PATHS["stream-binding-profiles"]]["profiles"]
        }
        target_profile = package_profiles.get(activation["stream_profile_id"])
        if target_profile is None:
            target_profile = self.configuration_service.get_stream_profile(
                activation["stream_profile_id"]
            )
        package_streams = {
            item["stream_id"]: item
            for item in documents[CONFIG_PATHS["stream-sources"]]["streams"]
        }
        current_streams = {
            item["stream_id"]: item
            for item in self.configuration_service.list_streams(reveal_credentials=True)
        }
        streams_by_id = {**current_streams, **package_streams}
        bindings = []
        for binding in target_profile["bindings"]:
            stream = streams_by_id.get(binding["stream_id"])
            if stream is None or not stream["enabled"]:
                raise ConfigurationError(
                    "CONFIG_ACTIVATION_INVALID",
                    "目标激活方案引用了不存在或已停用的流",
                    details=[{"stream_id": binding["stream_id"]}],
                )
            bindings.append({**binding, **stream})
        expected_cameras = {item["camera_id"] for item in self.configuration_service.list_cameras()}
        actual_cameras = {item["camera_id"] for item in bindings}
        if actual_cameras != expected_cameras or len({item["stream_id"] for item in bindings}) != len(expected_cameras):
            raise ConfigurationError(
                "STREAM_PROFILE_INCOMPLETE",
                "配置包目标激活方案未完整一对一覆盖固定摄像头目录",
                details=[{"missing_camera_ids": sorted(expected_cameras - actual_cameras)}],
            )
        probe_results = self.probe_service.probe_many(bindings)
        failed = [item for item in probe_results if not item["ok"]]
        if failed:
            raise ConfigurationError(
                "STREAM_PROBE_FAILED",
                f"配置包目标方案中有 {len(failed)} 路流未通过预检",
                status_code=422,
                details=failed,
            )

        package_topologies = {
            item["topology_id"]: item
            for item in documents[CONFIG_PATHS["topology-profiles"]]["topologies"]
        }
        target_topology = package_topologies.get(activation["topology_id"])
        if target_topology is None:
            target_topology = self.configuration_service.get_topology(
                activation["topology_id"]
            )
        if int(target_topology["revision"]) != int(activation["topology_revision"]):
            raise ConfigurationError(
                "CONFIG_ACTIVATION_INVALID",
                "目标激活拓扑修订号不存在",
            )
        scenes_by_id = {
            item["scene_id"]: item
            for item in documents[CONFIG_PATHS["scene-archives"]]["scenes"]
        }
        for field in ("no_parking_scene_id", "road_abnormal_scene_id"):
            scene_id = activation[field]
            if scene_id is None:
                continue
            scene = scenes_by_id[scene_id]
            if (
                scene["topology_id"] != activation["topology_id"]
                or int(scene["topology_revision"]) != int(activation["topology_revision"])
                or scene["review_status"] != "ready"
            ):
                raise ConfigurationError(
                    "CONFIG_ACTIVATION_INVALID",
                    "目标激活场景与拓扑修订不兼容",
                    details=[{"scene_id": scene_id}],
                )
        return {
            "device": settings["device_preference"],
            "streams": probe_results,
            "topology_id": activation["topology_id"],
            "topology_revision": activation["topology_revision"],
        }

    def _load_whitelist_runtime(self) -> None:
        assert self.configuration_repository is not None
        self.whitelist.clear()
        setting = self.configuration_repository.fetch_one(
            "SELECT enabled FROM whitelist_setting WHERE singleton_id = 1"
        )
        self.whitelist.enabled = bool(setting["enabled"]) if setting else True
        for item in self.configuration_repository.fetch_all(
            "SELECT plate, note FROM whitelist_entry ORDER BY plate"
        ):
            self.whitelist.add(item["plate"], item["note"])

    def _load_scenes_runtime(self) -> None:
        assert self.configuration_service is not None
        for scene_type, monitor in (
            ("no_parking", self.no_parking),
            ("road_abnormal", self.road_abnormal),
        ):
            for current in list(monitor.catalog()["scenes"]):
                monitor.delete_scene(current["scene_id"])
            for scene in self.configuration_service.list_scenes(scene_type=scene_type):
                payload = {
                    "scene_id": scene["scene_id"],
                    "name": scene["name"],
                    "camera_id": scene["camera_id"],
                    **scene["config"],
                }
                asset = scene.get("reference_asset")
                if asset:
                    source = self.asset_store.resolve(asset["relative_path"])
                    extension = source.suffix.lower()
                    filename = f"reference_{asset['sha256'][:32]}{extension}"
                    destination = monitor.references_dir / filename
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if not destination.is_file():
                        shutil.copy2(source, destination)
                    payload.update(
                        reference_image=filename,
                        reference_width=asset["width"],
                        reference_height=asset["height"],
                    )
                else:
                    payload.update(
                        reference_image="",
                        reference_width=0,
                        reference_height=0,
                    )
                monitor.upsert_scene(payload)

    def start(self) -> None:
        self.config.upload_dir.mkdir(parents=True, exist_ok=True)
        self.config.map_upload_dir.mkdir(parents=True, exist_ok=True)
        self.video.start()
        self.map_analysis.start()
        self.no_parking_video.start()
        self.road_abnormal_video.start()

    def shutdown(self) -> None:
        self.no_parking.stop()
        self.road_abnormal.stop()
        self.no_parking_video.stop()
        self.road_abnormal_video.stop()
        self.map_analysis.stop()
        self.video.stop()
        if not self.configuration_enabled:
            self.save_whitelist()
        with self.map_lock:
            self.traffic_map.save()

    def save_whitelist(self) -> bool:
        if not self.configuration_enabled:
            self.config.whitelist_file.parent.mkdir(parents=True, exist_ok=True)
            return self.whitelist.save(str(self.config.whitelist_file))
        assert self.configuration_repository is not None
        try:
            with self.configuration_repository.transaction() as connection:
                self.configuration_repository.execute(connection, "DELETE FROM whitelist_entry")
                self.configuration_repository.executemany(
                    connection,
                    "INSERT INTO whitelist_entry (plate, note, added_at) VALUES (?, ?, ?)",
                    (
                        (entry.plate, entry.note, entry.added_at or "1970-01-01T00:00:00Z")
                        for entry in self.whitelist.get_all()
                    ),
                )
                self.configuration_repository.execute(
                    connection,
                    "UPDATE whitelist_setting SET enabled = ? WHERE singleton_id = 1",
                    (int(self.whitelist.enabled),),
                )
            return True
        except OSError:
            return False

    def source_catalog(self) -> list[dict]:
        return [
            {"id": source_id, "name": source_id}
            for source_id in self._active_stream_sources
        ]

    def stream_url(self, camera_id: str) -> str | None:
        return self._active_stream_sources.get(camera_id)

    def current_stream_mapping(self) -> dict[str, str]:
        return dict(self._active_stream_sources)

    def apply_stream_mapping(self, mapping: dict[str, str]) -> dict:
        previous_mapping = dict(self._active_stream_sources)
        expected = set(previous_mapping)
        if set(mapping) != expected:
            raise ValueError("stream mapping must preserve the fixed camera catalog")
        reconnected: list[str] = []
        for stream in (
            self.video,
            self.map_analysis,
            self.no_parking_video,
            self.road_abnormal_video,
        ):
            status = stream.status()
            source = status.get("active_source")
            if source is None or source["id"] not in mapping:
                continue
            target_url = mapping[source["id"]]
            if previous_mapping.get(source["id"]) == target_url:
                continue
            stream.select_source(source["id"], source["id"], target_url)
            reconnected.append(source["id"])
            if status["running"]:
                after = stream.status()["frame_sequence"]
                _, frame = stream.wait_for_frame(after, timeout=8.0)
                if frame is None:
                    raise RuntimeError(f"摄像头 {source['id']} 重连后未取得首帧")
        self._active_stream_sources = dict(mapping)
        if self.preview_stream is not None:
            self.preview_stream.reconfigure(mapping)
        return {"reconnected_camera_ids": sorted(set(reconnected))}

    def map_snapshot(self) -> dict:
        with self.map_lock:
            states = self.traffic_map.segment_states()
            image_path = self.map_image_path()
            image_version = int(image_path.stat().st_mtime_ns) if image_path.is_file() else 0
            return {
                "image_url": f"/api/map/image?v={image_version}",
                "segments": [asdict(segment) for segment in self.traffic_map.segments.values()],
                "cameras": [asdict(camera) for camera in self.traffic_map.cameras.values()],
                "tracks": [asdict(track) for track in self.traffic_map.tracks.values()],
                "states": [asdict(state) for state in states.values()],
            }

    def map_image_path(self) -> Path:
        configured = self.traffic_map.map_image_path
        if configured:
            path = Path(configured)
            if not path.is_absolute():
                path = self.traffic_map.config_path.parent / path
            if path.is_file():
                return path.resolve()
        return self.config.fallback_map_image.resolve()

    def ensure_active_topology_editable(self) -> None:
        if not self.configuration_enabled:
            return
        assert self.configuration_service is not None
        state = self.configuration_service.get_activation_state()
        topology = self.configuration_service.get_topology(state["topology_id"])
        if topology["is_builtin"]:
            raise ConfigurationError(
                "BUILTIN_TOPOLOGY_READ_ONLY",
                "内置拓扑不可修改，请先复制为普通方案",
                status_code=409,
            )

    def set_map_image(self, path: Path) -> None:
        self.ensure_active_topology_editable()
        stored_asset = None
        with self.map_lock:
            if self.configuration_enabled:
                asset = self.asset_store.ingest(path, "map")
                stored_asset = self.configuration_service.register_asset(asset)
                stored = self.asset_store.resolve(stored_asset["relative_path"])
                self.traffic_map.map_image_path = str(stored)
            else:
                try:
                    portable = os.path.relpath(path, self.config.traffic_map_file.parent)
                except ValueError:
                    portable = str(path)
                self.traffic_map.map_image_path = portable
            self.traffic_map.save()
        if stored_asset is not None:
            self.persist_active_topology(map_asset=stored_asset)

    def persist_active_topology(self, *, map_asset: dict | None = None) -> dict | None:
        if not self.configuration_enabled:
            return None
        self.ensure_active_topology_editable()
        assert self.configuration_service is not None
        state = self.configuration_service.get_activation_state()
        current = self.configuration_service.get_topology(state["topology_id"])
        nodes = []
        segments = []
        for segment in self.traffic_map.segments.values():
            start_node = self._runtime_node_id(segment.segment_id, "start")
            end_node = self._runtime_node_id(segment.segment_id, "end")
            nodes.extend(
                [
                    {"node_id": start_node, "x": segment.points[0][0], "y": segment.points[0][1], "node_type": "endpoint"},
                    {"node_id": end_node, "x": segment.points[-1][0], "y": segment.points[-1][1], "node_type": "endpoint"},
                ]
            )
            value = asdict(segment)
            value.update(start_node_id=start_node, end_node_id=end_node)
            segments.append(value)
        payload = {
            "name": current["name"],
            "map_asset_id": map_asset["asset_id"] if map_asset else current["map_asset_id"],
            "map_width": map_asset["width"] if map_asset else current["map_width"],
            "map_height": map_asset["height"] if map_asset else current["map_height"],
            "nodes": nodes,
            "segments": segments,
            "cameras": [asdict(camera) for camera in self.traffic_map.cameras.values()],
        }
        updated = self.activation_coordinator.update_topology(
            state["topology_id"],
            payload,
            runtime_already_applied=True,
        )
        self.no_parking.stop()
        self.road_abnormal.stop()
        self.no_parking_video.stop_stream()
        self.road_abnormal_video.stop_stream()
        return updated

    def apply_topology(self, topology: dict) -> dict:
        deactivated: list[str] = []
        compatible_scenes: list[dict] = []
        if self.configuration_service is not None:
            state = self.configuration_service.get_activation_state()
            for field, scene_type in (
                ("no_parking_scene_id", "no_parking"),
                ("road_abnormal_scene_id", "road_abnormal"),
            ):
                scene_id = state.get(field)
                if not scene_id:
                    continue
                scene = self.configuration_service.get_scene(scene_id)
                if (
                    scene["topology_id"] != topology["topology_id"]
                    or scene["topology_revision"] != topology["revision"]
                ):
                    self.deactivate_scene_runtime(scene_type)
                    deactivated.append(scene_id)
                else:
                    compatible_scenes.append(scene)
        self._install_topology_runtime(topology)
        for scene in compatible_scenes:
            monitor = self.no_parking if scene["scene_type"] == "no_parking" else self.road_abnormal
            runtime_status = monitor.status()
            if runtime_status.get("running") and runtime_status.get("active_scene_id") == scene["scene_id"]:
                continue
            stream = self.configuration_service.resolve_camera_stream(scene["camera_id"])
            if stream is not None:
                self.activate_scene_runtime(scene, stream["rtsp_url"])
        return {"deactivated_scene_ids": deactivated, "runtime_reset": True}

    def _install_topology_runtime(self, topology: dict) -> None:
        if self.config.configuration_dir is None:
            raise RuntimeError("configuration runtime directory is unavailable")
        cache_path = self.config.configuration_dir / "active-topology.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        asset = topology.get("map_asset")
        map_path = self.asset_store.resolve(asset["relative_path"]) if asset else self.config.fallback_map_image
        payload = {
            "version": 3,
            "map_image": str(map_path),
            "segments": [
                {
                    key: value
                    for key, value in segment.items()
                    if key not in {"start_node_id", "end_node_id"}
                }
                for segment in topology["segments"]
            ],
            "cameras": [
                {key: value for key, value in camera.items() if key != "display_name"}
                for camera in topology["cameras"]
            ],
        }
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        replacement = TrafficMapModel(cache_path, self._active_stream_sources.keys())
        replacement.reset_runtime()
        with self.map_lock:
            self.traffic_map = replacement

    def persist_scene(self, scene_type: str, scene: dict) -> dict | None:
        if not self.configuration_enabled:
            return None
        assert self.configuration_service is not None
        state = self.configuration_service.get_activation_state()
        monitor = self.no_parking if scene_type == "no_parking" else self.road_abnormal
        reference_asset_id = None
        if scene.get("reference_image"):
            reference_path = monitor.reference_path(scene["reference_image"])
            if reference_path is None:
                raise ConfigurationError("SCENE_REFERENCE_MISSING", "场景参考图不存在")
            reference_asset_id = self.configuration_service.register_asset(
                self.asset_store.ingest(reference_path, "scene_reference")
            )["asset_id"]
        excluded = {
            "scene_id", "name", "camera_id", "reference_image", "reference_url",
            "reference_width", "reference_height", "created_at", "updated_at",
        }
        return self.configuration_service.upsert_scene_archive(
            {
                "scene_id": scene["scene_id"],
                "scene_type": scene_type,
                "name": scene["name"],
                "topology_id": state["topology_id"],
                "topology_revision": state["topology_revision"],
                "camera_id": scene["camera_id"],
                "reference_asset_id": reference_asset_id,
                "config": {key: value for key, value in scene.items() if key not in excluded},
            }
        )

    def delete_scene_archive(self, scene_id: str) -> None:
        if not self.configuration_enabled:
            return
        try:
            self.configuration_service.delete_scene(scene_id)
        except ConfigurationError as exc:
            if exc.code != "SCENE_NOT_FOUND":
                raise

    def activate_scene_runtime(self, scene: dict, stream_url: str) -> dict:
        scene_type = scene["scene_type"]
        runtime_scene = (
            self.no_parking.get_scene(scene["scene_id"])
            if scene_type == "no_parking"
            else self.road_abnormal.get_scene(scene["scene_id"])
        )
        if runtime_scene is None:
            raise RuntimeError("场景尚未装载到运行时")
        if scene_type == "no_parking":
            self.no_parking_video.select_source(scene["camera_id"], scene["camera_id"], stream_url)
            self.no_parking_video.update_detection_settings(enabled=True)
            return self.no_parking.start(scene["scene_id"])
        self.road_abnormal_video.select_source(scene["camera_id"], scene["camera_id"], stream_url)
        self.road_abnormal_video.update_detection_settings(enabled=False)
        return self.road_abnormal.start(scene["scene_id"])

    def start_legacy_scene(self, scene_type: str, scene_id: str) -> dict:
        monitor = self.no_parking if scene_type == "no_parking" else self.road_abnormal
        scene = monitor.get_scene(scene_id)
        if scene is None:
            raise ConfigurationError("SCENE_NOT_FOUND", "场景不存在", status_code=404)
        url = self.stream_url(scene["camera_id"])
        if url is None:
            raise ConfigurationError("SCENE_STREAM_MISSING", "场景摄像头没有可用流")
        if self.configuration_enabled and self.configuration_service._scene_exists(scene_id):
            self.activation_coordinator.activate_scene(scene_id)
            return monitor.status()
        return self.activate_scene_runtime(
            {**scene, "scene_type": scene_type},
            url,
        )

    def deactivate_scene_runtime(self, scene_type: str) -> dict:
        if scene_type == "no_parking":
            result = self.no_parking.stop()
            self.no_parking_video.stop_stream()
            return result
        if scene_type == "road_abnormal":
            result = self.road_abnormal.stop()
            self.road_abnormal_video.stop_stream()
            return result
        raise ValueError(f"unknown scene type: {scene_type}")

    def stop_legacy_scene(self, scene_type: str) -> dict:
        if self.configuration_enabled:
            self.activation_coordinator.deactivate_scene(scene_type)
            monitor = self.no_parking if scene_type == "no_parking" else self.road_abnormal
            return monitor.status()
        return self.deactivate_scene_runtime(scene_type)

    @staticmethod
    def _runtime_node_id(segment_id: str, endpoint: str) -> str:
        return f"node-{uuid5(NAMESPACE_URL, f'videotest:runtime-node:{segment_id}:{endpoint}').hex}"

    def _handle_detections(
        self,
        camera_id: str,
        detections: list[DetectionResult],
        frame_size: tuple[int, int],
    ) -> None:
        with self.map_lock:
            self.traffic_map.update_detections(camera_id, detections, frame_size)

    def _handle_no_parking_detections(
        self,
        camera_id: str,
        detections: list[DetectionResult],
        frame_size: tuple[int, int],
    ) -> None:
        self.no_parking.update_detections(camera_id, detections, frame_size)
