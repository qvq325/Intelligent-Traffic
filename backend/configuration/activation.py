"""Application-level coordination between durable config and runtime channels."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from .errors import ConfigurationError
from .security import redact_text


STREAM_PROFILE_PREFLIGHT_TTL_SECONDS = 5 * 60


@dataclass(slots=True)
class _PendingStreamProfilePreflight:
    fingerprint: str
    expires_at: float
    results: list[dict]


class RuntimeConfigurationAdapter(Protocol):
    def current_stream_mapping(self) -> dict[str, str]: ...

    def apply_stream_mapping(self, mapping: dict[str, str]) -> dict: ...

    def apply_topology(self, topology: dict) -> dict: ...

    def activate_scene_runtime(self, scene: dict, stream_url: str) -> dict: ...

    def deactivate_scene_runtime(self, scene_type: str) -> dict: ...


class ActivationCoordinator:
    def __init__(
        self,
        service,
        runtime: RuntimeConfigurationAdapter,
        probe_service,
        *,
        preflight_ttl_seconds: int = STREAM_PROFILE_PREFLIGHT_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if preflight_ttl_seconds <= 0:
            raise ValueError("preflight_ttl_seconds must be positive")
        self.service = service
        self.runtime = runtime
        self.probe_service = probe_service
        self._preflight_ttl_seconds = preflight_ttl_seconds
        self._clock = clock or time.monotonic
        self._preflight_tokens: dict[str, _PendingStreamProfilePreflight] = {}
        self._preflight_lock = threading.Lock()
        self._mutation_lock = threading.Lock()

    def preflight_stream_profile(self, profile_id: str) -> dict:
        started = self._clock()
        profile, bindings, structural, disabled = self._stream_profile_target(profile_id)
        results = (
            self.probe_service.probe_many(bindings)
            if structural["ok"] and not disabled
            else []
        )
        self.service.record_probe_results(results)
        ok = structural["ok"] and not disabled and all(item["ok"] for item in results)
        response = {
            "ok": ok,
            "profile_id": profile_id,
            "structural": structural,
            "disabled_stream_ids": disabled,
            "streams": results,
            "elapsed_ms": int((self._clock() - started) * 1000),
            "preflight_token": None,
            "expires_in_seconds": None,
        }
        if not ok:
            return response

        token = f"stream_preflight_{secrets.token_urlsafe(24)}"
        pending = _PendingStreamProfilePreflight(
            fingerprint=self._stream_profile_fingerprint(profile, bindings),
            expires_at=self._clock() + self._preflight_ttl_seconds,
            results=[dict(item) for item in results],
        )
        with self._preflight_lock:
            self._purge_expired_preflights_locked(self._clock())
            self._preflight_tokens[token] = pending
        response["preflight_token"] = token
        response["expires_in_seconds"] = self._preflight_ttl_seconds
        return response

    def activate_stream_profile(
        self,
        profile_id: str,
        *,
        run_probe: bool = True,
        preflight_token: str | None = None,
    ) -> dict:
        started = self._clock()
        with self._exclusive_mutation():
            profile, bindings, structural, disabled = self._stream_profile_target(profile_id)
            state = self.service.get_activation_state()
            if state["stream_profile_id"] == profile_id:
                return {
                    "operation_id": None,
                    "status": "succeeded",
                    "noop": True,
                    "profile_id": profile_id,
                    "rollback": "not_required",
                }
            if not structural["ok"]:
                raise ConfigurationError(
                    code="STREAM_PROFILE_INCOMPLETE",
                    message="关联方案必须完整且一对一地覆盖固定摄像头目录",
                    details=[structural],
                )
            if disabled:
                raise ConfigurationError(
                    code="STREAM_PROFILE_CONTAINS_DISABLED_STREAM",
                    message="关联方案包含已停用的流",
                    details=[{"stream_ids": disabled}],
                )
            if preflight_token is not None:
                pending = self._consume_stream_profile_preflight(
                    profile_id,
                    preflight_token,
                    self._stream_profile_fingerprint(profile, bindings),
                )
                probe_results = [dict(item) for item in pending.results]
            else:
                probe_results = self.probe_service.probe_many(bindings) if run_probe else []
                failed = [item for item in probe_results if not item["ok"]]
                self.service.record_probe_results(probe_results)
                if failed:
                    raise ConfigurationError(
                        code="STREAM_PROBE_FAILED",
                        message=f"关联方案中有 {len(failed)} 路流未通过预检",
                        details=failed,
                    )

            old_mapping = self.runtime.current_stream_mapping()
            target_mapping = {item["camera_id"]: item["rtsp_url"] for item in bindings}
            operation_id = self.service.start_operation(
                "activate_stream_profile",
                {"profile_id": state["stream_profile_id"]},
                {"profile_id": profile_id},
            )
            self.service.update_operation(operation_id, status="applying")
            try:
                runtime_result = self.runtime.apply_stream_mapping(target_mapping)
                self.service.update_activation_state(stream_profile_id=profile_id)
                self.service.finish_operation(
                    operation_id,
                    status="succeeded",
                    audit_summary={"profile_id": profile_id},
                )
            except Exception as exc:
                rollback = "succeeded"
                try:
                    self.runtime.apply_stream_mapping(old_mapping)
                except Exception:
                    rollback = "failed"
                status = "rolled_back" if rollback == "succeeded" else "failed"
                self.service.finish_operation(
                    operation_id,
                    status=status,
                    error_summary=str(exc),
                    audit_summary={"profile_id": profile_id, "rollback": rollback},
                )
                raise ConfigurationError(
                    code="STREAM_ACTIVATION_FAILED",
                    message="运行通道未能应用新的流关联方案",
                    status_code=502,
                    operation_id=operation_id,
                    rollback=rollback,
                    details=[{"reason": str(exc)[:500]}],
                ) from exc
            return {
                "operation_id": operation_id,
                "status": "succeeded",
                "noop": False,
                "profile_id": profile_id,
                "probe_results": probe_results,
                "runtime": runtime_result,
                "elapsed_ms": int((self._clock() - started) * 1000),
                "rollback": "not_required",
            }

    def _stream_profile_target(self, profile_id: str) -> tuple[dict, list[dict], dict, list[str]]:
        profile = self.service.get_stream_profile(profile_id)
        expected = {item["camera_id"] for item in self.service.list_cameras()}
        bindings = profile["bindings"]
        cameras = {item["camera_id"] for item in bindings}
        streams = {item["stream_id"] for item in bindings}
        structural = {
            "ok": cameras == expected and len(streams) == len(expected),
            "missing_camera_ids": sorted(expected - cameras),
            "binding_count": len(bindings),
        }
        disabled = [item["stream_id"] for item in bindings if not item["enabled"]]
        return profile, bindings, structural, disabled

    @staticmethod
    def _stream_profile_fingerprint(profile: dict, bindings: list[dict]) -> str:
        payload = {
            "profile_id": profile["profile_id"],
            "bindings": sorted(
                [
                    {
                        "camera_id": item["camera_id"],
                        "stream_id": item["stream_id"],
                        "rtsp_url": item["rtsp_url"],
                        "enabled": bool(item["enabled"]),
                    }
                    for item in bindings
                ],
                key=lambda item: (item["camera_id"], item["stream_id"]),
            ),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _consume_stream_profile_preflight(
        self,
        profile_id: str,
        token: str,
        fingerprint: str,
    ) -> _PendingStreamProfilePreflight:
        now = self._clock()
        with self._preflight_lock:
            self._purge_expired_preflights_locked(now)
            pending = self._preflight_tokens.pop(token, None)
        if pending is None or pending.fingerprint != fingerprint:
            raise ConfigurationError(
                code="STREAM_PREFLIGHT_EXPIRED",
                message="流关联方案预检凭证不存在、已使用、已过期或方案内容已变化，请重新预检",
                status_code=409,
                details=[{"profile_id": profile_id}],
            )
        return pending

    def _purge_expired_preflights_locked(self, now: float) -> None:
        expired = [
            token
            for token, pending in self._preflight_tokens.items()
            if pending.expires_at <= now
        ]
        for token in expired:
            self._preflight_tokens.pop(token, None)

    def update_stream_source(self, stream_id: str, values: dict) -> dict:
        old_stream = self.service.get_stream(stream_id)
        state = self.service.get_activation_state()
        active_profile = (
            self.service.get_stream_profile(state["stream_profile_id"])
            if state.get("stream_profile_id")
            else None
        )
        active_binding = active_profile and any(
            item["stream_id"] == stream_id for item in active_profile["bindings"]
        )
        if not active_binding:
            return self.service.update_stream(stream_id, values)
        target = {
            "name": values.get("name", old_stream["name"]),
            "rtsp_url": values.get("rtsp_url", old_stream["rtsp_url"]),
            "enabled": values.get("enabled", old_stream["enabled"]),
        }
        if not target["enabled"]:
            raise ConfigurationError(
                "ACTIVE_STREAM_CANNOT_BE_DISABLED",
                "当前激活方案使用的流不能直接停用",
                status_code=409,
                details=[{"stream_id": stream_id}],
            )
        probe = self.probe_service.probe(stream_id, target["rtsp_url"]).as_dict()
        if not probe["ok"]:
            raise ConfigurationError(
                "STREAM_PROBE_FAILED",
                "更新后的流未通过首帧探测",
                details=[probe],
            )
        with self._exclusive_mutation():
            old_mapping = self.runtime.current_stream_mapping()
            updated = self.service.update_stream(stream_id, target)
            try:
                profile = self.service.get_stream_profile(state["stream_profile_id"])
                mapping = {item["camera_id"]: item["rtsp_url"] for item in profile["bindings"]}
                self.runtime.apply_stream_mapping(mapping)
            except Exception as exc:
                self.service.update_stream(
                    stream_id,
                    {
                        "name": old_stream["name"],
                        "rtsp_url": old_stream["rtsp_url"],
                        "enabled": old_stream["enabled"],
                    },
                )
                rollback = "succeeded"
                try:
                    self.runtime.apply_stream_mapping(old_mapping)
                except Exception:
                    rollback = "failed"
                raise ConfigurationError(
                    "STREAM_UPDATE_APPLY_FAILED",
                    "流已更新但运行通道重连失败",
                    status_code=502,
                    rollback=rollback,
                    details=[{"reason": str(exc)[:500]}],
                ) from exc
            self.service.record_probe_results([probe])
            return updated

    def update_stream_sources(self, values: list[dict]) -> dict:
        started = time.monotonic()
        with self._exclusive_mutation():
            entries = self.service.prepare_stream_batch_update(values)
            state = self.service.get_activation_state()
            active_profile = (
                self.service.get_stream_profile(state["stream_profile_id"])
                if state.get("stream_profile_id")
                else None
            )
            active_bindings = active_profile["bindings"] if active_profile else []
            active_ids = {item["stream_id"] for item in active_bindings}
            disabled = [
                entry["target"]["stream_id"]
                for entry in entries
                if entry["target"]["stream_id"] in active_ids
                and not entry["target"]["enabled"]
            ]
            if disabled:
                raise ConfigurationError(
                    "ACTIVE_STREAM_CANNOT_BE_DISABLED",
                    "当前激活方案使用的流不能直接停用",
                    status_code=409,
                    details=[{"stream_id": stream_id} for stream_id in disabled],
                )

            active_entries = [
                entry
                for entry in entries
                if entry["target"]["stream_id"] in active_ids
                and entry["changed_fields"]
            ]
            probe_targets = [
                entry["target"]
                for entry in active_entries
                if "rtsp_url" in entry["changed_fields"]
            ]
            probe_results = (
                self.probe_service.probe_many(probe_targets) if probe_targets else []
            )
            failed = [result for result in probe_results if not result["ok"]]
            if failed:
                raise ConfigurationError(
                    "STREAM_PROBE_FAILED",
                    f"批量修改中有 {len(failed)} 路激活流未通过首帧探测",
                    details=failed,
                )

            if not active_entries:
                updated = self.service.update_stream_batch(entries)
                return {
                    "updated": len(updated),
                    "streams": updated,
                    "probe_results": [],
                    "runtime": None,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "rollback": "not_required",
                }

            old_mapping = self.runtime.current_stream_mapping()
            target_by_id = {
                entry["target"]["stream_id"]: entry["target"] for entry in entries
            }
            target_mapping = {
                binding["camera_id"]: target_by_id.get(
                    binding["stream_id"], binding
                )["rtsp_url"]
                for binding in active_bindings
            }
            phase = "runtime"
            try:
                runtime_result = self.runtime.apply_stream_mapping(target_mapping)
                phase = "database"
                updated = self.service.update_stream_batch(
                    entries,
                    probe_results=probe_results,
                )
            except Exception as exc:
                rollback = "succeeded"
                try:
                    self.runtime.apply_stream_mapping(old_mapping)
                except Exception:
                    rollback = "failed"
                raise ConfigurationError(
                    "STREAM_BATCH_UPDATE_APPLY_FAILED",
                    "批量流修改未能同时应用到运行态和数据库",
                    status_code=502,
                    rollback=rollback,
                    details=[
                        {
                            "phase": phase,
                            "reason": redact_text(str(exc))[:500],
                        }
                    ],
                ) from exc
            return {
                "updated": len(updated),
                "streams": updated,
                "probe_results": probe_results,
                "runtime": runtime_result,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "rollback": "not_required",
            }

    def update_stream_profile(self, profile_id: str, values: dict) -> dict:
        state = self.service.get_activation_state()
        if state.get("stream_profile_id") != profile_id:
            return self.service.update_stream_profile(profile_id, values)
        expected = {item["camera_id"] for item in self.service.list_cameras()}
        stream_ids = [item["stream_id"] for item in values["bindings"]]
        camera_ids = {item["camera_id"] for item in values["bindings"]}
        if camera_ids != expected or len(stream_ids) != len(set(stream_ids)) or len(stream_ids) != len(expected):
            raise ConfigurationError(
                "STREAM_PROFILE_INCOMPLETE",
                "当前激活方案必须完整且一对一覆盖固定摄像头目录",
            )
        streams = {item["stream_id"]: item for item in self.service.list_streams(reveal_credentials=True)}
        probe_targets = []
        for binding in values["bindings"]:
            stream = streams.get(binding["stream_id"])
            if stream is None or not stream["enabled"]:
                raise ConfigurationError(
                    "STREAM_PROFILE_INVALID",
                    "关联方案引用了不存在或已停用的流",
                    details=[binding],
                )
            probe_targets.append({**binding, **stream})
        results = self.probe_service.probe_many(probe_targets)
        failed = [item for item in results if not item["ok"]]
        if failed:
            raise ConfigurationError(
                "STREAM_PROBE_FAILED",
                f"关联方案中有 {len(failed)} 路流未通过预检",
                details=failed,
            )
        with self._exclusive_mutation():
            old_profile = self.service.get_stream_profile(profile_id)
            old_values = {
                "name": old_profile["name"],
                "description": old_profile["description"],
                "bindings": [
                    {"camera_id": item["camera_id"], "stream_id": item["stream_id"]}
                    for item in old_profile["bindings"]
                ],
            }
            old_mapping = self.runtime.current_stream_mapping()
            updated = self.service.update_stream_profile(profile_id, values)
            try:
                mapping = {
                    item["camera_id"]: item["rtsp_url"]
                    for item in updated["bindings"]
                }
                self.runtime.apply_stream_mapping(mapping)
            except Exception as exc:
                self.service.update_stream_profile(profile_id, old_values)
                rollback = "succeeded"
                try:
                    self.runtime.apply_stream_mapping(old_mapping)
                except Exception:
                    rollback = "failed"
                raise ConfigurationError(
                    "STREAM_PROFILE_UPDATE_FAILED",
                    "关联方案更新未能应用到运行通道",
                    status_code=502,
                    rollback=rollback,
                    details=[{"reason": str(exc)[:500]}],
                ) from exc
            self.service.record_probe_results(results)
            return updated

    def activate_topology(self, topology_id: str) -> dict:
        with self._exclusive_mutation():
            topology = self.service.get_topology(topology_id)
            state = self.service.get_activation_state()
            old_topology = (
                self.service.get_topology(state["topology_id"])
                if state.get("topology_id")
                else None
            )
            if (
                state["topology_id"] == topology_id
                and state["topology_revision"] == topology["revision"]
            ):
                return {
                    "operation_id": None,
                    "status": "succeeded",
                    "noop": True,
                    "topology_id": topology_id,
                    "rollback": "not_required",
                }
            self.service.validate_topology(topology)
            operation_id = self.service.start_operation(
                "activate_topology",
                {"topology_id": state["topology_id"], "revision": state["topology_revision"]},
                {"topology_id": topology_id, "revision": topology["revision"]},
            )
            self.service.update_operation(operation_id, status="applying")
            try:
                runtime_result = self.runtime.apply_topology(topology)
                deactivated = self.service.commit_topology_activation(
                    topology_id,
                    topology["revision"],
                    runtime_result.get("deactivated_scene_ids", []),
                )
                self.service.finish_operation(
                    operation_id,
                    status="succeeded",
                    audit_summary={"topology_id": topology_id, "revision": topology["revision"]},
                )
            except Exception as exc:
                rollback = "not_required"
                if old_topology is not None:
                    rollback = "succeeded"
                    try:
                        self.runtime.apply_topology(old_topology)
                    except Exception:
                        rollback = "failed"
                self.service.finish_operation(
                    operation_id,
                    status="rolled_back" if rollback == "succeeded" else "failed",
                    error_summary=str(exc),
                    audit_summary={"topology_id": topology_id, "rollback": rollback},
                )
                raise ConfigurationError(
                    code="TOPOLOGY_ACTIVATION_FAILED",
                    message="道路拓扑未能应用到运行态",
                    status_code=500,
                    operation_id=operation_id,
                    rollback=rollback,
                    details=[{"reason": str(exc)[:500]}],
                ) from exc
            return {
                "operation_id": operation_id,
                "status": "succeeded",
                "noop": False,
                "topology_id": topology_id,
                "revision": topology["revision"],
                "deactivated_scenes": deactivated,
                "runtime": runtime_result,
                "rollback": "not_required",
            }

    def update_topology(
        self,
        topology_id: str,
        values: dict,
        *,
        runtime_already_applied: bool = False,
    ) -> dict:
        old_topology = self.service.get_topology(topology_id)
        if not old_topology["is_active"]:
            return self.service.update_topology(topology_id, values)
        map_asset_id = values.get("map_asset_id") or old_topology["map_asset_id"]
        map_asset = self.service.repository.fetch_one(
            "SELECT * FROM asset WHERE asset_id = ?", (map_asset_id,)
        )
        candidate = {
            **old_topology,
            **values,
            "topology_id": topology_id,
            "revision": int(old_topology["revision"]) + 1,
            "map_asset_id": map_asset_id,
            "map_asset": dict(map_asset) if map_asset else None,
            "is_active": True,
        }
        self.service.validate_topology(candidate)
        with self._exclusive_mutation():
            try:
                if not runtime_already_applied:
                    self.runtime.apply_topology(candidate)
                return self.service.update_topology(topology_id, values)
            except Exception as exc:
                rollback = "succeeded"
                try:
                    self.runtime.apply_topology(old_topology)
                except Exception:
                    rollback = "failed"
                if isinstance(exc, ConfigurationError):
                    exc.rollback = rollback
                    raise
                raise ConfigurationError(
                    "TOPOLOGY_UPDATE_FAILED",
                    "拓扑修订未能同时应用到数据库和运行态",
                    status_code=500,
                    rollback=rollback,
                    details=[{"reason": str(exc)[:500]}],
                ) from exc

    def activate_scene(self, scene_id: str, *, run_probe: bool = True) -> dict:
        with self._exclusive_mutation():
            scene = self.service.get_scene(scene_id)
            state = self.service.get_activation_state()
            if scene["review_status"] != "ready":
                raise ConfigurationError(
                    code="SCENE_NEEDS_REVIEW",
                    message="场景绑定的拓扑修订已变化，请重新确认后再启用",
                    details=[{"scene_id": scene_id}],
                )
            if (
                scene["topology_id"] != state["topology_id"]
                or scene["topology_revision"] != state["topology_revision"]
            ):
                raise ConfigurationError(
                    code="SCENE_TOPOLOGY_MISMATCH",
                    message="场景与当前激活拓扑不兼容",
                    details=[{"scene_id": scene_id}],
                )
            stream = self.service.resolve_camera_stream(scene["camera_id"])
            if stream is None:
                raise ConfigurationError(
                    code="SCENE_STREAM_MISSING",
                    message="当前关联方案未提供场景摄像头的流",
                    details=[{"camera_id": scene["camera_id"]}],
                )
            if run_probe:
                probe = self.probe_service.probe(stream["stream_id"], stream["rtsp_url"]).as_dict()
                self.service.record_probe_results([probe])
                if not probe["ok"]:
                    raise ConfigurationError(
                        code="STREAM_PROBE_FAILED",
                        message="场景流未通过预检",
                        details=[probe],
                    )
            field = f"{scene['scene_type']}_scene_id"
            if state[field] == scene_id:
                return {"status": "succeeded", "noop": True, "scene_id": scene_id}
            operation_id = self.service.start_operation(
                f"activate_{scene['scene_type']}_scene",
                {"scene_id": state[field]},
                {"scene_id": scene_id},
            )
            self.service.update_operation(operation_id, status="applying")
            old_scene = None
            old_stream = None
            if state[field]:
                old_scene = self.service.get_scene(state[field])
                old_stream = self.service.resolve_camera_stream(old_scene["camera_id"])
            try:
                runtime_result = self.runtime.activate_scene_runtime(scene, stream["rtsp_url"])
                self.service.update_activation_state(**{field: scene_id})
                self.service.finish_operation(
                    operation_id,
                    status="succeeded",
                    audit_summary={"scene_id": scene_id, "scene_type": scene["scene_type"]},
                )
            except Exception as exc:
                rollback = "succeeded"
                try:
                    if old_scene is not None and old_stream is not None:
                        self.runtime.activate_scene_runtime(old_scene, old_stream["rtsp_url"])
                    else:
                        self.runtime.deactivate_scene_runtime(scene["scene_type"])
                except Exception:
                    rollback = "failed"
                self.service.finish_operation(
                    operation_id,
                    status="rolled_back" if rollback == "succeeded" else "failed",
                    error_summary=str(exc),
                    audit_summary={"scene_id": scene_id, "rollback": rollback},
                )
                raise ConfigurationError(
                    code="SCENE_ACTIVATION_FAILED",
                    message="场景运行通道启动失败",
                    status_code=500,
                    operation_id=operation_id,
                    rollback=rollback,
                    details=[{"reason": str(exc)[:500]}],
                ) from exc
            return {
                "operation_id": operation_id,
                "status": "succeeded",
                "noop": False,
                "scene_id": scene_id,
                "runtime": runtime_result,
                "rollback": "not_required",
            }

    def deactivate_scene(self, scene_type: str) -> dict:
        if scene_type not in {"no_parking", "road_abnormal"}:
            raise ConfigurationError("SCENE_TYPE_INVALID", "未知场景类型")
        with self._exclusive_mutation():
            result = self.runtime.deactivate_scene_runtime(scene_type)
            self.service.update_activation_state(**{f"{scene_type}_scene_id": None})
            self.service.write_audit(
                f"deactivate_{scene_type}_scene",
                scene_type,
                "succeeded",
                {"scene_type": scene_type},
            )
            return {"status": "succeeded", "scene_type": scene_type, "runtime": result}

    class _Mutation:
        def __init__(self, lock: threading.Lock) -> None:
            self.lock = lock

        def __enter__(self):
            if not self.lock.acquire(blocking=False):
                raise ConfigurationError(
                    code="CONFIG_MUTATION_IN_PROGRESS",
                    message="另一个配置写操作正在执行",
                    status_code=409,
                )
            return self

        def __exit__(self, *_args) -> None:
            self.lock.release()

    def _exclusive_mutation(self) -> _Mutation:
        return self._Mutation(self._mutation_lock)
