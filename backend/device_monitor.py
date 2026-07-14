"""Read-only host, process, GPU, and stream health snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Mapping

import psutil

from .configuration.security import redact_text


SCENE_KEYS = ("realtime", "traffic_map", "no_parking", "road_abnormal")
_DEFAULT_NVML = object()


class DeviceMonitor:
    """Collect independent metric sections without exposing runtime secrets."""

    def __init__(
        self,
        *,
        psutil_provider=psutil,
        nvml_provider=_DEFAULT_NVML,
        stream_status_providers: Mapping[str, Callable[[], dict]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._psutil = psutil_provider
        self._nvml = self._load_nvml() if nvml_provider is _DEFAULT_NVML else nvml_provider
        self._stream_status_providers = dict(stream_status_providers or {})
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        try:
            self._process = self._psutil.Process()
        except Exception:
            self._process = None

    @staticmethod
    def _load_nvml():
        try:
            import pynvml
        except ImportError:
            return None
        return pynvml

    def snapshot(self) -> dict:
        return {
            "collected_at": self._clock().astimezone(timezone.utc).isoformat(),
            "cpu": self._cpu_snapshot(),
            "memory": self._memory_snapshot(),
            "process": self._process_snapshot(),
            "gpu": self._gpu_snapshot(),
            "streams": self._stream_snapshots(),
        }

    def _cpu_snapshot(self) -> dict:
        try:
            return {
                "available": True,
                "utilization_percent": _number(
                    self._psutil.cpu_percent(interval=None)
                ),
                "physical_cores": self._psutil.cpu_count(logical=False),
                "logical_cores": self._psutil.cpu_count(logical=True),
            }
        except Exception:
            return {"available": False, "error": "CPU_METRICS_UNAVAILABLE"}

    def _memory_snapshot(self) -> dict:
        try:
            memory = self._psutil.virtual_memory()
            return {
                "available": True,
                "utilization_percent": _number(memory.percent),
                "used_bytes": int(memory.used),
                "available_bytes": int(memory.available),
                "total_bytes": int(memory.total),
            }
        except Exception:
            return {"available": False, "error": "MEMORY_METRICS_UNAVAILABLE"}

    def _process_snapshot(self) -> dict:
        if self._process is None:
            return {"available": False, "error": "PROCESS_METRICS_UNAVAILABLE"}
        try:
            memory = self._process.memory_info()
            return {
                "available": True,
                "cpu_percent": _number(self._process.cpu_percent(interval=None)),
                "memory_percent": _number(self._process.memory_percent(), digits=2),
                "rss_bytes": int(memory.rss),
            }
        except Exception:
            return {"available": False, "error": "PROCESS_METRICS_UNAVAILABLE"}

    def _gpu_snapshot(self) -> dict:
        if self._nvml is None:
            return {
                "available": False,
                "devices": [],
                "error": "NVML_UNAVAILABLE",
            }

        initialized = False
        try:
            self._nvml.nvmlInit()
            initialized = True
            devices = [
                self._gpu_device(index)
                for index in range(int(self._nvml.nvmlDeviceGetCount()))
            ]
            return {"available": True, "devices": devices, "error": None}
        except Exception:
            return {
                "available": False,
                "devices": [],
                "error": "NVML_UNAVAILABLE",
            }
        finally:
            if initialized:
                try:
                    self._nvml.nvmlShutdown()
                except Exception:
                    pass

    def _gpu_device(self, index: int) -> dict:
        handle = self._nvml.nvmlDeviceGetHandleByIndex(index)
        name = self._nvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")
        utilization = self._nvml.nvmlDeviceGetUtilizationRates(handle)
        memory = self._nvml.nvmlDeviceGetMemoryInfo(handle)
        total = int(memory.total)
        used = int(memory.used)
        temperature = _optional_number(
            lambda: self._nvml.nvmlDeviceGetTemperature(
                handle,
                self._nvml.NVML_TEMPERATURE_GPU,
            )
        )
        return {
            "index": index,
            "name": str(name)[:200],
            "utilization_percent": _number(utilization.gpu),
            "memory_utilization_percent": _number(
                (used / total * 100.0) if total else 0.0
            ),
            "memory_used_bytes": used,
            "memory_total_bytes": total,
            "temperature_c": temperature,
        }

    def _stream_snapshots(self) -> list[dict]:
        snapshots = []
        for scene_key in SCENE_KEYS:
            provider = self._stream_status_providers.get(scene_key)
            if provider is None:
                snapshots.append(_unavailable_stream(scene_key))
                continue
            try:
                status = provider()
                if not isinstance(status, dict):
                    raise TypeError("stream status must be a mapping")
                snapshots.append(_sanitize_stream(scene_key, status))
            except Exception:
                snapshots.append(_unavailable_stream(scene_key))
        return snapshots


def _sanitize_stream(scene_key: str, status: dict) -> dict:
    source = status.get("active_source")
    detection = status.get("detection")
    return {
        "scene_key": scene_key,
        "available": True,
        "running": bool(status.get("running")),
        "connected": bool(status.get("connected")),
        "paused": bool(status.get("paused")),
        "message": _safe_text(status.get("message")),
        "active_source": (
            {
                "id": _safe_text(source.get("id"), limit=120),
                "name": _safe_text(source.get("name"), limit=120),
                "display_name": _safe_text(
                    source.get("display_name"), limit=200
                ),
                "local": bool(source.get("local")),
            }
            if isinstance(source, dict)
            else None
        ),
        "resolution": _sanitize_resolution(status.get("resolution")),
        "fps": _optional_number(lambda: status.get("fps")),
        "frame_sequence": _optional_integer(status.get("frame_sequence")),
        "detection": (
            {
                "enabled": bool(detection.get("enabled")),
                "preset": _safe_text(detection.get("preset"), limit=40),
                "desired_revision": _optional_integer(
                    detection.get("desired_revision")
                ),
                "active_revision": _optional_integer(
                    detection.get("active_revision")
                ),
                "status": _safe_text(detection.get("status")),
            }
            if isinstance(detection, dict)
            else None
        ),
    }


def _sanitize_resolution(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    width = _optional_integer(value.get("width"))
    height = _optional_integer(value.get("height"))
    if width is None or height is None:
        return None
    return {"width": width, "height": height}


def _unavailable_stream(scene_key: str) -> dict:
    return {
        "scene_key": scene_key,
        "available": False,
        "error": "STREAM_STATUS_UNAVAILABLE",
    }


def _safe_text(value, *, limit: int = 500) -> str:
    return redact_text(str(value or ""))[:limit]


def _number(value, *, digits: int = 1) -> float:
    return round(float(value), digits)


def _optional_number(factory: Callable[[], object]) -> float | None:
    try:
        value = factory()
        return None if value is None else _number(value)
    except Exception:
        return None


def _optional_integer(value) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError, OverflowError):
        return None

