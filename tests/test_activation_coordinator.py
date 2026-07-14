import pytest

from backend.configuration.activation import ActivationCoordinator
from backend.configuration.errors import ConfigurationError


class FakeProbe:
    def __init__(self, *, failing=False):
        self.failing = failing

    def probe_many(self, bindings):
        return [
            {
                "stream_id": item["stream_id"],
                "ok": not self.failing,
                "code": "STREAM_CONNECT_FAILED" if self.failing else "OK",
                "message": "failed" if self.failing else "ok",
                "elapsed_ms": 1,
                "width": 10,
                "height": 10,
            }
            for item in bindings
        ]


class FakeService:
    def __init__(self):
        self.state = {
            "stream_profile_id": "old",
            "topology_id": "topology",
            "topology_revision": 1,
            "no_parking_scene_id": None,
            "road_abnormal_scene_id": None,
        }
        self.operations = {}
        self.probes = []

    def list_cameras(self):
        return [{"camera_id": "camera-1"}, {"camera_id": "camera-2"}]

    def get_stream_profile(self, _profile_id):
        return {
            "profile_id": "target",
            "bindings": [
                {
                    "camera_id": "camera-1",
                    "stream_id": "stream-1",
                    "rtsp_url": "rtsp://example.test/1",
                    "enabled": True,
                },
                {
                    "camera_id": "camera-2",
                    "stream_id": "stream-2",
                    "rtsp_url": "rtsp://example.test/2",
                    "enabled": True,
                },
            ],
        }

    def get_activation_state(self):
        return dict(self.state)

    def record_probe_results(self, results):
        self.probes = results

    def start_operation(self, operation_type, old, target):
        self.operations["op-1"] = {"type": operation_type, "old": old, "target": target}
        return "op-1"

    def update_operation(self, operation_id, **values):
        self.operations[operation_id].update(values)

    def update_activation_state(self, **values):
        self.state.update(values)

    def finish_operation(self, operation_id, **values):
        self.operations[operation_id].update(values)


class FakeRuntime:
    def __init__(self, *, fail_target=False):
        self.mapping = {
            "camera-1": "rtsp://old.test/1",
            "camera-2": "rtsp://old.test/2",
        }
        self.fail_target = fail_target
        self.applied = []

    def current_stream_mapping(self):
        return dict(self.mapping)

    def apply_stream_mapping(self, mapping):
        self.applied.append(dict(mapping))
        if self.fail_target and "example.test" in next(iter(mapping.values())):
            raise RuntimeError("decoder did not become ready")
        self.mapping = dict(mapping)
        return {"reconnected_camera_ids": list(mapping)}


class CountingProbe(FakeProbe):
    def __init__(self, *, failing=False):
        super().__init__(failing=failing)
        self.calls = 0

    def probe_many(self, bindings):
        self.calls += 1
        return super().probe_many(bindings)


class SuccessfulSceneProbe:
    class Result:
        def as_dict(self):
            return {
                "stream_id": "stream-1",
                "ok": True,
                "code": "OK",
                "message": "ok",
                "elapsed_ms": 1,
            }

    def probe(self, *_args):
        return self.Result()


class SceneService(FakeService):
    def __init__(
        self,
        *,
        scene_type,
        topology_id,
        topology_revision,
        review_status,
    ):
        super().__init__()
        self.scene = {
            "scene_id": "scene-1",
            "scene_type": scene_type,
            "topology_id": topology_id,
            "topology_revision": topology_revision,
            "review_status": review_status,
            "camera_id": "camera-1",
        }

    def get_scene(self, _scene_id):
        return dict(self.scene)

    def resolve_camera_stream(self, _camera_id):
        return {
            "stream_id": "stream-1",
            "rtsp_url": "rtsp://example.test/1",
        }


class SceneRuntime(FakeRuntime):
    def __init__(self):
        super().__init__()
        self.activated = []

    def activate_scene_runtime(self, scene, _stream_url):
        self.activated.append(scene["scene_id"])
        return {"running": True}

    def deactivate_scene_runtime(self, _scene_type):
        return {"running": False}


def test_stream_profile_activation_commits_after_runtime_apply():
    service = FakeService()
    runtime = FakeRuntime()
    result = ActivationCoordinator(service, runtime, FakeProbe()).activate_stream_profile("target")

    assert result["status"] == "succeeded"
    assert service.state["stream_profile_id"] == "target"
    assert service.operations["op-1"]["status"] == "succeeded"
    assert runtime.mapping["camera-1"] == "rtsp://example.test/1"


def test_stream_profile_activation_restores_old_mapping_on_runtime_failure():
    service = FakeService()
    runtime = FakeRuntime(fail_target=True)

    with pytest.raises(ConfigurationError) as caught:
        ActivationCoordinator(service, runtime, FakeProbe()).activate_stream_profile("target")

    assert caught.value.code == "STREAM_ACTIVATION_FAILED"
    assert caught.value.rollback == "succeeded"
    assert service.state["stream_profile_id"] == "old"
    assert service.operations["op-1"]["status"] == "rolled_back"
    assert runtime.mapping["camera-1"] == "rtsp://old.test/1"


def test_stream_profile_preflight_failure_never_changes_runtime():
    service = FakeService()
    runtime = FakeRuntime()

    with pytest.raises(ConfigurationError) as caught:
        ActivationCoordinator(service, runtime, FakeProbe(failing=True)).activate_stream_profile("target")

    assert caught.value.code == "STREAM_PROBE_FAILED"
    assert runtime.applied == []
    assert service.operations == {}


def test_successful_preflight_token_skips_duplicate_activation_probe():
    service = FakeService()
    runtime = FakeRuntime()
    probe = CountingProbe()
    coordinator = ActivationCoordinator(service, runtime, probe)

    preflight = coordinator.preflight_stream_profile("target")
    result = coordinator.activate_stream_profile(
        "target", preflight_token=preflight["preflight_token"]
    )

    assert preflight["ok"] is True
    assert preflight["expires_in_seconds"] > 0
    assert result["status"] == "succeeded"
    assert result["probe_results"] == preflight["streams"]
    assert probe.calls == 1


def test_preflight_token_is_rejected_after_profile_content_changes():
    class MutableService(FakeService):
        def __init__(self):
            super().__init__()
            self.first_url = "rtsp://example.test/1"

        def get_stream_profile(self, profile_id):
            profile = super().get_stream_profile(profile_id)
            profile["bindings"][0]["rtsp_url"] = self.first_url
            return profile

    service = MutableService()
    runtime = FakeRuntime()
    probe = CountingProbe()
    coordinator = ActivationCoordinator(service, runtime, probe)
    token = coordinator.preflight_stream_profile("target")["preflight_token"]
    service.first_url = "rtsp://example.test/changed"

    with pytest.raises(ConfigurationError) as caught:
        coordinator.activate_stream_profile("target", preflight_token=token)

    assert caught.value.code == "STREAM_PREFLIGHT_EXPIRED"
    assert runtime.applied == []
    assert probe.calls == 1


def test_invalid_expired_and_consumed_preflight_tokens_are_rejected():
    now = [100.0]
    service = FakeService()
    runtime = FakeRuntime()
    probe = CountingProbe()
    coordinator = ActivationCoordinator(
        service,
        runtime,
        probe,
        preflight_ttl_seconds=10,
        clock=lambda: now[0],
    )

    with pytest.raises(ConfigurationError) as invalid:
        coordinator.activate_stream_profile("target", preflight_token="missing-token")
    assert invalid.value.code == "STREAM_PREFLIGHT_EXPIRED"

    expired_token = coordinator.preflight_stream_profile("target")["preflight_token"]
    now[0] = 111.0
    with pytest.raises(ConfigurationError) as expired:
        coordinator.activate_stream_profile("target", preflight_token=expired_token)
    assert expired.value.code == "STREAM_PREFLIGHT_EXPIRED"

    now[0] = 200.0
    consumed_token = coordinator.preflight_stream_profile("target")["preflight_token"]
    coordinator.activate_stream_profile("target", preflight_token=consumed_token)
    service.state["stream_profile_id"] = "old"
    with pytest.raises(ConfigurationError) as consumed:
        coordinator.activate_stream_profile("target", preflight_token=consumed_token)
    assert consumed.value.code == "STREAM_PREFLIGHT_EXPIRED"


def test_direct_activation_without_preflight_token_still_probes():
    probe = CountingProbe()
    ActivationCoordinator(FakeService(), FakeRuntime(), probe).activate_stream_profile(
        "target"
    )

    assert probe.calls == 1


def test_topology_commit_failure_reinstalls_previous_runtime_topology():
    class Service(FakeService):
        def get_topology(self, topology_id):
            return {
                "topology_id": topology_id,
                "revision": 1,
                "cameras": [],
                "segments": [],
                "nodes": [],
            }

        def validate_topology(self, _topology):
            pass

        def commit_topology_activation(self, *_args):
            raise RuntimeError("database commit failed")

    class Runtime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.topologies = []

        def apply_topology(self, topology):
            self.topologies.append(topology["topology_id"])
            return {"deactivated_scene_ids": []}

    service = Service()
    runtime = Runtime()

    with pytest.raises(ConfigurationError) as caught:
        ActivationCoordinator(service, runtime, FakeProbe()).activate_topology("new-topology")

    assert caught.value.rollback == "succeeded"
    assert runtime.topologies == ["new-topology", "topology"]
    assert service.operations["op-1"]["status"] == "rolled_back"


def test_scene_switch_failure_restores_previous_scene_runtime():
    class Probe:
        class Result:
            def as_dict(self):
                return {
                    "stream_id": "stream-1",
                    "ok": True,
                    "code": "OK",
                    "message": "ok",
                    "elapsed_ms": 1,
                }

        def probe(self, *_args):
            return self.Result()

    class Service(FakeService):
        def __init__(self):
            super().__init__()
            self.state["no_parking_scene_id"] = "old-scene"

        def get_scene(self, scene_id):
            return {
                "scene_id": scene_id,
                "scene_type": "no_parking",
                "review_status": "ready",
                "topology_id": "topology",
                "topology_revision": 1,
                "camera_id": "camera-1",
            }

        def resolve_camera_stream(self, _camera_id):
            return {"stream_id": "stream-1", "rtsp_url": "rtsp://example.test/1"}

    class Runtime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.scenes = []

        def activate_scene_runtime(self, scene, _url):
            self.scenes.append(scene["scene_id"])
            if scene["scene_id"] == "new-scene":
                raise RuntimeError("new decoder failed")
            return {"running": True}

        def deactivate_scene_runtime(self, _scene_type):
            self.scenes.append(None)

    service = Service()
    runtime = Runtime()

    with pytest.raises(ConfigurationError) as caught:
        ActivationCoordinator(service, runtime, Probe()).activate_scene("new-scene")

    assert caught.value.rollback == "succeeded"
    assert runtime.scenes == ["new-scene", "old-scene"]
    assert service.state["no_parking_scene_id"] == "old-scene"
    assert service.operations["op-1"]["status"] == "rolled_back"


def test_no_parking_activation_ignores_topology_and_review_status():
    service = SceneService(
        scene_type="no_parking",
        topology_id="legacy-topology",
        topology_revision=1,
        review_status="needs_review",
    )
    service.state.update(topology_id="current-topology", topology_revision=15)
    runtime = SceneRuntime()

    result = ActivationCoordinator(
        service,
        runtime,
        SuccessfulSceneProbe(),
    ).activate_scene("scene-1")

    assert result["status"] == "succeeded"
    assert runtime.activated == ["scene-1"]
    assert service.state["no_parking_scene_id"] == "scene-1"


def test_road_abnormal_activation_still_requires_current_topology():
    service = SceneService(
        scene_type="road_abnormal",
        topology_id="legacy-topology",
        topology_revision=1,
        review_status="ready",
    )
    service.state.update(topology_id="current-topology", topology_revision=15)

    with pytest.raises(ConfigurationError) as caught:
        ActivationCoordinator(
            service,
            SceneRuntime(),
            SuccessfulSceneProbe(),
        ).activate_scene("scene-1")

    assert caught.value.code == "SCENE_TOPOLOGY_MISMATCH"


def test_active_stream_update_restores_database_and_mapping_on_reconnect_failure():
    class Probe:
        class Result:
            def as_dict(self):
                return {"stream_id": "stream-1", "ok": True, "code": "OK", "message": "ok", "elapsed_ms": 1}

        def probe(self, *_args):
            return self.Result()

    class Service(FakeService):
        def __init__(self):
            super().__init__()
            self.stream = {
                "stream_id": "stream-1",
                "name": "old",
                "rtsp_url": "rtsp://old.test/1",
                "enabled": True,
            }

        def get_stream(self, _stream_id):
            return dict(self.stream)

        def get_stream_profile(self, _profile_id):
            return {
                "profile_id": "old",
                "bindings": [
                    {
                        "camera_id": "camera-1",
                        "stream_id": "stream-1",
                        "rtsp_url": self.stream["rtsp_url"],
                        "enabled": self.stream["enabled"],
                    },
                    {
                        "camera_id": "camera-2",
                        "stream_id": "stream-2",
                        "rtsp_url": "rtsp://old.test/2",
                        "enabled": True,
                    },
                ],
            }

        def update_stream(self, _stream_id, values):
            self.stream.update(values)
            return dict(self.stream)

    service = Service()
    runtime = FakeRuntime(fail_target=True)

    with pytest.raises(ConfigurationError) as caught:
        ActivationCoordinator(service, runtime, Probe()).update_stream_source(
            "stream-1", {"rtsp_url": "rtsp://example.test/new"}
        )

    assert caught.value.rollback == "succeeded"
    assert service.stream["rtsp_url"] == "rtsp://old.test/1"
    assert runtime.mapping["camera-1"] == "rtsp://old.test/1"


class BatchProbe:
    def __init__(self, *, failing=False):
        self.failing = failing
        self.calls = []

    def probe_many(self, targets):
        self.calls.append([item["stream_id"] for item in targets])
        return [
            {
                "stream_id": item["stream_id"],
                "ok": not self.failing,
                "code": "STREAM_CONNECT_FAILED" if self.failing else "OK",
                "message": "failed" if self.failing else "ok",
                "elapsed_ms": 1,
            }
            for item in targets
        ]


class BatchService:
    def __init__(self, *, fail_database=False):
        self.fail_database = fail_database
        self.updated = []
        self.streams = {
            "stream-1": {
                "stream_id": "stream-1",
                "name": "one",
                "rtsp_url": "rtsp://old.test/1",
                "enabled": True,
            },
            "stream-2": {
                "stream_id": "stream-2",
                "name": "two",
                "rtsp_url": "rtsp://old.test/2",
                "enabled": True,
            },
            "stream-3": {
                "stream_id": "stream-3",
                "name": "three",
                "rtsp_url": "rtsp://old.test/3",
                "enabled": False,
            },
        }

    def prepare_stream_batch_update(self, values):
        entries = []
        for target in values:
            current = dict(self.streams[target["stream_id"]])
            changed_fields = [
                key for key in ("name", "rtsp_url", "enabled")
                if current[key] != target[key]
            ]
            entries.append(
                {
                    "current": current,
                    "target": dict(target),
                    "changed_fields": changed_fields,
                }
            )
        return entries

    def get_activation_state(self):
        return {"stream_profile_id": "active-profile"}

    def get_stream_profile(self, _profile_id):
        return {
            "bindings": [
                {"camera_id": "camera-1", **self.streams["stream-1"]},
                {"camera_id": "camera-2", **self.streams["stream-2"]},
            ]
        }

    def update_stream_batch(self, entries, *, probe_results=None):
        if self.fail_database:
            raise RuntimeError("database commit failed")
        self.updated.append(
            {
                "ids": [entry["target"]["stream_id"] for entry in entries],
                "probes": list(probe_results or []),
            }
        )
        for entry in entries:
            self.streams[entry["target"]["stream_id"]] = dict(entry["target"])
        return [dict(entry["target"]) for entry in entries]


class BatchRuntime(FakeRuntime):
    def __init__(self, *, fail_first_apply=False):
        super().__init__()
        self.fail_first_apply = fail_first_apply

    def apply_stream_mapping(self, mapping):
        self.applied.append(dict(mapping))
        if self.fail_first_apply and len(self.applied) == 1:
            raise RuntimeError("runtime apply failed")
        self.mapping = dict(mapping)
        return {"reconnected_camera_ids": list(mapping)}


def _batch_values():
    return [
        {
            "stream_id": "stream-1",
            "name": "one",
            "rtsp_url": "rtsp://new.test/1",
            "enabled": True,
        },
        {
            "stream_id": "stream-2",
            "name": "two renamed",
            "rtsp_url": "rtsp://old.test/2",
            "enabled": True,
        },
        {
            "stream_id": "stream-3",
            "name": "three",
            "rtsp_url": "rtsp://new.test/3",
            "enabled": False,
        },
    ]


def test_batch_stream_update_probes_only_changed_active_urls_and_applies_mapping_once():
    service = BatchService()
    runtime = BatchRuntime()
    probe = BatchProbe()

    result = ActivationCoordinator(service, runtime, probe).update_stream_sources(
        _batch_values()
    )

    assert probe.calls == [["stream-1"]]
    assert len(runtime.applied) == 1
    assert runtime.mapping == {
        "camera-1": "rtsp://new.test/1",
        "camera-2": "rtsp://old.test/2",
    }
    assert service.updated[0]["ids"] == ["stream-1", "stream-2", "stream-3"]
    assert [item["stream_id"] for item in service.updated[0]["probes"]] == ["stream-1"]
    assert result["updated"] == 3


def test_batch_stream_update_rejects_disabling_an_active_stream_before_probe():
    service = BatchService()
    runtime = BatchRuntime()
    probe = BatchProbe()
    values = _batch_values()
    values[0]["enabled"] = False

    with pytest.raises(ConfigurationError) as caught:
        ActivationCoordinator(service, runtime, probe).update_stream_sources(values)

    assert caught.value.code == "ACTIVE_STREAM_CANNOT_BE_DISABLED"
    assert probe.calls == []
    assert runtime.applied == []
    assert service.updated == []


def test_batch_stream_probe_failure_never_changes_runtime_or_database():
    service = BatchService()
    runtime = BatchRuntime()
    probe = BatchProbe(failing=True)

    with pytest.raises(ConfigurationError) as caught:
        ActivationCoordinator(service, runtime, probe).update_stream_sources(
            _batch_values()
        )

    assert caught.value.code == "STREAM_PROBE_FAILED"
    assert runtime.applied == []
    assert service.updated == []
    assert service.streams["stream-1"]["rtsp_url"] == "rtsp://old.test/1"


@pytest.mark.parametrize("failure", ["runtime", "database"])
def test_batch_stream_update_restores_old_mapping_on_apply_or_database_failure(failure):
    service = BatchService(fail_database=failure == "database")
    runtime = BatchRuntime(fail_first_apply=failure == "runtime")

    with pytest.raises(ConfigurationError) as caught:
        ActivationCoordinator(service, runtime, BatchProbe()).update_stream_sources(
            _batch_values()
        )

    assert caught.value.code == "STREAM_BATCH_UPDATE_APPLY_FAILED"
    assert caught.value.rollback == "succeeded"
    assert runtime.mapping == {
        "camera-1": "rtsp://old.test/1",
        "camera-2": "rtsp://old.test/2",
    }
    assert len(runtime.applied) == 2
    assert service.streams["stream-1"]["rtsp_url"] == "rtsp://old.test/1"
