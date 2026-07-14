from __future__ import annotations

import json
from types import SimpleNamespace

from backend.device_monitor import DeviceMonitor


SCENE_KEYS = ("realtime", "traffic_map", "no_parking", "road_abnormal")


class FakeProcess:
    def cpu_percent(self, interval=None):
        assert interval is None
        return 12.5

    def memory_percent(self):
        return 3.25

    def memory_info(self):
        return SimpleNamespace(rss=512 * 1024 * 1024)


class FakePsutil:
    def cpu_percent(self, interval=None):
        assert interval is None
        return 42.5

    def cpu_count(self, logical=True):
        return 8 if logical else 4

    def virtual_memory(self):
        return SimpleNamespace(
            total=16 * 1024**3,
            available=6 * 1024**3,
            used=10 * 1024**3,
            percent=62.5,
        )

    def Process(self):
        return FakeProcess()


class FakeNvml:
    NVML_TEMPERATURE_GPU = 0

    def __init__(self):
        self.shutdown_calls = 0

    def nvmlInit(self):
        return None

    def nvmlShutdown(self):
        self.shutdown_calls += 1

    def nvmlDeviceGetCount(self):
        return 2

    def nvmlDeviceGetHandleByIndex(self, index):
        return index

    def nvmlDeviceGetName(self, handle):
        return ("GPU Zero" if handle == 0 else "GPU One").encode()

    def nvmlDeviceGetUtilizationRates(self, handle):
        return SimpleNamespace(gpu=55 + handle, memory=20 + handle)

    def nvmlDeviceGetMemoryInfo(self, handle):
        total = (8 + handle) * 1024**3
        used = (2 + handle) * 1024**3
        return SimpleNamespace(total=total, used=used, free=total - used)

    def nvmlDeviceGetTemperature(self, handle, sensor):
        assert sensor == self.NVML_TEMPERATURE_GPU
        return 61 + handle


class MissingNvml:
    def nvmlInit(self):
        raise RuntimeError("NVML library unavailable at C:/private/driver.dll")


def _stream_status(scene_key: str):
    return {
        "scene_key": scene_key,
        "running": True,
        "connected": scene_key == "realtime",
        "paused": False,
        "message": "connected to rtsp://admin:secret@example.test/live",
        "rtsp_url": "rtsp://admin:secret@example.test/live",
        "active_source": {
            "id": f"{scene_key}-source",
            "name": scene_key,
            "display_name": scene_key.replace("_", " "),
            "local": False,
            "url": "rtsp://admin:secret@example.test/live",
        },
        "resolution": {"width": 1920, "height": 1080},
        "fps": 24.8,
        "frame_sequence": 120,
        "detection": {
            "enabled": True,
            "preset": "legacy",
            "desired_revision": 2,
            "active_revision": 2,
            "status": "ready",
            "model_path": "C:/private/model.pt",
        },
        "results": [{"plate": "secret"}],
    }


def test_snapshot_reports_cpu_memory_process_multiple_gpus_and_sanitized_streams():
    nvml = FakeNvml()
    monitor = DeviceMonitor(
        psutil_provider=FakePsutil(),
        nvml_provider=nvml,
        stream_status_providers={
            scene_key: (lambda key=scene_key: _stream_status(key))
            for scene_key in SCENE_KEYS
        },
    )

    snapshot = monitor.snapshot()

    assert snapshot["cpu"] == {
        "available": True,
        "utilization_percent": 42.5,
        "physical_cores": 4,
        "logical_cores": 8,
    }
    assert snapshot["memory"] == {
        "available": True,
        "utilization_percent": 62.5,
        "used_bytes": 10 * 1024**3,
        "available_bytes": 6 * 1024**3,
        "total_bytes": 16 * 1024**3,
    }
    assert snapshot["process"] == {
        "available": True,
        "cpu_percent": 12.5,
        "memory_percent": 3.25,
        "rss_bytes": 512 * 1024 * 1024,
    }
    assert snapshot["gpu"]["available"] is True
    assert [item["name"] for item in snapshot["gpu"]["devices"]] == [
        "GPU Zero",
        "GPU One",
    ]
    assert snapshot["gpu"]["devices"][0] == {
        "index": 0,
        "name": "GPU Zero",
        "utilization_percent": 55.0,
        "memory_utilization_percent": 25.0,
        "memory_used_bytes": 2 * 1024**3,
        "memory_total_bytes": 8 * 1024**3,
        "temperature_c": 61.0,
    }
    assert nvml.shutdown_calls == 1

    assert [item["scene_key"] for item in snapshot["streams"]] == list(SCENE_KEYS)
    assert all(item["available"] for item in snapshot["streams"])
    serialized = json.dumps(snapshot, ensure_ascii=False).lower()
    assert "admin" not in serialized
    assert "secret" not in serialized
    assert "rtsp_url" not in serialized
    assert "model_path" not in serialized
    assert "results" not in serialized


def test_snapshot_degrades_nvml_and_individual_stream_failures():
    def failed_stream():
        raise RuntimeError("rtsp://admin:secret@example.test/private failed")

    monitor = DeviceMonitor(
        psutil_provider=FakePsutil(),
        nvml_provider=MissingNvml(),
        stream_status_providers={
            "realtime": failed_stream,
            "traffic_map": lambda: _stream_status("traffic_map"),
        },
    )

    snapshot = monitor.snapshot()

    assert snapshot["cpu"]["available"] is True
    assert snapshot["memory"]["available"] is True
    assert snapshot["process"]["available"] is True
    assert snapshot["gpu"] == {
        "available": False,
        "devices": [],
        "error": "NVML_UNAVAILABLE",
    }
    assert [item["scene_key"] for item in snapshot["streams"]] == list(SCENE_KEYS)
    assert snapshot["streams"][0] == {
        "scene_key": "realtime",
        "available": False,
        "error": "STREAM_STATUS_UNAVAILABLE",
    }
    assert snapshot["streams"][2] == {
        "scene_key": "no_parking",
        "available": False,
        "error": "STREAM_STATUS_UNAVAILABLE",
    }
    assert "secret" not in json.dumps(snapshot).lower()

