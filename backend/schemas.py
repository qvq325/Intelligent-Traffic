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
