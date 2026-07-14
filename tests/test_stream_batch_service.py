import json

import pytest
from pydantic import ValidationError

from backend.configuration import ConfigurationRepository, build_camera_catalog
from backend.configuration.errors import ConfigurationError
from backend.configuration.models import (
    StreamBatchCreateRequest,
    StreamBatchDeleteRequest,
    StreamBatchUpdateRequest,
    StreamProbeRequest,
)
from backend.configuration.service import ConfigurationService


def _service(tmp_path):
    repository = ConfigurationRepository(tmp_path / "config.sqlite3")
    repository.initialize(
        build_camera_catalog({"camera-a": "Camera A", "camera-b": "Camera B"})
    )
    return ConfigurationService(repository, object())


def _stream(name, suffix, *, enabled=True):
    return {
        "name": name,
        "rtsp_url": f"rtsp://user:secret@example.test/{suffix}",
        "enabled": enabled,
    }


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (StreamBatchCreateRequest, {"streams": []}),
        (
            StreamBatchCreateRequest,
            {"streams": [_stream("duplicate", "1"), _stream("duplicate", "2")]},
        ),
        (
            StreamBatchCreateRequest,
            {"streams": [{**_stream("wrong", "1"), "rtsp_url": "http://example.test/1"}]},
        ),
        (
            StreamBatchCreateRequest,
            {"streams": [{**_stream("unknown", "1"), "unexpected": True}]},
        ),
        (
            StreamBatchUpdateRequest,
            {
                "streams": [
                    {"stream_id": "same", **_stream("one", "1")},
                    {"stream_id": "same", **_stream("two", "2")},
                ]
            },
        ),
        (StreamBatchDeleteRequest, {"stream_ids": ["same", "same"]}),
        (StreamProbeRequest, {"stream_ids": []}),
    ],
)
def test_stream_batch_models_reject_invalid_payloads(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_stream_batch_models_enforce_the_500_item_limit():
    with pytest.raises(ValidationError):
        StreamBatchCreateRequest.model_validate(
            {"streams": [_stream(f"stream-{index}", str(index)) for index in range(501)]}
        )


def test_batch_create_is_atomic_and_audit_never_stores_rtsp_urls(tmp_path):
    service = _service(tmp_path)

    token_stream = {
        **_stream("two", "2"),
        "rtsp_url": "rtsp://example.test/2?token=query-secret",
    }
    created = service.create_stream_batch([_stream("one", "1"), token_stream])

    assert created["created"] == 2
    assert [item["name"] for item in created["streams"]] == ["one", "two"]
    assert all("user" not in item["rtsp_url"] for item in created["streams"])
    assert "query-secret" not in created["streams"][1]["rtsp_url"]
    audit_rows = service.repository.fetch_all(
        "SELECT summary FROM audit_log WHERE operation_type = 'create_stream_batch'"
    )
    assert len(audit_rows) == 2
    assert all("rtsp" not in row["summary"].lower() for row in audit_rows)
    assert all("secret" not in row["summary"] for row in audit_rows)

    with pytest.raises(ConfigurationError) as caught:
        service.create_stream_batch([_stream("transient", "3"), _stream("one", "4")])

    assert caught.value.code == "STREAM_BATCH_CONFLICT"
    assert caught.value.details == [
        {
            "field": "name",
            "name": "one",
            "stream_id": created["streams"][0]["stream_id"],
            "reason": "already_exists",
        }
    ]
    assert service.repository.fetch_one(
        "SELECT 1 FROM stream_source WHERE name = 'transient'"
    ) is None


def test_batch_update_supports_name_swaps_and_rolls_back_mid_transaction_conflicts(tmp_path):
    service = _service(tmp_path)
    created = service.create_stream_batch([_stream("alpha", "a"), _stream("beta", "b")])
    first_id, second_id = [item["stream_id"] for item in created["streams"]]
    swapped = service.prepare_stream_batch_update(
        [
            {"stream_id": first_id, **_stream("beta", "a-new")},
            {"stream_id": second_id, **_stream("alpha", "b-new")},
        ]
    )

    updated = service.update_stream_batch(swapped)

    assert [item["name"] for item in updated] == ["beta", "alpha"]
    current = service.get_streams([first_id, second_id])
    invalid_entries = [
        {
            "current": current[0],
            "target": {**current[0], "name": "collision"},
            "changed_fields": ["name"],
        },
        {
            "current": current[1],
            "target": {**current[1], "name": "collision"},
            "changed_fields": ["name"],
        },
    ]

    with pytest.raises(ConfigurationError) as caught:
        service.update_stream_batch(invalid_entries)

    assert caught.value.code == "STREAM_BATCH_CONFLICT"
    after_failure = service.get_streams([first_id, second_id])
    assert [item["name"] for item in after_failure] == ["beta", "alpha"]


def test_batch_delete_reports_all_references_and_deletes_nothing(tmp_path):
    service = _service(tmp_path)
    created = service.create_stream_batch([_stream("bound", "bound"), _stream("free", "free")])
    bound_id, free_id = [item["stream_id"] for item in created["streams"]]
    with service.repository.transaction() as connection:
        service.repository.execute(
            connection,
            "INSERT INTO stream_binding_profile (profile_id, name) VALUES (?, ?)",
            ("profile-one", "Profile One"),
        )
        service.repository.execute(
            connection,
            "INSERT INTO stream_binding (profile_id, camera_id, stream_id) VALUES (?, ?, ?)",
            ("profile-one", "camera-a", bound_id),
        )

    with pytest.raises(ConfigurationError) as caught:
        service.delete_stream_batch([bound_id, free_id])

    assert caught.value.code == "STREAM_BATCH_IN_USE"
    assert caught.value.details == [
        {
            "stream_id": bound_id,
            "name": "bound",
            "profiles": [{"profile_id": "profile-one", "name": "Profile One"}],
        }
    ]
    assert len(service.get_streams([bound_id, free_id])) == 2

    with service.repository.transaction() as connection:
        service.repository.execute(
            connection,
            "DELETE FROM stream_binding WHERE profile_id = 'profile-one'",
        )
        service.repository.execute(
            connection,
            "DELETE FROM stream_binding_profile WHERE profile_id = 'profile-one'",
        )
    deleted = service.delete_stream_batch([bound_id, free_id])
    assert deleted == {"deleted": 2, "stream_ids": [bound_id, free_id]}
    summaries = [
        json.loads(row["summary"])
        for row in service.repository.fetch_all(
            "SELECT summary FROM audit_log WHERE operation_type = 'delete_stream_batch'"
        )
    ]
    assert summaries == [{"name": "bound"}, {"name": "free"}]
