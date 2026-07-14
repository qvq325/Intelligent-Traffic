"""Trusted model-pipeline preset resolution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from .configuration.errors import ConfigurationError


Inspector = Callable[[Path], Any]
DeviceProvider = Callable[[], Sequence[Any]]
_WeightSignature = tuple[Path, int, int, int, int, int]
_INSPECTION_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class ModelPipelineOptions:
    scene_key: str
    preset: str
    enabled: bool
    device_preference: str
    yolo_threshold: float
    lpr_threshold: float
    frame_interval: int
    inference_size: int
    parking_move_threshold: float
    mog_history: int
    mog_variance_threshold: float
    mog_min_area: int
    mog_min_duration: float
    mog_max_duration: float
    mog_warmup_frames: int
    revision: int
    vehicle_model_path: Path
    plate_model_path: Path | None
    plate_mode: str
    no_parking_mode: str
    road_abnormal_mode: str


@dataclass(frozen=True, slots=True)
class _PresetDefinition:
    preset_id: str
    label: str
    vehicle_weight: tuple[str, ...]
    plate_weight: tuple[str, ...] | None
    capabilities: tuple[tuple[str, str], ...]
    plate_mode: str
    no_parking_mode: str
    road_abnormal_mode: str


_PRESETS = (
    _PresetDefinition(
        preset_id="legacy",
        label="Legacy",
        vehicle_weight=("yolo11m.pt",),
        plate_weight=None,
        capabilities=(
            ("vehicle_detection", "legacy"),
            ("plate_recognition", "pose"),
            ("no_parking", "dwell"),
            ("road_abnormal", "mog2"),
        ),
        plate_mode="pose",
        no_parking_mode="dwell",
        road_abnormal_mode="mog2",
    ),
    _PresetDefinition(
        preset_id="trained",
        label="Trained",
        vehicle_weight=("\u8bad\u7ec3\u540e\u6a21\u578b", "yolo26x.pt"),
        plate_weight=("\u8bad\u7ec3\u540e\u6a21\u578b", "license_plate_best.pt"),
        capabilities=(
            ("vehicle_detection", "trained"),
            ("plate_recognition", "box"),
            ("no_parking", "stationary"),
            ("road_abnormal", "mog"),
        ),
        plate_mode="box",
        no_parking_mode="stationary",
        road_abnormal_mode="mog",
    ),
)
_PRESET_BY_ID = {preset.preset_id: preset for preset in _PRESETS}


def _default_inspector(path: Path) -> dict[str, str | None]:
    from ultralytics import YOLO

    model = YOLO(str(path))
    return {"task": getattr(model, "task", None)}


def _default_device_provider() -> Sequence[Any]:
    try:
        from detection_processor import DetectionProcessor

        return DetectionProcessor.get_available_devices()
    except (ImportError, RuntimeError):
        return [("cpu", "CPU")]


class ModelPipelineRegistry:
    """Resolve immutable runtime options from project-owned preset assets."""

    def __init__(
        self,
        project_root: Path | None = None,
        *,
        inspector: Inspector | None = None,
        device_provider: DeviceProvider | None = None,
    ) -> None:
        default_root = Path(__file__).resolve().parent.parent
        self.project_root = Path(project_root or default_root).resolve()
        self._inspector = inspector or _default_inspector
        self._device_provider = device_provider or _default_device_provider
        self._cache_lock = RLock()
        self._task_cache: dict[_WeightSignature, str | None] = {}

    def invalidate_cache(self, path: Path | None = None) -> None:
        """Drop cached inspections after externally managed weight updates.

        In-place changes that preserve every stat field are outside this
        registry's no-hash detection model. Call this method, or recreate the
        registry, after such an update.
        """
        resolved_path = Path(path).resolve() if path is not None else None
        with self._cache_lock:
            if resolved_path is None:
                self._task_cache.clear()
                return
            for cache_key in [
                key for key in self._task_cache if key[0] == resolved_path
            ]:
                del self._task_cache[cache_key]

    def list_presets(self) -> list[dict[str, Any]]:
        payload = []
        for preset in _PRESETS:
            try:
                self._resolve_preset_paths(preset, scene_key=None)
                available = True
            except ConfigurationError:
                available = False
            payload.append(
                {
                    "id": preset.preset_id,
                    "label": preset.label,
                    "available": available,
                    "capabilities": dict(preset.capabilities),
                }
            )
        return payload

    def list_devices(self) -> list[dict[str, Any]]:
        devices: list[dict[str, Any]] = [
            {"id": "cpu", "label": "CPU", "available": True}
        ]
        seen = {"cpu"}
        try:
            provided_devices = self._device_provider()
            for item in provided_devices:
                try:
                    device_id, label = self._normalize_device(item)
                except Exception:
                    continue
                if not device_id or device_id in seen:
                    continue
                seen.add(device_id)
                devices.append(
                    {"id": device_id, "label": label or device_id, "available": True}
                )
        except Exception:
            return devices
        return devices

    def resolve(self, setting: Mapping[str, Any]) -> ModelPipelineOptions:
        scene_key = str(setting.get("scene_key", ""))
        preset_id = str(setting.get("preset", ""))
        preset = _PRESET_BY_ID.get(preset_id)
        if preset is None:
            raise self._unavailable(scene_key, preset_id, "preset_unknown")

        device = str(setting.get("device_preference", ""))
        if device not in {item["id"] for item in self.list_devices()}:
            raise self._unavailable(
                scene_key,
                preset_id,
                "device_unavailable",
                device=device,
            )

        vehicle_path, plate_path = self._resolve_preset_paths(
            preset,
            scene_key=scene_key,
        )
        return ModelPipelineOptions(
            scene_key=scene_key,
            preset=preset_id,
            enabled=bool(setting.get("enabled", False)),
            device_preference=device,
            yolo_threshold=float(setting.get("yolo_threshold", 0.5)),
            lpr_threshold=float(setting.get("lpr_threshold", 0.7)),
            frame_interval=int(setting.get("frame_interval", 5)),
            inference_size=int(setting.get("inference_size", 640)),
            parking_move_threshold=float(setting.get("parking_move_threshold", 0.03)),
            mog_history=int(setting.get("mog_history", 500)),
            mog_variance_threshold=float(
                setting.get("mog_variance_threshold", 25.0)
            ),
            mog_min_area=int(setting.get("mog_min_area", 150)),
            mog_min_duration=float(setting.get("mog_min_duration", 2.0)),
            mog_max_duration=float(setting.get("mog_max_duration", 5.0)),
            mog_warmup_frames=int(setting.get("mog_warmup_frames", 50)),
            revision=int(setting.get("revision", 1)),
            vehicle_model_path=vehicle_path,
            plate_model_path=plate_path,
            plate_mode=preset.plate_mode,
            no_parking_mode=preset.no_parking_mode,
            road_abnormal_mode=preset.road_abnormal_mode,
        )

    def _resolve_preset_paths(
        self,
        preset: _PresetDefinition,
        *,
        scene_key: str | None,
    ) -> tuple[Path, Path | None]:
        vehicle = self._trusted_weight(
            preset.vehicle_weight,
            preset_id=preset.preset_id,
            scene_key=scene_key,
            role="vehicle",
        )
        plate = None
        if preset.plate_weight is not None:
            plate = self._trusted_weight(
                preset.plate_weight,
                preset_id=preset.preset_id,
                scene_key=scene_key,
                role="plate",
            )
        return vehicle, plate

    def _trusted_weight(
        self,
        relative_parts: tuple[str, ...],
        *,
        preset_id: str,
        scene_key: str | None,
        role: str,
    ) -> Path:
        path = self.project_root.joinpath(*relative_parts).resolve()
        try:
            path.relative_to(self.project_root)
        except ValueError as exc:
            raise self._unavailable(
                scene_key,
                preset_id,
                "outside_project_root",
                asset=role,
            ) from exc
        if not path.is_file():
            raise self._unavailable(
                scene_key,
                preset_id,
                "weight_missing",
                asset=role,
            )

        with self._cache_lock:
            self._validate_weight_task(
                path,
                preset_id=preset_id,
                scene_key=scene_key,
                role=role,
            )
        return path

    def _validate_weight_task(
        self,
        path: Path,
        *,
        preset_id: str,
        scene_key: str | None,
        role: str,
    ) -> None:
        task: str | None
        for _attempt in range(_INSPECTION_ATTEMPTS):
            try:
                before = self._weight_signature(path)
            except OSError as exc:
                raise self._unavailable(
                    scene_key,
                    preset_id,
                    "weight_missing",
                    asset=role,
                ) from exc

            if before in self._task_cache:
                task = self._task_cache[before]
                try:
                    after = self._weight_signature(path)
                except OSError:
                    continue
                if after == before:
                    break
                continue

            self.invalidate_cache(path)
            try:
                inspected = self._inspector(path)
            except Exception as exc:
                try:
                    after = self._weight_signature(path)
                except OSError:
                    continue
                if after != before:
                    continue
                raise self._unavailable(
                    scene_key,
                    preset_id,
                    "inspection_failed",
                    asset=role,
                ) from exc

            try:
                after = self._weight_signature(path)
            except OSError:
                continue
            if after != before:
                continue
            if isinstance(inspected, Mapping):
                inspected_task = inspected.get("task")
            else:
                inspected_task = getattr(inspected, "task", None)
            task = str(inspected_task) if inspected_task is not None else None
            self._task_cache[before] = task
            break
        else:
            raise self._unavailable(
                scene_key,
                preset_id,
                "inspection_unstable",
                asset=role,
            )

        if task != "detect":
            raise self._unavailable(
                scene_key,
                preset_id,
                "task_mismatch",
                asset=role,
            )

    @staticmethod
    def _weight_signature(path: Path) -> _WeightSignature:
        stat = path.stat()
        return (
            path,
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )

    @staticmethod
    def _normalize_device(item: Any) -> tuple[str, str]:
        if isinstance(item, Mapping):
            raw_device_id = item.get("id")
            device_id = "" if raw_device_id is None else str(raw_device_id).strip()
            raw_label = item.get("label") or item.get("name") or device_id
            label = "" if raw_label is None else str(raw_label).strip()
            return device_id, label
        if isinstance(item, str):
            return item.strip(), item.strip()
        try:
            raw_device_id, raw_label = item
        except (TypeError, ValueError):
            return "", ""
        device_id = "" if raw_device_id is None else str(raw_device_id).strip()
        label = "" if raw_label is None else str(raw_label).strip()
        return device_id, label

    @staticmethod
    def _unavailable(
        scene_key: str | None,
        preset_id: str,
        reason: str,
        **details: str,
    ) -> ConfigurationError:
        detail = {"preset": preset_id, "reason": reason, **details}
        if scene_key:
            detail["scene_key"] = scene_key
        return ConfigurationError(
            "MODEL_PIPELINE_UNAVAILABLE",
            "The requested model pipeline is unavailable",
            details=[detail],
        )


__all__ = ["ModelPipelineOptions", "ModelPipelineRegistry"]
