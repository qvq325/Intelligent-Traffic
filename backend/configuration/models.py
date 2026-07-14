"""Strict request and domain models for configuration management."""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class ConfigurationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StreamCreate(ConfigurationModel):
    name: str = Field(min_length=1, max_length=120)
    rtsp_url: str = Field(min_length=7, max_length=2048)
    enabled: bool = True

    @field_validator("rtsp_url")
    @classmethod
    def validate_rtsp_url(cls, value: str) -> str:
        if not value.lower().startswith("rtsp://"):
            raise ValueError("仅支持 RTSP 地址")
        return value


class StreamUpdate(ConfigurationModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    rtsp_url: str | None = Field(default=None, min_length=7, max_length=2048)
    enabled: bool | None = None

    @field_validator("rtsp_url")
    @classmethod
    def validate_rtsp_url(cls, value: str | None) -> str | None:
        if value is not None and not value.lower().startswith("rtsp://"):
            raise ValueError("仅支持 RTSP 地址")
        return value


MAX_STREAM_BATCH_SIZE = 500
StreamId = Annotated[str, Field(min_length=1, max_length=120)]


def _validate_strict_rtsp_url(value: str) -> str:
    if any(character.isspace() for character in value):
        raise ValueError("RTSP 地址不能包含空白字符")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("RTSP 地址无效") from exc
    if parsed.scheme.lower() != "rtsp" or not parsed.hostname:
        raise ValueError("仅支持包含主机名的 RTSP 地址")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("RTSP 端口无效")
    return value


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


class StreamBatchCreateItem(ConfigurationModel):
    name: str = Field(min_length=1, max_length=120)
    rtsp_url: str = Field(min_length=7, max_length=2048)
    enabled: bool = True

    @field_validator("rtsp_url")
    @classmethod
    def validate_rtsp_url(cls, value: str) -> str:
        return _validate_strict_rtsp_url(value)


class StreamBatchUpdateItem(StreamBatchCreateItem):
    stream_id: StreamId
    enabled: bool


class StreamBatchCreateRequest(ConfigurationModel):
    streams: Annotated[
        list[StreamBatchCreateItem],
        Field(min_length=1, max_length=MAX_STREAM_BATCH_SIZE),
    ]

    @model_validator(mode="after")
    def validate_unique_names(self):
        duplicates = _duplicates([item.name for item in self.streams])
        if duplicates:
            raise ValueError(f"批次内流名称重复: {', '.join(duplicates)}")
        return self


class StreamBatchUpdateRequest(ConfigurationModel):
    streams: Annotated[
        list[StreamBatchUpdateItem],
        Field(min_length=1, max_length=MAX_STREAM_BATCH_SIZE),
    ]

    @model_validator(mode="after")
    def validate_unique_items(self):
        duplicate_ids = _duplicates([item.stream_id for item in self.streams])
        duplicate_names = _duplicates([item.name for item in self.streams])
        if duplicate_ids or duplicate_names:
            reasons = []
            if duplicate_ids:
                reasons.append(f"stream_id 重复: {', '.join(duplicate_ids)}")
            if duplicate_names:
                reasons.append(f"流名称重复: {', '.join(duplicate_names)}")
            raise ValueError("；".join(reasons))
        return self


class StreamBatchDeleteRequest(ConfigurationModel):
    stream_ids: Annotated[
        list[StreamId],
        Field(min_length=1, max_length=MAX_STREAM_BATCH_SIZE),
    ]

    @model_validator(mode="after")
    def validate_unique_ids(self):
        duplicates = _duplicates(self.stream_ids)
        if duplicates:
            raise ValueError(f"stream_id 重复: {', '.join(duplicates)}")
        return self


class StreamProbeRequest(StreamBatchDeleteRequest):
    pass


class StreamBinding(ConfigurationModel):
    camera_id: str = Field(min_length=1, max_length=120)
    stream_id: str = Field(min_length=1, max_length=120)


class StreamProfileCreate(ConfigurationModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    bindings: list[StreamBinding] = Field(default_factory=list, max_length=100)


class StreamProfileUpdate(ConfigurationModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    bindings: list[StreamBinding] = Field(max_length=100)


class StreamProfileActivationRequest(ConfigurationModel):
    preflight_token: str | None = Field(default=None, min_length=1, max_length=256)


Point = tuple[float, float]


class TopologyNode(ConfigurationModel):
    node_id: str = Field(min_length=1, max_length=120)
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    node_type: Literal["endpoint", "intersection", "connector"] = "endpoint"


class TopologySegment(ConfigurationModel):
    segment_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    points: Annotated[list[Point], Field(min_length=2, max_length=200)]
    geometry_type: Literal["polyline", "polygon"] = "polyline"
    start_node_id: str = Field(min_length=1, max_length=120)
    end_node_id: str = Field(min_length=1, max_length=120)
    direction: str = Field(default="双向", min_length=1, max_length=30)
    level: Literal["ground", "bridge", "parking", "service"] = "ground"
    capacity: int = Field(default=4, ge=1, le=10000)
    road_width: float = Field(default=36.0 / 740.0, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_geometry(self):
        if self.geometry_type == "polygon" and len(self.points) < 3:
            raise ValueError("道路区域至少需要三个点")
        return self


class TopologyCamera(ConfigurationModel):
    camera_id: str = Field(min_length=1, max_length=120)
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    heading: float = Field(default=0.0, ge=0.0, le=360.0)
    view_range: float = Field(default=0.12, ge=0.01, le=0.5)
    segment_id: str = Field(default="", max_length=120)


class TopologyCreate(ConfigurationModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    map_asset_id: str | None = Field(default=None, max_length=120)
    map_width: int = Field(default=0, ge=0, le=10000)
    map_height: int = Field(default=0, ge=0, le=10000)
    nodes: list[TopologyNode] = Field(default_factory=list, max_length=1000)
    segments: list[TopologySegment] = Field(default_factory=list, max_length=1000)
    cameras: list[TopologyCamera] = Field(default_factory=list, max_length=100)


class TopologyUpdate(TopologyCreate):
    pass


SceneType = Literal["no_parking", "road_abnormal"]


class SceneActivation(ConfigurationModel):
    preflight: bool = True


class DetectionConfiguration(ConfigurationModel):
    enabled: bool | None = None
    yolo_threshold: float | None = Field(default=None, ge=0.05, le=1.0)
    lpr_threshold: float | None = Field(default=None, ge=0.05, le=1.0)
    interval: int | None = Field(default=None, ge=1, le=60)
    device_preference: str | None = Field(default=None, min_length=1, max_length=80)


ModelPipelineSceneKey = Literal[
    "realtime",
    "traffic_map",
    "no_parking",
    "road_abnormal",
]
MODEL_PIPELINE_SCENE_KEYS = (
    "realtime",
    "traffic_map",
    "no_parking",
    "road_abnormal",
)


class ModelPipelineConfiguration(ConfigurationModel):
    scene_key: ModelPipelineSceneKey
    preset: Literal["legacy", "trained"] = "legacy"
    enabled: bool
    device_preference: str = Field(min_length=1, max_length=80)
    yolo_threshold: float = Field(ge=0.05, le=1.0)
    lpr_threshold: float = Field(ge=0.05, le=1.0)
    frame_interval: int = Field(ge=1, le=60)
    inference_size: int = Field(default=640, ge=160, le=2048)
    parking_move_threshold: float = Field(default=0.03, gt=0.0, le=1.0)
    mog_history: int = Field(default=500, ge=10, le=5000)
    mog_variance_threshold: float = Field(default=25.0, ge=1.0, le=255.0)
    mog_min_area: int = Field(default=150, ge=1, le=1_000_000)
    mog_min_duration: float = Field(default=2.0, ge=0.1, le=300.0)
    mog_max_duration: float = Field(default=5.0, ge=0.1, le=3600.0)
    mog_warmup_frames: int = Field(default=50, ge=0, le=5000)

    @model_validator(mode="after")
    def validate_mog_duration_range(self):
        if self.mog_max_duration < self.mog_min_duration:
            raise ValueError(
                "mog_max_duration must be greater than or equal to mog_min_duration"
            )
        return self


class ModelPipelineBatchUpdate(ConfigurationModel):
    settings: Annotated[
        list[ModelPipelineConfiguration],
        Field(min_length=4, max_length=4),
    ]

    @model_validator(mode="after")
    def validate_scene_keys(self):
        scene_keys = [setting.scene_key for setting in self.settings]
        duplicates = _duplicates(scene_keys)
        if duplicates:
            raise ValueError(f"duplicate scene_key values: {', '.join(duplicates)}")
        missing = sorted(set(MODEL_PIPELINE_SCENE_KEYS) - set(scene_keys))
        if missing:
            raise ValueError(f"missing scene_key values: {', '.join(missing)}")
        return self


class ImportApplyRequest(ConfigurationModel):
    confirm: Literal[True]
