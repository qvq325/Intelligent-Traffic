"""FastAPI routes for the configuration management surface."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, File, Query, UploadFile, status

from .errors import ConfigurationError
from .models import (
    DetectionConfiguration,
    ImportApplyRequest,
    ModelPipelineBatchUpdate,
    StreamBatchCreateRequest,
    StreamBatchDeleteRequest,
    StreamBatchUpdateRequest,
    StreamCreate,
    StreamProbeRequest,
    StreamProfileActivationRequest,
    StreamProfileCreate,
    StreamProfileUpdate,
    StreamUpdate,
    TopologyCreate,
    TopologyUpdate,
)


def create_configuration_router(service, coordinator, package_service=None) -> APIRouter:
    router = APIRouter(prefix="/api/config", tags=["configuration"])

    @router.get("/summary")
    def summary() -> dict:
        return service.summary()

    @router.get("/model-pipelines")
    def model_pipelines() -> dict:
        return service.model_pipeline_settings()

    @router.put("/model-pipelines")
    def update_model_pipelines(payload: ModelPipelineBatchUpdate) -> dict:
        result = service.update_model_pipeline_settings(
            [item.model_dump() for item in payload.settings]
        )
        coordinator.runtime.apply_model_pipeline_settings()
        return result

    @router.get("/devices")
    def devices() -> dict:
        return coordinator.runtime.device_monitor.snapshot()

    @router.get("/operations/{operation_id}")
    def operation(operation_id: str) -> dict:
        return service.get_operation(operation_id)

    @router.get("/audit")
    def audit(
        limit: int | None = Query(default=None, ge=1, le=500),
        offset: int | None = Query(default=None, ge=0),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=25, ge=1, le=500),
        result: str | None = None,
    ) -> dict:
        actual_limit = limit or page_size
        actual_offset = offset if offset is not None else (page - 1) * actual_limit
        return service.list_audit(limit=actual_limit, offset=actual_offset, result=result)

    @router.get("/streams")
    def streams(reveal_credentials: bool = False) -> list[dict]:
        return service.list_streams(reveal_credentials=reveal_credentials)

    @router.post("/streams", status_code=status.HTTP_201_CREATED)
    def create_stream(payload: StreamCreate) -> dict:
        return service.create_stream(payload.model_dump())

    @router.post("/streams/batch", status_code=status.HTTP_201_CREATED)
    def create_stream_batch(payload: StreamBatchCreateRequest) -> dict:
        return service.create_stream_batch(
            [item.model_dump() for item in payload.streams]
        )

    @router.put("/streams/batch")
    def update_stream_batch(payload: StreamBatchUpdateRequest) -> dict:
        return coordinator.update_stream_sources(
            [item.model_dump() for item in payload.streams]
        )

    @router.delete("/streams/batch")
    def delete_stream_batch(payload: StreamBatchDeleteRequest) -> dict:
        return service.delete_stream_batch(payload.stream_ids)

    @router.post("/streams/probe")
    def probe_stream_batch(payload: StreamProbeRequest) -> dict:
        started = time.monotonic()
        streams = service.get_streams(
            payload.stream_ids,
            reveal_credentials=True,
        )
        results = coordinator.probe_service.probe_many(streams)
        service.record_probe_results(results)
        succeeded = sum(1 for result in results if result["ok"])
        return {
            "total": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "results": results,
        }

    @router.put("/streams/{stream_id}")
    def update_stream(stream_id: str, payload: StreamUpdate) -> dict:
        return coordinator.update_stream_source(
            stream_id, payload.model_dump(exclude_none=True)
        )

    @router.delete("/streams/{stream_id}")
    def delete_stream(stream_id: str) -> dict:
        return service.delete_stream(stream_id)

    @router.post("/streams/{stream_id}/probe")
    def probe_stream(stream_id: str) -> dict:
        stream = service.get_stream(stream_id)
        result = coordinator.probe_service.probe(stream_id, stream["rtsp_url"]).as_dict()
        service.record_probe_results([result])
        if not result["ok"]:
            raise ConfigurationError(
                "STREAM_PROBE_FAILED",
                "流未通过首帧探测",
                status_code=502,
                details=[result],
            )
        return result

    @router.get("/stream-profiles")
    def stream_profiles() -> list[dict]:
        return service.list_stream_profiles()

    @router.post("/stream-profiles", status_code=status.HTTP_201_CREATED)
    def create_stream_profile(payload: StreamProfileCreate) -> dict:
        return service.create_stream_profile(payload.model_dump())

    @router.get("/stream-profiles/{profile_id}")
    def stream_profile(profile_id: str) -> dict:
        return service.get_stream_profile(profile_id)

    @router.put("/stream-profiles/{profile_id}")
    def update_stream_profile(profile_id: str, payload: StreamProfileUpdate) -> dict:
        return coordinator.update_stream_profile(profile_id, payload.model_dump())

    @router.delete("/stream-profiles/{profile_id}")
    def delete_stream_profile(profile_id: str) -> dict:
        return service.delete_stream_profile(profile_id)

    @router.post("/stream-profiles/{profile_id}/clone", status_code=status.HTTP_201_CREATED)
    def clone_stream_profile(profile_id: str) -> dict:
        return service.clone_stream_profile(profile_id)

    @router.post("/stream-profiles/{profile_id}/preflight")
    def preflight_stream_profile(profile_id: str) -> dict:
        return coordinator.preflight_stream_profile(profile_id)

    @router.post("/stream-profiles/{profile_id}/activate")
    def activate_stream_profile(
        profile_id: str,
        payload: StreamProfileActivationRequest | None = None,
    ) -> dict:
        return coordinator.activate_stream_profile(
            profile_id,
            preflight_token=payload.preflight_token if payload else None,
        )

    @router.get("/topologies")
    def topologies() -> list[dict]:
        return service.list_topologies()

    @router.post("/topologies", status_code=status.HTTP_201_CREATED)
    def create_topology(payload: TopologyCreate) -> dict:
        return service.create_topology(payload.model_dump())

    @router.get("/topologies/{topology_id}")
    def topology(topology_id: str) -> dict:
        return service.get_topology(topology_id)

    @router.put("/topologies/{topology_id}")
    def update_topology(topology_id: str, payload: TopologyUpdate) -> dict:
        return coordinator.update_topology(topology_id, payload.model_dump())

    @router.delete("/topologies/{topology_id}")
    def delete_topology(topology_id: str) -> dict:
        return service.delete_topology(topology_id)

    @router.post("/topologies/{topology_id}/clone", status_code=status.HTTP_201_CREATED)
    def clone_topology(topology_id: str) -> dict:
        return service.clone_topology(topology_id)

    @router.post("/topologies/{topology_id}/activate")
    def activate_topology(topology_id: str) -> dict:
        return coordinator.activate_topology(topology_id)

    @router.post("/topologies/{topology_id}/map-image")
    async def upload_topology_map(topology_id: str, file: UploadFile = File(...)) -> dict:
        suffix = Path(file.filename or "map.img").suffix[:12]
        staging = service.asset_store.root_dir.parent / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=staging, prefix="map-upload-", suffix=suffix, delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                written = 0
                while chunk := await file.read(1024 * 1024):
                    written += len(chunk)
                    if written > 50 * 1024 * 1024:
                        raise ConfigurationError(
                            "ASSET_TOO_LARGE", "图片超过 50 MiB 限制", status_code=413
                        )
                    temporary.write(chunk)
            asset = service.asset_store.ingest(temporary_path, "map")
            stored = service.register_asset(asset)
            topology = service.get_topology(topology_id)
            return coordinator.update_topology(
                topology_id,
                {
                    "name": topology["name"],
                    "map_asset_id": stored["asset_id"],
                    "map_width": stored["width"],
                    "map_height": stored["height"],
                    "nodes": topology["nodes"],
                    "segments": topology["segments"],
                    "cameras": topology["cameras"],
                },
            )
        finally:
            await file.close()
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @router.get("/scenes")
    def scenes(
        scene_type: str | None = None,
        topology_id: str | None = None,
        camera_id: str | None = None,
    ) -> list[dict]:
        if scene_type not in {None, "no_parking", "road_abnormal"}:
            raise ConfigurationError("SCENE_TYPE_INVALID", "未知场景类型")
        return service.list_scenes(
            scene_type=scene_type, topology_id=topology_id, camera_id=camera_id
        )

    @router.post("/scenes/{scene_id}/activate")
    def activate_scene(scene_id: str) -> dict:
        return coordinator.activate_scene(scene_id)

    @router.post("/scene-types/{scene_type}/deactivate")
    def deactivate_scene(scene_type: str) -> dict:
        return coordinator.deactivate_scene(scene_type)

    @router.get("/settings/detection")
    def detection_settings() -> dict:
        return service.detection_settings()

    @router.put("/settings/detection")
    def update_detection_settings(payload: DetectionConfiguration) -> dict:
        return service.update_detection_settings(payload.model_dump(exclude_none=True))

    if package_service is not None:

        @router.post("/exports")
        def export_configuration():
            return package_service.export_response()

        @router.post("/imports/preflight")
        async def import_preflight(file: UploadFile = File(...)) -> dict:
            return await package_service.preflight_upload(file)

        @router.post("/imports/{token}/apply")
        def import_apply(token: str, payload: ImportApplyRequest) -> dict:
            return package_service.apply(token, confirmed=payload.confirm)

    return router
