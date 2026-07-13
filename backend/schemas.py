"""Validated request models for the HTTP API."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceSelection(ApiModel):
    source_id: str = Field(min_length=1, max_length=100)


class PauseRequest(ApiModel):
    paused: bool


class DetectionSettingsUpdate(ApiModel):
    enabled: bool | None = None
    yolo_threshold: float | None = Field(default=None, ge=0.05, le=1.0)
    lpr_threshold: float | None = Field(default=None, ge=0.05, le=1.0)
    interval: int | None = Field(default=None, ge=1, le=60)
    device: str | None = Field(default=None, min_length=1, max_length=50)


class WhitelistInput(ApiModel):
    plate: str = Field(min_length=2, max_length=20)
    note: str = Field(default="", max_length=200)


class WhitelistEnabledUpdate(ApiModel):
    enabled: bool


class CameraUpdate(ApiModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    heading: float = Field(default=0.0, ge=0.0, le=360.0)
    view_range: float = Field(default=0.12, ge=0.01, le=0.5)
    segment_id: str = Field(default="", max_length=100)


Point = tuple[float, float]


class SegmentPayload(ApiModel):
    segment_id: str = Field(default="", max_length=100)
    name: str = Field(min_length=1, max_length=100)
    points: Annotated[list[Point], Field(min_length=2, max_length=200)]
    capacity: int = Field(default=4, ge=1, le=10000)
    level: Literal["ground", "bridge", "parking", "service"] = "ground"
    direction: str = Field(default="双向", min_length=1, max_length=30)
    geometry_type: Literal["polyline", "polygon"] = "polyline"
    road_width: float = Field(
        default=36.0 / 740.0,
        ge=4.0 / 740.0,
        le=120.0 / 740.0,
    )


class NoParkingReferenceRequest(ApiModel):
    camera_id: str = Field(min_length=1, max_length=100)


class NoParkingZonePayload(ApiModel):
    zone_id: str = Field(default="", max_length=80)
    name: str = Field(default="禁停区域", min_length=1, max_length=100)
    points: Annotated[list[Point], Field(min_length=3, max_length=100)]
    dwell_seconds: float = Field(default=10.0, ge=1.0, le=3600.0)
    lost_tolerance_seconds: float = Field(default=2.0, ge=0.1, le=30.0)
    enabled: bool = True
    vehicle_classes: list[Literal["car", "motorcycle", "bus", "truck"]] = Field(
        default_factory=lambda: ["car", "motorcycle", "bus", "truck"],
        min_length=1,
        max_length=4,
    )


class NoParkingScenePayload(ApiModel):
    scene_id: str = Field(default="", max_length=80)
    name: str = Field(min_length=1, max_length=100)
    camera_id: str = Field(min_length=1, max_length=100)
    reference_image: str = Field(min_length=5, max_length=120, pattern=r"^[A-Za-z0-9_-]+\.jpg$")
    reference_width: int = Field(ge=1, le=10000)
    reference_height: int = Field(ge=1, le=10000)
    zones: Annotated[list[NoParkingZonePayload], Field(min_length=1, max_length=20)]


class NoParkingStartRequest(ApiModel):
    scene_id: str = Field(min_length=1, max_length=80)


RoadObjectClass = Literal["person", "bicycle", "car", "motorcycle", "bus", "truck"]


class RoadAbnormalReferenceRequest(ApiModel):
    camera_id: str = Field(min_length=1, max_length=100)


class RoadAbnormalZonePayload(ApiModel):
    zone_id: str = Field(default="", max_length=80)
    name: str = Field(default="道路检测区域", min_length=1, max_length=100)
    lane_name: str = Field(default="机动车道", min_length=1, max_length=100)
    points: Annotated[list[Point], Field(min_length=3, max_length=100)]
    enabled: bool = True


class RoadAbnormalScenePayload(ApiModel):
    scene_id: str = Field(default="", max_length=80)
    name: str = Field(min_length=1, max_length=100)
    camera_id: str = Field(min_length=1, max_length=100)
    reference_image: str = Field(
        default="", max_length=120, pattern=r"^$|^[A-Za-z0-9_-]+\.jpg$"
    )
    reference_width: int = Field(default=0, ge=0, le=10000)
    reference_height: int = Field(default=0, ge=0, le=10000)
    zones: Annotated[list[RoadAbnormalZonePayload], Field(min_length=1, max_length=20)]
    persistence_seconds: float = Field(default=3.0, ge=0.1, le=300.0)
    lost_tolerance_seconds: float = Field(default=1.0, ge=0.1, le=30.0)
    min_area_ratio: float = Field(default=0.001, ge=0.00001, le=0.25)
    history: int = Field(default=500, ge=10, le=5000)
    variance_threshold: float = Field(default=25.0, ge=1.0, le=255.0)
    detect_shadows: bool = True
    warmup_frames: int = Field(default=30, ge=0, le=1000)
    learning_rate: float = Field(default=0.002, ge=-1.0, le=1.0)
    inference_interval: int = Field(default=5, ge=1, le=60)
    yolo_threshold: float = Field(default=0.45, ge=0.05, le=1.0)
    anomaly_classes: list[RoadObjectClass] = Field(
        default_factory=lambda: ["person", "bicycle", "motorcycle"],
        min_length=1,
        max_length=6,
    )
    normal_classes: list[RoadObjectClass] = Field(
        default_factory=lambda: ["car", "bus", "truck"],
        min_length=1,
        max_length=6,
    )


class RoadAbnormalStartRequest(ApiModel):
    scene_id: str = Field(min_length=1, max_length=80)
