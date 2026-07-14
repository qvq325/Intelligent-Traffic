"""SQLite-backed system configuration primitives."""

from .camera_catalog import (
    CameraCatalog,
    CameraCatalogEntry,
    build_camera_catalog,
)
from .repository import (
    CameraCatalogMismatchError,
    ConfigurationRepository,
    ConfigurationRepositoryError,
    SchemaVersionError,
)
from .models import (
    MODEL_PIPELINE_SCENE_KEYS,
    ModelPipelineBatchUpdate,
    ModelPipelineConfiguration,
)
from .schema import SCHEMA_TABLES, SCHEMA_VERSION

__all__ = [
    "CameraCatalog",
    "CameraCatalogEntry",
    "CameraCatalogMismatchError",
    "ConfigurationRepository",
    "ConfigurationRepositoryError",
    "MODEL_PIPELINE_SCENE_KEYS",
    "ModelPipelineBatchUpdate",
    "ModelPipelineConfiguration",
    "SCHEMA_TABLES",
    "SCHEMA_VERSION",
    "SchemaVersionError",
    "build_camera_catalog",
]
