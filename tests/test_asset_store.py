import hashlib

import cv2
import numpy as np
import pytest

from backend.configuration.assets import AssetStore, AssetValidationError


def _encoded_image(extension=".png", *, width=12, height=8):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :, 1] = 170
    success, encoded = cv2.imencode(extension, image)
    assert success
    return encoded.tobytes()


def test_ingest_stores_a_validated_content_addressed_image(tmp_path):
    source = tmp_path / "legacy-name.bin"
    payload = _encoded_image(".png", width=16, height=9)
    source.write_bytes(payload)
    store = AssetStore(tmp_path / "assets")

    asset = store.ingest(source, "map")

    digest = hashlib.sha256(payload).hexdigest()
    assert asset == {
        "asset_id": f"asset-map-{digest}",
        "kind": "map",
        "relative_path": f"maps/{digest}.png",
        "sha256": digest,
        "size_bytes": len(payload),
        "media_type": "image/png",
        "width": 16,
        "height": 9,
    }
    assert store.resolve(asset["relative_path"]).read_bytes() == payload
    assert store.verify(asset) is True


def test_ingest_is_idempotent_for_identical_content(tmp_path):
    payload = _encoded_image(".jpg")
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.dat"
    first.write_bytes(payload)
    second.write_bytes(payload)
    store = AssetStore(tmp_path / "assets")

    first_asset = store.ingest(first, "scene_reference")
    second_asset = store.ingest(second, "scene_reference")

    assert second_asset == first_asset
    assert [path for path in (tmp_path / "assets").rglob("*") if path.is_file()] == [
        store.resolve(first_asset["relative_path"])
    ]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "empty"),
        (b"not an image", "unsupported image format"),
        (b"\x89PNG\r\n\x1a\ntruncated", "cannot be decoded"),
    ],
)
def test_ingest_rejects_invalid_images(tmp_path, payload, message):
    source = tmp_path / "invalid.img"
    source.write_bytes(payload)

    with pytest.raises(AssetValidationError, match=message):
        AssetStore(tmp_path / "assets").ingest(source, "map")


def test_ingest_rejects_files_over_the_byte_limit_without_reading_them(tmp_path):
    source = tmp_path / "large.png"
    with source.open("wb") as stream:
        stream.truncate(11)
    store = AssetStore(tmp_path / "assets", max_image_bytes=10)

    with pytest.raises(AssetValidationError, match="exceeds 10 byte limit"):
        store.ingest(source, "map")


def test_ingest_rejects_decoded_dimensions_over_the_limit(tmp_path):
    source = tmp_path / "wide.png"
    source.write_bytes(_encoded_image(width=21, height=2))
    store = AssetStore(tmp_path / "assets", max_dimension=20)

    with pytest.raises(AssetValidationError, match="dimensions exceed 20 x 20"):
        store.ingest(source, "map")


def test_resolve_rejects_paths_outside_the_store(tmp_path):
    store = AssetStore(tmp_path / "assets")

    for relative_path in (
        "../outside.png",
        "/absolute.png",
        "maps\\file.png",
        "other/" + "a" * 64 + ".png",
    ):
        with pytest.raises(AssetValidationError):
            store.resolve(relative_path)


def test_verify_detects_tampering(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(_encoded_image())
    store = AssetStore(tmp_path / "assets")
    asset = store.ingest(source, "map")
    store.resolve(asset["relative_path"]).write_bytes(b"changed")

    with pytest.raises(AssetValidationError, match="size does not match"):
        store.verify(asset)


def test_verify_rejects_a_non_deterministic_asset_id(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(_encoded_image())
    store = AssetStore(tmp_path / "assets")
    asset = store.ingest(source, "map")
    asset["asset_id"] = "random-id"

    with pytest.raises(AssetValidationError, match="ID is not content-addressed"):
        store.verify(asset)
