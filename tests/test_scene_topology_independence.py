from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.configuration import (
    ConfigurationRepository,
    build_camera_catalog,
)
from backend.configuration.errors import ConfigurationError
from backend.configuration.service import ConfigurationService
from backend.state import ApplicationState


def _topology_values(name: str) -> dict:
    return {
        "name": name,
        "map_asset_id": "map-asset",
        "map_width": 1105,
        "map_height": 740,
        "nodes": [
            {
                "node_id": "node-start",
                "x": 0.1,
                "y": 0.2,
                "node_type": "endpoint",
            },
            {
                "node_id": "node-end",
                "x": 0.8,
                "y": 0.2,
                "node_type": "endpoint",
            },
        ],
        "segments": [
            {
                "segment_id": "road-1",
                "name": "测试道路",
                "points": [[0.1, 0.2], [0.8, 0.2]],
                "geometry_type": "polyline",
                "start_node_id": "node-start",
                "end_node_id": "node-end",
                "direction": "双向",
                "level": "ground",
                "capacity": 4,
                "road_width": 0.05,
            }
        ],
        "cameras": [
            {
                "camera_id": "camera-a",
                "x": 0.2,
                "y": 0.2,
                "heading": 90.0,
                "view_range": 0.12,
                "segment_id": "road-1",
            },
            {
                "camera_id": "camera-b",
                "x": 0.7,
                "y": 0.2,
                "heading": 270.0,
                "view_range": 0.12,
                "segment_id": "road-1",
            },
        ],
    }


@pytest.fixture
def configured_service(tmp_path):
    repository = ConfigurationRepository(tmp_path / "configuration.sqlite3")
    repository.initialize(
        build_camera_catalog(
            {
                "camera-a": "南门",
                "camera-b": "北门",
            }
        ),
        builtin_baseline_version="test",
    )
    with repository.transaction() as connection:
        repository.execute(
            connection,
            """
            INSERT INTO asset (
                asset_id, kind, relative_path, sha256,
                size_bytes, media_type, width, height
            ) VALUES (
                'map-asset', 'map', 'assets/maps/map.png', ?,
                128, 'image/png', 1105, 740
            )
            """,
            ("a" * 64,),
        )
        repository.execute(
            connection,
            """
            INSERT INTO stream_binding_profile (profile_id, name)
            VALUES ('profile-1', '测试流方案')
            """,
        )

    service = ConfigurationService(repository, object())
    topology = service.create_topology(_topology_values("当前拓扑"))
    with repository.transaction() as connection:
        repository.execute(
            connection,
            """
            INSERT INTO activation_state (
                singleton_id, stream_profile_id, topology_id, topology_revision,
                no_parking_scene_id, road_abnormal_scene_id
            ) VALUES (1, 'profile-1', ?, 1, NULL, NULL)
            """,
            (topology["topology_id"],),
        )
    return service, topology["topology_id"]


def _scene_values(
    scene_id: str,
    scene_type: str,
    topology_id: str | None,
    *,
    camera_id: str = "camera-a",
) -> dict:
    return {
        "scene_id": scene_id,
        "scene_type": scene_type,
        "name": scene_id,
        "topology_id": topology_id,
        "topology_revision": 1 if topology_id else None,
        "camera_id": camera_id,
        "reference_asset_id": None,
        "config": {"zones": []},
    }


def test_no_parking_scene_normalizes_topology_and_validates_camera(
    configured_service,
):
    service, topology_id = configured_service

    saved = service.upsert_scene_archive(
        {
            **_scene_values("no-parking-1", "no_parking", topology_id),
            "topology_revision": 99,
        }
    )

    assert saved["topology_id"] is None
    assert saved["topology_revision"] is None
    assert saved["review_status"] == "ready"

    with pytest.raises(ConfigurationError) as caught:
        service.upsert_scene_archive(
            _scene_values(
                "no-parking-bad-camera",
                "no_parking",
                topology_id,
                camera_id="camera-missing",
            )
        )
    assert caught.value.code == "SCENE_CAMERA_INVALID"

    with pytest.raises(ConfigurationError) as caught:
        service.upsert_scene_archive(
            _scene_values("road-abnormal-no-topology", "road_abnormal", None)
        )
    assert caught.value.code == "SCENE_TOPOLOGY_REQUIRED"


def test_application_state_persists_topology_only_for_road_abnormal_scenes():
    class CapturingService:
        def __init__(self):
            self.values = []

        def get_activation_state(self):
            return {
                "topology_id": "topology-current",
                "topology_revision": 7,
            }

        def upsert_scene_archive(self, values):
            self.values.append(values)
            return values

    service = CapturingService()
    runtime = SimpleNamespace(
        configuration_enabled=True,
        configuration_service=service,
        no_parking=object(),
        road_abnormal=object(),
    )
    base_scene = {
        "scene_id": "scene-1",
        "name": "测试场景",
        "camera_id": "camera-a",
        "zones": [],
    }

    ApplicationState.persist_scene(runtime, "no_parking", base_scene)
    ApplicationState.persist_scene(
        runtime,
        "road_abnormal",
        {**base_scene, "scene_id": "scene-2"},
    )

    assert service.values[0]["topology_id"] is None
    assert service.values[0]["topology_revision"] is None
    assert service.values[1]["topology_id"] == "topology-current"
    assert service.values[1]["topology_revision"] == 7


def test_topology_update_invalidates_only_road_abnormal_scene(configured_service):
    service, topology_id = configured_service
    service.upsert_scene_archive(
        _scene_values("no-parking-1", "no_parking", topology_id)
    )
    service.upsert_scene_archive(
        _scene_values("road-abnormal-1", "road_abnormal", topology_id)
    )
    service.update_activation_state(
        no_parking_scene_id="no-parking-1",
        road_abnormal_scene_id="road-abnormal-1",
    )
    current = service.get_topology(topology_id)
    updated = {
        key: current[key]
        for key in (
            "name",
            "map_asset_id",
            "map_width",
            "map_height",
            "nodes",
            "segments",
            "cameras",
        )
    }

    service.update_topology(topology_id, updated)

    assert service.get_scene("no-parking-1")["review_status"] == "ready"
    assert service.get_scene("road-abnormal-1")["review_status"] == "needs_review"
    activation = service.get_activation_state()
    assert activation["topology_revision"] == 2
    assert activation["no_parking_scene_id"] == "no-parking-1"
    assert activation["road_abnormal_scene_id"] is None


def test_topology_delete_ignores_no_parking_but_rejects_road_abnormal_reference(
    configured_service,
):
    service, _ = configured_service
    no_parking_topology = service.create_topology(
        _topology_values("仅被禁停场景提交引用")
    )
    service.upsert_scene_archive(
        _scene_values(
            "no-parking-delete",
            "no_parking",
            no_parking_topology["topology_id"],
        )
    )

    deleted = service.delete_topology(no_parking_topology["topology_id"])

    assert deleted["deleted"] is True

    road_topology = service.create_topology(_topology_values("道路异常引用拓扑"))
    service.upsert_scene_archive(
        _scene_values(
            "road-abnormal-delete",
            "road_abnormal",
            road_topology["topology_id"],
        )
    )

    with pytest.raises(ConfigurationError) as caught:
        service.delete_topology(road_topology["topology_id"])
    assert caught.value.code == "TOPOLOGY_IN_USE"
