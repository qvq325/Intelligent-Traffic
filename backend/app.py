"""FastAPI application and HTTP routes."""

from __future__ import annotations

import re
import shutil
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .config import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, AppConfig, default_config
from .preview_stream import MultiCameraPreviewService
from .schemas import (
    CameraUpdate,
    DetectionSettingsUpdate,
    NoParkingReferenceRequest,
    NoParkingScenePayload,
    NoParkingStartRequest,
    PauseRequest,
    RoadAbnormalReferenceRequest,
    RoadAbnormalScenePayload,
    RoadAbnormalStartRequest,
    SegmentPayload,
    SourceSelection,
    WhitelistEnabledUpdate,
    WhitelistInput,
)
from .state import ApplicationState


def _safe_filename(filename: str, fallback: str) -> str:
    name = Path(filename or fallback).name
    name = re.sub(r"[^\w.()-]+", "_", name, flags=re.UNICODE).strip("._")
    return (name or fallback)[-120:]


def _copy_upload(source, destination: Path, max_bytes: int) -> None:
    written = 0
    with destination.open("wb") as target:
        while chunk := source.read(1024 * 1024):
            written += len(chunk)
            if written > max_bytes:
                raise ValueError("上传文件超过大小限制")
            target.write(chunk)


async def _store_upload(
    upload: UploadFile,
    directory: Path,
    allowed_extensions: set[str],
    max_bytes: int,
) -> Path:
    original_name = _safe_filename(upload.filename or "upload", "upload")
    extension = Path(original_name).suffix.lower()
    if extension not in allowed_extensions:
        allowed = ", ".join(sorted(allowed_extensions))
        raise HTTPException(status_code=415, detail=f"不支持的文件类型，可用类型: {allowed}")

    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{uuid4().hex}_{original_name}"
    try:
        await run_in_threadpool(_copy_upload, upload.file, destination, max_bytes)
    except ValueError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"文件保存失败: {exc}") from exc
    finally:
        await upload.close()
    return destination


def create_app(
    config: AppConfig | None = None,
    *,
    start_video: bool = True,
) -> FastAPI:
    config = config or default_config()
    runtime = ApplicationState(config)
    preview_stream = MultiCameraPreviewService(config.stream_sources)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_video:
            runtime.start()
            preview_stream.prewarm()
        yield
        preview_stream.stop()
        if start_video:
            runtime.shutdown()

    app = FastAPI(
        title="沙盘交通智控台 API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.state.preview_stream = preview_stream

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "service": "video-test", "version": app.version}

    @app.get("/api/system")
    def system_info() -> dict:
        return {
            "name": "沙盘交通智控台",
            "sources": runtime.source_catalog(),
            "devices": runtime.devices,
        }

    @app.get("/api/stream/status")
    def stream_status() -> dict:
        return runtime.video.status()

    @app.post("/api/stream/select")
    def select_stream(payload: SourceSelection) -> dict:
        url = config.stream_sources.get(payload.source_id)
        if url is None:
            raise HTTPException(status_code=404, detail="视频源不存在")
        preview_stream.cancel_prewarm()
        runtime.road_abnormal.stop()
        runtime.video.select_source(payload.source_id, payload.source_id, url)
        return runtime.video.status()

    @app.post("/api/stream/upload", status_code=status.HTTP_201_CREATED)
    async def upload_video(
        file: UploadFile = File(...),
        camera_id: str | None = Query(default=None, max_length=100),
    ) -> dict:
        if camera_id is not None and camera_id not in config.stream_sources:
            raise HTTPException(status_code=404, detail="关联摄像头不存在")
        path = await _store_upload(
            file,
            config.upload_dir,
            VIDEO_EXTENSIONS,
            max_bytes=4 * 1024 * 1024 * 1024,
        )
        source_id = camera_id or next(iter(config.stream_sources), "本地视频")
        preview_stream.cancel_prewarm()
        runtime.road_abnormal.stop()
        runtime.video.select_source(
            source_id,
            source_id,
            str(path),
            display_name=Path(file.filename or path.name).name,
        )
        return {"file": path.name, "stream": runtime.video.status()}

    @app.post("/api/stream/pause")
    def pause_stream(payload: PauseRequest) -> dict:
        runtime.video.set_paused(payload.paused)
        return runtime.video.status()

    @app.post("/api/stream/stop")
    def stop_stream() -> dict:
        runtime.video.stop_stream()
        return runtime.video.status()

    @app.put("/api/detection/settings")
    def update_detection(payload: DetectionSettingsUpdate) -> dict:
        values = payload.model_dump(exclude_none=True)
        if "device" in values and values["device"] not in {
            item["id"] for item in runtime.devices
        }:
            raise HTTPException(status_code=422, detail="推理设备不可用")
        if payload.enabled:
            runtime.road_abnormal.stop()
            runtime.map_analysis.update_detection_settings(enabled=False)
        runtime.video.update_detection_settings(**values)
        return runtime.video.status()["detection"]

    def map_analysis_status() -> dict:
        analysis = runtime.map_analysis.status()
        source = analysis.get("active_source")
        segment = None
        if source:
            with runtime.map_lock:
                camera = runtime.traffic_map.cameras.get(source["id"])
                road = (
                    runtime.traffic_map.segments.get(camera.segment_id)
                    if camera and camera.segment_id
                    else None
                )
                if road:
                    segment = {"id": road.segment_id, "name": road.name}
        analysis["segment"] = segment
        return analysis

    @app.get("/api/map/analysis/status")
    def get_map_analysis_status() -> dict:
        return map_analysis_status()

    @app.post("/api/map/analysis/select")
    def select_map_analysis(payload: SourceSelection) -> dict:
        url = config.stream_sources.get(payload.source_id)
        if url is None:
            raise HTTPException(status_code=404, detail="摄像头不存在")
        with runtime.map_lock:
            camera = runtime.traffic_map.cameras.get(payload.source_id)
            if not camera or camera.segment_id not in runtime.traffic_map.segments:
                raise HTTPException(status_code=422, detail="摄像头尚未绑定有效道路")
        runtime.video.update_detection_settings(enabled=False)
        runtime.map_analysis.select_source(payload.source_id, payload.source_id, url)
        runtime.map_analysis.update_detection_settings(enabled=True)
        return map_analysis_status()

    @app.post("/api/map/analysis/pause")
    def pause_map_analysis(payload: PauseRequest) -> dict:
        runtime.map_analysis.set_paused(payload.paused)
        return map_analysis_status()

    @app.post("/api/map/analysis/stop")
    def stop_map_analysis() -> dict:
        runtime.map_analysis.update_detection_settings(enabled=False)
        runtime.map_analysis.stop_stream()
        return map_analysis_status()

    @app.get("/api/video/feed")
    def video_feed() -> StreamingResponse:
        def frames():
            sequence = -1
            while True:
                sequence, frame = runtime.video.wait_for_frame(sequence)
                if frame is None:
                    continue
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                    + frame
                    + b"\r\n"
                )

        return StreamingResponse(
            frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/video/preview")
    def video_preview(
        source_id: str = Query(min_length=1, max_length=100),
    ) -> StreamingResponse:
        if not preview_stream.has_source(source_id):
            raise HTTPException(status_code=404, detail="视频源不存在")

        def frames():
            sequence = -1
            with preview_stream.subscribe(source_id):
                while True:
                    sequence, frame = preview_stream.wait_for_frame(source_id, sequence)
                    if frame is None:
                        continue
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                        + frame
                        + b"\r\n"
                    )

        return StreamingResponse(
            frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/video/preview/snapshot")
    def video_preview_snapshot(
        source_id: str = Query(min_length=1, max_length=100),
        cached_only: bool = Query(default=False),
    ) -> Response:
        if not preview_stream.has_source(source_id):
            raise HTTPException(status_code=404, detail="视频源不存在")
        frame = preview_stream.snapshot(source_id, cached_only=cached_only)
        if frame is None:
            if cached_only:
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            raise HTTPException(status_code=503, detail="摄像头暂时无可用画面")
        return Response(
            frame,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/video/snapshot")
    def video_snapshot() -> Response:
        frame = runtime.video.latest_frame()
        if frame is None:
            raise HTTPException(status_code=409, detail="当前没有可用视频帧")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return Response(
            frame,
            media_type="image/jpeg",
            headers={"Content-Disposition": f'attachment; filename="snapshot_{timestamp}.jpg"'},
        )

    @app.get("/api/no-parking")
    def get_no_parking() -> dict:
        return {
            **runtime.no_parking.catalog(),
            "status": runtime.no_parking.status(expire=True),
        }

    @app.get("/api/no-parking/status")
    def get_no_parking_status() -> dict:
        return runtime.no_parking.status(expire=True)

    @app.post("/api/no-parking/reference", status_code=status.HTTP_201_CREATED)
    def capture_no_parking_reference(payload: NoParkingReferenceRequest) -> dict:
        stream = runtime.video.status()
        source = stream.get("active_source")
        if source is None or source["id"] != payload.camera_id:
            raise HTTPException(status_code=409, detail="请先连接所选摄像头或关联本地视频")
        frame = runtime.video.latest_frame()
        resolution = stream.get("resolution")
        if frame is None or resolution is None:
            raise HTTPException(status_code=409, detail="当前没有可用于标定的视频帧")
        return runtime.no_parking.capture_reference(
            frame,
            payload.camera_id,
            resolution["width"],
            resolution["height"],
        )

    @app.get("/api/no-parking/references/{filename}")
    def get_no_parking_reference(filename: str) -> FileResponse:
        path = runtime.no_parking.reference_path(filename)
        if path is None:
            raise HTTPException(status_code=404, detail="参考帧不存在")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/no-parking/scenes", status_code=status.HTTP_201_CREATED)
    def upsert_no_parking_scene(payload: NoParkingScenePayload) -> dict:
        if payload.camera_id not in config.stream_sources:
            raise HTTPException(status_code=404, detail="关联摄像头不存在")
        try:
            return runtime.no_parking.upsert_scene(payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/no-parking/scenes/{scene_id}")
    def delete_no_parking_scene(scene_id: str) -> dict:
        if not runtime.no_parking.delete_scene(scene_id):
            raise HTTPException(status_code=404, detail="禁停场景不存在")
        return {"deleted": True, "scene_id": scene_id}

    @app.post("/api/no-parking/start")
    def start_no_parking(payload: NoParkingStartRequest) -> dict:
        scene = runtime.no_parking.get_scene(payload.scene_id)
        if scene is None:
            raise HTTPException(status_code=404, detail="禁停场景不存在")
        stream = runtime.video.status()
        source = stream.get("active_source")
        if source is None or source["id"] != scene["camera_id"]:
            raise HTTPException(status_code=409, detail="当前视频源与禁停场景不匹配")
        runtime.road_abnormal.stop()
        runtime.map_analysis.update_detection_settings(enabled=False)
        if source["local"] and not stream["connected"]:
            runtime.video.restart_source()
        else:
            runtime.video.set_paused(False)
        runtime.video.update_detection_settings(enabled=True)
        return runtime.no_parking.start(payload.scene_id)

    @app.post("/api/no-parking/stop")
    def stop_no_parking() -> dict:
        return runtime.no_parking.stop()

    @app.delete("/api/no-parking/events")
    def clear_no_parking_events() -> dict:
        return runtime.no_parking.clear_events()

    @app.get("/api/road-abnormal")
    def get_road_abnormal() -> dict:
        return {
            **runtime.road_abnormal.catalog(),
            "status": runtime.road_abnormal.status(expire=True),
        }

    @app.get("/api/road-abnormal/status")
    def get_road_abnormal_status() -> dict:
        return runtime.road_abnormal.status(expire=True)

    @app.post("/api/road-abnormal/reference", status_code=status.HTTP_201_CREATED)
    def capture_road_abnormal_reference(
        payload: RoadAbnormalReferenceRequest,
    ) -> dict:
        stream = runtime.video.status()
        source = stream.get("active_source")
        if source is None or source["id"] != payload.camera_id:
            raise HTTPException(status_code=409, detail="请先连接所选摄像头或关联本地视频")
        frame = runtime.video.latest_frame()
        resolution = stream.get("resolution")
        if frame is None or resolution is None:
            raise HTTPException(status_code=409, detail="当前没有可用于标定的视频帧")
        return runtime.road_abnormal.capture_reference(
            frame,
            payload.camera_id,
            resolution["width"],
            resolution["height"],
        )

    @app.get("/api/road-abnormal/references/{filename}")
    def get_road_abnormal_reference(filename: str) -> FileResponse:
        path = runtime.road_abnormal.reference_path(filename)
        if path is None:
            raise HTTPException(status_code=404, detail="参考帧不存在")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/road-abnormal/snapshots/{filename}")
    def get_road_abnormal_snapshot(filename: str) -> FileResponse:
        path = runtime.road_abnormal.snapshot_path(filename)
        if path is None:
            raise HTTPException(status_code=404, detail="异常快照不存在")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/road-abnormal/scenes", status_code=status.HTTP_201_CREATED)
    def upsert_road_abnormal_scene(payload: RoadAbnormalScenePayload) -> dict:
        if payload.camera_id not in config.stream_sources:
            raise HTTPException(status_code=404, detail="关联摄像头不存在")
        values = payload.model_dump()
        if not values["reference_image"]:
            stream = runtime.video.status()
            source = stream.get("active_source")
            if source is None or source["id"] != payload.camera_id:
                raise HTTPException(
                    status_code=409,
                    detail="请先连接所选摄像头或关联本地视频",
                )
            frame = runtime.video.latest_frame()
            resolution = stream.get("resolution")
            if frame is None or resolution is None:
                raise HTTPException(status_code=409, detail="当前没有可用于标定的视频帧")
            reference = runtime.road_abnormal.capture_reference(
                frame,
                payload.camera_id,
                resolution["width"],
                resolution["height"],
            )
            values.update(
                reference_image=reference["filename"],
                reference_width=reference["width"],
                reference_height=reference["height"],
            )
        try:
            return runtime.road_abnormal.upsert_scene(values)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/road-abnormal/scenes/{scene_id}")
    def delete_road_abnormal_scene(scene_id: str) -> dict:
        if not runtime.road_abnormal.delete_scene(scene_id):
            raise HTTPException(status_code=404, detail="道路异常场景不存在")
        return {"deleted": True, "scene_id": scene_id}

    @app.post("/api/road-abnormal/start")
    def start_road_abnormal(payload: RoadAbnormalStartRequest) -> dict:
        scene = runtime.road_abnormal.get_scene(payload.scene_id)
        if scene is None:
            raise HTTPException(status_code=404, detail="道路异常场景不存在")
        stream = runtime.video.status()
        source = stream.get("active_source")
        if source is None or source["id"] != scene["camera_id"]:
            raise HTTPException(status_code=409, detail="当前视频源与道路异常场景不匹配")
        runtime.no_parking.stop()
        runtime.map_analysis.update_detection_settings(enabled=False)
        runtime.video.update_detection_settings(enabled=False)
        if source["local"] and not stream["connected"]:
            runtime.video.restart_source()
        else:
            runtime.video.set_paused(False)
        return runtime.road_abnormal.start(payload.scene_id)

    @app.post("/api/road-abnormal/stop")
    def stop_road_abnormal() -> dict:
        return runtime.road_abnormal.stop()

    @app.delete("/api/road-abnormal/events")
    def clear_road_abnormal_events() -> dict:
        return runtime.road_abnormal.clear_events()

    @app.get("/api/whitelist")
    def get_whitelist() -> dict:
        return {
            "enabled": runtime.whitelist.enabled,
            "count": runtime.whitelist.count,
            "entries": [asdict(entry) for entry in runtime.whitelist.get_all()],
        }

    @app.post("/api/whitelist", status_code=status.HTTP_201_CREATED)
    def upsert_whitelist(payload: WhitelistInput) -> dict:
        existed = runtime.whitelist.get(payload.plate) is not None
        runtime.whitelist.add(payload.plate, payload.note)
        runtime.save_whitelist()
        entry = runtime.whitelist.get(payload.plate)
        return {"created": not existed, "entry": asdict(entry) if entry else None}

    @app.delete("/api/whitelist/{plate}")
    def delete_whitelist(plate: str) -> dict:
        if not runtime.whitelist.remove(plate):
            raise HTTPException(status_code=404, detail="白名单条目不存在")
        runtime.save_whitelist()
        return {"deleted": True, "count": runtime.whitelist.count}

    @app.delete("/api/whitelist")
    def clear_whitelist() -> dict:
        runtime.whitelist.clear()
        runtime.save_whitelist()
        return {"deleted": True, "count": 0}

    @app.patch("/api/whitelist/enabled")
    def set_whitelist_enabled(payload: WhitelistEnabledUpdate) -> dict:
        runtime.whitelist.enabled = payload.enabled
        return {"enabled": runtime.whitelist.enabled}

    @app.get("/api/map")
    def get_map() -> dict:
        return runtime.map_snapshot()

    @app.get("/api/map/image")
    def get_map_image() -> FileResponse:
        path = runtime.map_image_path()
        if not path.is_file():
            raise HTTPException(status_code=404, detail="地图底图不存在")
        return FileResponse(path)

    @app.post("/api/map/image", status_code=status.HTTP_201_CREATED)
    async def upload_map_image(file: UploadFile = File(...)) -> dict:
        path = await _store_upload(
            file,
            config.map_upload_dir,
            IMAGE_EXTENSIONS,
            max_bytes=30 * 1024 * 1024,
        )
        runtime.set_map_image(path)
        return {"image_url": runtime.map_snapshot()["image_url"]}

    @app.put("/api/map/cameras/{camera_id}")
    def update_camera(camera_id: str, payload: CameraUpdate) -> dict:
        with runtime.map_lock:
            if camera_id not in runtime.traffic_map.cameras:
                raise HTTPException(status_code=404, detail="摄像头不存在")
            if payload.segment_id and payload.segment_id not in runtime.traffic_map.segments:
                raise HTTPException(status_code=422, detail="关联道路不存在")
            camera = runtime.traffic_map.set_camera(camera_id=camera_id, **payload.model_dump())
            runtime.traffic_map.save()
            return asdict(camera)

    def save_segment(payload: SegmentPayload, segment_id: str = "") -> dict:
        with runtime.map_lock:
            values = payload.model_dump()
            values["segment_id"] = segment_id or values["segment_id"]
            try:
                segment = runtime.traffic_map.upsert_segment(**values)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            runtime.traffic_map.save()
            return asdict(segment)

    @app.post("/api/map/segments", status_code=status.HTTP_201_CREATED)
    def create_segment(payload: SegmentPayload) -> dict:
        return save_segment(payload)

    @app.put("/api/map/segments/{segment_id}")
    def update_segment(segment_id: str, payload: SegmentPayload) -> dict:
        with runtime.map_lock:
            if segment_id not in runtime.traffic_map.segments:
                raise HTTPException(status_code=404, detail="道路不存在")
        return save_segment(payload, segment_id)

    @app.delete("/api/map/segments/{segment_id}")
    def delete_segment(segment_id: str) -> dict:
        with runtime.map_lock:
            if segment_id not in runtime.traffic_map.segments:
                raise HTTPException(status_code=404, detail="道路不存在")
            if not runtime.traffic_map.delete_segment(segment_id):
                raise HTTPException(status_code=409, detail="至少需要保留一条道路")
            runtime.traffic_map.save()
            return {"deleted": True, "segment_id": segment_id}

    @app.post("/api/map/reset-runtime")
    def reset_map_runtime() -> dict:
        with runtime.map_lock:
            runtime.traffic_map.reset_runtime()
        return {"reset": True}

    app.mount("/static", StaticFiles(directory=config.frontend_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(
            config.frontend_dir / "index.html",
            headers={"Cache-Control": "no-store"},
        )

    return app


app = create_app()
