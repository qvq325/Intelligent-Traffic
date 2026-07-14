"""Stable fixed-camera catalog construction and fingerprinting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass


CATALOG_FORMAT = "videotest.camera-catalog"
CATALOG_VERSION = 1


@dataclass(frozen=True, slots=True)
class CameraCatalogEntry:
    camera_id: str
    display_name: str
    ordinal: int
    builtin_fingerprint: str


@dataclass(frozen=True, slots=True)
class CameraCatalog:
    entries: tuple[CameraCatalogEntry, ...]
    fingerprint: str

    def __iter__(self) -> Iterator[CameraCatalogEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def camera_ids(self) -> tuple[str, ...]:
        return tuple(entry.camera_id for entry in self.entries)


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_camera_catalog(cameras: Mapping[str, str]) -> CameraCatalog:
    """Build a catalog in mapping iteration order.

    The mapping is ``camera_id -> display_name``. Production supplies the fixed
    12-camera mapping; tests may use a smaller mapping without weakening the
    repository's ordering and fingerprint guarantees.
    """

    if not isinstance(cameras, Mapping):
        raise TypeError("cameras must be a mapping of camera_id to display_name")
    if not cameras:
        raise ValueError("camera catalog must contain at least one camera")

    canonical_entries: list[dict[str, object]] = []
    entries: list[CameraCatalogEntry] = []
    for ordinal, (raw_camera_id, raw_display_name) in enumerate(cameras.items(), start=1):
        if not isinstance(raw_camera_id, str) or not raw_camera_id.strip():
            raise ValueError("camera_id must be a non-empty string")
        if raw_camera_id != raw_camera_id.strip():
            raise ValueError("camera_id must not contain leading or trailing whitespace")
        if not isinstance(raw_display_name, str) or not raw_display_name.strip():
            raise ValueError(f"display name is required for camera {raw_camera_id!r}")

        canonical = {
            "camera_id": raw_camera_id,
            "display_name": raw_display_name.strip(),
            "ordinal": ordinal,
        }
        canonical_entries.append(canonical)
        entries.append(
            CameraCatalogEntry(
                **canonical,
                builtin_fingerprint=_fingerprint(canonical),
            )
        )

    catalog_payload = {
        "format": CATALOG_FORMAT,
        "version": CATALOG_VERSION,
        "cameras": canonical_entries,
    }
    return CameraCatalog(
        entries=tuple(entries),
        fingerprint=_fingerprint(catalog_payload),
    )
