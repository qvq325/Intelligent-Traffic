"""Application paths and built-in video source configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


PROJECT_DIR = Path(__file__).resolve().parent.parent

STREAM_SOURCES = {
    "桥面": "rtsp://10.126.59.120:8554/live/live1",
    "停车场出口": "rtsp://10.126.59.120:8554/live/live2",
    "行人": "rtsp://10.126.59.120:8554/live/live3",
    "消防车识别": "rtsp://10.126.59.120:8554/live/live4",
    "桥出口": "rtsp://10.126.59.120:8554/live/live5",
    "桥入口": "rtsp://10.126.59.120:8554/live/live6",
    "道路2": "rtsp://10.126.59.120:8554/live/live7",
    "隧道(事故识别)": "rtsp://10.126.59.120:8554/live/live8",
    "隧道(车辆数量)": "rtsp://10.126.59.120:8554/live/live9",
    "道路3": "rtsp://10.126.59.120:8554/live/live10",
    "停车场入口": "rtsp://10.126.59.120:8554/live/live11",
    "道路1": "rtsp://10.126.59.120:8554/live/live12",
}

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass(frozen=True, slots=True)
class AppConfig:
    project_dir: Path
    frontend_dir: Path
    whitelist_file: Path
    traffic_map_file: Path
    fallback_map_image: Path
    upload_dir: Path
    map_upload_dir: Path
    stream_sources: Mapping[str, str]


def default_config() -> AppConfig:
    runtime_dir = PROJECT_DIR / "runtime"
    return AppConfig(
        project_dir=PROJECT_DIR,
        frontend_dir=PROJECT_DIR / "frontend",
        whitelist_file=PROJECT_DIR / "whitelist.json",
        traffic_map_file=PROJECT_DIR / "traffic_map.json",
        fallback_map_image=PROJECT_DIR / "sandpan" / "沙盘平面图2.png",
        upload_dir=runtime_dir / "uploads",
        map_upload_dir=runtime_dir / "maps",
        stream_sources=STREAM_SOURCES,
    )
