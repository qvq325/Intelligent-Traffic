"""Stable errors for configuration APIs and application services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ConfigurationError(Exception):
    code: str
    message: str
    status_code: int = 422
    details: list[dict[str, Any]] = field(default_factory=list)
    operation_id: str | None = None
    rollback: str = "not_required"

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "operation_id": self.operation_id,
                "details": self.details,
                "rollback": self.rollback,
            }
        }


def not_found(entity: str, entity_id: str) -> ConfigurationError:
    return ConfigurationError(
        code=f"{entity.upper()}_NOT_FOUND",
        message=f"{entity_id} 不存在",
        status_code=404,
        details=[{"entity": entity, "id": entity_id}],
    )
