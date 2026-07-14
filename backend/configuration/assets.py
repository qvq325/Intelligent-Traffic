"""Content-addressed storage for configuration image assets."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import cv2
import numpy as np


MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_IMAGE_DIMENSION = 10_000

_KIND_DIRECTORIES = {
    "map": "maps",
    "scene_reference": "scene-references",
}

_IMAGE_FORMATS = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"BM", "image/bmp", ".bmp"),
)

_CONTENT_PATH_PATTERN = re.compile(
    r"^(?:maps|scene-references)/[0-9a-f]{64}\.(?:jpg|png|webp|bmp)$"
)


class AssetValidationError(ValueError):
    """Raised when an asset cannot be safely accepted or verified."""


class AssetStore:
    """Validate images and store immutable copies by their SHA-256 digest."""

    def __init__(
        self,
        root_dir: Path,
        *,
        max_image_bytes: int = MAX_IMAGE_BYTES,
        max_dimension: int = MAX_IMAGE_DIMENSION,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.max_image_bytes = int(max_image_bytes)
        self.max_dimension = int(max_dimension)
        if self.max_image_bytes < 1 or self.max_dimension < 1:
            raise ValueError("asset limits must be positive")

    def ingest(self, source: Path, kind: str) -> dict[str, Any]:
        """Copy a validated image into the store and return database metadata."""
        source_path = Path(source)
        if kind not in _KIND_DIRECTORIES:
            raise AssetValidationError(f"unsupported asset kind: {kind}")
        if source_path.is_symlink() or not source_path.is_file():
            raise AssetValidationError("asset source must be a regular file")

        try:
            size_bytes = source_path.stat().st_size
        except OSError as exc:
            raise AssetValidationError(f"cannot inspect asset source: {exc}") from exc
        if size_bytes < 1:
            raise AssetValidationError("image is empty")
        if size_bytes > self.max_image_bytes:
            raise AssetValidationError(
                f"image exceeds {self.max_image_bytes} byte limit"
            )

        try:
            payload = source_path.read_bytes()
        except OSError as exc:
            raise AssetValidationError(f"cannot read asset source: {exc}") from exc
        if len(payload) != size_bytes:
            raise AssetValidationError("asset source changed while it was being read")

        media_type, extension = self._detect_format(payload)
        width, height = self._decode_dimensions(payload)
        digest = hashlib.sha256(payload).hexdigest()
        relative_path = PurePosixPath(
            _KIND_DIRECTORIES[kind], f"{digest}{extension}"
        ).as_posix()
        destination = self.resolve(relative_path)

        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self._verify_existing_content(destination, digest, size_bytes)
        else:
            self._atomic_write(destination, payload)

        return {
            "asset_id": f"asset-{kind.replace('_', '-')}-{digest}",
            "kind": kind,
            "relative_path": relative_path,
            "sha256": digest,
            "size_bytes": size_bytes,
            "media_type": media_type,
            "width": width,
            "height": height,
        }

    def resolve(self, relative_path: str) -> Path:
        """Resolve a database path while preventing escape from the asset root."""
        if not isinstance(relative_path, str) or not relative_path:
            raise AssetValidationError("asset path must be a non-empty string")
        if not _CONTENT_PATH_PATTERN.fullmatch(relative_path):
            raise AssetValidationError("asset path must be content-addressed")
        portable = PurePosixPath(relative_path)
        resolved = self.root_dir.joinpath(*portable.parts).resolve()
        if not resolved.is_relative_to(self.root_dir):
            raise AssetValidationError("asset path escapes the asset root")
        return resolved

    def verify(self, asset: Mapping[str, Any]) -> bool:
        """Verify stored bytes and all persisted image metadata."""
        required = {
            "asset_id",
            "kind",
            "relative_path",
            "sha256",
            "size_bytes",
            "media_type",
            "width",
            "height",
        }
        missing = required.difference(asset)
        if missing:
            raise AssetValidationError(
                f"asset metadata is missing: {', '.join(sorted(missing))}"
            )
        kind = str(asset["kind"])
        if kind not in _KIND_DIRECTORIES:
            raise AssetValidationError(f"unsupported asset kind: {kind}")

        path = self.resolve(str(asset["relative_path"]))
        if not path.is_file() or path.is_symlink():
            raise AssetValidationError("stored asset is missing or is not a regular file")
        size_bytes = path.stat().st_size
        if size_bytes != int(asset["size_bytes"]):
            raise AssetValidationError("stored asset size does not match metadata")
        if size_bytes < 1 or size_bytes > self.max_image_bytes:
            raise AssetValidationError("stored asset violates the image size limit")

        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != str(asset["sha256"]):
            raise AssetValidationError("stored asset digest does not match metadata")
        media_type, extension = self._detect_format(payload)
        if media_type != str(asset["media_type"]):
            raise AssetValidationError("stored asset media type does not match metadata")
        width, height = self._decode_dimensions(payload)
        if width != int(asset["width"]) or height != int(asset["height"]):
            raise AssetValidationError("stored asset dimensions do not match metadata")

        expected = PurePosixPath(
            _KIND_DIRECTORIES[kind], f"{digest}{extension}"
        ).as_posix()
        if str(asset["relative_path"]) != expected:
            raise AssetValidationError("stored asset path is not content-addressed")
        expected_asset_id = f"asset-{kind.replace('_', '-')}-{digest}"
        if str(asset["asset_id"]) != expected_asset_id:
            raise AssetValidationError("stored asset ID is not content-addressed")
        return True

    @staticmethod
    def _detect_format(payload: bytes) -> tuple[str, str]:
        for signature, media_type, extension in _IMAGE_FORMATS:
            if payload.startswith(signature):
                return media_type, extension
        if (
            len(payload) >= 12
            and payload.startswith(b"RIFF")
            and payload[8:12] == b"WEBP"
        ):
            return "image/webp", ".webp"
        raise AssetValidationError("unsupported image format")

    def _decode_dimensions(self, payload: bytes) -> tuple[int, int]:
        encoded = np.frombuffer(payload, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
        if image is None or image.ndim < 2:
            raise AssetValidationError("image cannot be decoded")
        height, width = (int(value) for value in image.shape[:2])
        if width < 1 or height < 1:
            raise AssetValidationError("image dimensions must be positive")
        if width > self.max_dimension or height > self.max_dimension:
            raise AssetValidationError(
                f"image dimensions exceed {self.max_dimension} x {self.max_dimension}"
            )
        return width, height

    @staticmethod
    def _verify_existing_content(path: Path, digest: str, size_bytes: int) -> None:
        if path.is_symlink() or not path.is_file():
            raise AssetValidationError("content-addressed destination is not a regular file")
        if path.stat().st_size != size_bytes:
            raise AssetValidationError("content-addressed destination has an invalid size")
        try:
            existing_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise AssetValidationError(f"cannot verify stored asset: {exc}") from exc
        if existing_digest != digest:
            raise AssetValidationError("content-addressed destination is corrupted")

    @staticmethod
    def _atomic_write(destination: Path, payload: bytes) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, destination)
        except OSError as exc:
            raise AssetValidationError(f"cannot store asset: {exc}") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
