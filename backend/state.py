"""Shared application state and persistence boundaries."""

from __future__ import annotations

import os
import threading
from dataclasses import asdict
from pathlib import Path

from detection_processor import DetectionProcessor, DetectionResult
from traffic_map import TrafficMapModel
from whitelist_manager import WhitelistManager

from .config import AppConfig
from .no_parking import NoParkingMonitor
from .road_abnormal import RoadAbnormalMonitor
from .video_stream import VideoStreamService


class ApplicationState:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.map_lock = threading.RLock()

        self.whitelist = WhitelistManager()
        if config.whitelist_file.is_file():
            self.whitelist.load(str(config.whitelist_file))

        self.traffic_map = TrafficMapModel(
            config.traffic_map_file,
            config.stream_sources.keys(),
        )
        if not config.traffic_map_file.is_file():
            self.traffic_map.save()

        self.devices = [
            {"id": device_id, "name": display_name}
            for device_id, display_name in DetectionProcessor.get_available_devices()
        ]
        default_device = self.devices[1]["id"] if len(self.devices) > 1 else self.devices[0]["id"]

        self.no_parking = NoParkingMonitor(config.upload_dir.parent / "no_parking")
        self.road_abnormal = RoadAbnormalMonitor(
            config.upload_dir.parent / "road_abnormal",
            config.project_dir / "yolo11m.pt",
            device=default_device,
        )
        self.video = VideoStreamService(
            self.whitelist,
            frame_processor=self.road_abnormal.process_frame,
        )
        self.video.add_detection_listener(self._handle_no_parking_detections)
        self.map_analysis = VideoStreamService(
            self.whitelist,
            on_detections=self._handle_detections,
        )
        self.video.update_detection_settings(device=default_device)
        self.map_analysis.update_detection_settings(device=default_device)

    def start(self) -> None:
        self.config.upload_dir.mkdir(parents=True, exist_ok=True)
        self.config.map_upload_dir.mkdir(parents=True, exist_ok=True)
        self.video.start()
        self.map_analysis.start()

    def shutdown(self) -> None:
        self.road_abnormal.stop()
        self.map_analysis.stop()
        self.video.stop()
        self.save_whitelist()
        with self.map_lock:
            self.traffic_map.save()

    def save_whitelist(self) -> bool:
        self.config.whitelist_file.parent.mkdir(parents=True, exist_ok=True)
        return self.whitelist.save(str(self.config.whitelist_file))

    def source_catalog(self) -> list[dict]:
        return [
            {"id": source_id, "name": source_id}
            for source_id in self.config.stream_sources
        ]

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
                path = self.config.traffic_map_file.parent / path
            if path.is_file():
                return path.resolve()
        return self.config.fallback_map_image.resolve()

    def set_map_image(self, path: Path) -> None:
        with self.map_lock:
            try:
                portable = os.path.relpath(path, self.config.traffic_map_file.parent)
            except ValueError:
                portable = str(path)
            self.traffic_map.map_image_path = portable
            self.traffic_map.save()

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
