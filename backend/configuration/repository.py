"""Low-level SQLite persistence boundary for system configuration."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from .camera_catalog import CameraCatalog
from .schema import (
    MODEL_PIPELINE_SEED_STATEMENT,
    SCHEMA_MIGRATIONS,
    SCHEMA_STATEMENTS,
    SCHEMA_VERSION,
)


SqlParameters = Sequence[Any] | Mapping[str, Any]


class ConfigurationRepositoryError(RuntimeError):
    """Base error for repository initialization and integrity failures."""


class SchemaVersionError(ConfigurationRepositoryError):
    pass


class CameraCatalogMismatchError(ConfigurationRepositoryError):
    pass


def _require_integer_schema_version(metadata: sqlite3.Row) -> int:
    value = metadata["schema_version"]
    if type(value) is not int:
        raise SchemaVersionError(
            "invalid configuration schema version type: expected integer, "
            f"got {type(value).__name__}"
        )
    return value


def _migrate_schema(connection: sqlite3.Connection, from_version: int) -> None:
    current_version = from_version
    while current_version < SCHEMA_VERSION:
        statements = SCHEMA_MIGRATIONS.get(current_version)
        if statements is None:
            raise SchemaVersionError(
                "unsupported configuration schema migration: "
                f"{current_version} -> {SCHEMA_VERSION}"
            )
        for statement in statements:
            connection.execute(statement)
        current_version += 1
        connection.execute(
            """
            UPDATE schema_metadata
            SET schema_version = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE singleton_id = 1
            """,
            (current_version,),
        )
        connection.execute(f"PRAGMA user_version = {current_version}")


class ConfigurationRepository:
    """Own SQLite connection policy while keeping SQL out of application services."""

    def __init__(self, database_path: Path, *, busy_timeout_ms: int = 5000) -> None:
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        self.database_path = Path(database_path)
        self.busy_timeout_ms = int(busy_timeout_ms)

    def initialize(
        self,
        camera_catalog: CameraCatalog,
        *,
        builtin_baseline_version: str = "1",
    ) -> None:
        if not builtin_baseline_version.strip():
            raise ValueError("builtin_baseline_version must not be empty")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

        with self.transaction() as connection:
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            metadata_table_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'schema_metadata'
                """
            ).fetchone()
            metadata = None
            if metadata_table_exists is not None:
                metadata = connection.execute(
                    "SELECT * FROM schema_metadata WHERE singleton_id = 1"
                ).fetchone()

            if metadata is not None:
                metadata_version = _require_integer_schema_version(metadata)
                if metadata_version > SCHEMA_VERSION or user_version > SCHEMA_VERSION:
                    raise SchemaVersionError(
                        "unsupported future configuration schema version: "
                        f"metadata={metadata_version}, user_version={user_version}, "
                        f"supported={SCHEMA_VERSION}"
                    )
                if metadata_version != user_version:
                    raise SchemaVersionError(
                        "inconsistent configuration schema version: "
                        f"metadata={metadata_version}, user_version={user_version}"
                    )
                if metadata_version < SCHEMA_VERSION:
                    connection.execute(
                        "INSERT OR IGNORE INTO detection_settings (singleton_id) VALUES (1)"
                    )
                    _migrate_schema(connection, metadata_version)
            elif user_version != 0:
                raise SchemaVersionError(
                    "configuration schema metadata is missing for "
                    f"user_version={user_version}"
                )

            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)

            metadata = connection.execute(
                "SELECT * FROM schema_metadata WHERE singleton_id = 1"
            ).fetchone()
            if metadata is None:
                connection.execute(
                    """
                    INSERT INTO schema_metadata (
                        singleton_id,
                        schema_version,
                        legacy_migration_completed,
                        builtin_baseline_version,
                        camera_catalog_fingerprint
                    ) VALUES (1, ?, 0, ?, ?)
                    """,
                    (
                        SCHEMA_VERSION,
                        builtin_baseline_version,
                        camera_catalog.fingerprint,
                    ),
                )
            else:
                if _require_integer_schema_version(metadata) != SCHEMA_VERSION:
                    raise SchemaVersionError(
                        "unsupported configuration schema version: "
                        f"{metadata['schema_version']} (expected {SCHEMA_VERSION})"
                    )
                if metadata["camera_catalog_fingerprint"] != camera_catalog.fingerprint:
                    raise CameraCatalogMismatchError(
                        "fixed camera catalog fingerprint does not match the repository"
                    )

            existing_cameras = connection.execute(
                """
                SELECT camera_id, display_name, ordinal, builtin_fingerprint
                FROM camera
                ORDER BY ordinal
                """
            ).fetchall()
            expected_cameras = [
                (
                    entry.camera_id,
                    entry.display_name,
                    entry.ordinal,
                    entry.builtin_fingerprint,
                )
                for entry in camera_catalog
            ]
            if existing_cameras:
                actual_cameras = [tuple(row) for row in existing_cameras]
                if actual_cameras != expected_cameras:
                    raise CameraCatalogMismatchError(
                        "fixed camera rows do not match the supplied catalog"
                    )
            else:
                connection.executemany(
                    """
                    INSERT INTO camera (
                        camera_id, display_name, ordinal, builtin_fingerprint
                    ) VALUES (?, ?, ?, ?)
                    """,
                    expected_cameras,
                )

            connection.execute(
                "INSERT OR IGNORE INTO detection_settings (singleton_id) VALUES (1)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO whitelist_setting (singleton_id) VALUES (1)"
            )
            connection.execute(MODEL_PIPELINE_SEED_STATEMENT)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        messages = self.integrity_check()
        if messages != ["ok"]:
            raise ConfigurationRepositoryError(
                "configuration repository failed integrity checks: " + "; ".join(messages)
            )

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """Open an explicit transaction and commit or roll it back as one unit."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def execute(
        self,
        connection: sqlite3.Connection,
        statement: str,
        parameters: SqlParameters = (),
    ) -> sqlite3.Cursor:
        """Execute a write inside a caller-owned explicit transaction."""

        self._require_connection(connection)
        if not connection.in_transaction:
            raise ConfigurationRepositoryError(
                "execute requires a connection from repository.transaction()"
            )
        return connection.execute(statement, parameters)

    def executemany(
        self,
        connection: sqlite3.Connection,
        statement: str,
        parameter_rows: Iterable[SqlParameters],
    ) -> sqlite3.Cursor:
        """Execute repeated writes inside a caller-owned explicit transaction."""

        self._require_connection(connection)
        if not connection.in_transaction:
            raise ConfigurationRepositoryError(
                "executemany requires a connection from repository.transaction()"
            )
        return connection.executemany(statement, parameter_rows)

    def fetch_one(
        self,
        statement: str,
        parameters: SqlParameters = (),
        *,
        connection: sqlite3.Connection | None = None,
    ) -> sqlite3.Row | None:
        if connection is not None:
            self._require_connection(connection)
            return connection.execute(statement, parameters).fetchone()
        with self._read_connection() as read_connection:
            return read_connection.execute(statement, parameters).fetchone()

    def fetch_all(
        self,
        statement: str,
        parameters: SqlParameters = (),
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[sqlite3.Row]:
        if connection is not None:
            self._require_connection(connection)
            return list(connection.execute(statement, parameters).fetchall())
        with self._read_connection() as read_connection:
            return list(read_connection.execute(statement, parameters).fetchall())

    def integrity_check(self) -> list[str]:
        """Return ``['ok']`` or actionable database/foreign-key failures."""

        with self._read_connection() as connection:
            messages = [
                str(row[0])
                for row in connection.execute("PRAGMA integrity_check").fetchall()
            ]
            foreign_key_failures = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_failures:
            if messages == ["ok"]:
                messages = []
            messages.extend(
                "foreign key violation: "
                f"table={row[0]}, rowid={row[1]}, parent={row[2]}, constraint={row[3]}"
                for row in foreign_key_failures
            )
        return messages or ["ok"]

    def backup_to(self, destination: Path) -> Path:
        """Create a consistent SQLite backup and atomically install it."""

        if not self.database_path.is_file():
            raise FileNotFoundError(self.database_path)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.resolve() == self.database_path.resolve():
            raise ValueError("backup destination must differ from the repository path")

        temporary = destination.with_name(
            f".{destination.name}.{uuid4().hex}.tmp"
        )
        source: sqlite3.Connection | None = None
        target: sqlite3.Connection | None = None
        try:
            source = self._connect()
            target = sqlite3.connect(temporary, isolation_level=None)
            source.backup(target)
            result = [row[0] for row in target.execute("PRAGMA integrity_check").fetchall()]
            if result != ["ok"]:
                raise ConfigurationRepositoryError(
                    "backup failed integrity check: " + "; ".join(map(str, result))
                )
            target.close()
            target = None
            source.close()
            source = None
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()
        return destination

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1000.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @staticmethod
    def _require_connection(connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be a sqlite3.Connection")
